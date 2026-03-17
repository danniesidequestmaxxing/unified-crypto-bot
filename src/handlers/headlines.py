"""/headlines — CoinDesk crypto news + Claude Opus summary."""
from __future__ import annotations

from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.clients.coindesk import fetch_crypto_news
from src.core.message_utils import send_plain_chunks


async def cmd_headlines(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
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

    args = ctx.args or []
    hours = 6
    if args:
        try:
            hours = int(args[0])
        except ValueError:
            pass

    await update.message.reply_text(f"Fetching last {hours}hr news...")
    await update.message.chat.send_action(ChatAction.TYPING)

    news = await fetch_crypto_news(limit=12, hours=hours)

    if not news or (len(news) == 1 and "error" in news[0].get("title", "").lower()):
        await update.message.reply_text(
            f"📭 No news articles found in the last {hours} hours.\n\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M')} UTC"
        )
        return

    await db.record_user_call(chat_id)
    summary = await analyst.news_summary(news)

    links = "\n".join(
        f"- {n['title'][:60]}{'...' if len(n['title']) > 60 else ''}  {n['url']}"
        for n in news[:6] if n.get("url")
    )
    msg = (
        f"News Summary (Last {hours} Hours)\n\n"
        f"{summary}\n\n"
        f"Top Links:\n{links}\n\n"
        f"Time: {datetime.now(timezone.utc).strftime('%H:%M')} UTC"
    )
    await send_plain_chunks(update, msg)
