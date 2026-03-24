"""Equity analyst engine — DCF/FCF valuation, peer comparables, EPS impact.

Fetches live fundamentals from Yahoo Finance, computes intrinsic value via
simplified DCF and peer multiples, then hands the data to Claude for
news-aware analysis (regulatory impact, earnings revisions, etc.).
"""
from __future__ import annotations

import structlog

from src.clients.yahoo_finance import StockClient

log = structlog.get_logger()


def _fmt(val, prefix="$", suffix="", pct=False, decimals=2) -> str:
    """Format a number for display."""
    if val is None:
        return "N/A"
    if pct:
        return f"{val * 100:.1f}%"
    if abs(val) >= 1e12:
        return f"{prefix}{val / 1e12:.{decimals}f}T{suffix}"
    if abs(val) >= 1e9:
        return f"{prefix}{val / 1e9:.{decimals}f}B{suffix}"
    if abs(val) >= 1e6:
        return f"{prefix}{val / 1e6:.{decimals}f}M{suffix}"
    return f"{prefix}{val:,.{decimals}f}{suffix}"


def _safe_div(a, b, default=None):
    if a is None or b is None or b == 0:
        return default
    return a / b


class EquityAnalyst:
    """Computes DCF, peer multiples, and formats data for Claude analysis."""

    # Peer groups by sector/theme for comparable analysis
    PEER_GROUPS: dict[str, list[str]] = {
        # ── Crypto-adjacent / fintech ──
        "CRCL": ["COIN", "HOOD", "SQ", "PYPL"],
        "COIN": ["CRCL", "HOOD", "MARA", "RIOT"],
        "MSTR": ["COIN", "MARA", "RIOT", "CLSK"],
        "HOOD": ["COIN", "SCHW", "IBKR", "CRCL"],
        "GBTC": ["COIN", "MSTR", "MARA", "RIOT"],
        "GLXY": ["COIN", "MSTR", "MARA", "RIOT"],
        # ── BTC mining ──
        "MARA": ["RIOT", "CLSK", "BITF", "HIVE"],
        "RIOT": ["MARA", "CLSK", "BITF", "HIVE"],
        "CLSK": ["MARA", "RIOT", "BITF", "HIVE"],
        "HIVE": ["MARA", "RIOT", "CLSK", "BITF"],
        "BITF": ["MARA", "RIOT", "CLSK", "HIVE"],
        # ── Mega-cap tech ──
        "AAPL": ["MSFT", "GOOGL", "AMZN", "META"],
        "MSFT": ["AAPL", "GOOGL", "AMZN", "META"],
        "GOOGL": ["META", "MSFT", "AMZN", "AAPL"],
        "META": ["GOOGL", "SNAP", "PINS", "MSFT"],
        "AMZN": ["SHOP", "WMT", "MSFT", "GOOGL"],
        # ── Semiconductors ──
        "NVDA": ["AMD", "INTC", "TSM", "AVGO"],
        "TSM": ["NVDA", "AMD", "INTC", "AVGO"],
        "AMD": ["NVDA", "INTC", "TSM", "AVGO"],
        "INTC": ["AMD", "NVDA", "TSM", "AVGO"],
        "000660.KS": ["TSM", "NVDA", "AMD", "AVGO"],  # SK Hynix
        "005930.KS": ["TSM", "NVDA", "AAPL", "INTC"],  # Samsung
        "ASML": ["TSM", "NVDA", "AMD", "INTC"],
        "ARM": ["NVDA", "AMD", "INTC", "AVGO"],
        # ── Auto / EV ──
        "TSLA": ["RIVN", "LCID", "GM", "F"],
        # ── Streaming / entertainment ──
        "NFLX": ["DIS", "WBD", "PARA", "ROKU"],
        # ── Enterprise SaaS / cloud ──
        "CRM": ["NOW", "WDAY", "ORCL", "SAP"],
        "PLTR": ["CRM", "NOW", "SNOW", "AI"],
        # ── Fintech / payments ──
        "PYPL": ["SQ", "V", "MA", "AFRM"],
        "SQ": ["PYPL", "V", "MA", "AFRM"],
        # ── E-commerce / China tech ──
        "BABA": ["JD", "PDD", "BIDU", "TCEHY"],
        # ── Ride-hailing / travel ──
        "UBER": ["LYFT", "DASH", "ABNB", "BKNG"],
        "ABNB": ["BKNG", "EXPE", "MAR", "UBER"],
        # ── Major ETFs ──
        "SPY": ["QQQ", "DIA", "IWM", "VTI"],
        "QQQ": ["SPY", "VGT", "XLK", "ARKK"],
        "DIA": ["SPY", "IWM", "VTI", "QQQ"],
        "IWM": ["SPY", "MDY", "VTI", "DIA"],
        "VTI": ["SPY", "VOO", "IWM", "QQQ"],
    }

    # Sector → representative peers for stocks not in PEER_GROUPS
    SECTOR_PEERS: dict[str, list[str]] = {
        "Technology": ["AAPL", "MSFT", "GOOGL", "META"],
        "Communication Services": ["GOOGL", "META", "NFLX", "DIS"],
        "Consumer Cyclical": ["AMZN", "TSLA", "HD", "NKE"],
        "Consumer Defensive": ["PG", "KO", "PEP", "WMT"],
        "Financial Services": ["JPM", "BAC", "GS", "V"],
        "Healthcare": ["UNH", "JNJ", "LLY", "PFE"],
        "Industrials": ["CAT", "HON", "UPS", "GE"],
        "Energy": ["XOM", "CVX", "COP", "SLB"],
        "Basic Materials": ["LIN", "APD", "ECL", "NEM"],
        "Real Estate": ["PLD", "AMT", "CCI", "SPG"],
        "Utilities": ["NEE", "DUK", "SO", "AEP"],
    }

    # Default peer group when sector is also unknown
    DEFAULT_PEERS = ["SPY"]

    def __init__(self) -> None:
        pass

    async def full_analysis(self, symbol: str) -> dict:
        """Run full equity analysis: fundamentals + DCF + peer comps.

        Returns a dict with all data formatted for Claude consumption.
        """
        async with StockClient() as client:
            # Fetch target company data
            fundamentals = await client.get_fundamentals(symbol)
            quote = await client.get_quote(symbol)

            # Fetch peer data for comparables
            # 1) Check hardcoded peer groups first
            # 2) Fall back to sector-based peers from the fetched profile
            # 3) Last resort: compare to SPY
            peers = self.PEER_GROUPS.get(symbol.upper())
            if not peers:
                sector = fundamentals.get("profile", {}).get("sector", "")
                peers = self.SECTOR_PEERS.get(sector, self.DEFAULT_PEERS)
                # Remove self from sector peers if present
                peers = [p for p in peers if p != symbol.upper()]
            peer_data = {}
            for peer in peers[:4]:  # limit to 4 peers
                try:
                    pf = await client.get_fundamentals(peer)
                    pq = await client.get_quote(peer)
                    peer_data[peer] = {"fundamentals": pf, "quote": pq}
                except Exception as e:
                    log.warning("peer_fetch_failed", peer=peer, error=str(e))

        # Compute valuations
        dcf = self._compute_dcf(fundamentals, quote)
        peer_comps = self._compute_peer_comps(symbol, fundamentals, quote, peer_data)
        eps_analysis = self._format_eps_analysis(fundamentals)

        return {
            "symbol": symbol,
            "quote": quote,
            "fundamentals": fundamentals,
            "dcf_valuation": dcf,
            "peer_comparables": peer_comps,
            "eps_analysis": eps_analysis,
            "formatted_context": self._format_for_claude(
                symbol, quote, fundamentals, dcf, peer_comps, eps_analysis,
            ),
        }

    def _compute_dcf(self, fund: dict, quote: dict) -> dict:
        """Simplified DCF using free cash flow and growth estimates."""
        fin = fund.get("financials", {})
        ks = fund.get("key_stats", {})
        trend = fund.get("earnings_trend", [])

        fcf = fin.get("free_cash_flow")
        op_cf = fin.get("operating_cash_flow")
        shares = ks.get("shares_outstanding")
        price = quote.get("price")

        if not fcf or not shares:
            return {"error": "Insufficient data for DCF", "fcf": fcf, "shares": shares}

        # Get forward growth rate from analyst estimates
        fwd_growth = None
        for t in trend:
            if t.get("period") in ("+5y", "5y"):
                fwd_growth = t.get("eps_growth")
                break
        if not fwd_growth:
            for t in trend:
                if t.get("eps_growth") is not None:
                    fwd_growth = t["eps_growth"]
                    break
        if not fwd_growth:
            rev_growth = fin.get("revenue_growth")
            fwd_growth = rev_growth if rev_growth else 0.05  # default 5%

        # Cap growth at reasonable bounds
        fwd_growth = max(-0.20, min(fwd_growth, 0.40))

        # Discount rate (WACC proxy: 10% for large cap, 12% for mid, 15% for small)
        mcap = ks.get("market_cap", 0) or 0
        if mcap > 100e9:
            wacc = 0.10
        elif mcap > 10e9:
            wacc = 0.12
        else:
            wacc = 0.15

        # Terminal growth rate
        terminal_growth = 0.03

        # 5-year DCF projection
        projected_fcf = []
        current_fcf = fcf
        for year in range(1, 6):
            # Decay growth rate toward terminal
            yr_growth = fwd_growth * (1 - (year - 1) * 0.1) + terminal_growth * ((year - 1) * 0.1)
            current_fcf = current_fcf * (1 + yr_growth)
            pv = current_fcf / ((1 + wacc) ** year)
            projected_fcf.append({
                "year": year,
                "fcf": current_fcf,
                "pv": pv,
                "growth": yr_growth,
            })

        # Terminal value (Gordon Growth)
        terminal_fcf = projected_fcf[-1]["fcf"] * (1 + terminal_growth)
        terminal_value = terminal_fcf / (wacc - terminal_growth)
        pv_terminal = terminal_value / ((1 + wacc) ** 5)

        sum_pv_fcf = sum(p["pv"] for p in projected_fcf)
        enterprise_value = sum_pv_fcf + pv_terminal

        # Subtract net debt
        total_debt = fund["financials"].get("total_debt", 0) or 0
        total_cash = fund["financials"].get("total_cash", 0) or 0
        net_debt = total_debt - total_cash

        equity_value = enterprise_value - net_debt
        intrinsic_per_share = equity_value / shares

        upside = ((intrinsic_per_share - price) / price) if price else None

        return {
            "fcf_ttm": fcf,
            "growth_rate": fwd_growth,
            "wacc": wacc,
            "terminal_growth": terminal_growth,
            "projected_fcf": projected_fcf,
            "pv_fcf_sum": sum_pv_fcf,
            "terminal_value": terminal_value,
            "pv_terminal": pv_terminal,
            "enterprise_value": enterprise_value,
            "net_debt": net_debt,
            "equity_value": equity_value,
            "intrinsic_per_share": intrinsic_per_share,
            "current_price": price,
            "upside_pct": upside,
            "shares": shares,
        }

    def _compute_peer_comps(
        self, symbol: str, fund: dict, quote: dict, peer_data: dict,
    ) -> dict:
        """Build peer comparable multiples table."""
        ks = fund.get("key_stats", {})
        fin = fund.get("financials", {})

        target = {
            "symbol": symbol,
            "price": quote.get("price"),
            "market_cap": ks.get("market_cap"),
            "trailing_pe": ks.get("trailing_pe"),
            "forward_pe": ks.get("forward_pe"),
            "ev_to_ebitda": ks.get("ev_to_ebitda"),
            "ev_to_revenue": ks.get("ev_to_revenue"),
            "price_to_book": ks.get("price_to_book"),
            "peg_ratio": ks.get("peg_ratio"),
            "revenue_growth": fin.get("revenue_growth"),
            "profit_margins": fin.get("profit_margins"),
            "roe": fin.get("return_on_equity"),
        }

        peers = []
        for psym, pd in peer_data.items():
            pks = pd["fundamentals"].get("key_stats", {})
            pfin = pd["fundamentals"].get("financials", {})
            peers.append({
                "symbol": psym,
                "price": pd["quote"].get("price"),
                "market_cap": pks.get("market_cap"),
                "trailing_pe": pks.get("trailing_pe"),
                "forward_pe": pks.get("forward_pe"),
                "ev_to_ebitda": pks.get("ev_to_ebitda"),
                "ev_to_revenue": pks.get("ev_to_revenue"),
                "price_to_book": pks.get("price_to_book"),
                "peg_ratio": pks.get("peg_ratio"),
                "revenue_growth": pfin.get("revenue_growth"),
                "profit_margins": pfin.get("profit_margins"),
                "roe": pfin.get("return_on_equity"),
            })

        # Compute peer medians
        def _median(vals):
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            vals.sort()
            n = len(vals)
            return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2

        metrics = ["trailing_pe", "forward_pe", "ev_to_ebitda", "ev_to_revenue",
                    "price_to_book", "peg_ratio"]
        medians = {}
        for m in metrics:
            medians[m] = _median([p[m] for p in peers])

        # Implied price from peer median forward P/E
        fwd_eps = ks.get("forward_eps")
        median_fwd_pe = medians.get("forward_pe")
        implied_price = (fwd_eps * median_fwd_pe) if (fwd_eps and median_fwd_pe) else None

        return {
            "target": target,
            "peers": peers,
            "medians": medians,
            "implied_price_fwd_pe": implied_price,
        }

    def _format_eps_analysis(self, fund: dict) -> dict:
        """Format EPS history, beats/misses, and forward estimates."""
        hist = fund.get("earnings_history", [])
        trend = fund.get("earnings_trend", [])
        ks = fund.get("key_stats", {})

        beat_count = sum(1 for h in hist if (h.get("surprise_pct") or 0) > 0)
        total = len(hist)

        return {
            "trailing_eps": ks.get("trailing_eps"),
            "forward_eps": ks.get("forward_eps"),
            "earnings_history": hist,
            "beat_rate": f"{beat_count}/{total}" if total else "N/A",
            "forward_estimates": trend,
        }

    def _format_for_claude(
        self, symbol: str, quote: dict, fund: dict,
        dcf: dict, comps: dict, eps: dict,
    ) -> str:
        """Format all data into a structured context string for Claude."""
        ks = fund.get("key_stats", {})
        fin = fund.get("financials", {})
        profile = fund.get("profile", {})
        analyst = fund.get("analyst", [])

        lines = []
        lines.append(f"═══ EQUITY ANALYSIS DATA: {symbol} ═══\n")

        # Company profile
        lines.append(f"COMPANY: {profile.get('sector', 'N/A')} | {profile.get('industry', 'N/A')}")
        lines.append(f"Summary: {profile.get('summary', 'N/A')}\n")

        # Current quote
        price = quote.get("price")
        prev = quote.get("previousClose", 0)
        chg = ((price - prev) / prev * 100) if (price and prev) else 0
        lines.append(f"PRICE: ${price} ({chg:+.2f}%) | Market: {quote.get('marketState', '')}")
        lines.append(f"52W Range: {_fmt(ks.get('52w_low'))} - {_fmt(ks.get('52w_high'))}")
        lines.append(f"50D MA: {_fmt(ks.get('50d_avg'))} | 200D MA: {_fmt(ks.get('200d_avg'))}\n")

        # Key metrics
        lines.append("── KEY METRICS ──")
        lines.append(f"Market Cap: {_fmt(ks.get('market_cap'))}")
        lines.append(f"EV: {_fmt(ks.get('enterprise_value'))}")
        lines.append(f"P/E (TTM): {_fmt(ks.get('trailing_pe'), prefix='')}")
        lines.append(f"P/E (Fwd): {_fmt(ks.get('forward_pe'), prefix='')}")
        lines.append(f"PEG: {_fmt(ks.get('peg_ratio'), prefix='')}")
        lines.append(f"P/B: {_fmt(ks.get('price_to_book'), prefix='')}")
        lines.append(f"EV/EBITDA: {_fmt(ks.get('ev_to_ebitda'), prefix='')}")
        lines.append(f"EV/Revenue: {_fmt(ks.get('ev_to_revenue'), prefix='')}")
        lines.append(f"Beta: {_fmt(ks.get('beta'), prefix='')}")
        lines.append(f"Short Interest: {_fmt(ks.get('short_pct_float'), prefix='', pct=True)}\n")

        # Financials
        lines.append("── FINANCIALS ──")
        lines.append(f"Revenue (TTM): {_fmt(fin.get('total_revenue'))}")
        lines.append(f"Revenue Growth: {_fmt(fin.get('revenue_growth'), prefix='', pct=True)}")
        lines.append(f"Gross Margin: {_fmt(fin.get('gross_margins'), prefix='', pct=True)}")
        lines.append(f"Operating Margin: {_fmt(fin.get('operating_margins'), prefix='', pct=True)}")
        lines.append(f"Net Margin: {_fmt(fin.get('profit_margins'), prefix='', pct=True)}")
        lines.append(f"EBITDA: {_fmt(fin.get('ebitda'))}")
        lines.append(f"FCF: {_fmt(fin.get('free_cash_flow'))}")
        lines.append(f"Op CF: {_fmt(fin.get('operating_cash_flow'))}")
        lines.append(f"ROE: {_fmt(fin.get('return_on_equity'), prefix='', pct=True)}")
        lines.append(f"ROA: {_fmt(fin.get('return_on_assets'), prefix='', pct=True)}")
        lines.append(f"D/E: {_fmt(fin.get('debt_to_equity'), prefix='')}")
        lines.append(f"Current Ratio: {_fmt(fin.get('current_ratio'), prefix='')}")
        lines.append(f"Total Debt: {_fmt(fin.get('total_debt'))}")
        lines.append(f"Total Cash: {_fmt(fin.get('total_cash'))}\n")

        # EPS
        lines.append("── EPS ANALYSIS ──")
        lines.append(f"EPS (TTM): {_fmt(eps.get('trailing_eps'), prefix='$')}")
        lines.append(f"EPS (Fwd): {_fmt(eps.get('forward_eps'), prefix='$')}")
        lines.append(f"Beat Rate: {eps.get('beat_rate', 'N/A')}")
        if eps.get("earnings_history"):
            lines.append("Recent Quarters:")
            for h in eps["earnings_history"][:4]:
                surprise = h.get("surprise_pct")
                sp = f" ({surprise:+.1%} surprise)" if surprise is not None else ""
                est = _fmt(h.get("eps_estimate"), prefix="$")
                act = _fmt(h.get("eps_actual"), prefix="$")
                lines.append(f"  Q: Est {est} → Act {act}{sp}")
        if eps.get("forward_estimates"):
            lines.append("Forward Estimates:")
            for t in eps["forward_estimates"][:4]:
                period = t.get("period", "")
                avg = _fmt(t.get("eps_avg"), prefix="$")
                gr = t.get("eps_growth")
                grs = f" ({gr:+.1%})" if gr is not None else ""
                rev = _fmt(t.get("revenue_avg"))
                lines.append(f"  {period}: EPS {avg}{grs} | Rev {rev}")
        lines.append("")

        # DCF
        lines.append("── DCF VALUATION (Simplified) ──")
        if dcf.get("error"):
            lines.append(f"  {dcf['error']}")
        else:
            lines.append(f"FCF (TTM): {_fmt(dcf.get('fcf_ttm'))}")
            lines.append(f"Growth Rate: {_fmt(dcf.get('growth_rate'), prefix='', pct=True)}")
            lines.append(f"WACC: {_fmt(dcf.get('wacc'), prefix='', pct=True)}")
            lines.append(f"Terminal Growth: {_fmt(dcf.get('terminal_growth'), prefix='', pct=True)}")
            lines.append(f"PV of FCFs: {_fmt(dcf.get('pv_fcf_sum'))}")
            lines.append(f"PV of Terminal: {_fmt(dcf.get('pv_terminal'))}")
            lines.append(f"Enterprise Value: {_fmt(dcf.get('enterprise_value'))}")
            lines.append(f"Net Debt: {_fmt(dcf.get('net_debt'))}")
            lines.append(f"Equity Value: {_fmt(dcf.get('equity_value'))}")
            lines.append(f"→ INTRINSIC VALUE: {_fmt(dcf.get('intrinsic_per_share'))} per share")
            lines.append(f"→ Current Price: {_fmt(dcf.get('current_price'))}")
            up = dcf.get("upside_pct")
            if up is not None:
                verdict = "UNDERVALUED" if up > 0.05 else "OVERVALUED" if up < -0.05 else "FAIRLY VALUED"
                lines.append(f"→ Upside/Downside: {up:+.1%} ({verdict})")
        lines.append("")

        # Peer comps
        lines.append("── PEER COMPARABLES ──")
        target = comps.get("target", {})
        peers = comps.get("peers", [])
        medians = comps.get("medians", {})

        header = f"{'Ticker':<8} {'Fwd P/E':<10} {'EV/EBITDA':<10} {'P/B':<8} {'PEG':<8} {'Rev Gr':<8} {'Margin':<8}"
        lines.append(header)
        lines.append("-" * len(header))

        def _row(d):
            return (
                f"{d.get('symbol', '?'):<8} "
                f"{_fmt(d.get('forward_pe'), prefix=''):<10} "
                f"{_fmt(d.get('ev_to_ebitda'), prefix=''):<10} "
                f"{_fmt(d.get('price_to_book'), prefix=''):<8} "
                f"{_fmt(d.get('peg_ratio'), prefix=''):<8} "
                f"{_fmt(d.get('revenue_growth'), prefix='', pct=True):<8} "
                f"{_fmt(d.get('profit_margins'), prefix='', pct=True):<8}"
            )

        lines.append(_row(target) + " ← TARGET")
        for p in peers:
            lines.append(_row(p))

        lines.append("-" * len(header))
        lines.append(
            f"{'MEDIAN':<8} "
            f"{_fmt(medians.get('forward_pe'), prefix=''):<10} "
            f"{_fmt(medians.get('ev_to_ebitda'), prefix=''):<10} "
            f"{_fmt(medians.get('price_to_book'), prefix=''):<8} "
            f"{_fmt(medians.get('peg_ratio'), prefix=''):<8}"
        )

        implied = comps.get("implied_price_fwd_pe")
        if implied:
            lines.append(f"\n→ Implied Price (Peer Fwd P/E × Fwd EPS): {_fmt(implied)}")
            if price:
                imp_upside = (implied - price) / price
                lines.append(f"→ vs Current: {imp_upside:+.1%}")
        lines.append("")

        # Analyst consensus
        if analyst:
            lines.append("── ANALYST CONSENSUS ──")
            a = analyst[0]
            total = sum(a.get(k, 0) for k in ["strong_buy", "buy", "hold", "sell", "strong_sell"])
            lines.append(
                f"Strong Buy: {a.get('strong_buy', 0)} | Buy: {a.get('buy', 0)} | "
                f"Hold: {a.get('hold', 0)} | Sell: {a.get('sell', 0)} | "
                f"Strong Sell: {a.get('strong_sell', 0)} (Total: {total})"
            )
            lines.append(f"Recommendation: {fin.get('recommendation', 'N/A').upper()}")
            lines.append(f"Price Target: {_fmt(fin.get('target_mean'))} "
                         f"(Low: {_fmt(fin.get('target_low'))} / High: {_fmt(fin.get('target_high'))})")
            lines.append("")

        # Income statement history
        inc = fund.get("income_statements", [])
        if inc:
            lines.append("── INCOME STATEMENT (Annual) ──")
            for stmt in inc[:3]:
                date = stmt.get("endDate", "?")
                if isinstance(date, dict):
                    date = date.get("fmt", "?")
                rev = _fmt(stmt.get("totalRevenue"))
                ni = _fmt(stmt.get("netIncome"))
                gp = _fmt(stmt.get("grossProfit"))
                oi = _fmt(stmt.get("operatingIncome"))
                lines.append(f"  {date}: Rev {rev} | GP {gp} | OpInc {oi} | NI {ni}")
            lines.append("")

        # Cash flow history
        cfs = fund.get("cash_flows", [])
        if cfs:
            lines.append("── CASH FLOW (Annual) ──")
            for cf in cfs[:3]:
                date = cf.get("endDate", "?")
                if isinstance(date, dict):
                    date = date.get("fmt", "?")
                opcf = _fmt(cf.get("totalCashFromOperatingActivities"))
                capex = _fmt(cf.get("capitalExpenditures"))
                fcf_val = None
                op_val = cf.get("totalCashFromOperatingActivities")
                cap_val = cf.get("capitalExpenditures")
                if op_val is not None and cap_val is not None:
                    fcf_val = op_val + cap_val  # capex is negative
                lines.append(f"  {date}: OpCF {opcf} | CapEx {capex} | FCF {_fmt(fcf_val)}")
            lines.append("")

        return "\n".join(lines)
