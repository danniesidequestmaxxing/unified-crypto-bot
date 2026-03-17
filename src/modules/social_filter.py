"""Module 3: Social Velocity Filter — Elfa AI integration.

Ported from liquidation-bot modules/social.py.
"""
from __future__ import annotations

import structlog

from src.clients.elfa import ElfaClient
from src.config import Settings
from src.core.database import Database

log = structlog.get_logger()


class SocialFilter:
    def __init__(
        self, elfa_client: ElfaClient, db: Database, settings: Settings,
    ) -> None:
        self.elfa = elfa_client
        self.db = db
        self.settings = settings
        self._trending_cache: dict | None = None

    async def invalidate_trending_cache(self) -> None:
        self._trending_cache = None

    async def check(self, symbol: str) -> tuple[bool, float | None, float | None]:
        """Run the Social Velocity Filter for *symbol*."""
        try:
            is_trending = await self._is_trending(symbol)
            mention_count_recent = await self._get_recent_mentions(symbol)
            mention_7d_ma = await self._calc_7day_ma(symbol)

            ghost_confirmed = False
            if mention_7d_ma is not None and mention_7d_ma > 0:
                ghost_confirmed = (mention_count_recent < mention_7d_ma) and (not is_trending)

            await self.db.insert_social(
                symbol=symbol, mention_count_recent=mention_count_recent,
                mention_7d_ma=mention_7d_ma or 0,
                is_trending=is_trending, ghost_confirmed=ghost_confirmed,
            )

            log.info("social_check", symbol=symbol,
                     mentions_recent=mention_count_recent,
                     ma_7d=mention_7d_ma, trending=is_trending, ghost=ghost_confirmed)
            return ghost_confirmed, mention_count_recent, mention_7d_ma

        except Exception as exc:
            log.error("social_check_error", symbol=symbol, error=str(exc))
            return False, None, None

    async def _is_trending(self, symbol: str) -> bool:
        if self._trending_cache is None:
            self._trending_cache = await self.elfa.get_trending_tokens("24h")

        tokens = self._trending_cache.get("data", [])
        if isinstance(tokens, dict):
            tokens = tokens.get("items", []) or tokens.get("tokens", [])

        symbol_upper = symbol.upper()
        for token in tokens:
            if isinstance(token, dict):
                tok_symbol = (
                    token.get("symbol", "")
                    or token.get("ticker", "")
                    or token.get("name", "")
                )
                if tok_symbol.upper().strip("$") == symbol_upper:
                    return True
        return False

    async def _get_recent_mentions(self, symbol: str) -> float:
        data = await self.elfa.get_top_mentions(symbol, time_window="4h")
        items = data.get("data", {})
        if isinstance(items, dict):
            items = items.get("items", []) or items.get("mentions", [])
        return float(len(items)) if isinstance(items, list) else 0.0

    async def _calc_7day_ma(self, symbol: str) -> float | None:
        data = await self.elfa.get_top_mentions_24h(symbol)
        items = data.get("data", {})
        if isinstance(items, dict):
            items = items.get("items", []) or items.get("mentions", [])
        if isinstance(items, list):
            return float(len(items))
        return None
