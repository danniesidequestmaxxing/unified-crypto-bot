"""Claude-powered trading analysis and PineScript generation engine.

Ported from telegram-pinescript-bot, refactored to use shared ClaudeService.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter

import structlog

from src.ai.prompts import TRADING_SYSTEM_PROMPT
from src.clients.binance import BinanceClient
from src.clients.claude import ClaudeService
from src.clients.coingecko import CoinGeckoClient
from src.clients.coinglass import CoinGlassHobbyistClient
from src.core.database import Database

log = structlog.get_logger()

# Bare ticker → CoinGecko ID mapping (static fallback, overridden by CoinRegistry at runtime)
COIN_TO_GECKO_ID: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "DOT": "polkadot", "MATIC": "matic-network", "LINK": "chainlink",
    "UNI": "uniswap", "ATOM": "cosmos", "LTC": "litecoin", "ARB": "arbitrum",
    "OP": "optimism", "APT": "aptos", "SUI": "sui", "NEAR": "near",
    "FTM": "fantom", "INJ": "injective-protocol", "TIA": "celestia",
    "SEI": "sei-network", "JUP": "jupiter-exchange-solana", "WIF": "dogwifcoin",
    "PEPE": "pepe", "BONK": "bonk", "FIL": "filecoin", "RENDER": "render-token",
    "TAO": "bittensor", "WLD": "worldcoin-wld", "STRK": "starknet",
    "AAVE": "aave", "MKR": "maker", "PENDLE": "pendle", "ASTER": "aster-2",
}

KNOWN_COINS: set[str] = set(COIN_TO_GECKO_ID.keys())

# Common English words that could be mistaken for tickers
_TICKER_STOPWORDS = {
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
    "WAS", "ONE", "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW",
    "ITS", "LET", "MAY", "NEW", "NOW", "OLD", "SEE", "WAY", "WHO", "DID",
    "GOT", "HIT", "RUN", "SET", "TOP", "USE", "WIN", "BIG", "LOW", "HIGH",
    "LOOK", "GIVE", "WHAT", "WHEN", "WITH", "THIS", "THAT", "FROM", "HAVE",
    "WILL", "YOUR", "BEEN", "CALL", "EACH", "MAKE", "LIKE", "LONG", "OVER",
    "SUCH", "TAKE", "THAN", "THEM", "VERY", "COME", "JUST", "KNOW", "TIME",
    "SOME", "GOOD", "INTO", "YEAR", "MOST", "ALSO", "BACK", "WANT", "ONLY",
    "FIRST", "PRICE", "CURRENT", "TRADE", "RECOMMEND", "STRATEGY", "TRADING",
    "ANALYSIS", "MARKET", "TODAY", "CHECK", "CHART", "STOCK", "CIRCLE",
    "HAPPENING", "ABOUT", "THINK", "TELL", "SHOW", "HELP", "DOES", "MUCH",
}

# Map user-friendly timeframe strings to Binance interval codes
TIMEFRAME_MAP = {
    "1M": "1m", "3M": "3m", "5M": "5m",
    "15M": "15m", "30M": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h",
    "6H": "6h", "8H": "8h", "12H": "12h",
    "1D": "1d", "3D": "3d", "1W": "1w",
}


class TradingEngine:
    """AI-powered trading analysis engine with self-learning."""

    def __init__(
        self,
        claude: ClaudeService,
        binance: BinanceClient,
        db: Database,
        coinglass: CoinGlassHobbyistClient | None = None,
        coingecko: CoinGeckoClient | None = None,
    ) -> None:
        self.claude = claude
        self.binance = binance
        self.db = db
        self.coinglass = coinglass
        self.coingecko = coingecko

    @staticmethod
    def update_coin_registry(symbol_to_id: dict[str, str], known: set[str]) -> None:
        """Update module-level coin mappings from CoinRegistry."""
        global COIN_TO_GECKO_ID, KNOWN_COINS
        COIN_TO_GECKO_ID = symbol_to_id
        KNOWN_COINS = known

    async def _fetch_market_data(self, symbol: str, timeframe: str = "4h") -> str:
        """Fetch live price, 24h stats, recent klines, and derived volatility metrics."""
        try:
            ticker = await self.binance.get_ticker_24hr(symbol)
            interval = TIMEFRAME_MAP.get(timeframe.upper(), timeframe.lower())
            klines = await self.binance.get_klines(symbol, interval, limit=20)

            price = ticker.get("lastPrice", "N/A")
            high_24h = ticker.get("highPrice", "N/A")
            low_24h = ticker.get("lowPrice", "N/A")
            change_pct = ticker.get("priceChangePercent", "N/A")
            volume = ticker.get("volume", "N/A")

            candle_lines = []
            for k in klines[-10:]:
                o, h, l, c, v = k[1], k[2], k[3], k[4], k[5]
                candle_lines.append(f"  O:{o} H:{h} L:{l} C:{c} Vol:{v}")

            volatility_block = ""
            if len(klines) >= 2:
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                volumes = [float(k[5]) for k in klines]

                true_ranges = []
                for i in range(1, len(klines)):
                    h, l, prev_c = highs[i], lows[i], closes[i - 1]
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                    true_ranges.append(tr)
                atr_14 = sum(true_ranges[-14:]) / min(14, len(true_ranges)) if true_ranges else 0
                atr_pct = (atr_14 / closes[-1] * 100) if closes[-1] else 0

                recent_high = max(highs[-10:])
                recent_low = min(lows[-10:])
                range_pct = ((recent_high - recent_low) / closes[-1] * 100) if closes[-1] else 0

                body_ratios = []
                for k in klines[-10:]:
                    o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                    wick = h - l
                    body_ratios.append(abs(c - o) / wick if wick > 0 else 0)
                avg_body_ratio = sum(body_ratios) / len(body_ratios) if body_ratios else 0

                vol_recent = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else sum(volumes) / len(volumes)
                vol_prior = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_recent
                vol_change = ((vol_recent - vol_prior) / vol_prior * 100) if vol_prior > 0 else 0

                streak = 0
                if len(closes) >= 2:
                    direction = 1 if closes[-1] >= closes[-2] else -1
                    for i in range(len(closes) - 1, 0, -1):
                        if (closes[i] >= closes[i - 1]) == (direction == 1):
                            streak += 1
                        else:
                            break
                    streak *= direction

                volatility_block = (
                    f"\n  PRE-COMPUTED METRICS (use these — do NOT re-derive from candles):\n"
                    f"  ATR ({timeframe.upper()}, 14-period): {atr_14:.2f} ({atr_pct:.3f}% of price)\n"
                    f"  Recent 10-candle range: {recent_low:.2f} – {recent_high:.2f} ({range_pct:.3f}%)\n"
                    f"  Avg candle body ratio: {avg_body_ratio:.2f}\n"
                    f"  Volume trend: {vol_change:+.1f}% (recent 5 candles vs prior 5)\n"
                    f"  Candle streak: {streak:+d} ({'bullish' if streak > 0 else 'bearish' if streak < 0 else 'neutral'})\n"
                )

            binance_block = (
                f"\n--- LIVE MARKET DATA (Binance) ---\n"
                f"Symbol: {symbol}\nCurrent Price: {price}\n"
                f"24h High: {high_24h}\n24h Low: {low_24h}\n"
                f"24h Change: {change_pct}%\n24h Volume: {volume}\n"
                f"\nRecent {timeframe.upper()} Candles (last 10):\n"
                + "\n".join(candle_lines)
                + volatility_block
                + "\n--- END MARKET DATA ---\n"
            )

            # Enrich with CoinGlass & CoinGecko data (non-blocking)
            extra_block = await self._fetch_extra_market_data(symbol)
            return binance_block + extra_block

        except Exception as e:
            log.warning("market_data_fetch_failed", symbol=symbol, error=str(e))
            return f"\n(Could not fetch live data for {symbol})\n"

    async def _fetch_extra_market_data(self, symbol: str) -> str:
        """Fetch supplementary data from CoinGlass and CoinGecko (best-effort)."""
        # Strip USDT suffix to get bare coin
        bare = symbol.replace("USDT", "").replace("USD", "").replace("BUSD", "")
        parts: list[str] = []

        # Build tasks for parallel fetch
        tasks: dict[str, asyncio.Task] = {}
        if self.coinglass:
            tasks["funding"] = asyncio.ensure_future(
                self.coinglass.get_funding_rate(bare)
            )
            tasks["oi"] = asyncio.ensure_future(
                self.coinglass.get_aggregated_oi_history(bare, interval="1h", limit=4)
            )
        gecko_id = COIN_TO_GECKO_ID.get(bare)
        if self.coingecko and gecko_id:
            tasks["gecko"] = asyncio.ensure_future(
                self.coingecko.get_price(gecko_id)
            )

        if not tasks:
            return ""

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data = dict(zip(tasks.keys(), results))

        # CoinGecko price / market cap
        gecko = data.get("gecko")
        if gecko and not isinstance(gecko, Exception) and gecko_id:
            coin_data = gecko.get(gecko_id, {})
            if coin_data:
                mcap = coin_data.get("usd_market_cap")
                vol = coin_data.get("usd_24h_vol")
                change = coin_data.get("usd_24h_change")
                mcap_str = f"${mcap / 1e9:.2f}B" if mcap else "N/A"
                vol_str = f"${vol / 1e9:.2f}B" if vol else "N/A"
                change_str = f"{change:+.2f}%" if change else "N/A"
                parts.append(
                    f"\n--- COINGECKO DATA ---\n"
                    f"Market Cap: {mcap_str}\n"
                    f"24h Volume (USD): {vol_str}\n"
                    f"24h Change: {change_str}\n"
                )

        # CoinGlass funding rate
        funding = data.get("funding")
        if funding and not isinstance(funding, Exception):
            fr_data = funding.get("data") if isinstance(funding, dict) else None
            if fr_data and isinstance(fr_data, list) and len(fr_data) > 0:
                rates = []
                for ex in fr_data:
                    rate = ex.get("rate")
                    name = ex.get("exchangeName", "")
                    if rate is not None:
                        rates.append(f"  {name}: {float(rate) * 100:.4f}%")
                if rates:
                    parts.append(
                        f"\n--- FUNDING RATES (CoinGlass) ---\n"
                        + "\n".join(rates[:5])
                        + "\n"
                    )

        # CoinGlass OI history
        oi = data.get("oi")
        if oi and not isinstance(oi, Exception):
            oi_data = oi.get("data") if isinstance(oi, dict) else None
            if oi_data and isinstance(oi_data, list) and len(oi_data) >= 2:
                try:
                    latest_oi = float(oi_data[-1].get("openInterest", 0))
                    prev_oi = float(oi_data[-2].get("openInterest", 0))
                    if prev_oi > 0:
                        oi_change = ((latest_oi - prev_oi) / prev_oi) * 100
                        parts.append(
                            f"\n--- OPEN INTEREST (CoinGlass) ---\n"
                            f"Current OI: ${latest_oi / 1e9:.2f}B\n"
                            f"1h OI Change: {oi_change:+.2f}%\n"
                        )
                except (ValueError, TypeError):
                    pass

        return "".join(parts)

    async def _build_learning_context(self, asset: str, timeframe: str) -> str:
        """Build a diagnosed context block from historical signal performance."""
        parts = []
        recent = await self.db.get_recent_signals_for_learning(asset, timeframe, limit=10)
        if not recent:
            return ""

        wins = sum(1 for r in recent if r["tp1_hit"])
        losses = sum(1 for r in recent if r["sl_hit"] and not r["tp1_hit"])
        timeouts = sum(1 for r in recent if r["exit_reason"] == "timeout")
        total = len(recent)
        win_rate = (wins / total * 100) if total > 0 else 0

        parts.append(f"\n--- SELF-LEARNING CONTEXT ({asset} {timeframe}) ---")
        parts.append(f"Track record (last {total}): {wins}W / {losses}L / {timeouts}T ({win_rate:.0f}% WR)")

        for i, r in enumerate(recent[:5], 1):
            result_str = r["exit_reason"] or "open"
            pnl = r["pnl_percent"] or 0
            session = r["market_session"] or "unknown"
            parts.append(
                f"  #{i}: {r['direction']} @ {r['entry']} → {result_str} ({pnl:+.2f}%) | "
                f"Fav: {r['max_favorable']:.2f}% Adv: {r['max_adverse']:.2f}% | "
                f"{r['candles_to_exit']} candles | {session}"
            )

        directives = []

        sl_signals = [r for r in recent if r["sl_hit"] and not r["tp1_hit"]]
        if sl_signals:
            avg_adverse_on_sl = sum(r["max_adverse"] for r in sl_signals) / len(sl_signals)
            avg_favorable_on_sl = sum(r["max_favorable"] for r in sl_signals) / len(sl_signals)
            if avg_favorable_on_sl > avg_adverse_on_sl * 0.5:
                directives.append(
                    f"STOP PLACEMENT ISSUE: On SL losses, price moved {avg_favorable_on_sl:.2f}% "
                    f"in your favor before reversing. Use 1.5–2.0x ATR instead."
                )
            if len(sl_signals) >= 3 and all(
                (r["candles_to_exit"] or 99) <= 3 for r in sl_signals[-3:]
            ):
                directives.append(
                    "RAPID STOP-OUTS: Last 3 losses hit within 3 candles. "
                    "Wait for pullbacks to structure before entering."
                )

        if total >= 5:
            recent_5_pnl = [r["pnl_percent"] or 0 for r in recent[:5]]
            alternating = sum(
                1 for i in range(len(recent_5_pnl) - 1)
                if (recent_5_pnl[i] >= 0) != (recent_5_pnl[i + 1] >= 0)
            )
            if alternating >= 3 and timeouts >= 2:
                directives.append(
                    "CHOPPY MARKET DETECTED: Reduce signal frequency. "
                    "Only trade at range extremes with mean-reversion setups."
                )
            elif wins >= 4:
                directives.append(
                    "TRENDING MARKET: Maintain approach. Consider TP2/TP3 more aggressively."
                )
            elif losses >= 4:
                losing_dirs = [r["direction"] for r in recent[:5] if r["sl_hit"] and not r["tp1_hit"]]
                if losing_dirs and all(d == losing_dirs[0] for d in losing_dirs):
                    directives.append(
                        f"DIRECTIONAL BIAS ERROR: Recent losses are all {losing_dirs[0]}. "
                        f"Consider the opposite direction, or output NO_TRADE."
                    )
                else:
                    directives.append(
                        "LOSING STREAK: 4+ recent losses. Raise confidence threshold."
                    )

        tp1_hits = sum(1 for r in recent if r["tp1_hit"])
        tp3_hits = sum(1 for r in recent if r["tp3_hit"])
        if tp1_hits > 0 and tp3_hits / max(tp1_hits, 1) < 0.25:
            avg_fav_on_wins = sum(r["max_favorable"] for r in recent if r["tp1_hit"]) / max(tp1_hits, 1)
            directives.append(
                f"TP CALIBRATION: TP1 hits {tp1_hits}x but TP3 only {tp3_hits}x. "
                f"Avg favorable on wins: {avg_fav_on_wins:.2f}%. Set TP2 closer."
            )

        if wins > 0 and losses > 0:
            win_sessions = [r["market_session"] for r in recent if r["tp1_hit"] and r["market_session"]]
            loss_sessions = [r["market_session"] for r in recent if r["sl_hit"] and not r["tp1_hit"] and r["market_session"]]
            loss_counts = Counter(loss_sessions)
            win_counts = Counter(win_sessions)
            for sess, lcount in loss_counts.items():
                wcount = win_counts.get(sess, 0)
                if lcount >= 3 and wcount <= 1:
                    directives.append(
                        f"SESSION AVOID: {sess} has {lcount} losses vs {wcount} wins."
                    )

        if directives:
            parts.append("\n  DIAGNOSED PATTERNS & DIRECTIVES:")
            for d in directives:
                parts.append(f"  >> {d}")

        parts.append("--- END SELF-LEARNING ---\n")

        perf = await self.db.get_performance_summary(asset=asset, days=30)
        if perf and perf.get("total_signals") and perf["total_signals"] > 0:
            parts.append(
                f"30-DAY STATS ({asset}): {perf['total_signals']} signals, "
                f"TP1: {perf['tp1_wins']}/{perf['total_signals']}, "
                f"Avg P&L: {perf['avg_pnl']:+.2f}%"
            )

        return "\n".join(parts)

    async def suggest_trade(
        self, asset: str, timeframe: str = "1H", extra: str = "",
    ) -> tuple[str, dict | None]:
        """Get a trade suggestion. Returns (analysis_text, levels_dict)."""
        from src.chart.market_sessions import get_current_sessions, format_session_context

        interval = TIMEFRAME_MAP.get(timeframe.upper(), timeframe.lower())
        market_data = await self._fetch_market_data(asset, interval)

        session_info = get_current_sessions()
        session_context = format_session_context(session_info)
        learning_context = await self._build_learning_context(asset, timeframe)

        prompt = (
            f"Analyze {asset} on the {timeframe} timeframe.\n\n"
            f"REAL-TIME MARKET DATA:\n{market_data}\n"
            f"CURRENT SESSION:\n{session_context}\n\n"
        )
        if learning_context:
            prompt += (
                f"{learning_context}\n"
                f"CRITICAL: The DIRECTIVES above are derived from your own past performance. "
                f"You MUST follow them.\n\n"
            )

        prompt += (
            f"Follow your Chain of Thought methodology.\n\n"
            f"RESPONSE FORMAT:\n\n"
            f"🧠 **Thesis**\n- Regime / Bias / Session factor\n\n"
            f"📊 **{asset} {timeframe} Analysis**\n1-2 sentences.\n\n"
            f"🎯 **Trade Setup** (or **NO TRADE**)\n"
            f"- Direction, Entry, SL (ATR-based), TP1/TP2/TP3, R:R\n\n"
            f"📈 **Key Levels** / ✅ **Confirmation** / ⚠️ **Invalidation**\n\n"
            f"RULES: NO markdown tables. SL must be ATR-based. R:R >= 1.5:1.\n\n"
            f"At the very end, include a JSON block:\n"
            f"```json\n"
            f'{{"direction": "LONG", "entry": 00000, "sl": 00000, '
            f'"tp1": 00000, "tp2": 00000, "tp3": 00000, "confidence": 7}}\n'
            f"```\n"
        )
        if extra:
            prompt += f"\nAdditional context: {extra}"

        text = await self.claude.complete_fast(
            messages=[{"role": "user", "content": prompt}],
            system=TRADING_SYSTEM_PROMPT,
        )
        levels = _parse_levels(text)
        display_text = _strip_levels_block(text)
        return display_text, levels

    async def generate_pinescript(self, description: str) -> str:
        """Generate PineScript v6 code from a natural-language description."""
        prompt = (
            f"Generate a complete, ready-to-use PineScript v6 script for:\n\n"
            f"{description}\n\n"
            f"Requirements:\n"
            f"- Use PineScript v6 syntax\n"
            f"- Include alertcondition() calls with JSON messages\n"
            f"- Add clear comments\n"
            f"- Make it production-ready\n"
            f"- Return ONLY the PineScript code block"
        )
        return await self.claude.complete_fast(
            messages=[{"role": "user", "content": prompt}],
            system=TRADING_SYSTEM_PROMPT,
        )

    async def draw_indicator(
        self, indicator_type: str, asset: str = "", params: str = "",
    ) -> str:
        """Generate PineScript for a specific indicator type."""
        prompt = (
            f"Generate a PineScript v6 indicator for: {indicator_type}\n"
            f"{'Asset: ' + asset if asset else ''}\n"
            f"{'Parameters: ' + params if params else ''}\n\n"
            f"Include clean styling, alert conditions, and JSON alert messages."
        )
        return await self.claude.complete_fast(
            messages=[{"role": "user", "content": prompt}],
            system=TRADING_SYSTEM_PROMPT,
        )

    async def analyze(self, prompt: str, context: str = "") -> str:
        """Send a trading/strategy prompt to Claude (free-form handler)."""
        symbol = _extract_symbol(prompt)
        market_info = ""
        if symbol:
            market_info = await self._fetch_market_data(symbol)

        enriched_prompt = prompt
        if market_info:
            enriched_prompt = f"{prompt}\n\nReal-time market data:\n{market_info}"

        messages = []
        if context:
            messages.append({"role": "user", "content": context})
            messages.append({"role": "assistant", "content": "Understood. What would you like to analyze?"})
        messages.append({"role": "user", "content": enriched_prompt})

        return await self.claude.complete_fast(
            messages=messages, system=TRADING_SYSTEM_PROMPT,
        )


# ── Helpers ────────────────────────────────────────────

def _extract_symbol(text: str) -> str | None:
    # Explicit pair first (BTCUSDT, BTC/USDT, etc.)
    m = re.search(r"\b([A-Z]{2,10}(?:[/-]?USDT?|BUSD))\b", text.upper())
    if m:
        return m.group(1).replace("/", "").replace("-", "")
    # Bare ticker fallback — prefer known coins, then try any plausible ticker
    best_unknown: str | None = None
    for word in text.upper().split():
        clean = re.sub(r"[^A-Z]", "", word)
        if len(clean) < 2 or len(clean) > 10:
            continue
        if clean in KNOWN_COINS:
            return f"{clean}USDT"
        if clean in _TICKER_STOPWORDS:
            continue
        if len(clean) >= 3 and best_unknown is None:
            best_unknown = clean
    return f"{best_unknown}USDT" if best_unknown else None


def _parse_levels(text: str) -> dict | None:
    m = re.search(r"```json\s*\n(\{[^}]*\"direction\"[^}]*\})\s*\n```", text, re.DOTALL)
    if not m:
        m = re.search(r"TRADE_LEVELS\s*[:=]?\s*(\{[^}]*\"direction\"[^}]*\})", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        direction = data.get("direction", "")
        if direction and direction.upper() == "NO_TRADE":
            return {"direction": "NO_TRADE", "confidence": data.get("confidence", 0)}
        levels = {}
        for key in ("direction", "entry", "sl", "tp1", "tp2", "tp3"):
            val = data.get(key)
            if val is not None:
                levels[key] = float(val) if key != "direction" else val
        conf = data.get("confidence")
        if conf is not None:
            try:
                levels["confidence"] = int(conf)
            except (ValueError, TypeError):
                pass
        return levels if "entry" in levels else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _strip_levels_block(text: str) -> str:
    text = re.sub(r"\n*```json\s*\n\{[^}]*\"direction\"[^}]*\}\s*\n```\n*", "\n", text)
    text = re.sub(r"\n*TRADE_LEVELS\s*[:=]?\s*\{[^}]*\"direction\"[^}]*\}\n*", "\n", text)
    return text.strip()
