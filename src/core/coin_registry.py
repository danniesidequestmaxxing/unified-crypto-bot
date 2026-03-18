"""Dynamic coin registry — fetches top coins from CoinGecko at startup and refreshes periodically."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import structlog

from src.clients.coingecko import CoinGeckoClient

log = structlog.get_logger()

# Hardcoded fallback for coins that CoinGecko may list under unexpected symbols
_STATIC_OVERRIDES: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "DOT": "polkadot", "MATIC": "matic-network", "LINK": "chainlink",
    "UNI": "uniswap", "ATOM": "cosmos", "LTC": "litecoin", "ARB": "arbitrum",
    "OP": "optimism", "APT": "aptos", "SUI": "sui", "NEAR": "near",
    "FTM": "fantom", "INJ": "injective-protocol", "TIA": "celestia",
    "SEI": "sei-network", "JUP": "jupiter-exchange-solana", "WIF": "dogwifcoin",
    "PEPE": "pepe", "BONK": "bonk", "FIL": "filecoin", "RENDER": "render-token",
    "TAO": "bittensor", "WLD": "worldcoin-wld", "STRK": "starknet",
    "AAVE": "aave", "MKR": "maker", "PENDLE": "pendle", "ASTER": "aster-2",
}

# Refresh interval (6 hours)
_REFRESH_INTERVAL = 6 * 60 * 60


class CoinRegistry:
    """Maintains a live symbol→CoinGecko-ID mapping for top coins."""

    def __init__(
        self,
        coingecko: CoinGeckoClient,
        on_update: Callable[[dict[str, str], set[str]], None] | None = None,
    ) -> None:
        self._cg = coingecko
        self._symbol_to_id: dict[str, str] = dict(_STATIC_OVERRIDES)
        self._known: set[str] = set(self._symbol_to_id.keys())
        self._task: asyncio.Task | None = None
        self._on_update = on_update

    @property
    def symbol_to_id(self) -> dict[str, str]:
        return self._symbol_to_id

    @property
    def known_coins(self) -> set[str]:
        return self._known

    async def load(self, pages: int = 5) -> None:
        """Fetch top coins (100 per page) from CoinGecko and build the mapping."""
        merged: dict[str, str] = dict(_STATIC_OVERRIDES)
        for page in range(1, pages + 1):
            try:
                coins = await self._cg.get_coins_markets(per_page=100, page=page)
                for coin in coins:
                    symbol = coin.get("symbol", "").upper()
                    gecko_id = coin.get("id", "")
                    if symbol and gecko_id and symbol not in merged:
                        merged[symbol] = gecko_id
            except Exception as exc:
                log.warning("coin_registry_page_failed", page=page, error=str(exc))
                break  # Don't hammer API if it's failing

        self._symbol_to_id = merged
        self._known = set(merged.keys())
        log.info("coin_registry_loaded", count=len(merged))
        if self._on_update:
            self._on_update(self._symbol_to_id, self._known)

    async def _refresh_loop(self) -> None:
        """Background loop to keep the registry fresh."""
        while True:
            await asyncio.sleep(_REFRESH_INTERVAL)
            try:
                await self.load()
            except Exception as exc:
                log.warning("coin_registry_refresh_failed", error=str(exc))

    def start_refresh(self) -> None:
        """Start the background refresh task."""
        if self._task is None:
            self._task = asyncio.create_task(self._refresh_loop())

    def stop_refresh(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
