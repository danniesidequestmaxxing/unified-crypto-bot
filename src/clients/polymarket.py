"""Polymarket Gamma API client — Fed rate prediction markets."""
from __future__ import annotations

import json

import aiohttp
import structlog

log = structlog.get_logger()

_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=10)

# FOMC meeting markets to track
FED_MARKETS = [
    {"slug": "fed-decision-in-march-885", "label": "Mar 18, 2026"},
    {"slug": "fed-decision-in-april", "label": "Apr 29, 2026"},
    {"slug": "fed-decision-in-june-825", "label": "Jun 17, 2026"},
]


class PolymarketClient:
    """Async Polymarket Gamma API client."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT)
        return self._session

    async def get_fed_data(self) -> list[dict]:
        """Fetch Fed rate prediction data from all tracked FOMC markets."""
        session = await self._ensure_session()
        results = []

        for m in FED_MARKETS:
            try:
                async with session.get(
                    f"https://gamma-api.polymarket.com/events/slug/{m['slug']}"
                ) as resp:
                    if not resp.ok:
                        results.append({"label": m["label"], "outcomes": {}})
                        continue
                    data = await resp.json()

                outcomes = {}
                for market in data.get("markets", []):
                    question = market.get("question", "")
                    prices = market.get("outcomePrices", "[]")
                    try:
                        prices_list = json.loads(prices) if isinstance(prices, str) else prices
                        yes_price = float(prices_list[0]) * 100
                    except Exception:
                        yes_price = 0

                    q = question.lower()
                    if "no change" in q or "hold" in q or "unchanged" in q:
                        label = "No Change"
                    elif "50+" in q or "50 +" in q or "50 basis" in q:
                        label = "50+ bps Cut"
                    elif ("25" in q and ("cut" in q or "decrease" in q or "lower" in q
                                         or "reduction" in q or "basis" in q)) and "50" not in q:
                        label = "25 bps Cut"
                    elif "increase" in q or "hike" in q or "raise" in q:
                        label = "25+ bps Hike"
                    else:
                        label = question[:40]

                    outcomes[label] = round(yes_price, 1)
                results.append({"label": m["label"], "outcomes": outcomes})

            except Exception:
                results.append({"label": m["label"], "outcomes": {}})

        return results

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
