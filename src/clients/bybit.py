"""Bybit API client — BTC derivatives data (async)."""
from __future__ import annotations

import aiohttp
import structlog

log = structlog.get_logger()

_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=10)


class BybitClient:
    """Async Bybit API client for futures data."""

    def __init__(self) -> None:
        self.base_url = "https://api.bybit.com"
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self._session

    async def _get(self, path: str, params: dict | None = None) -> dict:
        session = await self._ensure_session()
        async with session.get(f"{self.base_url}{path}", params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_btc_derivatives(self) -> dict:
        """Aggregate BTC derivatives data from Bybit."""
        result: dict = {}

        # Open Interest
        try:
            data = await self._get(
                "/v5/market/tickers",
                {"category": "linear", "symbol": "BTCUSDT"},
            )
            lst = data.get("result", {}).get("list", [])
            if lst:
                result["oi_usd"] = round(float(lst[0].get("openInterestValue", 0)) / 1e9, 2)
            else:
                result["oi_usd"] = None
        except Exception:
            result["oi_usd"] = None

        # Funding Rate
        try:
            data = await self._get(
                "/v5/market/funding/history",
                {"category": "linear", "symbol": "BTCUSDT", "limit": 1},
            )
            lst = data.get("result", {}).get("list", [])
            result["funding_rate"] = round(float(lst[0]["fundingRate"]) * 100, 4) if lst else None
        except Exception:
            result["funding_rate"] = None

        # L/S Ratio
        try:
            data = await self._get(
                "/v5/market/account-ratio",
                {"category": "linear", "symbol": "BTCUSDT", "period": "5min", "limit": 1},
            )
            lst = data.get("result", {}).get("list", [])
            if lst:
                buy_ratio = float(lst[0]["buyRatio"])
                result["long_pct"] = round(buy_ratio * 100, 1)
                result["short_pct"] = round((1 - buy_ratio) * 100, 1)
            else:
                result["long_pct"] = None
                result["short_pct"] = None
        except Exception:
            result["long_pct"] = None
            result["short_pct"] = None

        return result

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
