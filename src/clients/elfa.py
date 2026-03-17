"""Unified Elfa AI client — covers both liquidation-bot and elfa-intel endpoints.

Async, rate-limited, with retry via BaseAPIClient.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.clients.base import BaseAPIClient

TIMEFRAME_DELTAS = {
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}

VALID_TIMEFRAMES = list(TIMEFRAME_DELTAS.keys())


def _timeframe_to_from_to(timeframe: str) -> tuple[int, int]:
    """Convert a timeframe string to (from_ts, to_ts) as epoch seconds."""
    now = datetime.now(timezone.utc)
    from_dt = now - TIMEFRAME_DELTAS[timeframe]
    return int(from_dt.timestamp()), int(now.timestamp())


class ElfaClient(BaseAPIClient):
    """Elfa AI REST API — social intelligence for crypto.

    Merges all endpoints from both elfa-intel and liquidation-bot.
    """

    BASE_URL = "https://api.elfa.ai"
    AUTH_HEADER = "x-elfa-api-key"

    # ── Health ─────────────────────────────────────────

    async def ping(self) -> dict:
        return await self.get("/v2/ping")

    # ── Trending Tokens (from/to timestamps) ───────────

    async def get_trending_tokens(self, timeframe: str = "24h") -> dict:
        from_ts, to_ts = _timeframe_to_from_to(timeframe)
        return await self.get(
            "/v2/aggregations/trending-tokens",
            params={"from": from_ts, "to": to_ts},
        )

    # ── Top Mentions (timeWindow param) ────────────────

    async def get_top_mentions(
        self, ticker: str, time_window: str = "4h", limit: int = 100,
    ) -> dict:
        return await self.get(
            "/v2/data/top-mentions",
            params={"ticker": f"${ticker}", "timeWindow": time_window, "limit": limit},
        )

    async def get_top_mentions_24h(self, ticker: str, limit: int = 100) -> dict:
        return await self.get_top_mentions(ticker, time_window="24h", limit=limit)

    # ── Keyword Mentions (timeWindow param) ────────────

    async def keyword_mentions(
        self, keywords: str, timeframe: str = "24h", limit: int = 20,
    ) -> dict:
        return await self.get(
            "/v2/data/keyword-mentions",
            params={"keywords": keywords, "timeWindow": timeframe, "limit": limit},
        )

    # ── Account Smart Stats ────────────────────────────

    async def account_smart_stats(self, username: str) -> dict:
        return await self.get("/v2/account/smart-stats", params={"username": username})

    # ── Event Summary (5 credits) ──────────────────────

    async def event_summary(self, keywords: str, timeframe: str = "24h") -> dict:
        return await self.get(
            "/v2/data/event-summary",
            params={"keywords": keywords, "timeWindow": timeframe},
        )

    # ── Trending Narratives (5 credits) ────────────────

    async def trending_narratives(self, timeframe: str = "24h") -> dict:
        return await self.get(
            "/v2/data/trending-narratives",
            params={"timeWindow": timeframe},
        )

    # ── Token News (timeWindow param) ──────────────────

    async def token_news(
        self, ticker: str, timeframe: str = "24h", limit: int = 10,
    ) -> dict:
        return await self.get(
            "/v2/data/token-news",
            params={"ticker": ticker, "timeWindow": timeframe, "limit": limit},
        )

    # ── Trending Contract Addresses (from/to) ──────────

    async def trending_cas_twitter(self, timeframe: str = "24h") -> dict:
        from_ts, to_ts = _timeframe_to_from_to(timeframe)
        return await self.get(
            "/v2/aggregations/trending-cas/twitter",
            params={"from": from_ts, "to": to_ts},
        )

    async def trending_cas_telegram(self, timeframe: str = "24h") -> dict:
        from_ts, to_ts = _timeframe_to_from_to(timeframe)
        return await self.get(
            "/v2/aggregations/trending-cas/telegram",
            params={"from": from_ts, "to": to_ts},
        )

    # ── AI Chat (PAYG+ plan) ──────────────────────────

    async def chat(
        self, message: str, session_id: str | None = None,
        analysis_type: str = "chat", asset_metadata: dict | None = None,
    ) -> dict:
        body: dict = {"message": message, "analysisType": analysis_type}
        if session_id:
            body["sessionId"] = session_id
        if asset_metadata:
            body["assetMetadata"] = asset_metadata
        return await self.post("/v2/chat", json=body)
