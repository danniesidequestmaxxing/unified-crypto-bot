"""/weekly — Weekly catalyst report with predictions."""
from __future__ import annotations

from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.clients.coindesk import fetch_crypto_news
from src.core.message_utils import send_plain_chunks


async def cmd_weekly(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
    binance = deps["binance"]
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

    await update.message.reply_text("Generating deep catalyst report (may take ~20s)...")
    await update.message.chat.send_action(ChatAction.TYPING)

    news = await fetch_crypto_news(limit=15)
    ticker = await binance.get_ticker_24hr("BTCUSDT")
    btc = {
        "price": float(ticker.get("lastPrice", 0)),
        "change_24h": float(ticker.get("priceChangePercent", 0)),
    }

    await db.record_user_call(chat_id)
    report = await analyst.weekly_report(news, btc)

    msg = (
        f"Weekly Catalyst Report\n\n"
        f"{report}\n\n"
        f"Time: {datetime.now().strftime('%A %b %d, %Y - %H:%M')}"
    )
    await send_plain_chunks(update, msg)
