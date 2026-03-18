"""Catch-all text handler — routes free-form messages to Claude as trading questions.

When the user mentions a specific ticker/asset, automatically generates a
candlestick chart (with EMA, RSI, volume) alongside the AI analysis — mirroring
the /analyze command experience.
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timezone

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.chart.generator import fetch_klines, generate_chart
from src.chart.market_sessions import get_current_sessions
from src.clients.coindesk import fetch_crypto_news
from src.core.message_utils import send_long, send_plain_chunks

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

# Keywords that suggest the user wants crypto news / headlines
_NEWS_KEYWORDS = re.compile(
    r"\b(news|headlines?|latest\s+(?:crypto|market|bitcoin|btc)|"
    r"what(?:'s|\s+is)\s+happening|market\s+update|crypto\s+update|"
    r"what\s+happened|recent\s+events?)\b",
    re.IGNORECASE,
)


def _extract_symbol(text: str) -> str | None:
    """Extract a trading pair from free text (mirrors engine._extract_symbol)."""
    from src.ai.engine import KNOWN_COINS, _TICKER_STOPWORDS

    m = re.search(r"\b([A-Z]{2,10}(?:[/-]?USDT?|BUSD))\b", text.upper())
    if m:
        return m.group(1).replace("/", "").replace("-", "")
    best_unknown: str | None = None
    for word in text.upper().split():
        clean = re.sub(r"[^A-Z]", "", word)
        if len(clean) < 2 or len(clean) > 10:
            continue
        if clean in KNOWN_COINS:
            return f"{clean}USDT"
        if clean in _TICKER_STOPWORDS:
            continue
        if len(clean) >= 3 and best_unknown is None:
            best_unknown = clean
    return f"{best_unknown}USDT" if best_unknown else None


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

    # ── News/headlines route ──────────────────────────────────
    if _NEWS_KEYWORDS.search(text):
        await db.record_user_call(chat_id)
        # Extract hours from message (e.g. "last 12 hours")
        hours = 6
        hr_match = re.search(r"(\d{1,2})\s*h(?:ou)?rs?", text, re.IGNORECASE)
        if hr_match:
            hours = min(int(hr_match.group(1)), 48)

        # Fetch CoinDesk news + Elfa trending narratives in parallel
        elfa = deps.get("elfa")
        news_task = fetch_crypto_news(limit=12, hours=hours)
        narratives_task = (
            elfa.trending_narratives(timeframe="24h") if elfa else asyncio.sleep(0)
        )
        news, narratives_raw = await asyncio.gather(
            news_task, narratives_task, return_exceptions=True,
        )

        # Build news items
        if isinstance(news, Exception) or not news:
            news = []
        has_news = news and not (
            len(news) == 1 and "error" in news[0].get("title", "").lower()
        )

        # Build narratives context
        narratives_text = ""
        if not isinstance(narratives_raw, (Exception, type(None))):
            try:
                items = narratives_raw.get("data", [])
                if items:
                    parts = []
                    for n in items[:8]:
                        title = n.get("title", n.get("narrative", ""))
                        summary = n.get("summary", "")
                        if title:
                            parts.append(f"- {title}: {summary}" if summary else f"- {title}")
                    if parts:
                        narratives_text = "\n\nTrending Crypto Narratives (Elfa AI):\n" + "\n".join(parts)
            except Exception:
                pass

        if has_news or narratives_text:
            analyst = deps["market_analyst"]
            # Combine sources for Claude summary
            news_for_summary = news if has_news else []
            extra_context = narratives_text if narratives_text else ""
            summary = await analyst.news_summary(news_for_summary, extra_context=extra_context)

            links = ""
            if has_news:
                links = "\n".join(
                    f"- {n['title'][:60]}{'...' if len(n['title']) > 60 else ''}  {n['url']}"
                    for n in news[:6] if n.get("url")
                )

            msg = (
                f"📰 News Summary (Last {hours} Hours)\n\n"
                f"{summary}\n"
            )
            if links:
                msg += f"\n🔗 Top Links:\n{links}\n"
            msg += f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M')} UTC"

            chat_context[chat_id] = summary
            await send_plain_chunks(update, msg)
            return

        # No news found — fall through to general Q&A
        await update.message.reply_text(
            f"📭 No news articles found in the last {hours} hours."
        )
        return

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
