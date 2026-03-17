"""Unified SQLite persistence layer — merged schemas from liquidation-bot + pinescript-bot.

Uses aiosqlite with WAL mode for concurrent async access.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import structlog

log = structlog.get_logger()

_SCHEMA = """
-- ── Liquidation-bot tables ────────────────────────────

CREATE TABLE IF NOT EXISTS heatmap_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    mid_price   REAL NOT NULL,
    cluster_json TEXT NOT NULL,
    alert_sent  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_heatmap_ts ON heatmap_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS oi_funding_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    oi_usd          REAL NOT NULL,
    oi_change_1h_pct REAL,
    volume_usd      REAL,
    funding_rate    REAL,
    flagged         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_oifr_ts ON oi_funding_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS social_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    mention_count_recent REAL,
    mention_7d_ma     REAL,
    is_trending       INTEGER NOT NULL DEFAULT 0,
    ghost_confirmed   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_social_ts ON social_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    module      TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    message     TEXT NOT NULL,
    telegram_ok INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(symbol, ts);

-- ── Pinescript-bot tables ─────────────────────────────

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    asset           TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    direction       TEXT,
    entry           REAL,
    sl              REAL,
    tp1             REAL,
    tp2             REAL,
    tp3             REAL,
    market_session  TEXT,
    session_detail  TEXT,
    analysis_text   TEXT,
    created_at      TEXT NOT NULL,
    source          TEXT DEFAULT 'autosignal'
);
CREATE INDEX IF NOT EXISTS idx_signals_asset_tf ON signals(asset, timeframe);
CREATE INDEX IF NOT EXISTS idx_signals_session ON signals(market_session);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER NOT NULL UNIQUE REFERENCES signals(id),
    price_at_check  REAL,
    tp1_hit         INTEGER DEFAULT 0,
    tp2_hit         INTEGER DEFAULT 0,
    tp3_hit         INTEGER DEFAULT 0,
    sl_hit          INTEGER DEFAULT 0,
    max_favorable   REAL,
    max_adverse     REAL,
    pnl_percent     REAL,
    exit_reason     TEXT,
    candles_to_exit INTEGER,
    checked_at      TEXT NOT NULL,
    final           INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outcomes_final ON outcomes(final);

CREATE TABLE IF NOT EXISTS autosignal_subs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    asset           TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    active          INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL,
    UNIQUE(chat_id, asset, timeframe)
);

-- ── Per-user rate limiting ────────────────────────────

