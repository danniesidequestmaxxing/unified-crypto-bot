"""Position monitoring command handlers for Telegram.

Commands:
  /positions   — Force immediate portfolio update with charts
  /posplan     — Show the current trading plan with all levels
  /pospnl      — PnL breakdown with progress toward $10k
  /posfill     — Record a TP fill (e.g. /posfill SOL 85.00 50)
  /posadd      — Record an ADD (e.g. /posadd SOL 93.00 80)
  /posclose    — Close/deactivate a position plan (e.g. /posclose HYPE 39.06)
"""
from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.modules.position_config import PNL_TARGET


def _is_authorized(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Only allow the configured chat to use position commands."""
    allowed = ctx.bot_data.get("settings", {})
    if hasattr(allowed, "telegram_chat_id"):
        return str(update.effective_chat.id) == str(allowed.telegram_chat_id)
    return True


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Force an immediate portfolio update with charts."""
    if not _is_authorized(update, ctx):
        return
    monitor = ctx.bot_data.get("position_monitor")
    if not monitor:
        await update.message.reply_text("Position monitor not initialized.")
        return

    await update.message.reply_text("⏳ Fetching live data...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        await monitor._send_hourly_update()
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_posplan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the current trading plan."""
    if not _is_authorized(update, ctx):
        return
    monitor = ctx.bot_data.get("position_monitor")
    if not monitor:
        await update.message.reply_text("Position monitor not initialized.")
        return

    lines = ["📋 *Active Trading Plan*\n"]

    for plan in monitor._plans:
        tps = [l for l in plan.levels if l.action == "TP" and not l.triggered]
        adds = [l for l in plan.levels if l.action == "ADD" and not l.triggered]
        triggered = [l for l in plan.levels if l.triggered]

        bias_emoji = {"bearish": "🔴", "bullish": "🟢", "parabolic": "🟡"}.get(
            plan.trend_bias, "⚪"
        )

        lines.append(
            f"{bias_emoji} *{plan.coin}* {plan.leverage}x Short\n"
            f"  Entry: `${plan.entry:.2f}` │ Inv: `${plan.invalidation:.0f}`\n"
            f"  TPs: {', '.join(f'${l.price:.0f}' for l in tps) or 'none'}\n"
            f"  ADDs: {', '.join(f'${l.price:.0f}' for l in adds) or 'none'}\n"
        )
        if triggered:
            lines.append(
                f"  ✓ Triggered: {', '.join(f'{l.action} ${l.price:.0f}' for l in triggered)}\n"
            )
        lines.append(f"  _{plan.notes[:120]}_\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pospnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show PnL breakdown with progress."""
    if not _is_authorized(update, ctx):
        return
    monitor = ctx.bot_data.get("position_monitor")
    pos_db = ctx.bot_data.get("pos_db")
    if not monitor or not pos_db:
        await update.message.reply_text("Position monitor not initialized.")
        return

    try:
        from src.modules.position_monitor import hl_get_all_mids
        import asyncio

        prices = await asyncio.to_thread(hl_get_all_mids)

        total_unrealized = 0.0
        position_lines = []

        for plan in monitor._plans:
            price = prices.get(plan.coin, 0)
            pnl = plan.size * (plan.entry - price) if price else 0
            total_unrealized += pnl
            pnl_sign = "+" if pnl >= 0 else ""
            position_lines.append(
                f"  {plan.coin}: `{pnl_sign}${pnl:,.2f}` "
                f"(${price:.2f} vs ${plan.entry:.2f})"
            )

        realized = await pos_db.get_total_realized_pnl()
        total = total_unrealized + realized
        progress = (total / PNL_TARGET) * 100
        remaining = max(0, PNL_TARGET - total)

        # Event history
        events = await pos_db.get_events(limit=10)
        event_lines = []
        for e in events[:5]:
            pnl_str = f" ${e['pnl']:+,.0f}" if e["pnl"] else ""
            event_lines.append(
                f"  {e['event_type']} {e['coin']} @ ${e['price']:.2f}{pnl_str} — {e['ts'][:16]}"
            )

        text = (
            f"💰 *PnL Summary*\n\n"
            f"*Unrealized:*\n"
            + "\n".join(position_lines) + "\n"
            f"  Total: `{'+'if total_unrealized>=0 else ''}${total_unrealized:,.2f}`\n\n"
            f"*Realized:* `${realized:,.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*Combined:* `{'+'if total>=0 else ''}${total:,.2f}`\n"
            f"*Target:* `${PNL_TARGET:,.0f}` ({progress:.1f}%)\n"
            f"*Remaining:* `${remaining:,.0f}`\n"
        )

        if event_lines:
            text += f"\n📝 *Recent Events:*\n" + "\n".join(event_lines)

        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_posfill(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a TP fill. Usage: /posfill SOL 85.00 50"""
    if not _is_authorized(update, ctx):
        return
    monitor = ctx.bot_data.get("position_monitor")
    if not monitor:
        await update.message.reply_text("Position monitor not initialized.")
        return

    parts = (update.message.text or "").split()
    if len(parts) < 4:
        await update.message.reply_text(
            "Usage: `/posfill COIN PRICE SIZE`\n"
            "Example: `/posfill SOL 85.00 50`",
            parse_mode="Markdown",
        )
        return

    try:
        coin = parts[1].upper()
        price = float(parts[2])
        size = float(parts[3])
        label = " ".join(parts[4:]) if len(parts) > 4 else ""

        result = await monitor.record_tp_fill(coin, price, size, label)
        await update.message.reply_text(result, parse_mode="Markdown")

    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"Invalid input: {e}")


async def cmd_posadd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Record an ADD. Usage: /posadd SOL 93.00 80"""
    if not _is_authorized(update, ctx):
        return
    monitor = ctx.bot_data.get("position_monitor")
    if not monitor:
        await update.message.reply_text("Position monitor not initialized.")
        return

    parts = (update.message.text or "").split()
    if len(parts) < 4:
        await update.message.reply_text(
            "Usage: `/posadd COIN PRICE SIZE`\n"
            "Example: `/posadd SOL 93.00 80`",
            parse_mode="Markdown",
        )
        return

    try:
        coin = parts[1].upper()
        price = float(parts[2])
        size = float(parts[3])
        label = " ".join(parts[4:]) if len(parts) > 4 else ""

        result = await monitor.record_add(coin, price, size, label)
        await update.message.reply_text(result, parse_mode="Markdown")

    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"Invalid input: {e}")


async def cmd_fills(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent HL wallet fills. Usage: /fills [COIN] [limit]"""
    monitor = ctx.bot_data.get("position_monitor")
    if not monitor or not monitor.wallet:
        await update.message.reply_text("Position monitor not initialized or no wallet set.")
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    parts = (update.message.text or "").split()
    coin_filter = parts[1].upper() if len(parts) > 1 else None
    limit = int(parts[2]) if len(parts) > 2 else 20

    try:
        from src.modules.position_monitor import hl_get_user_fills
        import asyncio
        from datetime import datetime, timezone

        fills = await asyncio.to_thread(hl_get_user_fills, monitor.wallet, 50)

        if coin_filter:
            fills = [f for f in fills
                     if coin_filter in f["coin"].upper() or f["coin"].upper() in coin_filter]

        if not fills:
            await update.message.reply_text(f"No fills found{' for ' + coin_filter if coin_filter else ''}.")
            return

        fills = sorted(fills, key=lambda x: x["time"], reverse=True)[:limit]

        lines = [f"📋 *Recent Fills*{' — ' + coin_filter if coin_filter else ''}\n"]
        for f in fills:
            side = {"A": "Buy", "B": "Sell"}.get(f["side"].upper(), f["side"])
            direction = f.get("dir", "")
            closed_pnl = f["closed_pnl"]
            pnl_str = f" | PnL: ${closed_pnl:+.2f}" if abs(closed_pnl) > 0.001 else ""
            ts = datetime.fromtimestamp(f["time"] / 1000, tz=timezone.utc)
            time_str = ts.strftime("%m/%d %H:%M")

            lines.append(
                f"{time_str} | {f['coin']}\n"
                f"  {direction or side}: {f['sz']} @ ${f['px']:.4f}"
                f"{pnl_str}\n"
            )

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
