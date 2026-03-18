"""Catch-all text handler — routes free-form messages to Claude as trading questions.

When the user mentions a specific ticker/asset, automatically generates a
candlestick chart (with EMA, RSI, volume) alongside the AI analysis — mirroring
the /analyze command experience.
"""
from __future__ import annotations

import asyncio
import io
import re

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.chart.generator import fetch_klines, generate_chart
from src.chart.market_sessions import get_current_sessions
from src.core.message_utils import send_long

# Keywords that suggest the user wants a chart / technical analysis
_ANALYSIS_KEYWORDS = re.compile(
    r"\b(analy[sz]e|chart|look\s+at|check|setup|trade|signal|technical|ta\b|"
    r"breakout|breakdown|price\s+action|entry|long|short|scalp|swing)\b",
    re.IGNORECASE,
)

# Keywords that suggest Fed / FOMC / rate cut questions
_FED_KEYWORDS = re.compile(
    r"\b(fomc|fed\b|federal\s+reserve|rate\s+cut|rate\s+hike|rate\s+decision|"
    r"interest\s+rate|basis\s+point|bps\s+cut|powell|dot\s+plot|fed\s+fund)\b",
    re.IGNORECASE,
)


def _extract_symbol(text: str) -> str | None:
    """Extract a trading pair from free text (mirrors engine._extract_symbol)."""
    from src.ai.engine import KNOWN_COINS

    m = re.search(r"\b([A-Z]{2,10}(?:[/-]?USDT?|BUSD))\b", text.upper())
    if m:
        return m.group(1).replace("/", "").replace("-", "")
    for word in text.upper().split():
        clean = re.sub(r"[^A-Z]", "", word)
        if clean in KNOWN_COINS:
            return f"{clean}USDT"
    return None


def _should_chart(text: str) -> bool:
    """Determine if the message warrants a chart alongside the analysis."""
    return bool(_ANALYSIS_KEYWORDS.search(text))


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

    # Detect Fed/FOMC questions and inject Polymarket data
    fed_context = ""
    if _FED_KEYWORDS.search(text):
        try:
            polymarket = deps.get("polymarket")
            if polymarket:
                fed_data = await polymarket.get_fed_data()
                parts = []
                for d in fed_data:
                    if d["outcomes"]:
                        parts.append(f"{d['label']}: {d['outcomes']}")
                if parts:
                    fed_context = (
                        "\n\nReal-time Polymarket Fed rate prediction data:\n"
                        + "\n".join(parts)
                        + "\n\nUse this data to answer the user's question with "
                        "actual probabilities. Present the data clearly."
                    )
        except Exception:
            pass  # Non-fatal; fall through to normal AI response

    symbol = _extract_symbol(text)
    use_chart = symbol and _should_chart(text)

    if use_chart:
        # Route through suggest_trade for structured analysis + chart
        timeframe = "1H"
        tf_match = re.search(r"\b(\d{1,2}[HhMmDdWw])\b", text)
        if tf_match:
            timeframe = tf_match.group(1).upper()

        extra = context  # pass conversation context as extra notes
        analysis_text, levels = await engine.suggest_trade(symbol, timeframe, extra)
        chat_context[chat_id] = analysis_text

        # Record signal for self-learning
        session_info = get_current_sessions()
        await db.record_signal(
            chat_id=chat_id,
            asset=symbol,
            timeframe=timeframe,
            direction=levels.get("direction") if levels else None,
            entry=levels.get("entry") if levels else None,
            sl=levels.get("sl") if levels else None,
            tp1=levels.get("tp1") if levels else None,
            tp2=levels.get("tp2") if levels else None,
            tp3=levels.get("tp3") if levels else None,
            market_session=session_info["primary_session"],
            session_detail=session_info,
            analysis_text=analysis_text,
            source="freeform",
        )

        # Generate and send chart
        try:
            binance = deps["binance"]
            df = await fetch_klines(binance, symbol, timeframe)
            img_bytes = await asyncio.to_thread(
                generate_chart, df, symbol, timeframe, levels,
            )
            await update.message.reply_photo(
                photo=InputFile(
                    io.BytesIO(img_bytes),
                    filename=f"{symbol}_{timeframe}.png",
                ),
            )
        except Exception:
            pass  # Chart failure is non-fatal

        await send_long(update, analysis_text)
    else:
        # General Q&A — no chart needed
        enriched = text + fed_context if fed_context else text
        result = await engine.analyze(enriched, context)
        chat_context[chat_id] = result
        await send_long(update, result)
