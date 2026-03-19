"""Position Monitor — background module for tracking leveraged positions.

Plugs into the unified-crypto-bot architecture alongside HeatmapSniper and GhostScreener.
Uses the shared ClaudeService for AI-powered next-step recommendations with self-learning.

Lifecycle:
  1. On startup: loads plans from position_config, syncs to DB, fetches live HL state
  2. Every 30s: checks prices against key levels, sends proximity alerts
  3. Every 60min: full update — charts, PnL, AI analysis with self-learning context
  4. On TP/SL fill: records event, updates realized PnL, generates new directives
"""
from __future__ import annotations

import asyncio
import os
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import structlog

from src.ai.prompts import TRADING_SYSTEM_PROMPT
from src.clients.binance import BinanceClient
from src.clients.claude import ClaudeService
from src.clients.hyperliquid import HyperliquidClient
from src.chart.generator import fetch_klines, generate_chart
from src.core.database_positions import PositionDatabase
from src.delivery.alerts import TelegramDelivery
from src.modules.position_config import (
    INITIAL_PLANS, PositionPlan, Level,
    HOURLY_INTERVAL, PRICE_CHECK_INTERVAL, ALERT_COOLDOWN,
    PROXIMITY_PCT, PNL_TARGET, HL_WALLET_ADDRESS_ENV,
)

log = structlog.get_logger()

# ── Hyperliquid direct API (sync, for position reads) ──────────
import requests

HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def _hl_post(payload: dict) -> dict:
    resp = requests.post(HL_INFO_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def hl_get_all_mids() -> Dict[str, float]:
    data = _hl_post({"type": "allMids"})
    return {k: float(v) for k, v in data.items()}


def hl_get_user_positions(wallet: str) -> List[dict]:
    state = _hl_post({"type": "clearinghouseState", "user": wallet})
    positions = []
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        size = float(pos.get("szi", 0))
        if size == 0:
            continue
        positions.append({
            "coin": pos.get("coin", ""),
            "size": size,
            "entry": float(pos.get("entryPx", 0)),
            "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
            "leverage": pos.get("leverage", {}),
            "liquidation_price": float(pos.get("liquidationPx", 0) or 0),
        })
    return positions


def hl_get_funding(coin: str) -> float | None:
    try:
        data = _hl_post({"type": "metaAndAssetCtxs"})
        universe = data[0].get("universe", [])
        ctxs = data[1]
        for i, asset in enumerate(universe):
            if asset.get("name") == coin and i < len(ctxs):
                return float(ctxs[i].get("funding", 0))
    except Exception:
        pass
    return None


def hl_get_candles(coin: str, interval: str = "1h", hours: int = 168) -> List[dict]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (hours * 3600 * 1000)
    data = _hl_post({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms},
    })
    candles = []
    for c in data:
        candles.append({
            "time": datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc),
            "open": float(c["o"]), "high": float(c["h"]),
            "low": float(c["l"]), "close": float(c["c"]),
            "volume": float(c["v"]),
        })
    return sorted(candles, key=lambda x: x["time"])


def hl_candles_to_df(candles: List[dict]):
    """Convert HL candles to a pandas DataFrame compatible with generate_chart."""
    import pandas as pd
    if not candles:
        return None
    df = pd.DataFrame(candles)
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    }, inplace=True)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def hl_get_user_fills(wallet: str, limit: int = 50) -> List[dict]:
    """Fetch recent fills (trades) for a wallet."""
    try:
        data = _hl_post({"type": "userFills", "user": wallet})
        fills = []
        for f in data[-limit:]:
            fills.append({
                "coin": f.get("coin", ""),
                "side": f.get("side", ""),
                "px": float(f.get("px", 0)),
                "sz": float(f.get("sz", 0)),
                "time": f.get("time", 0),
                "fee": float(f.get("fee", 0)),
                "oid": f.get("oid", ""),
                "closed_pnl": float(f.get("closedPnl", 0)),
                "dir": f.get("dir", ""),
                "hash": f.get("hash", ""),
            })
        return fills
    except Exception:
        return []


