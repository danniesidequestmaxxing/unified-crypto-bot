"""/fed — Polymarket Fed rate predictions + Claude Opus analysis."""
from __future__ import annotations

from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.core.message_utils import send_plain_chunks


async def cmd_fed(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
    polymarket = deps["polymarket"]
    analyst = deps["market_analyst"]
    db = deps["db"]
    settings = deps["settings"]

    chat_id = update.effective_chat.id
    calls = await db.get_user_calls_last_hour(chat_id)
    if calls >= settings.claude_calls_per_user_per_hour:
        await update.message.reply_text(
            "Rate limit reached. Please wait before using AI-powered commands."
        )
        return

    await update.message.reply_text("Fetching Fed rate predictions from Polymarket...")
    await update.message.chat.send_action(ChatAction.TYPING)

    fed_data = await polymarket.get_fed_data()

    lines = ""
    for d in fed_data:
        lines += f"\nFOMC {d['label']} \n"
        if d["outcomes"]:
            order = ["No Change", "25 bps Cut", "50+ bps Cut", "25+ bps Hike"]
            shown: set[str] = set()
            for key in order:
                if key in d["outcomes"]:
                    pct = d["outcomes"][key]
                    filled = int(pct / 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    if key == "No Change":
                        color = "🟢"
                    elif "Cut" in key:
                        color = "🔴"
                    else:
                        color = "🟡"
                    lines += f"  {color} {key}: {bar} {pct}%\n"
                    shown.add(key)
            for key, pct in d["outcomes"].items():
                if key not in shown:
                    lines += f"  ❓ {key}: {pct}%\n"
        else:
            lines += "  Data unavailable\n"

    summary = ""
    for d in fed_data:
        if d["outcomes"]:
            summary += f"\n{d['label']}: {d['outcomes']}\n"

    await db.record_user_call(chat_id)
    analysis = await analyst.fed_analysis(summary)

    msg = (
        f"Fed Rate Predictions (Polymarket)\n"
        f"==================\n"
        f"{lines}\n"
        f"AI Analysis:\n{analysis}\n\n"
        f"Time: {datetime.now(timezone.utc).strftime('%H:%M')} UTC"
    )
    await send_plain_chunks(update, msg)
