"""Catch-all text handler — routes free-form messages intelligently.

Routing priority:
1. Forwarded messages / macro data → BTC impact analysis + conditional chart
2. News keywords → CoinDesk + Elfa AI news summary
3. Fed/FOMC keywords → Polymarket-enriched Q&A
4. Explicit chart/trade request with ticker → structured trade analysis + chart
5. Everything else → general Q&A (no chart)
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timezone

import structlog

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.chart.generator import fetch_klines, generate_chart
from src.chart.market_sessions import get_current_sessions
from src.clients.coindesk import fetch_crypto_news
from src.core.message_utils import send_long, send_plain_chunks

log = structlog.get_logger()

# ── Keyword patterns ─────────────────────────────────────────

# User explicitly wants a chart / trade analysis
_CHART_KEYWORDS = re.compile(
    r"\b(analy[sz]e|chart|look\s+at|check\s+(?:the\s+)?chart|"
    r"setup|trade\s+(?:setup|idea|signal)|technical|ta\b|"
    r"give\s+me\s+(?:a\s+)?(?:chart|setup|signal|analysis))\b",
    re.IGNORECASE,
)

# Macro / economic data patterns (forwarded intel)
_MACRO_KEYWORDS = re.compile(
    r"\b(cpi|ppi|nfp|non.?farm|gdp|pce|jobless\s+claims|unemployment|"
    r"retail\s+sales|ism\s+|pmi\b|producer\s+price|consumer\s+price|"
    r"inflation\s+(?:data|print|rate|expectations?)|"
    r"core\s+(?:cpi|ppi|pce)|"
    r"m/?m\s*;?\s*est|y/?y\s*;?\s*est|vs\.?\s*est|"
    r"rate\s+decision|basis\s+points?\s+(?:cut|hike)|"
    r"tariff|sanctions?|etf\s+(?:in|out)flow|"
    r"whale\s+alert|liquidat(?:ion|ed)|"
    r"open\s+interest|funding\s+rate|"
    r"cpi\s+swaps?|energy\s+price|"
    r"hawkish|dovish)\b",
    re.IGNORECASE,
)

# Fed / FOMC keywords
_FED_KEYWORDS = re.compile(
    r"\b(fomc|fed\b|federal\s+reserve|rate\s+cut|rate\s+hike|rate\s+decision|"
    r"interest\s+rate|basis\s+point|bps\s+cut|powell|dot\s+plot|fed\s+fund)\b",
    re.IGNORECASE,
)

# News keywords — only match when user is explicitly requesting a news digest/summary.
# Phrases like "its recent news" or "news about X" are questions ABOUT news, not requests FOR news.
_NEWS_KEYWORDS = re.compile(
    r"(?:^|\b)(?:show|get|give|fetch|pull|what(?:'?s| is| are) the)\s+"
    r"(?:me\s+)?(?:the\s+)?(?:latest\s+)?(?:news|headlines?)"
    r"|^(?:news|headlines?)$"
    r"|\b(?:latest\s+(?:crypto|market|bitcoin|btc)\s+(?:news|headlines?)|"
    r"market\s+update|crypto\s+update|news\s+summary|news\s+digest)\b",
    re.IGNORECASE,
)


def _extract_symbol(text: str):
    """Extract a trading symbol from free text. Returns SymbolResult or None."""
    from src.ai.engine import _extract_symbol as engine_extract
    return engine_extract(text)


def _is_forwarded(update: Update) -> bool:
    """Check if the message is forwarded from another chat/channel."""
    msg = update.message
    return bool(getattr(msg, "forward_origin", None) or getattr(msg, "forward_date", None))


def _is_macro_data(text: str) -> bool:
    """Detect if text contains macro/economic data or market intel."""
    return bool(_MACRO_KEYWORDS.search(text))


async def _fetch_btc_price(deps: dict) -> str:
    """Fetch current BTC price string for context injection."""
    try:
        binance = deps["binance"]
        ticker = await binance.get_ticker_24hr("BTCUSDT")
        if ticker:
            price = float(ticker.get("lastPrice", 0))
            change = float(ticker.get("priceChangePercent", 0))
            return f"${price:,.0f} ({change:+.2f}% 24h)"
    except Exception:
        pass
    return ""


def _extract_keywords(text: str) -> str:
    """Extract meaningful keywords from user text for Elfa search."""
    # Remove common question words, keep nouns/proper nouns
    stop = {
        "what", "is", "are", "the", "a", "an", "how", "why", "when", "where",
        "do", "does", "did", "can", "could", "will", "would", "should", "to",
        "in", "on", "at", "for", "of", "with", "about", "happening", "going",
        "think", "tell", "me", "give", "show", "please", "stock", "price",
        "right", "now", "today", "currently",
    }
    words = [w for w in re.sub(r"[^\w\s]", "", text).split() if w.lower() not in stop]
    return " ".join(words[:5]) if words else text[:50]


async def _gather_intel(text: str, deps: dict) -> str:
    """Pull real-time intelligence from Elfa AI + CoinDesk for general Q&A."""
    parts: list[str] = []
    keywords = _extract_keywords(text)

    # Build parallel tasks
    tasks: dict[str, asyncio.Task] = {}

    elfa = deps.get("elfa")
    if elfa:
        tasks["mentions"] = asyncio.ensure_future(
            elfa.keyword_mentions(keywords, timeframe="24h", limit=10)
        )
        tasks["narratives"] = asyncio.ensure_future(
            elfa.trending_narratives(timeframe="24h")
        )

    tasks["news"] = asyncio.ensure_future(
        fetch_crypto_news(limit=8, hours=12)
    )

    if not tasks:
        return ""

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    data = dict(zip(tasks.keys(), results))

    # Elfa keyword mentions (social intelligence)
    mentions = data.get("mentions")
    if mentions and not isinstance(mentions, Exception):
        items = mentions.get("data", []) if isinstance(mentions, dict) else []
        if isinstance(items, list) and items:
            mention_lines = []
            for m in items[:8]:
                if not isinstance(m, dict):
                    continue
                acc = m.get("account", {}) or {}
                username = acc.get("username", "") if isinstance(acc, dict) else ""
                content = m.get("content", m.get("text", ""))
                if not content:
                    continue
                content = str(content)[:200]
                likes = m.get("likeCount", 0)
                views = m.get("viewCount", 0)
                prefix = f"@{username}" if username else "anon"
                mention_lines.append(
                    f"  {prefix} ({likes} likes, {views} views): {content}"
                )
            if mention_lines:
                parts.append(
                    f"\n\n--- SOCIAL INTELLIGENCE (Elfa AI — last 24h for '{keywords}') ---\n"
                    + "\n".join(mention_lines)
                )

    # Elfa trending narratives
    narratives = data.get("narratives")
    if narratives and not isinstance(narratives, Exception):
        items = narratives.get("data", []) if isinstance(narratives, dict) else []
        if isinstance(items, list) and items:
            narr_lines = []
            for n in items[:5]:
                title = n.get("title", n.get("narrative", ""))
                summary = n.get("summary", "")
                if title:
                    narr_lines.append(f"  - {title}: {summary}" if summary else f"  - {title}")
            if narr_lines:
                parts.append(
                    "\n\n--- TRENDING NARRATIVES (Elfa AI) ---\n"
                    + "\n".join(narr_lines)
                )

    # CoinDesk news headlines
    news = data.get("news")
    if news and not isinstance(news, Exception) and isinstance(news, list):
        headlines = [
            f"  - [{n.get('source', 'CoinDesk')}] {n['title']}"
            for n in news[:6] if n.get("title")
        ]
        if headlines:
            parts.append(
                "\n\n--- RECENT CRYPTO NEWS (last 12h) ---\n"
                + "\n".join(headlines)
            )

    if parts:
        return (
            "\n\n".join(parts)
            + "\n\nUse the above real-time data to inform your answer. "
            "Cite specific data points where relevant."
        )
    return ""


async def _generate_and_send_chart(
    update: Update, deps: dict, symbol: str, timeframe: str,
    levels: dict | None = None, asset_type: str = "crypto",
) -> None:
    """Generate a candlestick chart and send it as a photo. Non-fatal on failure."""
    try:
        if asset_type == "stock":
            from src.clients.yahoo_finance import StockClient
            from src.chart.generator import fetch_stock_klines
            async with StockClient() as client:
                df = await fetch_stock_klines(client, symbol, timeframe)
        else:
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
    except Exception as e:
        log.warning("chart_generation_failed", symbol=symbol, asset_type=asset_type, error=str(e))


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if not text:
        return

    try:
        await _route_message(update, ctx)
    except Exception as exc:
        log.error("freeform_handler_error", error=str(exc), exc_info=True)
        try:
            await update.message.reply_text(
                f"Error: {type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


async def _route_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
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
    forwarded = _is_forwarded(update)

    # ── Route 1: Forwarded messages or macro data → BTC impact analysis ──
    if forwarded or _is_macro_data(text):
        await db.record_user_call(chat_id)

        # Determine if this is forwarded data or a follow-up question
        user_question = ""
        forwarded_data = text
        if not forwarded and context:
            user_question = text
            forwarded_data = context

        # Fetch BTC price + run macro impact analysis
        btc_price = await _fetch_btc_price(deps)
        analyst = deps["market_analyst"]
        result = await analyst.macro_impact(
            forwarded_data=forwarded_data,
            user_question=user_question,
            btc_price=btc_price,
        )

        # Store forwarded data for follow-up questions
        chat_context[chat_id] = text

        # Send the text analysis first
        await send_long(update, result.analysis_text)

        # Conditional chart: only if Claude determined it's needed
        if result.requires_chart:
            await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
            await _generate_and_send_chart(
                update, deps,
                symbol=result.chart_asset,
                timeframe=result.chart_timeframe,
            )
        return

    # ── Route 2: News/headlines ──────────────────────────────
    if _NEWS_KEYWORDS.search(text):
        await db.record_user_call(chat_id)
        hours = 6
        hr_match = re.search(r"(\d{1,2})\s*h(?:ou)?rs?", text, re.IGNORECASE)
        if hr_match:
            hours = min(int(hr_match.group(1)), 48)

        elfa = deps.get("elfa")
        news_task = fetch_crypto_news(limit=12, hours=hours)
        narratives_task = (
            elfa.trending_narratives(timeframe="24h") if elfa else asyncio.sleep(0)
        )
        news, narratives_raw = await asyncio.gather(
            news_task, narratives_task, return_exceptions=True,
        )

        if isinstance(news, Exception) or not news:
            news = []
        has_news = news and not (
            len(news) == 1 and "error" in news[0].get("title", "").lower()
        )

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
                        narratives_text = (
                            "\n\nTrending Crypto Narratives (Elfa AI):\n"
                            + "\n".join(parts)
                        )
            except Exception:
                pass

        if has_news or narratives_text:
            analyst = deps["market_analyst"]
            news_for_summary = news if has_news else []
            extra_context = narratives_text if narratives_text else ""
            summary = await analyst.news_summary(
                news_for_summary, extra_context=extra_context,
            )

            links = ""
            if has_news:
                links = "\n".join(
                    f"- {n['title'][:60]}{'...' if len(n['title']) > 60 else ''}  {n['url']}"
                    for n in news[:6] if n.get("url")
                )

            msg = f"📰 News Summary (Last {hours} Hours)\n\n{summary}\n"
            if links:
                msg += f"\n🔗 Top Links:\n{links}\n"
            msg += f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M')} UTC"

            chat_context[chat_id] = summary
            await send_plain_chunks(update, msg)
            return

        await update.message.reply_text(
            f"📭 No news articles found in the last {hours} hours."
        )
        return

    await db.record_user_call(chat_id)

    # ── Route 3: Fed/FOMC questions → enrich with Polymarket ──
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
            pass

    # ── Route 4: Explicit chart/trade request with ticker → chart + analysis ──
    sym_result = _extract_symbol(text)
    wants_chart = bool(_CHART_KEYWORDS.search(text))

    if sym_result and wants_chart:
        timeframe = "1H"
        tf_match = re.search(r"\b(\d{1,2}[HhMmDdWw])\b", text)
        if tf_match:
            timeframe = tf_match.group(1).upper()

        ticker = sym_result.symbol
        asset_type = sym_result.asset_type

        if asset_type == "stock":
            # For stocks, use general Q&A with stock data (no suggest_trade)
            # Route to Route 5 logic below
            pass
        else:
            extra = context
            analysis_text, levels = await engine.suggest_trade(ticker, timeframe, extra)
            chat_context[chat_id] = analysis_text

            session_info = get_current_sessions()
            await db.record_signal(
                chat_id=chat_id,
                asset=ticker,
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

            await _generate_and_send_chart(update, deps, ticker, timeframe, levels, asset_type)
            await send_long(update, analysis_text)
            return

    # ── Route 5: General Q&A — enrich with live intel, then answer ──
    intel_context = await _gather_intel(text, deps)
    enriched = text
    if fed_context:
        enriched += fed_context
    if intel_context:
        enriched += intel_context
    result, detected_sym = await engine.analyze(enriched, context, raw_question=text)
    chat_context[chat_id] = result
    await send_long(update, result)

    # Auto-generate chart if the question is about a tradeable asset
    chart_sym = detected_sym or sym_result
    if chart_sym:
        tf = "1D" if chart_sym.asset_type == "stock" else "1H"
        await _generate_and_send_chart(
            update, deps, chart_sym.symbol, tf,
            asset_type=chart_sym.asset_type,
        )
