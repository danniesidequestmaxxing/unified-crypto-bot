"""Social intelligence handlers — all Elfa AI commands + inline keyboard callback.

Ported from elfa-intel bot.py, converted from Markdown V2 to HTML.
"""
from __future__ import annotations

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.core.message_utils import escape_html, split_html_chunks

log = structlog.get_logger()

TIMEFRAMES = ["15m", "30m", "1h", "4h", "8h", "12h", "24h", "3d", "7d"]
DEFAULT_TF = "24h"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tf_keyboard(command: str, extra: str = "") -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for tf in TIMEFRAMES:
        cb = f"{command}|{tf}|{extra}" if extra else f"{command}|{tf}"
        row.append(InlineKeyboardButton(tf, callback_data=cb))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def _parse_args(text: str | None) -> list[str]:
    if not text:
        return []
    parts = text.strip().split()
    return parts[1:] if len(parts) > 1 else []


def _extract_tf(args: list[str], default: str = DEFAULT_TF) -> tuple[str, list[str]]:
    remaining = []
    tf = default
    for a in args:
        if a.lower() in TIMEFRAMES:
            tf = a.lower()
        else:
            remaining.append(a)
    return tf, remaining


async def _send_html(update: Update, text: str) -> None:
    target = update.callback_query.message if update.callback_query else update.message
    for chunk in split_html_chunks(text):
        try:
            await target.reply_text(chunk, parse_mode=ParseMode.HTML)
        except Exception:
            plain = chunk.replace("<b>", "").replace("</b>", "")
            plain = plain.replace("<code>", "").replace("</code>", "")
            plain = plain.replace("<i>", "").replace("</i>", "")
            await target.reply_text(plain)


# ── Formatters (HTML) ─────────────────────────────────────────────────────────


def _fmt_tokens(data: dict, tf: str) -> str:
    tokens = []
    if isinstance(data, dict):
        inner = data.get("data", {})
        if isinstance(inner, dict):
            tokens = inner.get("data", [])
        elif isinstance(inner, list):
            tokens = inner

    if not tokens:
        return f"<b>Trending Tokens</b> ({escape_html(tf)})\n\nNo data found."

    lines = [f"<b>Trending Tokens</b> ({escape_html(tf)})\n"]
    for i, t in enumerate(tokens[:20], 1):
        if not isinstance(t, dict):
            continue
        sym = t.get("token", "?").upper()
        count = t.get("current_count", "")
        prev = t.get("previous_count", "")
        change = t.get("change_percent")

        line = f"{i}. <b>{escape_html(sym)}</b>"
        if count:
            line += f"  mentions: <code>{count}</code>"
        if prev:
            line += f"  prev: <code>{prev}</code>"
        if change is not None:
            arrow = "+" if change >= 0 else ""
            line += f"  {escape_html(arrow + str(round(change, 1)) + '%')}"
        lines.append(line)
    return "\n".join(lines)


def _fmt_tweet_list(items: list, title: str, tf: str, limit: int = 10) -> str:
    if not isinstance(items, list) or not items:
        return f"<b>{escape_html(title)}</b> ({escape_html(tf)})\n\nNo data found."

    lines = [f"<b>{escape_html(title)}</b> ({escape_html(tf)})\n"]
    for i, m in enumerate(items[:limit], 1):
        if not isinstance(m, dict):
            continue
        acc = m.get("account", {})
        username = acc.get("username", "") if isinstance(acc, dict) else ""
        if not username:
            link_str = m.get("link", "")
            link_parts = link_str.split("x.com/")
            if len(link_parts) > 1:
                username = link_parts[1].split("/")[0]
        username = username or "unknown"
        link = m.get("link", "")
        likes = m.get("likeCount", 0)
        reposts = m.get("repostCount", 0)
        views = m.get("viewCount", 0)
        date = str(m.get("mentionedAt", ""))[:16]

        lines.append(f"{i}. <b>@{escape_html(username)}</b>")
        lines.append(f"   likes: <code>{likes}</code>  reposts: <code>{reposts}</code>  views: <code>{views}</code>")
        if date:
            lines.append(f"   {escape_html(date)}")
        if link:
            lines.append(f"   {escape_html(link)}")
        lines.append("")
    return "\n".join(lines)


def _fmt_mentions(data: dict, ticker: str, tf: str) -> str:
    items = data.get("data", []) if isinstance(data, dict) else []
    return _fmt_tweet_list(items, f"Top Mentions: {ticker.upper()}", tf)


