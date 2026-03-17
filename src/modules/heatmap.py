"""Module 1: Heatmap Sniper — BTC & ETH liquidation magnet detection.

Ported from liquidation-bot modules/heatmap.py.
"""
from __future__ import annotations

import asyncio

import structlog

from src.clients.coinglass import CoinGlassHobbyistClient, CoinGlassPrimeClient
from src.config import Settings
from src.core.database import Database
from src.delivery.alerts import TelegramDelivery

log = structlog.get_logger()

SYMBOLS = ("BTC", "ETH")


class HeatmapSniper:
    def __init__(
        self, prime_client: CoinGlassPrimeClient,
        hobbyist_client: CoinGlassHobbyistClient,
        db: Database, telegram: TelegramDelivery, settings: Settings,
    ) -> None:
        self.prime = prime_client
        self.hobbyist = hobbyist_client
        self.db = db
        self.telegram = telegram
        self.settings = settings

    async def run_forever(self) -> None:
        log.info("heatmap_sniper_started", poll_seconds=self.settings.heatmap_poll_seconds)
        while True:
            for symbol in SYMBOLS:
                try:
                    await self._check_symbol(symbol)
                except Exception as exc:
                    log.error("heatmap_check_error", symbol=symbol, error=str(exc))
            await asyncio.sleep(self.settings.heatmap_poll_seconds)

    async def _check_symbol(self, symbol: str) -> None:
        data = await self.prime.get_liquidation_heatmap(symbol)
        if not data or data.get("code") != "0":
            log.warning("heatmap_bad_response", symbol=symbol, code=data.get("code"))
            return

        raw = data.get("data")
        if not raw:
            return

        mid_price = self._extract_mid_price(raw)
        if mid_price is None or mid_price <= 0:
            return

        clusters = self._find_nearby_clusters(raw, mid_price)
        total_liq = sum(c["liq_value_usd"] for c in clusters)
        await self.db.insert_heatmap(symbol, mid_price, clusters, alert_sent=False)

        log.info("heatmap_scan", symbol=symbol, mid_price=mid_price,
                 clusters=len(clusters), total_liq_usd=total_liq)

        if total_liq >= self.settings.heatmap_min_liq_usd:
            if await self.db.was_recently_alerted("heatmap", symbol, minutes=30):
                return

            target_price = None
            if clusters:
                dominant = max(clusters, key=lambda c: c["liq_value_usd"])
                target_price = dominant["price"]

            await self.telegram.send_heatmap_alert(
                symbol=symbol, mid_price=mid_price, clusters=clusters,
                total_liq_usd=total_liq, target_price=target_price,
            )

    @staticmethod
    def _extract_mid_price(raw: dict | list) -> float | None:
        if isinstance(raw, dict):
            if "currentPrice" in raw:
                return float(raw["currentPrice"])
            if "price" in raw:
                return float(raw["price"])

        if isinstance(raw, list) and len(raw) > 0:
            prices = []
            for item in raw:
                if isinstance(item, dict) and "price" in item:
                    prices.append(float(item["price"]))
            if prices:
                return (min(prices) + max(prices)) / 2

        if isinstance(raw, dict):
            for key in ("data", "list", "prices"):
                nested = raw.get(key)
                if nested:
                    result = HeatmapSniper._extract_mid_price(nested)
                    if result:
                        return result
        return None

    def _find_nearby_clusters(self, raw: dict | list, mid_price: float) -> list[dict]:
        delta = self.settings.heatmap_delta_pct
        lower = mid_price * (1 - delta)
        upper = mid_price * (1 + delta)
        levels = self._extract_levels(raw)
        return [
            {"price": lv["price"], "liq_value_usd": lv["liq_value_usd"]}
            for lv in levels
            if lower <= lv.get("price", 0) <= upper and lv.get("liq_value_usd", 0) > 0
        ]

    @staticmethod
    def _extract_levels(raw: dict | list) -> list[dict]:
        levels: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    price = item.get("price") or item.get("p")
                    liq = (item.get("liq_value_usd") or item.get("liquidation")
                           or item.get("vol") or item.get("value") or 0)
                    if price is not None:
                        levels.append({"price": float(price), "liq_value_usd": float(liq)})
        elif isinstance(raw, dict):
            for key in ("data", "list", "prices", "liquidationLevels"):
                nested = raw.get(key)
                if isinstance(nested, list):
                    levels = HeatmapSniper._extract_levels(nested)
                    if levels:
                        break
        return levels
