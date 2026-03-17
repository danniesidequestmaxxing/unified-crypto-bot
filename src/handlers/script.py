"""/script and /indicator — PineScript generation (Claude Sonnet)."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.core.message_utils import send_long


async def cmd_script(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
    engine = deps["trading_engine"]
    db = deps["db"]
    settings = deps["settings"]

    chat_id = update.effective_chat.id
    calls = await db.get_user_calls_last_hour(chat_id)
    if calls >= settings.claude_calls_per_user_per_hour:
        await update.message.reply_text(
            "Rate limit reached. Please wait before using AI-powered commands."
        )
        return

    description = " ".join(ctx.args or [])
    if not description:
        await update.message.reply_text(
            "Usage: /script <description>\n"
            "Example: /script EMA crossover strategy 9/21 with volume filter and alerts"
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    await db.record_user_call(chat_id)
    result = await engine.generate_pinescript(description)

    chat_context = ctx.bot_data.setdefault("_chat_context", {})
    chat_context[chat_id] = result
    await send_long(update, result)


async def cmd_indicator(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
    engine = deps["trading_engine"]
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
    if not args:
        await update.message.reply_text(
            "Usage: /indicator <type> [asset] [params]\n"
            "Example: /indicator volume_profile BTCUSDT\n"
            "Example: /indicator RSI divergence"
        )
        return

    indicator_type = args[0]
    asset = args[1] if len(args) > 1 else ""
    params = " ".join(args[2:]) if len(args) > 2 else ""

    await update.message.chat.send_action(ChatAction.TYPING)
    await db.record_user_call(chat_id)
    result = await engine.draw_indicator(indicator_type, asset, params)

    chat_context = ctx.bot_data.setdefault("_chat_context", {})
    chat_context[chat_id] = result
    await send_long(update, result)
