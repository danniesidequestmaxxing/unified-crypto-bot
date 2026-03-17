"""Module 2: Ghost Anomaly Screener — stealth positioning detection on altcoins.

Ported from liquidation-bot modules/ghost.py.
"""
from __future__ import annotations

import asyncio

import structlog

from src.clients.coinglass import CoinGlassHobbyistClient
from src.config import Settings
from src.core.database import Database
from src.delivery.alerts import TelegramDelivery
from src.modules.social_filter import SocialFilter

log = structlog.get_logger()


class GhostScreener:
    def __init__(
        self, hobbyist_client: CoinGlassHobbyistClient,
        social_filter: SocialFilter, db: Database,
        telegram: TelegramDelivery, settings: Settings,
    ) -> None:
        self.hobbyist = hobbyist_client
        self.social = social_filter
        self.db = db
        self.telegram = telegram
        self.settings = settings

    async def run_forever(self) -> None:
        log.info("ghost_screener_started", poll_seconds=self.settings.ghost_poll_seconds)
        while True:
            try:
                await self._screen_cycle()
            except Exception as exc:
                log.error("ghost_cycle_error", error=str(exc))
            await asyncio.sleep(self.settings.ghost_poll_seconds)

    async def _screen_cycle(self) -> None:
        await self.social.invalidate_trending_cache()
        markets_data = await self.hobbyist.get_coins_markets()
        top_alts = self._pick_top_altcoins(markets_data, n=100)
        if not top_alts:
            return

        log.info("ghost_screening", count=len(top_alts))
        sem = asyncio.Semaphore(10)
        tasks = [self._check_coin(sem, coin) for coin in top_alts]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_coin(self, sem: asyncio.Semaphore, coin: dict) -> None:
        async with sem:
            symbol = coin["symbol"]
            try:
                oi_data = await self.hobbyist.get_aggregated_oi_history(symbol)
                current_oi = self._extract_current_oi(oi_data)
                fr_data = await self.hobbyist.get_funding_rate(symbol)
                funding = self._extract_avg_funding(fr_data)
                prev_oi = await self.db.get_oi_1h_ago(symbol)

                oi_change: float | None = None
                if prev_oi and prev_oi > 0 and current_oi > 0:
                    oi_change = (current_oi - prev_oi) / prev_oi

                volume_usd = coin.get("volume_usd")
                price = coin.get("price")

                await self.db.insert_oi_funding(
                    symbol=symbol, oi_usd=current_oi,
                    oi_change_1h_pct=oi_change, volume_usd=volume_usd,
                    funding_rate=funding, flagged=False,
                )

                if oi_change is None or funding is None:
                    return

                is_anomaly = (
                    oi_change > self.settings.ghost_oi_change_threshold
                    and funding <= 0
                )
                if not is_anomaly:
                    return

                await self.db.insert_oi_funding(
                    symbol=symbol, oi_usd=current_oi,
                    oi_change_1h_pct=oi_change, volume_usd=volume_usd,
                    funding_rate=funding, flagged=True,
                )

                if await self.db.was_recently_alerted("ghost", symbol, minutes=60):
                    return
                if await self.db.was_recently_alerted("ghost+social", symbol, minutes=60):
                    return

                ghost_confirmed, mention_count, mention_7d_ma = await self.social.check(symbol)
                await self.telegram.send_ghost_alert(
                    symbol=symbol, price=price, oi_change_pct=oi_change,
                    funding_rate=funding, volume_usd=volume_usd,
                    ghost_confirmed=ghost_confirmed,
                    mention_count=mention_count, mention_7d_ma=mention_7d_ma,
                )
            except Exception as exc:
                log.error("ghost_coin_error", symbol=symbol, error=str(exc))

    def _pick_top_altcoins(self, markets_data: dict, n: int = 100) -> list[dict]:
        raw = markets_data.get("data", [])
        if not isinstance(raw, list):
            return []
        excluded = {"BTC", "ETH"} | set(self.settings.excluded_symbols)
        alts = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol", "")
            if sym.upper() in excluded:
                continue
            vol = self._safe_float(item.get("volUsd") or item.get("vol24hUsd") or item.get("turnoverUsd"))
            price = self._safe_float(item.get("price") or item.get("lastPrice"))
            if vol is not None and vol > 0:
                alts.append({"symbol": sym, "volume_usd": vol, "price": price})
        alts.sort(key=lambda x: x["volume_usd"], reverse=True)
        return alts[:n]

    @staticmethod
    def _extract_current_oi(oi_data: dict) -> float:
        data = oi_data.get("data", [])
        if isinstance(data, list) and data:
            last = data[-1]
            if isinstance(last, dict):
                return float(last.get("c") or last.get("close") or last.get("oi") or 0)
        return 0.0

    @staticmethod
    def _extract_avg_funding(fr_data: dict) -> float | None:
        data = fr_data.get("data", [])
        if not isinstance(data, list) or not data:
            return None
        rates = []
        for item in data:
            if isinstance(item, dict):
                rate = item.get("rate") or item.get("fundingRate") or item.get("r")
                if rate is not None:
                    try:
                        rates.append(float(rate))
                    except (ValueError, TypeError):
                        pass
        return sum(rates) / len(rates) if rates else None

    @staticmethod
    def _safe_float(val) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
