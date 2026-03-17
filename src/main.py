"""Entry point — lifecycle orchestration for the unified crypto intelligence bot."""
from __future__ import annotations

import asyncio
import os
import signal
import sys

import structlog
from telegram import Update
from telegram.ext import Application

from src.ai.engine import TradingEngine
from src.ai.market_analyst import MarketAnalyst
from src.clients.binance import BinanceClient
from src.clients.bybit import BybitClient
from src.clients.claude import ClaudeService
from src.clients.coinglass import CoinGlassHobbyistClient, CoinGlassPrimeClient
from src.clients.elfa import ElfaClient
from src.clients.hyperliquid import HyperliquidClient
from src.clients.polymarket import PolymarketClient
from src.config import Settings
from src.core.database import Database
from src.core.logging import setup_logging
from src.core.rate_limiter import TokenBucket
from src.delivery.alerts import TelegramDelivery
from src.handlers.registry import register_handlers
from src.handlers.signals import restore_autosignal_subs
from src.modules.ghost import GhostScreener
from src.modules.heatmap import HeatmapSniper
from src.modules.scheduler import schedule_jobs
from src.modules.social_filter import SocialFilter
from src.webhook.server import create_webhook_app, init as init_webhook

log = structlog.get_logger()


async def main() -> None:
    setup_logging()
    settings = Settings()

    # ── Database ────────────────────────────────────────
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    db = Database(settings.db_path)
    await db.connect()

    # ── API Clients ─────────────────────────────────────
    claude = ClaudeService(
        api_key=settings.anthropic_api_key,
        model_deep=settings.claude_model_deep,
        model_fast=settings.claude_model_fast,
    )

    binance = BinanceClient(base_url=settings.binance_base_url)
    bybit = BybitClient()
    hyperliquid = HyperliquidClient()
    polymarket = PolymarketClient()

    elfa_rl = TokenBucket(settings.elfa_rpm)
    elfa = ElfaClient(api_key=settings.elfa_api_key, rate_limiter=elfa_rl)

    cg_hobbyist_rl = TokenBucket(settings.coinglass_hobbyist_rpm)
    cg_hobbyist = CoinGlassHobbyistClient(
        api_key=settings.coinglass_api_key, rate_limiter=cg_hobbyist_rl,
    )
    cg_prime_rl = TokenBucket(settings.coinglass_prime_rpm)
    cg_prime = CoinGlassPrimeClient(
        api_key=settings.coinglass_api_key, rate_limiter=cg_prime_rl,
    )

    # ── AI Engines ──────────────────────────────────────
    market_analyst = MarketAnalyst(claude)
    trading_engine = TradingEngine(claude, binance, db)

    # ── Delivery ────────────────────────────────────────
    telegram_delivery = TelegramDelivery(
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        db=db,
    )

    # ── Background Modules ──────────────────────────────
    social_filter = SocialFilter(elfa, db, settings)
    heatmap = HeatmapSniper(cg_prime, cg_hobbyist, db, telegram_delivery, settings)
    ghost = GhostScreener(cg_hobbyist, social_filter, db, telegram_delivery, settings)

    # ── Telegram Application ────────────────────────────
    app = Application.builder().token(settings.telegram_bot_token).build()

    # Inject shared dependencies into bot_data
    app.bot_data.update({
        "settings": settings,
        "db": db,
        "binance": binance,
        "bybit": bybit,
        "hyperliquid": hyperliquid,
        "polymarket": polymarket,
        "elfa": elfa,
        "market_analyst": market_analyst,
        "trading_engine": trading_engine,
    })

    register_handlers(app)
    schedule_jobs(app)

    # ── Lifecycle ───────────────────────────────────────
    background_tasks: list[asyncio.Task] = []

    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info("bot_started")

        # Restore persisted autosignal subscriptions
        await restore_autosignal_subs(app)

        # Start background modules
        background_tasks.append(asyncio.create_task(heatmap.run_forever()))
        background_tasks.append(asyncio.create_task(ghost.run_forever()))
        log.info("background_modules_started")

        # Start webhook server (TradingView alerts)
        webhook_app = create_webhook_app(settings.webhook_secret)
        init_webhook(webhook_app, app.bot, app.bot_data)
        runner = __import__("aiohttp.web", fromlist=["AppRunner"]).AppRunner(webhook_app)
        await runner.setup()
        site = __import__("aiohttp.web", fromlist=["TCPSite"]).TCPSite(
            runner, settings.webhook_host, settings.webhook_port,
        )
        await site.start()
        log.info("webhook_server_started", host=settings.webhook_host, port=settings.webhook_port)

        # Wait for shutdown signal
        stop_event = asyncio.Event()

        def _signal_handler(sig, _frame):
            log.info("shutdown_signal", signal=sig)
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _signal_handler)

        await stop_event.wait()

        # ── Graceful shutdown ───────────────────────────
        log.info("shutting_down")

        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)

        await site.stop()
        await runner.cleanup()

        await app.updater.stop()
        await app.stop()

    # Close all clients
    for client in (binance, bybit, hyperliquid, polymarket, elfa, cg_hobbyist, cg_prime):
        await client.close()
    await db.close()
    log.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