def hl_get_user_funding(wallet: str, limit: int = 20) -> List[dict]:
    """Fetch recent funding payments for a wallet."""
    try:
        data = _hl_post({"type": "userFunding", "user": wallet})
        entries = []
        for f in data[-limit:]:
            entries.append({
                "coin": f.get("coin", ""),
                "usdc": float(f.get("usdc", 0)),
                "szi": float(f.get("szi", 0)),
                "funding_rate": float(f.get("fundingRate", 0)),
                "time": f.get("time", 0),
            })
        return entries
    except Exception:
        return []


# ── AI analysis prompt for position monitoring ─────────────────

POSITION_ANALYSIS_PROMPT = """You are an expert quantitative crypto derivatives trader managing leveraged short positions.
You are monitoring a live portfolio targeting $10,000 cumulative profit.

Your job: analyze current market conditions for each position and provide SPECIFIC, ACTIONABLE next steps.

RULES:
- Be brutally honest about trend alignment. If a short is fighting an uptrend, say so.
- Factor in funding rates — positive funding benefits shorts.
- Consider volatility (ATR) when evaluating proximity to levels.
- If the best action is "do nothing", say "do nothing" — don't force trades.
- Reference the self-learning directives if provided — they are derived from actual outcomes.
- Keep response under 1500 chars per position. Telegram formatting: plain text + emojis, no markdown.

OUTPUT FORMAT (for each position):
1. Trend check (1 sentence)
2. Key observation from current data (1-2 sentences)
3. Recommended action (specific: "tighten SL to $X", "take partial at $X", or "hold, do nothing")
4. Risk flag if any (invalidation proximity, funding flip, volume divergence)
"""


