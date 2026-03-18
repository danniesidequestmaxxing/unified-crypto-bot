"""Registers all command and message handlers on the Telegram Application."""
from __future__ import annotations

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.handlers.alerts import cmd_alerts, cmd_stopalerts
from src.handlers.analyze import cmd_analyze
from src.handlers.fed import cmd_fed
from src.handlers.freeform import handle_message
from src.handlers.headlines import cmd_headlines
from src.handlers.market import cmd_market
from src.handlers.performance import cmd_performance
from src.handlers.price import cmd_price, cmd_top
from src.handlers.script import cmd_indicator, cmd_script
from src.handlers.signals import cmd_autosignal, cmd_signals, cmd_stopsignal
from src.handlers.social import (
    button_handler,
    cmd_account,
    cmd_briefing,
    cmd_cas,
    cmd_chat,
    cmd_mentions,
    cmd_narratives,
    cmd_news,
    cmd_ping,
    cmd_research,
    cmd_search,
    cmd_summary,
    cmd_trending,
)
from src.handlers.start_help import cmd_help, cmd_start
from src.handlers.weekly import cmd_weekly


def register_handlers(app: Application) -> None:
    """Register all command, callback, and message handlers."""

    # General
    app.add_handler(CommandHandler(["start", "help"], cmd_start))

    # Market intelligence (Claude Opus)
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("headlines", cmd_headlines))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("fed", cmd_fed))

    # Trading analysis (Claude Sonnet)
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("script", cmd_script))
    app.add_handler(CommandHandler("indicator", cmd_indicator))
    app.add_handler(CommandHandler("autosignal", cmd_autosignal))
    app.add_handler(CommandHandler("stopsignal", cmd_stopsignal))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("performance", cmd_performance))

    # Price & market data (CoinGecko)
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("top", cmd_top))

    # TradingView webhooks
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("stopalerts", cmd_stopalerts))

    # Social intelligence (Elfa AI)
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("mentions", cmd_mentions))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("narratives", cmd_narratives))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("cas", cmd_cas))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("ping", cmd_ping))

    # Inline keyboard callback (social commands timeframe selection)
    app.add_handler(CallbackQueryHandler(button_handler))

    # Catch-all: free-form text → Claude trading Q&A (lowest priority)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
