"""/market — BTC derivatives snapshot across exchanges + Claude Opus analysis."""
from __future__ import annotations

from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from src.core.message_utils import send_long, send_plain_chunks


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
    binance = deps["binance"]
    bybit = deps["bybit"]
    hyperliquid = deps["hyperliquid"]
    analyst = deps["market_analyst"]
    db = deps["db"]
    settings = deps["settings"]

    # Rate limit check for Claude-powered command
    chat_id = update.effective_chat.id
    calls = await db.get_user_calls_last_hour(chat_id)
    if calls >= settings.claude_calls_per_user_per_hour:
        await update.message.reply_text(
            "Rate limit reached. Please wait before using AI-powered commands."
        )
        return

    await update.message.reply_text("Fetching real-time data from Binance, Bybit, Hyperliquid...")
    await update.message.chat.send_action(ChatAction.TYPING)

    btc = await binance.get_ticker_24hr("BTCUSDT")
    btc_price = float(btc.get("lastPrice", 0))
    btc_data = {
        "price": btc_price,
        "change_24h": float(btc.get("priceChangePercent", 0)),
        "volume_24h": float(btc.get("quoteVolume", 0)),
    }

    bin_f = await binance.get_btc_derivatives()
    byb_f = await bybit.get_btc_derivatives()
    hl_f = await hyperliquid.get_btc_derivatives()

    await db.record_user_call(chat_id)
    analysis = await analyst.market_snapshot(btc_data, bin_f, byb_f, hl_f)

    def fmt_oi(d: dict) -> str:
        return f"${d['oi_usd']:.2f}B" if d.get("oi_usd") is not None else "N/A"

    def fmt_fr(d: dict) -> str:
        if d.get("funding_rate") is None:
            return "N/A"
        return f"{'🟢' if d['funding_rate'] >= 0 else '🔴'} {d['funding_rate']:.4f}%"

    def fmt_ls(d: dict) -> str:
        if d.get("long_pct") is None:
            return "N/A"
        return f"🟢 {d['long_pct']:.1f}% / 🔴 {d['short_pct']:.1f}%"

    change_emoji = "📈" if btc_data["change_24h"] > 0 else "📉"
    msg = (
        f"BTC Market Snapshot\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Price:      ${btc_data['price']:,}\n"
        f"24h Change: {change_emoji} {btc_data['change_24h']:.2f}%\n"
        f"Volume:     ${btc_data['volume_24h'] / 1e9:.2f}B (Binance 24h)\n\n"
        f"📊 Binance Futures\n"
        f"OI: {fmt_oi(bin_f)}  |  Funding: {fmt_fr(bin_f)}\n"
        f"L/S (5m): {fmt_ls(bin_f)}\n\n"
        f"📊 Bybit\n"
        f"OI: {fmt_oi(byb_f)}  |  Funding: {fmt_fr(byb_f)}\n"
        f"L/S (5m): {fmt_ls(byb_f)}\n\n"
        f"📊 Hyperliquid\n"
        f"OI: {fmt_oi(hl_f)}  |  Funding: {fmt_fr(hl_f)}\n\n"
        f"🤖 AI Analysis:\n{analysis}\n\n"
        f"Time: {datetime.now(timezone.utc).strftime('%H:%M')} UTC"
    )
    await send_plain_chunks(update, msg)