class PositionMonitor:
    """Background module — monitors leveraged positions with AI-powered analysis."""

    def __init__(
        self,
        claude: ClaudeService,
        binance: BinanceClient,
        pos_db: PositionDatabase,
        delivery: TelegramDelivery,
    ) -> None:
        self.claude = claude
        self.binance = binance
        self.pos_db = pos_db
        self.delivery = delivery
        self.wallet = os.getenv(HL_WALLET_ADDRESS_ENV, "")
        self._last_alert: Dict[Tuple[str, float], float] = {}
        self._plans: List[PositionPlan] = []
        self._last_fill_time: int = 0       # epoch ms of last seen fill
        self._last_funding_time: int = 0    # epoch ms of last seen funding

    async def initialize(self) -> None:
        """Load plans into DB and memory."""
        await self.pos_db.init_schema()

        existing = await self.pos_db.get_active_plans()
        existing_coins = {p["coin"] for p in existing}

        for plan in INITIAL_PLANS:
            if plan.coin not in existing_coins:
                levels_dicts = [
                    {"price": l.price, "action": l.action, "size": l.size,
                     "label": l.label, "triggered": l.triggered}
                    for l in plan.levels
                ]
                await self.pos_db.upsert_plan(
                    coin=plan.coin, direction=plan.direction,
                    entry_price=plan.entry, size=plan.size,
                    leverage=plan.leverage, margin_mode=plan.margin_mode,
                    trend_bias=plan.trend_bias, invalidation=plan.invalidation,
                    levels=levels_dicts, notes=plan.notes,
                )

        self._plans = INITIAL_PLANS
        log.info("position_monitor_initialized", plans=len(self._plans))

    def _can_alert(self, coin: str, price: float) -> bool:
        key = (coin, price)
        now = time.time()
        if key in self._last_alert and (now - self._last_alert[key]) < ALERT_COOLDOWN:
            return False
        self._last_alert[key] = now
        return True

    # ── Price alerts (runs every 30s) ──────────────────────

    async def _check_alerts(self) -> None:
        try:
            prices = await asyncio.to_thread(hl_get_all_mids)
        except Exception as e:
            log.warning("hl_price_fetch_failed", error=str(e))
            return

        for plan in self._plans:
            price = prices.get(plan.coin, 0)
            if price == 0:
                continue

            # Invalidation — most critical
            inv_dist = abs(price - plan.invalidation) / price * 100
            if inv_dist < PROXIMITY_PCT and self._can_alert(plan.coin, plan.invalidation):
                await self.delivery.send_text(
                    f"🚨🚨🚨 *INVALIDATION WARNING*\n\n"
                    f"*{plan.coin}* at `${price:.2f}` — only `{inv_dist:.1f}%` from "
                    f"`${plan.invalidation:.0f}`!\n\n"
                    f"→ Close entire position if hourly candle closes above this level."
                )

            # Level proximity
            for level in plan.levels:
                if level.triggered:
                    continue
                dist_pct = abs(price - level.price) / price * 100
                if dist_pct < PROXIMITY_PCT and self._can_alert(plan.coin, level.price):
                    emoji = {"TP": "🟢", "ADD": "⚡", "SL": "🔴"}.get(level.action, "📍")
                    await self.delivery.send_text(
                        f"{emoji} *{plan.coin} {level.action} Approaching*\n\n"
                        f"Price `${price:.2f}` is `{dist_pct:.1f}%` from "
                        f"`${level.price:.0f}`\n→ {level.label}"
                    )

    # ── Hourly update (runs every 60min) ───────────────────

    async def _send_hourly_update(self) -> None:
        log.info("position_hourly_update_start")

        try:
            prices = await asyncio.to_thread(hl_get_all_mids)
        except Exception as e:
            log.error("hl_price_fetch_failed", error=str(e))
            return

        # Fetch live positions if wallet is set
        live_positions = []
        if self.wallet:
            try:
                live_positions = await asyncio.to_thread(hl_get_user_positions, self.wallet)
            except Exception:
                pass

        # Build text update
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"📊 *Hourly Update* — {now}\n"]
        total_unrealized = 0.0

        market_data_blocks = {}

        for plan in self._plans:
            coin = plan.coin
            price = prices.get(coin, 0)
            if price == 0:
                continue

            # Match live position or estimate
            live = next((p for p in live_positions if p["coin"] == coin), None)
            pnl = live["unrealized_pnl"] if live else plan.size * (plan.entry - price)
            total_unrealized += pnl

            # Funding
            funding = await asyncio.to_thread(hl_get_funding, coin)
            fr_str = f"{funding * 100:.4f}%" if funding is not None else "N/A"
            fr_emoji = "💰" if funding and funding > 0 else "💸" if funding and funding < 0 else "➖"

            dist_pct = ((plan.entry - price) / plan.entry) * 100
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"

            lines.append(
                f"{pnl_emoji} *{coin}* {plan.leverage}x Short │ {plan.trend_bias.upper()}\n"
                f"   Mark: `${price:,.2f}` │ Entry: `${plan.entry:.2f}`\n"
                f"   PnL: `{'+'if pnl>=0 else ''}${pnl:,.2f}` │ Dist: `{dist_pct:+.1f}%`\n"
                f"   {fr_emoji} Funding: `{fr_str}`\n"
            )

            # Snapshot to DB
            await self.pos_db.insert_snapshot(
                coin=coin, mark_price=price, entry_price=plan.entry,
                size=plan.size, unrealized_pnl=pnl,
                funding_rate=funding,
            )

            # Fetch Binance market data for AI context
            try:
                symbol = f"{coin}USDT"
                ticker = await self.binance.get_ticker_24hr(symbol)
                klines = await self.binance.get_klines(symbol, "1h", limit=20)
                closes = [float(k[4]) for k in klines]
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]

                # ATR
                true_ranges = []
                for i in range(1, len(klines)):
                    h, l, prev_c = highs[i], lows[i], closes[i - 1]
                    true_ranges.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
                atr = sum(true_ranges[-14:]) / min(14, len(true_ranges)) if true_ranges else 0
                atr_pct = (atr / closes[-1] * 100) if closes[-1] else 0

                volumes = [float(k[5]) for k in klines]
                vol_recent = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
                vol_prior = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_recent
                vol_change = ((vol_recent - vol_prior) / vol_prior * 100) if vol_prior > 0 else 0

                market_data_blocks[coin] = (
                    f"{coin} 1H: Price ${price:.2f}, ATR {atr:.2f} ({atr_pct:.2f}%), "
                    f"24h change {ticker.get('priceChangePercent', 'N/A')}%, "
                    f"Volume trend: {vol_change:+.1f}%, "
                    f"Funding: {fr_str}"
                )
            except Exception:
                market_data_blocks[coin] = f"{coin}: ${price:.2f}, Funding: {fr_str}"

        # Portfolio summary
        realized = await self.pos_db.get_total_realized_pnl()
        total = total_unrealized + realized
        progress = (total / PNL_TARGET) * 100

        lines.append(
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Unrealized:* `{'+'if total_unrealized>=0 else ''}${total_unrealized:,.2f}`\n"
            f"💵 *Realized:* `${realized:,.2f}`\n"
            f"🎯 *Progress:* `${total:,.0f} / ${PNL_TARGET:,.0f}` ({progress:.1f}%)\n"
        )

        # ── AI analysis with self-learning ─────────────────
        try:
            ai_context_parts = []
            for plan in self._plans:
                coin = plan.coin
                learning = await self.pos_db.build_position_learning_context(coin)
                market = market_data_blocks.get(coin, "")

                # Active levels
                active_levels = [
                    f"  {l.action} ${l.price:.0f} ({l.label})"
                    for l in plan.levels if not l.triggered
                ]

                ai_context_parts.append(
                    f"\n--- {coin} {plan.leverage}x SHORT ---\n"
                    f"Entry: ${plan.entry:.2f} | Invalidation: ${plan.invalidation:.0f}\n"
                    f"Trend bias: {plan.trend_bias}\n"
                    f"Market: {market}\n"
                    f"Active levels:\n" + "\n".join(active_levels) + "\n"
                    f"Notes: {plan.notes}\n"
                    f"{learning}"
                )

            ai_prompt = (
                f"Current portfolio: 3 short positions targeting ${PNL_TARGET:,.0f} total profit.\n"
                f"Progress: ${total:,.0f} / ${PNL_TARGET:,.0f} ({progress:.1f}%)\n\n"
                + "\n".join(ai_context_parts)
                + "\n\nGive me the recommended next steps for each position."
            )

            ai_response = await self.claude.complete_fast(
                messages=[{"role": "user", "content": ai_prompt}],
                system=POSITION_ANALYSIS_PROMPT,
                max_tokens=2000,
            )

            lines.append(f"\n🤖 *AI Analysis:*\n{ai_response}")

        except Exception as e:
            log.warning("ai_analysis_failed", error=str(e))
            lines.append("\n⚠️ AI analysis unavailable this hour.")

        # Send text update
        full_msg = "\n".join(lines)
        await self.delivery.send_text(full_msg)

        # ── Charts ─────────────────────────────────────────
        for plan in self._plans:
            try:
                symbol = f"{plan.coin}USDT"

                # Try Binance first, fall back to Hyperliquid candles
                df = None
                try:
                    df = await fetch_klines(self.binance, symbol, "1H")
                except Exception:
                    pass

                if df is None or df.empty:
                    # Fallback: use HL candles directly
                    # Try multiple ticker formats (pre-market stocks may differ)
                    hl_candles = []
                    for ticker in [plan.coin, f"@{plan.coin}", f"{plan.coin}-USD"]:
                        try:
                            hl_candles = await asyncio.to_thread(hl_get_candles, ticker, "1h", 72)
                            if hl_candles:
                                break
                        except Exception:
                            continue
                    df = hl_candles_to_df(hl_candles) if hl_candles else None

                if df is None or df.empty:
                    log.warning("no_candle_data", coin=plan.coin)
                    # Send text-only update instead of chart
                    price = prices.get(plan.coin, 0)
                    live = next((p for p in live_positions if p["coin"] == plan.coin), None)
                    pnl = live["unrealized_pnl"] if live else plan.size * (plan.entry - price)
                    await self.delivery.send_text(
                        f"📊 *{plan.coin}* {plan.leverage}x Short │ "
                        f"PnL: `{'+'if pnl>=0 else ''}${pnl:,.0f}`\n"
                        f"_(Chart unavailable — candle data not supported for this asset)_"
                    )
                    continue

                # Build levels dict for chart annotations
                chart_levels: dict = {"direction": "SHORT"}
                chart_levels["entry"] = plan.entry
                chart_levels["sl"] = plan.invalidation

                tps = sorted([l for l in plan.levels if l.action == "TP" and not l.triggered],
                             key=lambda x: x.price, reverse=True)
                if len(tps) >= 1:
                    chart_levels["tp1"] = tps[0].price
                if len(tps) >= 2:
                    chart_levels["tp2"] = tps[1].price
                if len(tps) >= 3:
                    chart_levels["tp3"] = tps[2].price

                img_bytes = await asyncio.to_thread(
                    generate_chart, df, symbol, "1H", chart_levels,
                )

                price = prices.get(plan.coin, 0)
                live = next((p for p in live_positions if p["coin"] == plan.coin), None)
                pnl = live["unrealized_pnl"] if live else plan.size * (plan.entry - price)

                await self.delivery.send_photo(
                    img_bytes,
                    caption=(
                        f"📈 {plan.coin} 1H │ {plan.leverage}x Short │ "
                        f"PnL: {'+'if pnl>=0 else ''}${pnl:,.0f}"
                    ),
                )
            except Exception as e:
                log.warning("chart_send_failed", coin=plan.coin, error=str(e))

        log.info("position_hourly_update_complete")

    # ── Transaction monitoring (runs every 30s) ─────────────

    async def _check_transactions(self) -> None:
        """Poll HL for new fills and funding payments, alert on new ones."""
        if not self.wallet:
            return

        # ── Check fills (trades) ─────────────────────────
        try:
            fills = await asyncio.to_thread(hl_get_user_fills, self.wallet, 20)
            for fill in fills:
                fill_time = fill["time"]
                if fill_time <= self._last_fill_time:
                    continue

                self._last_fill_time = max(self._last_fill_time, fill_time)
                coin = fill["coin"]
                side = fill["side"].upper()
                # Map HL side codes to readable labels
                side_label = {"A": "Buy", "B": "Sell"}.get(side, side)
                px = fill["px"]
                sz = fill["sz"]
                fee = fill["fee"]
                closed_pnl = fill["closed_pnl"]
                direction = fill.get("dir", "")

                # Determine emoji and label
                if "Open" in direction:
                    emoji = "📥"
                    label = f"Opened {side_label}"
                elif "Close" in direction:
                    emoji = "📤"
                    label = f"Closed {side_label}"
                else:
                    emoji = "⚡"
                    label = side_label

                pnl_line = ""
                if closed_pnl != 0:
                    pnl_sign = "+" if closed_pnl > 0 else ""
                    pnl_line = f"\nRealized PnL: `{pnl_sign}${closed_pnl:.2f}`"

                await self.delivery.send_text(
                    f"{emoji} *HL Fill — {coin}*\n\n"
                    f"{label}: {sz} {coin} @ `${px:.4f}`\n"
                    f"Fee: `${fee:.4f}`"
                    f"{pnl_line}"
                )
                log.info("hl_fill_alert", coin=coin, side=side, px=px, sz=sz)

        except Exception as e:
            log.warning("fill_check_failed", error=str(e))

        # ── Check funding payments ───────────────────────
        try:
            fundings = await asyncio.to_thread(hl_get_user_funding, self.wallet, 10)
            for f in fundings:
                f_time = f["time"]
                if f_time <= self._last_funding_time:
                    continue

                self._last_funding_time = max(self._last_funding_time, f_time)
                coin = f["coin"]
                usdc = f["usdc"]
                rate = f["funding_rate"]

                if abs(usdc) < 0.01:
                    continue  # Skip dust

                emoji = "💰" if usdc > 0 else "💸"
                sign = "+" if usdc > 0 else ""
                direction = "received" if usdc > 0 else "paid"

                await self.delivery.send_text(
                    f"{emoji} *Funding {direction} — {coin}*\n\n"
                    f"Amount: `{sign}${usdc:.4f}` USDC\n"
                    f"Rate: `{rate * 100:.4f}%`"
                )
                log.info("hl_funding_alert", coin=coin, usdc=usdc, rate=rate)

        except Exception as e:
            log.warning("funding_check_failed", error=str(e))

    # ── Main loops ─────────────────────────────────────────

    async def run_forever(self) -> None:
        """Main entry — runs alert, hourly, and transaction loops concurrently."""
        await self.initialize()

        # Seed last fill/funding times so we don't alert on historical data
        if self.wallet:
            try:
                fills = await asyncio.to_thread(hl_get_user_fills, self.wallet, 5)
                if fills:
                    self._last_fill_time = max(f["time"] for f in fills)
                fundings = await asyncio.to_thread(hl_get_user_funding, self.wallet, 5)
                if fundings:
                    self._last_funding_time = max(f["time"] for f in fundings)
                log.info("transaction_tracking_seeded",
                         last_fill=self._last_fill_time,
                         last_funding=self._last_funding_time)
            except Exception as e:
                log.warning("transaction_seed_failed", error=str(e))

        # Startup message
        await self.delivery.send_text(
            "🟢 *Position Monitor Online*\n\n"
            f"Tracking {len(self._plans)} short positions:\n"
            + "\n".join(f"• {p.coin} {p.leverage}x" for p in self._plans)
            + f"\n\n⏱ Hourly updates with AI analysis + charts\n"
            f"🔔 Price alerts within {PROXIMITY_PCT}% of key levels\n"
            f"📥 Real-time fill & funding alerts\n"
            f"🎯 Target: ${PNL_TARGET:,.0f}\n\n"
            f"Commands: /positions /posplan /pospnl"
        )

        # First update immediately
        try:
            await self._send_hourly_update()
        except Exception as e:
            log.error("initial_update_failed", error=str(e))

        # Run all three loops
        await asyncio.gather(
            self._alert_loop(),
            self._hourly_loop(),
            self._transaction_loop(),
        )

    async def _alert_loop(self) -> None:
        while True:
            try:
                await self._check_alerts()
            except Exception as e:
                log.error("alert_loop_error", error=str(e))
            await asyncio.sleep(PRICE_CHECK_INTERVAL)

    async def _transaction_loop(self) -> None:
        """Poll for new fills and funding every 30 seconds."""
        while True:
            try:
                await self._check_transactions()
            except Exception as e:
                log.error("transaction_loop_error", error=str(e))
            await asyncio.sleep(PRICE_CHECK_INTERVAL)

    async def _hourly_loop(self) -> None:
        await asyncio.sleep(HOURLY_INTERVAL)  # Skip first (already sent on init)
        while True:
            try:
                await self._send_hourly_update()
            except Exception as e:
                log.error("hourly_loop_error", error=str(e))
                try:
                    await self.delivery.send_text(
                        f"⚠️ Hourly update failed: `{e}`"
                    )
                except Exception:
                    pass
            await asyncio.sleep(HOURLY_INTERVAL)

    # ── Event recording (called from command handlers) ─────

    async def record_tp_fill(
        self, coin: str, price: float, size: float, level_label: str = "",
    ) -> str:
        """Record a TP fill event. Returns confirmation message."""
        plans = await self.pos_db.get_active_plans()
        plan = next((p for p in plans if p["coin"] == coin), None)
        if not plan:
            return f"No active plan found for {coin}"

        entry = plan["entry_price"]
        pnl = size * (entry - price)  # short PnL
        cumulative = await self.pos_db.record_realized_pnl(coin, "TP_FILL", pnl)

        await self.pos_db.record_event(
            plan_id=plan["id"], event_type="TP_FILL",
            price=price, size=size, pnl=pnl, level_label=level_label,
        )

        # Mark level as triggered in plan
        for level in plan["levels"]:
            if level["action"] == "TP" and abs(level["price"] - price) < price * 0.02:
                level["triggered"] = True
        await self.pos_db.update_plan_levels(plan["id"], plan["levels"])

        return (
            f"✅ *{coin} TP Filled*\n\n"
            f"Price: `${price:.2f}` │ Size: {size}\n"
            f"PnL: `+${pnl:.2f}`\n"
            f"Cumulative realized: `${cumulative:,.2f}` / ${PNL_TARGET:,.0f}"
        )

    async def record_add(
        self, coin: str, price: float, size: float, level_label: str = "",
    ) -> str:
        """Record an ADD event."""
        plans = await self.pos_db.get_active_plans()
        plan = next((p for p in plans if p["coin"] == coin), None)
        if not plan:
            return f"No active plan found for {coin}"

        await self.pos_db.record_event(
            plan_id=plan["id"], event_type="ADD",
            price=price, size=size, level_label=level_label,
        )

        return (
            f"⚡ *{coin} Short Added*\n\n"
            f"Price: `${price:.2f}` │ Size: +{size}\n"
            f"→ {level_label}"
        )
