"""Position tracking persistence — extends the unified Database.

Adds tables for:
  - position_plans: The trading plan (entries, levels, invalidations)
  - position_snapshots: Hourly price/PnL snapshots for charting progress
  - position_events: TP fills, SL triggers, adds, closes — feeds self-learning
  - position_directives: AI-generated directives from outcome analysis
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import structlog

log = structlog.get_logger()

POSITION_SCHEMA = """
-- ── Position monitoring tables ─────────────────────────

CREATE TABLE IF NOT EXISTS position_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'short',
    entry_price     REAL NOT NULL,
    size            REAL NOT NULL,
    leverage        INTEGER NOT NULL,
    margin_mode     TEXT NOT NULL DEFAULT 'cross',
    trend_bias      TEXT NOT NULL DEFAULT 'bearish',
    invalidation    REAL NOT NULL,
    levels_json     TEXT NOT NULL,
    notes           TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(coin, direction, active)
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    mark_price      REAL NOT NULL,
    entry_price     REAL NOT NULL,
    size            REAL NOT NULL,
    unrealized_pnl  REAL NOT NULL,
    funding_rate    REAL,
    oi_usd          REAL,
    atr_pct         REAL,
    volume_change   REAL,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pos_snap_ts ON position_snapshots(coin, ts);

CREATE TABLE IF NOT EXISTS position_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         INTEGER NOT NULL REFERENCES position_plans(id),
    event_type      TEXT NOT NULL,
    price           REAL NOT NULL,
    size            REAL,
    pnl             REAL,
    level_label     TEXT,
    notes           TEXT,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pos_events ON position_events(plan_id, ts);

