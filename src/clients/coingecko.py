"""CoinGecko Pro API client — price, market data, and trending coins."""
from __future__ import annotations

from src.clients.base import BaseAPIClient


class CoinGeckoClient(BaseAPIClient):
    """Async CoinGecko Pro API client with rate limiting and retry."""

    BASE_URL = "https://pro-api.coingecko.com/api/v3"
    AUTH_HEADER = "x-cg-pro-api-key"

    async def get_price(
        self,
        ids: str,
        vs_currencies: str = "usd",
        include_market_cap: bool = True,
        include_24hr_vol: bool = True,
        include_24hr_change: bool = True,
    ) -> dict:
        """Fetch price data for one or more coin IDs (comma-separated)."""
        params: dict = {
            "ids": ids,
            "vs_currencies": vs_currencies,
            "include_market_cap": str(include_market_cap).lower(),
            "include_24hr_vol": str(include_24hr_vol).lower(),
            "include_24hr_change": str(include_24hr_change).lower(),
        }
        return await self.get("/simple/price", params=params)

    async def get_coins_markets(
        self,
        vs_currency: str = "usd",
        order: str = "market_cap_desc",
        per_page: int = 100,
        page: int = 1,
    ) -> list[dict]:
        """Fetch coin market data (price, market cap, volume, changes)."""
        params = {
            "vs_currency": vs_currency,
            "order": order,
            "per_page": per_page,
            "page": page,
        }
        return await self.get("/coins/markets", params=params)

    async def get_trending(self) -> dict:
        """Fetch trending coins, NFTs, and categories on CoinGecko."""
        return await self.get("/search/trending")
