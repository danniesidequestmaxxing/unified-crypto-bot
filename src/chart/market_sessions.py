"""Market session awareness — identifies current trading session context.

Ported verbatim from telegram-pinescript-bot market_sessions.py.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

ET_OFFSET = timedelta(hours=-5)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_et(dt: datetime) -> datetime:
    return dt.astimezone(timezone(ET_OFFSET))


def get_current_sessions(dt: datetime | None = None) -> dict:
    """Return a dict describing all active market sessions right now."""
    if dt is None:
        dt = _utc_now()

    et = _to_et(dt)
    hour_et = et.hour
    minute_et = et.minute
    time_decimal = hour_et + minute_et / 60.0
    weekday = et.weekday()

    sessions = []
    detail_parts = []
    cme_open = False
    cme_gap_risk = False

    is_weekend_closed = (
        (weekday == 5 and time_decimal >= 17)
        or weekday == 6 and time_decimal < 18
        or (weekday == 5)
    )
    is_daily_break = 17 <= time_decimal < 18 and weekday < 5

    if not is_weekend_closed and not is_daily_break:
        cme_open = True

    if weekday == 6 and 18 <= time_decimal < 18.5:
        sessions.append("cme_weekly_open")
        detail_parts.append("CME weekly open — watch for gap fill")
        cme_gap_risk = True
    elif 18 <= time_decimal < 18.25 and weekday < 5:
        sessions.append("cme_daily_reopen")
        detail_parts.append("CME daily reopen after maintenance break")

    if weekday < 5:
        if 4 <= time_decimal < 9.5:
            sessions.append("us_premarket")
            detail_parts.append("US pre-market session")
        elif 9.5 <= time_decimal < 10:
            sessions.append("us_market_open")
            detail_parts.append("US market open (first 30 min) — high volatility expected")
        elif 10 <= time_decimal < 16:
            sessions.append("us_market")
            if 10 <= time_decimal < 11.5:
                detail_parts.append("US market early session — momentum moves")
            elif 11.5 <= time_decimal < 13.5:
                detail_parts.append("US midday — typically lower volatility")
            elif 15 <= time_decimal < 16:
                detail_parts.append("US power hour — watch for strong directional moves")
            else:
                detail_parts.append("US regular session")
        elif 16 <= time_decimal < 20:
            sessions.append("us_afterhours")
            detail_parts.append("US after-hours — lower liquidity")

    if weekday < 5 and 3 <= time_decimal < 11.5:
        sessions.append("london")
        if 3 <= time_decimal < 3.5:
            detail_parts.append("London open — EUR/GBP pairs active")
        elif 9.5 <= time_decimal < 11.5:
            detail_parts.append("London/NY overlap — peak global liquidity")

    asian_active = (
        (weekday < 5 and time_decimal >= 19)
        or (weekday < 5 and time_decimal < 4)
        or (weekday == 6 and time_decimal >= 19)
    )
    if asian_active:
        sessions.append("asia")
        if 20 <= time_decimal < 21:
            detail_parts.append("Asian session open — Tokyo/HK coming online")
        elif 0 <= time_decimal < 4:
            detail_parts.append("Late Asian session — pre-London positioning")
        else:
            detail_parts.append("Asian session active")

    if not sessions:
        sessions.append("off_hours")
        detail_parts.append("Between sessions — lower volume/liquidity")

    priority = [
        "cme_weekly_open", "us_market_open", "us_market", "london",
        "us_premarket", "asia", "cme_daily_reopen", "us_afterhours", "off_hours",
    ]
    primary = "off_hours"
    for p in priority:
        if p in sessions:
            primary = p
            break

    return {
        "timestamp_utc": dt.isoformat(),
        "timestamp_et": et.isoformat(),
        "weekday": et.strftime("%A"),
        "hour_et": hour_et,
        "sessions": sessions,
        "primary_session": primary,
        "detail": " | ".join(detail_parts) if detail_parts else "No major session active",
        "cme_open": cme_open,
        "cme_gap_risk": cme_gap_risk,
    }


def format_session_context(session_info: dict) -> str:
    """Format session info as a string for inclusion in Claude prompts."""
    lines = [
        f"Current Time (ET): {session_info['timestamp_et']}",
        f"Day: {session_info['weekday']}",
        f"Active Sessions: {', '.join(session_info['sessions'])}",
        f"Primary Session: {session_info['primary_session']}",
        f"Context: {session_info['detail']}",
        f"CME Futures Open: {'Yes' if session_info['cme_open'] else 'No'}",
    ]
    if session_info["cme_gap_risk"]:
        lines.append("CME GAP RISK: Yes — Sunday open, watch for gap fill vs continuation")
    return "\n".join(lines)