CREATE TABLE IF NOT EXISTS position_directives (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    directive       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'ai_analysis',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS realized_pnl_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    pnl             REAL NOT NULL,
    cumulative      REAL NOT NULL,
    ts              TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PositionDatabase:
    """Position-tracking persistence layer — wraps the shared aiosqlite connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def init_schema(self) -> None:
        await self._conn.executescript(POSITION_SCHEMA)
        log.info("position_schema_initialized")

    # ── Plans ──────────────────────────────────────────────

    async def upsert_plan(
        self, coin: str, direction: str, entry_price: float,
        size: float, leverage: int, margin_mode: str,
        trend_bias: str, invalidation: float,
        levels: list[dict], notes: str = "",
    ) -> int:
        now = _utcnow()
        # Deactivate existing plan for same coin/direction
        await self._conn.execute(
            "UPDATE position_plans SET active=0, updated_at=? "
            "WHERE coin=? AND direction=? AND active=1",
            (now, coin, direction),
        )
        cur = await self._conn.execute(
            "INSERT INTO position_plans "
            "(coin, direction, entry_price, size, leverage, margin_mode, "
            " trend_bias, invalidation, levels_json, notes, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (coin, direction, entry_price, size, leverage, margin_mode,
             trend_bias, invalidation, json.dumps(levels), notes, now, now),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_active_plans(self) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM position_plans WHERE active=1 ORDER BY coin",
        )
        cols = [d[0] for d in cur.description]
        rows = []
        for row in await cur.fetchall():
            d = dict(zip(cols, row))
            d["levels"] = json.loads(d.pop("levels_json"))
            rows.append(d)
        return rows

    async def update_plan_levels(self, plan_id: int, levels: list[dict]) -> None:
        await self._conn.execute(
            "UPDATE position_plans SET levels_json=?, updated_at=? WHERE id=?",
            (json.dumps(levels), _utcnow(), plan_id),
        )
        await self._conn.commit()

    async def deactivate_plan(self, plan_id: int) -> None:
        await self._conn.execute(
            "UPDATE position_plans SET active=0, updated_at=? WHERE id=?",
            (_utcnow(), plan_id),
        )
        await self._conn.commit()

    # ── Snapshots ──────────────────────────────────────────

    async def insert_snapshot(
        self, coin: str, mark_price: float, entry_price: float,
        size: float, unrealized_pnl: float,
        funding_rate: float | None = None,
        oi_usd: float | None = None,
        atr_pct: float | None = None,
        volume_change: float | None = None,
    ) -> int:
        cur = await self._conn.execute(
            "INSERT INTO position_snapshots "
            "(coin, mark_price, entry_price, size, unrealized_pnl, "
            " funding_rate, oi_usd, atr_pct, volume_change, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (coin, mark_price, entry_price, size, unrealized_pnl,
             funding_rate, oi_usd, atr_pct, volume_change, _utcnow()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_snapshots(
        self, coin: str, hours: int = 168,
    ) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM position_snapshots "
            "WHERE coin=? AND ts >= datetime('now', ?) ORDER BY ts",
            (coin, f"-{hours} hours"),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    # ── Events ─────────────────────────────────────────────

    async def record_event(
        self, plan_id: int, event_type: str, price: float,
        size: float | None = None, pnl: float | None = None,
        level_label: str = "", notes: str = "",
    ) -> int:
        cur = await self._conn.execute(
            "INSERT INTO position_events "
            "(plan_id, event_type, price, size, pnl, level_label, notes, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, event_type, price, size, pnl, level_label, notes, _utcnow()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_events(
        self, plan_id: int | None = None, coin: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        where, params = ["1=1"], []
        if plan_id:
            where.append("e.plan_id=?")
            params.append(plan_id)
        if coin:
            where.append("p.coin=?")
            params.append(coin)
        cur = await self._conn.execute(
            f"SELECT e.*, p.coin FROM position_events e "
            f"JOIN position_plans p ON p.id = e.plan_id "
            f"WHERE {' AND '.join(where)} ORDER BY e.ts DESC LIMIT ?",
            (*params, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    # ── Realized PnL ───────────────────────────────────────

    async def record_realized_pnl(
        self, coin: str, event_type: str, pnl: float,
    ) -> float:
        """Record realized PnL and return new cumulative total."""
        cur = await self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM realized_pnl_log",
        )
        row = await cur.fetchone()
        cumulative = (row[0] if row else 0) + pnl

        await self._conn.execute(
            "INSERT INTO realized_pnl_log (coin, event_type, pnl, cumulative, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (coin, event_type, pnl, cumulative, _utcnow()),
        )
        await self._conn.commit()
        return cumulative

    async def get_total_realized_pnl(self) -> float:
        cur = await self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM realized_pnl_log",
        )
        row = await cur.fetchone()
        return row[0] if row else 0.0

    # ── Directives ─────────────────────────────────────────

    async def add_directive(
        self, coin: str, directive: str, source: str = "ai_analysis",
    ) -> None:
        await self._conn.execute(
            "INSERT INTO position_directives (coin, directive, source, active, created_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (coin, directive, source, _utcnow()),
        )
        await self._conn.commit()

    async def get_active_directives(self, coin: str | None = None) -> list[dict]:
        where, params = ["active=1"], []
        if coin:
            where.append("coin=?")
            params.append(coin)
        cur = await self._conn.execute(
            f"SELECT * FROM position_directives WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC LIMIT 20",
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def deactivate_directive(self, directive_id: int) -> None:
        await self._conn.execute(
            "UPDATE position_directives SET active=0 WHERE id=?",
            (directive_id,),
        )
        await self._conn.commit()

    # ── Learning context builder ───────────────────────────

    async def build_position_learning_context(self, coin: str) -> str:
        """Build a self-learning context block from position events and directives."""
        events = await self.get_events(coin=coin, limit=20)
        directives = await self.get_active_directives(coin=coin)

        if not events and not directives:
            return ""

        parts = [f"\n--- POSITION SELF-LEARNING ({coin}) ---"]

        if events:
            tp_events = [e for e in events if e["event_type"] == "TP_FILL"]
            sl_events = [e for e in events if e["event_type"] == "SL_TRIGGER"]
            add_events = [e for e in events if e["event_type"] == "ADD"]

            total_tp_pnl = sum(e["pnl"] or 0 for e in tp_events)
            total_sl_pnl = sum(e["pnl"] or 0 for e in sl_events)

            parts.append(
                f"Events: {len(tp_events)} TPs (+${total_tp_pnl:.0f}), "
                f"{len(sl_events)} SLs (${total_sl_pnl:.0f}), "
                f"{len(add_events)} ADDs"
            )

            for e in events[:10]:
                pnl_val = e["pnl"]
                pnl_str = f"PnL: ${pnl_val:.0f}" if pnl_val else ""
                label = e["level_label"] or ""
                parts.append(
                    f"  {e['event_type']} @ ${e['price']:.2f} "
                    f"{pnl_str} {label} | {e['ts'][:16]}"
                )

        if directives:
            parts.append("\n  ACTIVE DIRECTIVES:")
            for d in directives:
                parts.append(f"  >> [{d['source']}] {d['directive']}")

        parts.append("--- END POSITION SELF-LEARNING ---")
        return "\n".join(parts)
