"""/start and /help — categorized help menu."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

HELP_TEXT = """<b>Unified Crypto Intelligence Bot</b>

<b>Market Data (Claude Opus)</b>
/market — BTC derivatives snapshot + AI analysis
/headlines [hours] — CoinDesk crypto news + summary
/weekly — Weekly catalyst report with predictions
/fed — Polymarket Fed rate predictions + analysis

<b>Price &amp; Market Data (CoinGecko)</b>
/price &lt;coin&gt; — Price, volume, market cap lookup
/top [N] — Top N coins by market cap (default 10)

<b>Trading Analysis (Claude Sonnet)</b>
/analyze &lt;asset&gt; [tf] — Chart + AI trade analysis
/script &lt;description&gt; — Generate PineScript v6 code
/indicator &lt;type&gt; — Generate specific indicator
/autosignal &lt;asset&gt; [tf] — Schedule recurring signals
/stopsignal [asset] [tf] — Stop auto-signals
/signals — List active auto-signals
/performance [asset] [days] — Signal track record

<b>TradingView Webhooks</b>
/alerts — Subscribe to webhook alerts
/stopalerts — Unsubscribe

<b>Social Intelligence (Elfa AI)</b>
/trending [tf] — Trending tokens
/mentions TICKER [tf] — Top social mentions
/search KEYWORDS [tf] — Keyword mention search
/account USERNAME — KOL / account stats
/summary KEYWORDS [tf] — AI event summary
/narratives [tf] — Trending narratives
/news TICKER [tf] — Token social news
/cas [tf] — Trending contract addresses
/briefing [tf] — Full market briefing
/research TICKER [tf] — Token research report
/chat MESSAGE — Chat with Elfa AI
/ping — Elfa API health check

<b>Tips:</b>
• Social commands without a timeframe show buttons
• Timeframes: 15m, 30m, 1h, 4h, 8h, 12h, 24h, 3d, 7d
• Send any text message → treated as a trading question
• The bot <b>self-learns</b> from past signal outcomes
"""


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)
