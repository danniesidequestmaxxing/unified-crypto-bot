"""Catch-all text handler — routes free-form messages to Claude as trading questions."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.core.message_utils import send_long


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if not text:
        return

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

    await update.message.chat.send_action(ChatAction.TYPING)

    chat_context = ctx.bot_data.setdefault("_chat_context", {})
    context = chat_context.get(chat_id, "")

    await db.record_user_call(chat_id)
    result = await engine.analyze(text, context)
    chat_context[chat_id] = result
    await send_long(update, result)
