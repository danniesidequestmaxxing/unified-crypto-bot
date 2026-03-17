"""Unified async Binance client — spot + futures data.

Merges marketbot (BTC derivatives) and pinescript-bot (klines, ticker) into
a single async client using aiohttp.
"""
from __future__ import annotations

import aiohttp
import structlog

log = structlog.get_logger()

_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=10)


class BinanceClient:
    """Async Binance API client for spot and futures data."""

    def __init__(self, base_url: str = "https://api.binance.com") -> None:
        self.base_url = base_url
        self.futures_url = "https://fapi.binance.com"
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self._session

    async def _get(self, url: str, params: dict | None = None) -> dict | list:
        session = await self._ensure_session()
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ── Spot ───────────────────────────────────────────

    async def get_ticker_24hr(self, symbol: str = "BTCUSDT") -> dict:
        return await self._get(
            f"{self.base_url}/api/v3/ticker/24hr", {"symbol": symbol},
        )

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 60,
        start_time: int | None = None,
    ) -> list:
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        return await self._get(f"{self.base_url}/api/v3/klines", params)

    # ── Futures ────────────────────────────────────────

    async def get_futures_oi(self, symbol: str = "BTCUSDT") -> dict | None:
        try:
            oi = await self._get(
                f"{self.futures_url}/fapi/v1/openInterest", {"symbol": symbol},
            )
            price_data = await self._get(
                f"{self.futures_url}/fapi/v1/ticker/price", {"symbol": symbol},
            )
            btc_price = float(price_data["price"]) if price_data else 0
            return {
                "oi_usd": round(float(oi["openInterest"]) * btc_price / 1e9, 2),
            }
        except Exception:
            return None

    async def get_futures_funding(self, symbol: str = "BTCUSDT") -> float | None:
        try:
            data = await self._get(
                f"{self.futures_url}/fapi/v1/fundingRate",
                {"symbol": symbol, "limit": 1},
            )
            return round(float(data[0]["fundingRate"]) * 100, 4)
        except Exception:
            return None

    async def get_futures_ls_ratio(self, symbol: str = "BTCUSDT") -> dict | None:
        try:
            data = await self._get(
                f"{self.futures_url}/futures/data/globalLongShortAccountRatio",
                {"symbol": symbol, "period": "5m", "limit": 1},
            )
            d = data[0]
            return {
                "long_pct": round(float(d["longAccount"]) * 100, 1),
                "short_pct": round(float(d["shortAccount"]) * 100, 1),
            }
        except Exception:
            return None

    async def get_btc_derivatives(self) -> dict:
        """Aggregate BTC derivatives data from Binance Futures."""
        result: dict = {}
        oi = await self.get_futures_oi()
        result["oi_usd"] = oi["oi_usd"] if oi else None
        result["funding_rate"] = await self.get_futures_funding()
        ls = await self.get_futures_ls_ratio()
        if ls:
            result["long_pct"] = ls["long_pct"]
            result["short_pct"] = ls["short_pct"]
        else:
            result["long_pct"] = None
            result["short_pct"] = None
        return result

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
