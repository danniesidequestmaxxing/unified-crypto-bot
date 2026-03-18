"""Market analysis functions — ported from marketbot, using shared ClaudeService.

All functions use the deep (Opus) model for thorough analysis.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.ai.prompts import (
    FED_ANALYSIS_PROMPT,
    MARKET_SNAPSHOT_PROMPT,
    NEWS_SUMMARY_PROMPT,
    WEEKLY_REPORT_PROMPT,
)
from src.clients.claude import ClaudeService


class MarketAnalyst:
    """Claude-powered market analysis using the deep model."""

    def __init__(self, claude: ClaudeService) -> None:
        self.claude = claude

    async def market_snapshot(
        self, btc: dict, bin_f: dict, byb_f: dict, hl_f: dict,
    ) -> str:
        """Analyze BTC derivatives snapshot across exchanges."""

        def fmt_oi(d: dict) -> str:
            return f"${d['oi_usd']:.2f}B" if d.get("oi_usd") is not None else "N/A"

        def fmt_fr(d: dict) -> str:
            return f"{d['funding_rate']:.4f}%" if d.get("funding_rate") is not None else "N/A"

        def fmt_ls(d: dict) -> str:
            return f"L {d['long_pct']:.1f}% / S {d['short_pct']:.1f}%" if d.get("long_pct") is not None else "N/A"

        data = (
            f"BTC Price: ${btc.get('price', 0):,} ({btc.get('change_24h', 0):.2f}% 24h)  |  "
            f"Volume: ${btc.get('volume_24h', 0):,.0f} (Binance)\n\n"
            f"Derivatives snapshot across exchanges (5min L/S):\n\n"
            f"Binance Futures:\n- OI: {fmt_oi(bin_f)}  Funding: {fmt_fr(bin_f)}  L/S (5m): {fmt_ls(bin_f)}\n\n"
            f"Bybit:\n- OI: {fmt_oi(byb_f)}  Funding: {fmt_fr(byb_f)}  L/S (5m): {fmt_ls(byb_f)}\n\n"
            f"Hyperliquid:\n- OI: {fmt_oi(hl_f)}  Funding: {fmt_fr(hl_f)}"
        )

        prompt = MARKET_SNAPSHOT_PROMPT.format(data=data)
        return await self.claude.complete_deep(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )

    async def news_summary(
        self, news_items: list[dict], extra_context: str = "",
    ) -> str:
        """Summarize crypto news headlines with optional extra intelligence."""
        headlines = "\n".join(
            f"- [{n['source']}] {n['title']}" for n in news_items
        ) if news_items else "(No CoinDesk headlines available)"
        prompt = NEWS_SUMMARY_PROMPT.format(headlines=headlines)
        if extra_context:
            prompt += f"\n\nAdditional intelligence:\n{extra_context}"
        return await self.claude.complete_deep(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )

    async def weekly_report(self, news_items: list[dict], btc: dict) -> str:
        """Generate weekly catalyst report."""
        headlines = "\n".join(
            f"- [{n['source']}] {n['title']}" for n in news_items
        )
        prompt = WEEKLY_REPORT_PROMPT.format(
            price=btc.get("price", 0),
            change=btc.get("change_24h", 0),
            date=datetime.now().strftime("%A %B %d, %Y"),
            headlines=headlines,
        )
        return await self.claude.complete_deep(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )

    async def fed_analysis(self, summary: str) -> str:
        """Analyze Fed rate predictions from Polymarket."""
        prompt = FED_ANALYSIS_PROMPT.format(summary=summary)
        return await self.claude.complete_deep(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