CREATE TABLE IF NOT EXISTS user_rate_limits (
    chat_id     INTEGER NOT NULL,
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_url_chat ON user_rate_limits(chat_id, ts);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)
        log.info("db_connected", path=self.path)

    # ── Heatmap (from liquidation-bot) ─────────────────

    async def insert_heatmap(
        self, symbol: str, mid_price: float, clusters: list[dict],
        alert_sent: bool = False,
    ) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "INSERT INTO heatmap_snapshots (ts, symbol, mid_price, cluster_json, alert_sent) "
            "VALUES (?, ?, ?, ?, ?)",
            (_utcnow(), symbol, mid_price, json.dumps(clusters), int(alert_sent)),
        )
        await self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_recent_heatmap_alert(
        self, symbol: str, minutes: int = 30,
    ) -> dict | None:
        assert self._conn
        cur = await self._conn.execute(
            "SELECT mid_price, ts FROM alerts "
            "WHERE module = 'heatmap' AND symbol = ? "
            "  AND ts >= datetime('now', ?) "
            "ORDER BY ts DESC LIMIT 1",
            (symbol, f"-{minutes} minutes"),
        )
        row = await cur.fetchone()
        return None if row is None else {"mid_price": row[0], "ts": row[1]}

    # ── OI / Funding (from liquidation-bot) ────────────

    async def insert_oi_funding(
        self, symbol: str, oi_usd: float,
        oi_change_1h_pct: float | None, volume_usd: float | None,
        funding_rate: float | None, flagged: bool = False,
    ) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "INSERT INTO oi_funding_snapshots "
            "(ts, symbol, oi_usd, oi_change_1h_pct, volume_usd, funding_rate, flagged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_utcnow(), symbol, oi_usd, oi_change_1h_pct, volume_usd,
             funding_rate, int(flagged)),
        )
        await self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_oi_1h_ago(self, symbol: str) -> float | None:
        assert self._conn
        cur = await self._conn.execute(
            "SELECT oi_usd FROM oi_funding_snapshots "
            "WHERE symbol = ? AND ts <= datetime('now', '-50 minutes') "
            "ORDER BY ts DESC LIMIT 1",
            (symbol,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    # ── Social (from liquidation-bot) ──────────────────

    async def insert_social(
        self, symbol: str, mention_count_recent: float,
        mention_7d_ma: float, is_trending: bool,
        ghost_confirmed: bool = False,
    ) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "INSERT INTO social_snapshots "
            "(ts, symbol, mention_count_recent, mention_7d_ma, is_trending, ghost_confirmed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_utcnow(), symbol, mention_count_recent, mention_7d_ma,
             int(is_trending), int(ghost_confirmed)),
        )
        await self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ── Alerts (from liquidation-bot) ──────────────────

    async def insert_alert(
        self, module: str, symbol: str, message: str,
        telegram_ok: bool = True,
    ) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "INSERT INTO alerts (ts, module, symbol, message, telegram_ok) "
            "VALUES (?, ?, ?, ?, ?)",
            (_utcnow(), module, symbol, message, int(telegram_ok)),
        )
        await self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def was_recently_alerted(
        self, module: str, symbol: str, minutes: int = 30,
    ) -> bool:
        assert self._conn
        cur = await self._conn.execute(
            "SELECT 1 FROM alerts "
            "WHERE module = ? AND symbol = ? AND telegram_ok = 1 "
            "  AND ts >= datetime('now', ?) LIMIT 1",
            (module, symbol, f"-{minutes} minutes"),
        )
        return (await cur.fetchone()) is not None

    # ── Signals (from pinescript-bot, async port) ──────

    async def record_signal(
        self, chat_id: int, asset: str, timeframe: str,
        direction: str | None, entry: float | None,
        sl: float | None, tp1: float | None,
        tp2: float | None, tp3: float | None,
        market_session: str, session_detail: dict,
        analysis_text: str, source: str = "autosignal",
    ) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "INSERT INTO signals "
            "(chat_id, asset, timeframe, direction, entry, sl, tp1, tp2, tp3, "
            " market_session, session_detail, analysis_text, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, asset, timeframe, direction, entry, sl, tp1, tp2, tp3,
             market_session, json.dumps(session_detail), analysis_text,
             datetime.now(timezone.utc).isoformat(), source),
        )
        await self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_pending_outcomes(self) -> list[dict]:
        assert self._conn
        cur = await self._conn.execute("""
            SELECT s.id, s.asset, s.timeframe, s.direction, s.entry, s.sl,
                   s.tp1, s.tp2, s.tp3, s.created_at,
                   o.tp1_hit, o.tp2_hit, o.tp3_hit, o.sl_hit
            FROM signals s
            LEFT JOIN outcomes o ON o.signal_id = s.id
            WHERE s.entry IS NOT NULL
              AND s.direction IS NOT NULL
              AND (o.id IS NULL OR o.final = 0)
              AND s.created_at > datetime('now', '-7 days')
            ORDER BY s.created_at
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def upsert_outcome(
        self, signal_id: int, price_at_check: float,
        tp1_hit: bool, tp2_hit: bool, tp3_hit: bool, sl_hit: bool,
        max_favorable: float, max_adverse: float, pnl_percent: float,
        exit_reason: str, candles_to_exit: int, final: bool,
    ) -> None:
        assert self._conn
        await self._conn.execute(
            """INSERT INTO outcomes
               (signal_id, price_at_check, tp1_hit, tp2_hit, tp3_hit, sl_hit,
                max_favorable, max_adverse, pnl_percent, exit_reason,
                candles_to_exit, checked_at, final)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(signal_id) DO UPDATE SET
                price_at_check=excluded.price_at_check,
                tp1_hit=excluded.tp1_hit, tp2_hit=excluded.tp2_hit,
                tp3_hit=excluded.tp3_hit, sl_hit=excluded.sl_hit,
                max_favorable=excluded.max_favorable,
                max_adverse=excluded.max_adverse,
                pnl_percent=excluded.pnl_percent,
                exit_reason=excluded.exit_reason,
                candles_to_exit=excluded.candles_to_exit,
                checked_at=excluded.checked_at,
                final=excluded.final
            """,
            (signal_id, price_at_check, int(tp1_hit), int(tp2_hit),
             int(tp3_hit), int(sl_hit), max_favorable, max_adverse,
             pnl_percent, exit_reason, candles_to_exit,
             datetime.now(timezone.utc).isoformat(), int(final)),
        )
        await self._conn.commit()

    async def get_performance_summary(
        self, asset: str | None = None, days: int = 30,
    ) -> dict:
        assert self._conn
        where = ["o.final = 1", f"s.created_at > datetime('now', '-{days} days')"]
        params: list = []
        if asset:
            where.append("s.asset = ?")
            params.append(asset)
        where_clause = " AND ".join(where)
        cur = await self._conn.execute(f"""
            SELECT
                COUNT(*) AS total_signals,
                SUM(CASE WHEN o.tp1_hit THEN 1 ELSE 0 END) AS tp1_wins,
                SUM(CASE WHEN o.tp2_hit THEN 1 ELSE 0 END) AS tp2_wins,
                SUM(CASE WHEN o.tp3_hit THEN 1 ELSE 0 END) AS tp3_wins,
                SUM(CASE WHEN o.sl_hit THEN 1 ELSE 0 END) AS sl_losses,
                AVG(o.pnl_percent) AS avg_pnl,
                AVG(o.max_favorable) AS avg_max_favorable,
                AVG(o.max_adverse) AS avg_max_adverse,
                AVG(o.candles_to_exit) AS avg_candles_to_exit
            FROM signals s JOIN outcomes o ON o.signal_id = s.id
            WHERE {where_clause}
        """, params)
        row = await cur.fetchone()
        if not row:
            return {}
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get_session_performance(self, days: int = 30) -> list[dict]:
        assert self._conn
        cur = await self._conn.execute(f"""
            SELECT s.market_session, COUNT(*) AS total,
                SUM(CASE WHEN o.tp1_hit THEN 1 ELSE 0 END) AS tp1_wins,
                SUM(CASE WHEN o.sl_hit THEN 1 ELSE 0 END) AS sl_losses,
                AVG(o.pnl_percent) AS avg_pnl,
                ROUND(100.0 * SUM(CASE WHEN o.tp1_hit THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
            FROM signals s JOIN outcomes o ON o.signal_id = s.id
            WHERE o.final = 1 AND s.created_at > datetime('now', '-{days} days')
              AND s.market_session IS NOT NULL
            GROUP BY s.market_session ORDER BY win_rate DESC
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def get_asset_performance(self, days: int = 30) -> list[dict]:
        assert self._conn
        cur = await self._conn.execute(f"""
            SELECT s.asset, s.timeframe, COUNT(*) AS total,
                SUM(CASE WHEN o.tp1_hit THEN 1 ELSE 0 END) AS tp1_wins,
                SUM(CASE WHEN o.sl_hit THEN 1 ELSE 0 END) AS sl_losses,
                AVG(o.pnl_percent) AS avg_pnl,
                ROUND(100.0 * SUM(CASE WHEN o.tp1_hit THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
            FROM signals s JOIN outcomes o ON o.signal_id = s.id
            WHERE o.final = 1 AND s.created_at > datetime('now', '-{days} days')
            GROUP BY s.asset, s.timeframe ORDER BY total DESC
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def get_recent_signals_for_learning(
        self, asset: str, timeframe: str, limit: int = 10,
    ) -> list[dict]:
        assert self._conn
        cur = await self._conn.execute("""
            SELECT s.direction, s.entry, s.sl, s.tp1, s.tp2, s.tp3,
                   s.market_session, s.created_at,
                   o.tp1_hit, o.tp2_hit, o.tp3_hit, o.sl_hit,
                   o.pnl_percent, o.exit_reason, o.candles_to_exit,
                   o.max_favorable, o.max_adverse
            FROM signals s JOIN outcomes o ON o.signal_id = s.id
            WHERE s.asset = ? AND s.timeframe = ? AND o.final = 1
            ORDER BY s.created_at DESC LIMIT ?
        """, (asset, timeframe, limit))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    # ── Autosignal persistence ─────────────────────────

    async def save_autosignal_sub(
        self, chat_id: int, asset: str, timeframe: str,
    ) -> None:
        assert self._conn
        await self._conn.execute(
            "INSERT INTO autosignal_subs (chat_id, asset, timeframe, active, created_at) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(chat_id, asset, timeframe) DO UPDATE SET active=1",
            (chat_id, asset, timeframe, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def remove_autosignal_sub(
        self, chat_id: int, asset: str, timeframe: str,
    ) -> None:
        assert self._conn
        await self._conn.execute(
            "UPDATE autosignal_subs SET active=0 "
            "WHERE chat_id=? AND asset=? AND timeframe=?",
            (chat_id, asset, timeframe),
        )
        await self._conn.commit()

    async def remove_all_autosignal_subs(self, chat_id: int) -> None:
        assert self._conn
        await self._conn.execute(
            "UPDATE autosignal_subs SET active=0 WHERE chat_id=?", (chat_id,),
        )
        await self._conn.commit()

    async def get_active_autosignal_subs(self) -> list[dict]:
        assert self._conn
        cur = await self._conn.execute(
            "SELECT chat_id, asset, timeframe FROM autosignal_subs WHERE active=1",
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    # ── Per-user rate limiting ─────────────────────────

    async def record_user_call(self, chat_id: int) -> None:
        assert self._conn
        await self._conn.execute(
            "INSERT INTO user_rate_limits (chat_id, ts) VALUES (?, ?)",
            (chat_id, _utcnow()),
        )
        await self._conn.commit()

    async def get_user_calls_last_hour(self, chat_id: int) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "SELECT COUNT(*) FROM user_rate_limits "
            "WHERE chat_id = ? AND ts >= datetime('now', '-1 hour')",
            (chat_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    # ── Lifecycle ──────────────────────────────────────

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