def _fmt_search(data: dict, keywords: str, tf: str) -> str:
    items = data.get("data", []) if isinstance(data, dict) else []
    return _fmt_tweet_list(items, f"Search: {keywords}", tf, limit=15)


def _fmt_news(data: dict, ticker: str, tf: str) -> str:
    items = data.get("data", []) if isinstance(data, dict) else []
    return _fmt_tweet_list(items, f"News: {ticker.upper()}", tf)


def _fmt_account(data: dict, username: str) -> str:
    info = data.get("data", {}) if isinstance(data, dict) else {}
    if not isinstance(info, dict) or not info:
        return f"<b>Account: @{escape_html(username)}</b>\n\nNo data found."

    labels = {
        "followerCount": "Followers",
        "smartFollowerCount": "Smart Followers",
        "smartFollowingCount": "Smart Following",
        "averageEngagement": "Avg Engagement",
        "averageReach": "Avg Reach",
    }
    lines = [f"<b>Account: @{escape_html(username)}</b>\n"]
    for key, label in labels.items():
        val = info.get(key)
        if val is not None:
            if isinstance(val, float):
                val = round(val, 4)
            lines.append(f"{escape_html(label)}: <code>{val}</code>")
    return "\n".join(lines)


def _fmt_summary(data: dict, keywords: str, tf: str) -> str:
    items = data.get("data", []) if isinstance(data, dict) else []
    if not items:
        return f"<b>Event Summary: {escape_html(keywords)}</b> ({escape_html(tf)})\n\nNo summary available."

    lines = [f"<b>Event Summary: {escape_html(keywords)}</b> ({escape_html(tf)})\n"]
    for entry in (items if isinstance(items, list) else [items]):
        if isinstance(entry, dict):
            summary = entry.get("summary", "")
            links = entry.get("sourceLinks", [])
            lines.append(escape_html(summary))
            if links:
                lines.append("")
                for link in links[:5]:
                    lines.append(f"  {escape_html(str(link))}")
            lines.append("")
    return "\n".join(lines)


def _fmt_narratives(data: dict, tf: str) -> str:
    inner = data.get("data", {}) if isinstance(data, dict) else {}
    items = inner.get("trending_narratives", []) if isinstance(inner, dict) else []
    if not items:
        return f"<b>Trending Narratives</b> ({escape_html(tf)})\n\nNo data found."

    lines = [f"<b>Trending Narratives</b> ({escape_html(tf)})\n"]
    for i, n in enumerate(items[:15], 1):
        if not isinstance(n, dict):
            continue
        narrative = n.get("narrative", "")
        source_links = n.get("source_links", [])
        lines.append(f"{i}. <b>{escape_html(str(narrative)[:120])}</b>")
        if source_links:
            for link in source_links[:3]:
                lines.append(f"   {escape_html(str(link))}")
        lines.append("")
    return "\n".join(lines)


def _fmt_cas(data: dict, platform: str, tf: str) -> str:
    inner = data.get("data", {}) if isinstance(data, dict) else {}
    items = inner.get("data", []) if isinstance(inner, dict) else []
    if not items:
        return f"<b>Trending CAs — {escape_html(platform)}</b> ({escape_html(tf)})\n\nNo data found."

    lines = [f"<b>Trending CAs — {escape_html(platform)}</b> ({escape_html(tf)})\n"]
    for i, ca in enumerate(items[:15], 1):
        if not isinstance(ca, dict):
            continue
        addr = ca.get("contractAddress", "?")
        chain = ca.get("chain", "")
        mentions = ca.get("mentionCount", "")

        lines.append(f"{i}. <code>{escape_html(str(addr))}</code>")
        detail = []
        if chain:
            detail.append(f"chain: {escape_html(str(chain))}")
        if mentions:
            detail.append(f"mentions: {escape_html(str(mentions))}")
        if detail:
            lines.append(f"   {' | '.join(detail)}")
    return "\n".join(lines)


