"""Position monitor configuration — all levels from the $10k optimization plan.

This is the single source of truth for the active trading plan.
Update levels here as positions scale in/out.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Level:
    price: float
    action: str          # "TP", "ADD", "SL", "INVALIDATION"
    size: float | None = None
    label: str = ""
    triggered: bool = False
    requires_tp1: bool = False  # Only alert/activate after TP1 has filled


@dataclass
class PositionPlan:
    coin: str
    direction: str       # "short" or "long"
    entry: float
    size: float
    leverage: int
    margin_mode: str     # "cross" or "isolated"
    trend_bias: str      # "bearish", "bullish", "parabolic"
    invalidation: float
    levels: list[Level] = field(default_factory=list)
    notes: str = ""
    hl_ticker: str = ""  # Hyperliquid internal ticker if different from coin


# ─── Hyperliquid wallet (read-only, for position fetching) ─────
# Set via HL_WALLET_ADDRESS env var
HL_WALLET_ADDRESS_ENV = "HL_WALLET_ADDRESS"

# ─── Monitoring intervals ──────────────────────────────────────
HOURLY_INTERVAL = 3600       # Full update with charts
PRICE_CHECK_INTERVAL = 30    # Alert checks
ALERT_COOLDOWN = 900         # 15min cooldown per (coin, level)
PROXIMITY_PCT = 2.0          # Alert within 2% of level
PNL_TARGET = 10_000.0

# ─── The Plan ──────────────────────────────────────────────────

INITIAL_PLANS: list[PositionPlan] = [
    PositionPlan(
        coin="SOL",
        direction="short",
        entry=91.8949,
        size=170.0,
        leverage=20,
        margin_mode="cross",
        trend_bias="bearish",
        invalidation=98.00,
        notes=(
            "Primary workhorse — 46% of $10k target. "
            "Bearish structure: lower highs from Jan 145, failed reclaim of 97-98 dashed line. "
            "Recycling mechanic: TP into wicks, re-add on bounces. 2-3 full cycles expected."
        ),
        levels=[
            Level(price=85.00, action="TP", size=50.0,   label="Partial TP — first support"),
            Level(price=80.00, action="TP", size=100.0,  label="TP — Feb breakdown level"),
            Level(price=75.00, action="TP", size=70.0,   label="TP — wick target"),
            Level(price=65.00, action="TP", size=50.0,   label="Final TP — Feb capitulation low"),
            Level(price=93.00, action="ADD", size=80.0,  label="Add short — bounce to resistance"),
            Level(price=89.00, action="ADD", size=100.0, label="Add short — bounce after TP1 fill", requires_tp1=True),
            Level(price=91.61, action="SL", size=170.0,  label="Trailing stop 2% — active on HL"),
        ],
    ),
    PositionPlan(
        coin="HYPE",
        direction="short",
        entry=41.2569,
        size=27.57,
        leverage=10,
        margin_mode="cross",
        trend_bias="bullish",
        invalidation=44.00,
        notes=(
            "Counter-trend short in uptrend — 6% of target. "
            "Higher highs since Jan $21. Trade the pullback only. "
            "Do NOT add above $41.50. Close everything if $44 breaks with conviction."
        ),
        levels=[
            Level(price=34.00, action="TP", size=15.0,   label="Partial TP — weak high"),
            Level(price=30.00, action="TP", size=15.0,   label="TP — flush to $30"),
            Level(price=27.00, action="TP", size=7.57,   label="Final TP — March low area"),
            Level(price=40.50, action="ADD", size=40.0,   label="Add short — dead cat bounce (only after TP1)", requires_tp1=True),
            Level(price=40.75, action="SL", size=27.57,  label="Trailing stop 3% — active on HL"),
        ],
    ),
    PositionPlan(
        coin="CRCL",
        direction="short",
        entry=132.60,
        size=18.087,
        leverage=10,
        margin_mode="isolated",
        trend_bias="parabolic",
        invalidation=140.00,
        hl_ticker="CRCL-USDC",
        notes=(
            "Highest risk — parabolic uptrend, 24% of target with deep plan. "
            "Add ONLY on confirmed breakdown below $128 (break + retest as resistance). "
            "Fib levels from $50→$133 move. If $140 closes on 1H, cut everything."
        ),
        levels=[
            Level(price=113.00, action="TP", size=10.0,   label="TP1 — 0.236 fib (first flush)"),
            Level(price=100.00, action="TP", size=10.0,   label="TP2 — 0.382 fib / round number"),
            Level(price=91.00,  action="TP", size=10.0,   label="TP3 — 0.500 fib (midpoint)"),
            Level(price=82.00,  action="TP", size=8.0,    label="TP4 — 0.618 fib (golden pocket)"),
            Level(price=80.00,  action="TP", size=5.087,  label="TP5 — final deep flush"),
            Level(price=128.00, action="ADD", size=25.0,   label="Add — ONLY if $128 breaks & retests as resistance"),
        ],
    ),
]
