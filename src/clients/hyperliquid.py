"""Hyperliquid API client — BTC derivatives data (async)."""
from __future__ import annotations

import aiohttp
import structlog

log = structlog.get_logger()

_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=10)
API_URL = "https://api.hyperliquid.xyz/info"


class HyperliquidClient:
    """Async Hyperliquid API client."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self._session

    async def get_btc_derivatives(self) -> dict:
        """Fetch BTC OI and funding from Hyperliquid."""
        result: dict = {"oi_usd": None, "funding_rate": None,
                        "long_pct": None, "short_pct": None}
        try:
            session = await self._ensure_session()
            async with session.post(
                API_URL,
                json={"type": "metaAndAssetCtxs"},
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.ok:
                    data = await resp.json()
                    universe = data[0].get("universe", [])
                    ctxs = data[1]
                    btc_idx = next(
                        (i for i, a in enumerate(universe) if a["name"] == "BTC"),
                        None,
                    )
                    if btc_idx is not None:
                        ctx = ctxs[btc_idx]
                        oi_usd = float(ctx.get("openInterest", 0)) * float(ctx.get("markPx", 0))
                        result["oi_usd"] = round(oi_usd / 1e9, 2)
                        result["funding_rate"] = round(float(ctx.get("funding", 0)) * 100, 4)
        except Exception:
            pass
        return result

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
