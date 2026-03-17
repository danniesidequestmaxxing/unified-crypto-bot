"""Telegram push alert delivery — for background modules (heatmap, ghost).

Ported from liquidation-bot telegram_bot.py.
"""
from __future__ import annotations

import structlog
from telegram import Bot
from telegram.constants import ParseMode

from src.core.database import Database

log = structlog.get_logger()


class TelegramDelivery:
    def __init__(self, token: str, chat_id: str, db: Database) -> None:
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.db = db

    async def send_heatmap_alert(
        self, symbol: str, mid_price: float, clusters: list[dict],
        total_liq_usd: float, target_price: float | None = None,
    ) -> None:
        direction = ""
        if target_price is not None:
            direction = "ABOVE" if target_price > mid_price else "BELOW"

        lines = [
            f"<b>LIQUIDATION MAGNET — {symbol}</b>", "",
            f"Mid Price: <code>${mid_price:,.2f}</code>",
            f"Liq Cluster: <code>${total_liq_usd:,.0f}</code> within ±1%",
            f"Clusters: <code>{len(clusters)}</code> price levels",
        ]
        if target_price is not None:
            lines.append(f"Target Zone: <code>${target_price:,.2f}</code> ({direction})")
        lines += ["", "<i>High probability of volatility / reversal.</i>"]
        msg = "\n".join(lines)
        await self._send(msg, module="heatmap", symbol=symbol)

    async def send_ghost_alert(
        self, symbol: str, price: float | None, oi_change_pct: float,
        funding_rate: float, volume_usd: float | None,
        ghost_confirmed: bool, mention_count: float | None = None,
        mention_7d_ma: float | None = None,
    ) -> None:
        tag = "GHOST CONFIRMED" if ghost_confirmed else "ANOMALY DETECTED"
        lines = [f"<b>{tag} — {symbol}</b>", ""]
        if price is not None:
            lines.append(f"Price: <code>${price:,.4f}</code>")
        lines += [
            f"OI Change (1h): <code>{oi_change_pct:+.1%}</code>",
            f"Funding Rate: <code>{funding_rate:.4%}</code>",
        ]
        if volume_usd is not None:
            lines.append(f"24h Volume: <code>${volume_usd:,.0f}</code>")

        if ghost_confirmed and mention_count is not None and mention_7d_ma is not None:
            lines += [
                "", "<b>Social Divergence:</b>",
                f"  Recent Mentions: <code>{mention_count:.0f}</code>",
                f"  7d MA: <code>{mention_7d_ma:.1f}</code>",
                "  Trending: <code>NO</code>",
            ]

        lines += [
            "",
            "<i>Stealth accumulation detected — institutional positioning before retail awareness.</i>"
            if ghost_confirmed
            else "<i>OI surge with flat/negative funding — monitor closely.</i>",
        ]
        msg = "\n".join(lines)
        module = "ghost+social" if ghost_confirmed else "ghost"
        await self._send(msg, module=module, symbol=symbol)

    async def _send(self, text: str, module: str, symbol: str) -> None:
        try:
            await self.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode=ParseMode.HTML,
            )
            await self.db.insert_alert(module, symbol, text, telegram_ok=True)
            log.info("telegram_sent", module=module, symbol=symbol)
        except Exception as exc:
            log.error("telegram_send_failed", module=module, symbol=symbol, error=str(exc))
            await self.db.insert_alert(module, symbol, text, telegram_ok=False)
