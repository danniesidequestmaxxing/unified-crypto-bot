"""/price and /top — CoinGecko-powered price and market data commands."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from src.core.message_utils import friendly_error_message, send_plain_chunks


def _fmt_usd(value: float) -> str:
    """Format a USD value with appropriate suffix."""
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value:,.0f}"


def _fmt_price(price: float) -> str:
    """Format price with appropriate decimals."""
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:.6f}"


async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/price <coin> — look up price, 24h change, volume, and market cap."""
    coingecko = ctx.bot_data["coingecko"]
    args = (update.message.text or "").strip().split()[1:]
    if not args:
        await update.message.reply_text(
            "Usage: /price <coin>\n"
            "Examples: /price bitcoin, /price ethereum, /price solana"
        )
        return

    coin_id = args[0].lower()
    await update.message.reply_text(f"Looking up {coin_id}...")

    try:
        data = await coingecko.get_price(ids=coin_id)
    except Exception as e:
        await update.message.reply_text(friendly_error_message(e))
        return

    if not data or coin_id not in data:
        await update.message.reply_text(
            f"Coin '{coin_id}' not found. Use the CoinGecko coin ID "
            "(e.g. bitcoin, ethereum, solana, cardano)."
        )
        return

    info = data[coin_id]
    price = info.get("usd", 0)
    change = info.get("usd_24h_change", 0)
    vol = info.get("usd_24h_vol", 0)
    mcap = info.get("usd_market_cap", 0)

    arrow = "+" if change >= 0 else ""
    emoji = "\U0001f4c8" if change >= 0 else "\U0001f4c9"

    msg = (
        f"{coin_id.upper()}\n"
        f"{'=' * 20}\n"
        f"Price:      {_fmt_price(price)}\n"
        f"24h Change: {emoji} {arrow}{change:.2f}%\n"
        f"24h Volume: {_fmt_usd(vol)}\n"
        f"Market Cap: {_fmt_usd(mcap)}"
    )
    await send_plain_chunks(update, msg)


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/top [N] — top N coins by market cap (default 10, max 25)."""
    coingecko = ctx.bot_data["coingecko"]
    args = (update.message.text or "").strip().split()[1:]

    count = 10
    if args:
        try:
            count = min(max(int(args[0]), 1), 25)
        except ValueError:
            pass

    await update.message.reply_text(f"Fetching top {count} coins...")

    try:
        coins = await coingecko.get_coins_markets(per_page=count)
    except Exception as e:
        await update.message.reply_text(friendly_error_message(e))
        return

    if not coins:
        await update.message.reply_text("No market data available.")
        return

    lines = [f"Top {count} Coins by Market Cap", "=" * 30]
    for c in coins:
        rank = c.get("market_cap_rank", "?")
        sym = (c.get("symbol") or "?").upper()
        price = c.get("current_price") or 0
        change = c.get("price_change_percentage_24h") or 0
        mcap = c.get("market_cap") or 0

        arrow = "+" if change >= 0 else ""
        lines.append(
            f"{rank}. {sym:>6}  {_fmt_price(price):>12}  "
            f"{arrow}{change:.1f}%  MC: {_fmt_usd(mcap)}"
        )

    await send_plain_chunks(update, "\n".join(lines))
