"""Job-queue scheduler — weekly report + outcome checker."""
from __future__ import annotations

import structlog
from telegram.ext import Application, ContextTypes

from src.clients.binance import BinanceClient
from src.clients.coindesk import fetch_crypto_news
from src.core.message_utils import send_long_to_chat
from src.modules.outcome_tracker import check_all_outcomes

log = structlog.get_logger()


async def _weekly_report_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: generate and send weekly catalyst report."""
    deps = ctx.bot_data
    analyst = deps["market_analyst"]
    binance: BinanceClient = deps["binance"]
    settings = deps["settings"]
    chat_id = settings.telegram_chat_id

    try:
        news = await fetch_crypto_news(limit=15)
        ticker = await binance.get_ticker_24hr("BTCUSDT")
        btc = {
            "price": float(ticker.get("lastPrice", 0)),
            "change_24h": float(ticker.get("priceChangePercent", 0)),
        }
        report = await analyst.weekly_report(news, btc)
        msg = f"AUTO Weekly Catalyst Report\n\n{report}"
        await send_long_to_chat(ctx.bot, int(chat_id), msg)
        log.info("weekly_report_sent")
    except Exception as e:
        log.error("weekly_report_failed", error=str(e))


async def _outcome_check_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: check real price outcomes for all pending signals."""
    deps = ctx.bot_data
    binance: BinanceClient = deps["binance"]
    db = deps["db"]

    try:
        checked = await check_all_outcomes(binance, db)
        if checked > 0:
            log.info("outcome_checker_done", checked=checked)
    except Exception as e:
        log.error("outcome_checker_failed", error=str(e))


def schedule_jobs(app: Application) -> None:
    """Register background jobs on the application's job queue."""
    jq = app.job_queue

    # Weekly report — Monday 8:00 AM UTC
    jq.run_daily(
        _weekly_report_job,
        time=__import__("datetime").time(hour=8, minute=0),
        days=(0,),  # Monday = 0
        name="weekly_report",
    )
    log.info("scheduled_weekly_report", day="Monday", time="08:00 UTC")

    # Outcome checker — every 15 minutes
    jq.run_repeating(
        _outcome_check_job,
        interval=900,
        first=60,
        name="outcome_checker",
    )
    log.info("scheduled_outcome_checker", interval_seconds=900)
