"""CoinGecko API client — BTC market cap (async, no key required)."""
from __future__ import annotations

import aiohttp
import structlog

log = structlog.get_logger()

_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)


async def get_btc_market_cap() -> float:
    """Fetch BTC market cap from CoinGecko. Returns 0 on failure."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd",
                        "include_market_cap": "true"},
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                if resp.ok:
                    data = await resp.json()
                    return data.get("bitcoin", {}).get("usd_market_cap", 0)
    except Exception:
        pass
    return 0
