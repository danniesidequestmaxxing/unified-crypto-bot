"""CoinGlass API clients — Prime (heatmap) and Hobbyist (OI, funding, markets)."""
from __future__ import annotations

from src.clients.base import BaseAPIClient


class CoinGlassPrimeClient(BaseAPIClient):
    """Prime-tier client — liquidation heatmap endpoints."""

    BASE_URL = "https://open-api-v4.coinglass.com"
    AUTH_HEADER = "CG-API-KEY"

    async def get_liquidation_heatmap(
        self, symbol: str = "BTC", exchange: str = "Binance",
    ) -> dict:
        return await self.get(
            "/api/futures/liquidation/heatmap/model3",
            params={"exchange": exchange, "symbol": f"{symbol}USDT"},
        )


class CoinGlassHobbyistClient(BaseAPIClient):
    """Hobbyist-tier client — OI, funding rates, market overview."""

    BASE_URL = "https://open-api-v4.coinglass.com"
    AUTH_HEADER = "CG-API-KEY"

    async def get_coins_markets(self) -> dict:
        return await self.get("/api/futures/coins-markets")

    async def get_aggregated_oi_history(
        self, symbol: str, interval: str = "1h", limit: int = 4,
    ) -> dict:
        return await self.get(
            "/api/futures/open-interest/aggregated-history",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )

    async def get_funding_rate(self, symbol: str) -> dict:
        return await self.get(
            "/api/futures/funding-rate/exchange-list",
            params={"symbol": symbol},
        )