# ── Command Handlers ─────────────────────────────────────────────────────────


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    try:
        await elfa.ping()
        await update.message.reply_text("API Status: Online")
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, _ = _extract_tf(args)
    if not args:
        await update.message.reply_text(
            "Pick a timeframe for trending tokens:",
            reply_markup=_tf_keyboard("trending"),
        )
        return
    try:
        await update.message.reply_text("Fetching trending tokens...")
        data = await elfa.get_trending_tokens(tf)
        await _send_html(update, _fmt_tokens(data, tf))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_mentions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, remaining = _extract_tf(args)
    ticker = remaining[0] if remaining else None
    if not ticker:
        await update.message.reply_text("Usage: /mentions TICKER [timeframe]\nExample: /mentions BTC 4h")
        return
    if len(args) == 1:
        await update.message.reply_text(
            f"Pick a timeframe for {ticker.upper()} mentions:",
            reply_markup=_tf_keyboard("mentions", ticker),
        )
        return
    try:
        await update.message.reply_text(f"Fetching top mentions for {ticker.upper()}...")
        data = await elfa.get_top_mentions(ticker, time_window=tf)
        await _send_html(update, _fmt_mentions(data, ticker, tf))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, remaining = _extract_tf(args)
    keywords = " ".join(remaining) if remaining else None
    if not keywords:
        await update.message.reply_text("Usage: /search KEYWORDS [timeframe]\nExample: /search solana,defi 1h")
        return
    if not any(a.lower() in TIMEFRAMES for a in args):
        await update.message.reply_text(
            f'Pick a timeframe for "{keywords}":',
            reply_markup=_tf_keyboard("search", keywords),
        )
        return
    try:
        await update.message.reply_text(f'Searching "{keywords}"...')
        data = await elfa.keyword_mentions(keywords, tf)
        await _send_html(update, _fmt_search(data, keywords, tf))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    if not args:
        await update.message.reply_text("Usage: /account USERNAME\nExample: /account VitalikButerin")
        return
    username = args[0].lstrip("@")
    try:
        await update.message.reply_text(f"Fetching stats for @{username}...")
        data = await elfa.account_smart_stats(username)
        await _send_html(update, _fmt_account(data, username))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, remaining = _extract_tf(args)
    keywords = " ".join(remaining) if remaining else None
    if not keywords:
        await update.message.reply_text("Usage: /summary KEYWORDS [timeframe]\nExample: /summary ethereum 4h\n(Costs 5 credits)")
        return
    if not any(a.lower() in TIMEFRAMES for a in args):
        await update.message.reply_text(
            f'Pick a timeframe for "{keywords}" summary (5 credits):',
            reply_markup=_tf_keyboard("summary", keywords),
        )
        return
    try:
        await update.message.reply_text(f'Generating summary for "{keywords}"...')
        data = await elfa.event_summary(keywords, tf)
        await _send_html(update, _fmt_summary(data, keywords, tf))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_narratives(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, _ = _extract_tf(args)
    if not args:
        await update.message.reply_text(
            "Pick a timeframe for trending narratives (5 credits):",
            reply_markup=_tf_keyboard("narratives"),
        )
        return
    try:
        await update.message.reply_text("Fetching trending narratives...")
        data = await elfa.trending_narratives(tf)
        await _send_html(update, _fmt_narratives(data, tf))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, remaining = _extract_tf(args)
    ticker = remaining[0] if remaining else None
    if not ticker:
        await update.message.reply_text("Usage: /news TICKER [timeframe]\nExample: /news ETH 1h")
        return
    if len(args) == 1:
        await update.message.reply_text(
            f"Pick a timeframe for {ticker.upper()} news:",
            reply_markup=_tf_keyboard("news", ticker),
        )
        return
    try:
        await update.message.reply_text(f"Fetching news for {ticker.upper()}...")
        data = await elfa.token_news(ticker, tf)
        await _send_html(update, _fmt_news(data, ticker, tf))
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_cas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, _ = _extract_tf(args)
    if not args:
        await update.message.reply_text(
            "Pick a timeframe for trending contract addresses:",
            reply_markup=_tf_keyboard("cas"),
        )
        return
    try:
        await update.message.reply_text("Fetching trending contract addresses...")
        tw = await elfa.trending_cas_twitter(tf)
        tg = await elfa.trending_cas_telegram(tf)
        msg = _fmt_cas(tw, "Twitter/X", tf) + "\n\n" + _fmt_cas(tg, "Telegram", tf)
        await _send_html(update, msg)
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, _ = _extract_tf(args)
    if not args:
        await update.message.reply_text(
            "Pick a timeframe for your market briefing:",
            reply_markup=_tf_keyboard("briefing"),
        )
        return
    try:
        await update.message.reply_text(f"Generating market briefing ({tf})... this may take a moment.")
        tokens = await elfa.get_trending_tokens(tf)
        cas_tw = await elfa.trending_cas_twitter(tf)
        cas_tg = await elfa.trending_cas_telegram(tf)
        msg = (
            _fmt_tokens(tokens, tf)
            + "\n\n"
            + _fmt_cas(cas_tw, "Twitter/X", tf)
            + "\n\n"
            + _fmt_cas(cas_tg, "Telegram", tf)
        )
        await _send_html(update, msg)
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_research(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    tf, remaining = _extract_tf(args)
    ticker = remaining[0] if remaining else None
    if not ticker:
        await update.message.reply_text("Usage: /research TICKER [timeframe]\nExample: /research SOL 4h")
        return
    if len(args) == 1:
        await update.message.reply_text(
            f"Pick a timeframe to research {ticker.upper()}:",
            reply_markup=_tf_keyboard("research", ticker),
        )
        return
    try:
        await update.message.reply_text(f"Researching {ticker.upper()} ({tf})...")
        mentions_data = await elfa.get_top_mentions(ticker, time_window=tf, limit=10)
        news_data = await elfa.token_news(ticker, tf, limit=5)
        msg = _fmt_mentions(mentions_data, ticker, tf) + "\n\n" + _fmt_news(news_data, ticker, tf)
        await _send_html(update, msg)
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    elfa = ctx.bot_data["elfa"]
    args = _parse_args(update.message.text)
    if not args:
        await update.message.reply_text("Usage: /chat MESSAGE\nExample: /chat What's happening with SOL?")
        return
    message = " ".join(args)
    try:
        await update.message.reply_text("Thinking...")
        data = await elfa.chat(message)
        result = data.get("data", data) if isinstance(data, dict) else data
        reply = (
            result.get("response") or result.get("message") or result.get("text")
            if isinstance(result, dict) else str(result)
        )
        sid = result.get("sessionId", "") if isinstance(result, dict) else ""
        text = f"<b>Elfa AI</b>\n\n{escape_html(str(reply))}"
        if sid:
            text += f"\n\nsession: <code>{escape_html(str(sid))}</code>"
        await _send_html(update, text)
    except Exception as e:
        await update.message.reply_text(f"<b>Error</b>: {escape_html(str(e))}", parse_mode=ParseMode.HTML)


# ── Inline Keyboard Callback Handler ─────────────────────────────────────────


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    command = parts[0]
    tf = parts[1] if len(parts) > 1 else DEFAULT_TF
    extra = parts[2] if len(parts) > 2 else ""
    elfa = ctx.bot_data["elfa"]

    try:
        await query.message.reply_text(f"Loading ({tf})...")

        if command == "trending":
            data = await elfa.get_trending_tokens(tf)
            await _send_html(update, _fmt_tokens(data, tf))
        elif command == "mentions":
            data = await elfa.get_top_mentions(extra, time_window=tf)
            await _send_html(update, _fmt_mentions(data, extra, tf))
        elif command == "search":
            data = await elfa.keyword_mentions(extra, tf)
            await _send_html(update, _fmt_search(data, extra, tf))
        elif command == "summary":
            data = await elfa.event_summary(extra, tf)
            await _send_html(update, _fmt_summary(data, extra, tf))
        elif command == "narratives":
            data = await elfa.trending_narratives(tf)
            await _send_html(update, _fmt_narratives(data, tf))
        elif command == "news":
            data = await elfa.token_news(extra, tf)
            await _send_html(update, _fmt_news(data, extra, tf))
        elif command == "cas":
            tw = await elfa.trending_cas_twitter(tf)
            tg = await elfa.trending_cas_telegram(tf)
            msg = _fmt_cas(tw, "Twitter/X", tf) + "\n\n" + _fmt_cas(tg, "Telegram", tf)
            await _send_html(update, msg)
        elif command == "briefing":
            tokens = await elfa.get_trending_tokens(tf)
            cas_tw = await elfa.trending_cas_twitter(tf)
            cas_tg = await elfa.trending_cas_telegram(tf)
            msg = (
                _fmt_tokens(tokens, tf)
                + "\n\n" + _fmt_cas(cas_tw, "Twitter/X", tf)
                + "\n\n" + _fmt_cas(cas_tg, "Telegram", tf)
            )
            await _send_html(update, msg)
        elif command == "research":
            mentions_data = await elfa.get_top_mentions(extra, time_window=tf, limit=10)
            news_data = await elfa.token_news(extra, tf, limit=5)
            msg = _fmt_mentions(mentions_data, extra, tf) + "\n\n" + _fmt_news(news_data, extra, tf)
            await _send_html(update, msg)

    except Exception as e:
        log.error("social_button_error", command=command, error=str(e))
        await _send_html(update, f"<b>Error</b>: {escape_html(str(e))}")
