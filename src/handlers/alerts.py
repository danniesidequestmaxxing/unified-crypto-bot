"""/alerts and /stopalerts — TradingView webhook subscription."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    deps = ctx.bot_data
    settings = deps["settings"]
    subscribers: set = deps.setdefault("alert_subscribers", set())
    subscribers.add(chat_id)

    webhook_url = settings.webhook_url or f"http://YOUR_SERVER:{settings.webhook_port}/webhook"
    await update.message.reply_text(
        f"Alerts enabled for this chat.\n\n"
        f"<b>TradingView webhook URL:</b>\n"
        f"<code>{webhook_url}</code>\n\n"
        f"<b>Secret header:</b>\n"
        f"<code>X-Webhook-Secret: {settings.webhook_secret}</code>\n\n"
        f"Set this as your alert webhook URL in TradingView. "
        f"The alert message should be the JSON from the PineScript alertcondition().",
        parse_mode=ParseMode.HTML,
    )


async def cmd_stopalerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    subscribers: set = ctx.bot_data.setdefault("alert_subscribers", set())
    subscribers.discard(chat_id)
    await update.message.reply_text("Alerts disabled for this chat.")
