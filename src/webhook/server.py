"""TradingView webhook receiver — aiohttp server.

Ported from telegram-pinescript-bot webhook_server.py.
"""
from __future__ import annotations

import json
import typing

import structlog
from aiohttp import web

log = structlog.get_logger()

_bot_app = None
_get_subscribers: typing.Callable | None = None


def init(bot_app, get_subscribers: typing.Callable) -> None:
    """Wire up the bot application and subscriber accessor."""
    global _bot_app, _get_subscribers
    _bot_app = bot_app
    _get_subscribers = get_subscribers


def _format_alert(data: dict) -> str:
    action = data.get("action", "SIGNAL").upper()
    ticker = data.get("ticker", "???")
    price = data.get("price", "—")
    tp = data.get("tp", "—")
    sl = data.get("sl", "—")
    extra = {k: v for k, v in data.items() if k not in ("action", "ticker", "price", "tp", "sl")}

    arrow = "\u2B06" if action == "LONG" else "\u2B07" if action == "SHORT" else "\u26A1"
    lines = [
        f"{arrow} <b>{action} {ticker}</b>",
        f"Price: <code>{price}</code>",
        f"TP: <code>{tp}</code>",
        f"SL: <code>{sl}</code>",
    ]
    if extra:
        lines.append(f"Details: <code>{json.dumps(extra)}</code>")
    return "\n".join(lines)


async def handle_webhook(request: web.Request) -> web.Response:
    """POST /webhook — receives TradingView alert JSON."""
    from src.config import Settings
    settings = Settings()

    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != settings.webhook_secret:
        return web.Response(status=403, text="Forbidden")

    try:
        body = await request.text()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"action": "ALERT", "message": body}
    except Exception:
        return web.Response(status=400, text="Bad request")

    msg = _format_alert(data)
    log.info("webhook_alert_received", msg=msg)

    subscribers = _get_subscribers() if _get_subscribers else set()
    bot = _bot_app.bot if _bot_app else None

    if bot and subscribers:
        for chat_id in subscribers:
            try:
                await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            except Exception as e:
                log.error("webhook_send_failed", chat_id=chat_id, error=str(e))

    return web.Response(text="OK")


def create_webhook_app() -> web.Application:
    """Create the aiohttp web app for the webhook server."""
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/health", lambda _: web.Response(text="OK"))
    return app
