"""Background outcome tracker — checks real price action against past signals.

Ported from telegram-pinescript-bot outcome_tracker.py, async-ified.
"""
from __future__ import annotations

import structlog

from src.clients.binance import BinanceClient
from src.chart.generator import TIMEFRAME_MAP
from src.core.database import Database

log = structlog.get_logger()

MAX_CANDLES_TIMEOUT = {
    "15M": 48, "30M": 48, "1H": 48, "2H": 36,
    "4H": 30, "6H": 28, "8H": 21, "12H": 14, "1D": 7,
}


async def _fetch_candles_since(
    binance: BinanceClient, asset: str, timeframe: str,
    since_iso: str, limit: int = 100,
) -> list[dict]:
    from datetime import datetime
    interval = TIMEFRAME_MAP.get(timeframe.upper(), timeframe.lower())
    start_dt = datetime.fromisoformat(since_iso)
    start_ms = int(start_dt.timestamp() * 1000)
    try:
        raw = await binance.get_klines(asset, interval, limit=limit, start_time=start_ms)
        return [
            {"open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
             "close": float(k[4]), "volume": float(k[5]), "timestamp": k[0]}
            for k in raw
        ]
    except Exception as e:
        log.warning("outcome_fetch_failed", asset=asset, error=str(e))
        return []


def _evaluate_signal(signal: dict, candles: list[dict]) -> dict | None:
    direction = signal["direction"].upper() if signal["direction"] else None
    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    tp3 = signal["tp3"]

    if not direction or not entry:
        return None

    is_long = direction == "LONG"
    tp1_hit = bool(signal.get("tp1_hit"))
    tp2_hit = bool(signal.get("tp2_hit"))
    tp3_hit = bool(signal.get("tp3_hit"))
    sl_hit = bool(signal.get("sl_hit"))

    max_favorable = 0.0
    max_adverse = 0.0
    exit_reason = "open"
    final = False
    candles_to_exit = len(candles)

    for i, c in enumerate(candles):
        high, low = c["high"], c["low"]
        if is_long:
            favorable = ((high - entry) / entry) * 100
            adverse = ((entry - low) / entry) * 100
        else:
            favorable = ((entry - low) / entry) * 100
            adverse = ((high - entry) / entry) * 100

        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)

        if tp1 and not tp1_hit:
            if (is_long and high >= tp1) or (not is_long and low <= tp1):
                tp1_hit = True
                if exit_reason == "open":
                    exit_reason = "tp1"
                    candles_to_exit = i + 1
        if tp2 and not tp2_hit:
            if (is_long and high >= tp2) or (not is_long and low <= tp2):
                tp2_hit = True
                exit_reason = "tp2"
                candles_to_exit = i + 1
        if tp3 and not tp3_hit:
            if (is_long and high >= tp3) or (not is_long and low <= tp3):
                tp3_hit = True
                exit_reason = "tp3"
                candles_to_exit = i + 1
        if sl and not sl_hit:
            if (is_long and low <= sl) or (not is_long and high >= sl):
                sl_hit = True
                if not tp1_hit:
                    exit_reason = "sl"
                    candles_to_exit = i + 1

    pnl = 0.0
    if exit_reason == "sl" and sl:
        pnl = -abs((sl - entry) / entry) * 100
    elif exit_reason == "tp3" and tp3:
        pnl = abs((tp3 - entry) / entry) * 100
    elif exit_reason == "tp2" and tp2:
        pnl = abs((tp2 - entry) / entry) * 100
    elif exit_reason == "tp1" and tp1:
        pnl = abs((tp1 - entry) / entry) * 100
    elif exit_reason == "open" and candles:
        current = candles[-1]["close"]
        pnl = ((current - entry) / entry * 100) if is_long else ((entry - current) / entry * 100)

    timeout_candles = MAX_CANDLES_TIMEOUT.get(signal["timeframe"], 30)
    if sl_hit or tp3_hit or (tp1_hit and not tp2) or len(candles) >= timeout_candles:
        final = True
        if exit_reason == "open":
            exit_reason = "timeout"

    return {
        "price_at_check": candles[-1]["close"] if candles else entry,
        "tp1_hit": tp1_hit, "tp2_hit": tp2_hit, "tp3_hit": tp3_hit,
        "sl_hit": sl_hit, "max_favorable": round(max_favorable, 4),
        "max_adverse": round(max_adverse, 4), "pnl_percent": round(pnl, 4),
        "exit_reason": exit_reason, "candles_to_exit": candles_to_exit, "final": final,
    }


async def check_all_outcomes(binance: BinanceClient, db: Database) -> int:
    """Check outcomes for all pending signals. Returns number checked."""
    pending = await db.get_pending_outcomes()
    checked = 0
    for sig in pending:
        candles = await _fetch_candles_since(binance, sig["asset"], sig["timeframe"], sig["created_at"])
        if not candles:
            continue
        result = _evaluate_signal(sig, candles)
        if result is None:
            continue
        await db.upsert_outcome(
            signal_id=sig["id"], price_at_check=result["price_at_check"],
            tp1_hit=result["tp1_hit"], tp2_hit=result["tp2_hit"],
            tp3_hit=result["tp3_hit"], sl_hit=result["sl_hit"],
            max_favorable=result["max_favorable"], max_adverse=result["max_adverse"],
            pnl_percent=result["pnl_percent"], exit_reason=result["exit_reason"],
            candles_to_exit=result["candles_to_exit"], final=result["final"],
        )
        checked += 1
        log.info("outcome_checked", signal_id=sig["id"], asset=sig["asset"],
                 result=result["exit_reason"], pnl=result["pnl_percent"])
    return checked
