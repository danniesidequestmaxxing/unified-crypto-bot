"""/performance — Signal track record and stats."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


async def cmd_performance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    deps = ctx.bot_data
    db = deps["db"]

    args = ctx.args or []
    asset = args[0].upper() if args else None
    days = 30
    if len(args) > 1:
        try:
            days = int(args[1])
        except ValueError:
            pass

    lines = [f"<b>Signal Performance Report</b> (last {days} days)\n"]

    perf = await db.get_performance_summary(asset=asset, days=days)
    total = perf.get("total_signals", 0)
    if total == 0:
        await update.message.reply_text(
            "No resolved signals yet. The bot needs time to track outcomes.\n"
            "Signals are checked automatically — results typically appear after "
            "a few candle closes.",
            parse_mode=ParseMode.HTML,
        )
        return

    tp1_wins = perf.get("tp1_wins", 0)
    tp2_wins = perf.get("tp2_wins", 0)
    tp3_wins = perf.get("tp3_wins", 0)
    sl_losses = perf.get("sl_losses", 0)
    win_rate = (tp1_wins / total * 100) if total > 0 else 0

    lines.append("<b>Overall:</b>")
    lines.append(f"  Signals resolved: {total}")
    lines.append(f"  Win rate (TP1+): <b>{win_rate:.1f}%</b>")
    lines.append(f"  TP1 hits: {tp1_wins} | TP2: {tp2_wins} | TP3: {tp3_wins}")
    lines.append(f"  SL hits: {sl_losses}")
    lines.append(f"  Avg P&L: <b>{perf.get('avg_pnl', 0):+.2f}%</b>")
    lines.append(f"  Avg max favorable: {perf.get('avg_max_favorable', 0):.2f}%")
    lines.append(f"  Avg max adverse: {perf.get('avg_max_adverse', 0):.2f}%")
    lines.append(f"  Avg candles to exit: {perf.get('avg_candles_to_exit', 0):.1f}")
    lines.append("")

    session_perf = await db.get_session_performance(days=days)
    if session_perf:
        lines.append("<b>By Market Session:</b>")
        for sp in session_perf:
            lines.append(
                f"  {sp['market_session']}: {sp['win_rate']}% WR "
                f"({sp['tp1_wins']}W/{sp['sl_losses']}L) "
                f"avg {sp['avg_pnl']:+.2f}%"
            )
        lines.append("")

    asset_perf = await db.get_asset_performance(days=days)
    if asset_perf:
        lines.append("<b>By Asset:</b>")
        for ap in asset_perf:
            lines.append(
                f"  {ap['asset']} {ap['timeframe']}: {ap['win_rate']}% WR "
                f"({ap['tp1_wins']}W/{ap['sl_losses']}L) "
                f"avg {ap['avg_pnl']:+.2f}%"
            )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
