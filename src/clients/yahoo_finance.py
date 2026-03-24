"""Stock market data client using Yahoo Finance APIs.

Provides OHLCV candlestick data AND fundamental financial data for US stocks.
No API key required — uses Yahoo Finance v8 chart + v10 quoteSummary endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
import pandas as pd
import structlog

log = structlog.get_logger()

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; UnifiedSignalBot/1.0)"}

# Map user-friendly timeframes to Yahoo Finance intervals
STOCK_TIMEFRAME_MAP = {
    "1M": "1m", "5M": "5m", "15M": "15m", "30M": "30m",
    "1H": "1h", "2H": "1h", "4H": "1h",  # Yahoo free: max 1h intraday
    "1D": "1d", "5D": "5d", "1W": "1wk", "1MO": "1mo",
}

# Range to fetch for each interval
_RANGE_MAP = {
    "1m": "1d", "5m": "5d", "15m": "5d", "30m": "5d",
    "1h": "5d", "1d": "3mo", "5d": "1y", "1wk": "1y", "1mo": "5y",
}

# Well-known companies and their stock tickers
COMPANY_TO_STOCK: dict[str, str] = {
    "CIRCLE": "CRCL",
    "COINBASE": "COIN",
    "MICROSTRATEGY": "MSTR",
    "STRATEGY": "MSTR",
    "TESLA": "TSLA",
    "APPLE": "AAPL",
    "NVIDIA": "NVDA",
    "MICROSOFT": "MSFT",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "AMAZON": "AMZN",
    "META": "META",
    "FACEBOOK": "META",
    "ROBINHOOD": "HOOD",
    "MARATHON": "MARA",
    "RIOT": "RIOT",
    "MARA": "MARA",
    "CLEANSPARK": "CLSK",
    "HIVE": "HIVE",
    "BITFARMS": "BITF",
    "GALAXY": "GLXY",
    "GRAYSCALE": "GBTC",
}

# Stock tickers that are NOT crypto tickers (avoids confusion)
KNOWN_STOCKS: set[str] = {
    "CRCL", "COIN", "MSTR", "TSLA", "AAPL", "NVDA", "MSFT", "GOOGL",
    "AMZN", "META", "HOOD", "MARA", "RIOT", "CLSK", "HIVE", "BITF",
    "GLXY", "GBTC", "SPY", "QQQ", "DIA", "IWM", "VTI",
}


class StockClient:
    """Async client for Yahoo Finance chart data."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=_HEADERS)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> StockClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def get_klines(
        self, symbol: str, interval: str = "1h", range_: str | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles for a stock ticker.

        Returns a DataFrame with columns: Open, High, Low, Close, Volume
        indexed by Date (timezone-aware).
        """
        yf_interval = STOCK_TIMEFRAME_MAP.get(interval.upper(), interval.lower())
        yf_range = range_ or _RANGE_MAP.get(yf_interval, "5d")

        url = f"{_CHART_URL}/{symbol}"
        params = {"interval": yf_interval, "range": yf_range}

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Yahoo Finance returned {resp.status} for {symbol}")
            data = await resp.json()

        chart = data.get("chart", {})
        error = chart.get("error")
        if error:
            raise ValueError(f"Yahoo Finance error: {error.get('description', error)}")

        results = chart.get("result", [])
        if not results:
            raise ValueError(f"No data returned for {symbol}")

        result = results[0]
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]

        if not timestamps:
            raise ValueError(f"No candle data for {symbol}")

        df = pd.DataFrame({
            "Date": pd.to_datetime(timestamps, unit="s", utc=True),
            "Open": quote.get("open", []),
            "High": quote.get("high", []),
            "Low": quote.get("low", []),
            "Close": quote.get("close", []),
            "Volume": quote.get("volume", []),
        })
        df.set_index("Date", inplace=True)

        # Drop rows where all OHLC are None (market closed candles)
        df.dropna(subset=["Open", "High", "Low", "Close"], how="all", inplace=True)

        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Fill any remaining NaN volumes with 0
        df["Volume"] = df["Volume"].fillna(0)

        return df

    async def get_quote(self, symbol: str) -> dict:
        """Get current quote data for a stock."""
        url = f"{_CHART_URL}/{symbol}"
        params = {"interval": "1d", "range": "2d"}

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Yahoo Finance returned {resp.status}")
            data = await resp.json()

        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
        return {
            "symbol": meta.get("symbol", symbol),
            "price": meta.get("regularMarketPrice"),
            "previousClose": meta.get("chartPreviousClose"),
            "currency": meta.get("currency", "USD"),
            "exchangeName": meta.get("exchangeName", ""),
            "marketState": meta.get("marketState", ""),
        }

    # ── Fundamentals via quoteSummary v10 ──────────────────────────────

    _FUNDAMENTAL_MODULES = (
        "assetProfile,"
        "defaultKeyStatistics,"
        "financialData,"
        "summaryDetail,"
        "incomeStatementHistory,"
        "incomeStatementHistoryQuarterly,"
        "balanceSheetHistory,"
        "cashflowStatementHistory,"
        "earningsHistory,"
        "earningsTrend,"
        "recommendationTrend"
    )

    async def get_fundamentals(self, symbol: str) -> dict:
        """Fetch comprehensive fundamental data via Yahoo Finance quoteSummary.

        Returns a dict with keys: profile, key_stats, financials,
        income_statements, balance_sheets, cash_flows, earnings, analyst.
        """
        url = f"{_SUMMARY_URL}/{symbol}"
        params = {
            "modules": self._FUNDAMENTAL_MODULES,
            "formatted": "false",
            "lang": "en-US",
            "region": "US",
        }

        session = await self._get_session()
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Yahoo quoteSummary returned {resp.status} for {symbol}")
            data = await resp.json()

        qs = data.get("quoteSummary", {})
        error = qs.get("error")
        if error:
            raise ValueError(f"quoteSummary error: {error}")

        results = qs.get("result", [])
        if not results:
            raise ValueError(f"No fundamentals for {symbol}")

        r = results[0]
        return {
            "profile": self._parse_profile(r.get("assetProfile", {})),
            "key_stats": self._parse_key_stats(
                r.get("defaultKeyStatistics", {}),
                r.get("summaryDetail", {}),
            ),
            "financials": self._parse_financial_data(r.get("financialData", {})),
            "income_statements": self._parse_statements(
                r.get("incomeStatementHistory", {}).get("incomeStatementHistory", [])
            ),
            "income_quarterly": self._parse_statements(
                r.get("incomeStatementHistoryQuarterly", {})
                .get("incomeStatementHistory", [])
            ),
            "balance_sheets": self._parse_statements(
                r.get("balanceSheetHistory", {}).get("balanceSheetStatements", [])
            ),
            "cash_flows": self._parse_statements(
                r.get("cashflowStatementHistory", {}).get("cashflowStatements", [])
            ),
            "earnings_history": self._parse_earnings_history(
                r.get("earningsHistory", {}).get("history", [])
            ),
            "earnings_trend": self._parse_earnings_trend(
                r.get("earningsTrend", {}).get("trend", [])
            ),
            "analyst": self._parse_recommendations(
                r.get("recommendationTrend", {}).get("trend", [])
            ),
        }

    # ── Parsers ────────────────────────────────────────────────────────

    @staticmethod
    def _raw(obj: dict, key: str, default=None):
        """Extract raw value from Yahoo's {raw, fmt} structure or plain value."""
        val = obj.get(key)
        if isinstance(val, dict):
            return val.get("raw", default)
        return val if val is not None else default

    def _parse_profile(self, p: dict) -> dict:
        return {
            "sector": p.get("sector", ""),
            "industry": p.get("industry", ""),
            "employees": p.get("fullTimeEmployees"),
            "summary": (p.get("longBusinessSummary", "") or "")[:500],
            "country": p.get("country", ""),
            "website": p.get("website", ""),
        }

    def _parse_key_stats(self, ks: dict, sd: dict) -> dict:
        return {
            "market_cap": self._raw(sd, "marketCap"),
            "enterprise_value": self._raw(ks, "enterpriseValue"),
            "trailing_pe": self._raw(sd, "trailingPE"),
            "forward_pe": self._raw(ks, "forwardPE"),
            "peg_ratio": self._raw(ks, "pegRatio"),
            "price_to_book": self._raw(ks, "priceToBook"),
            "price_to_sales": self._raw(sd, "priceToSalesTrailing12Months"),
            "ev_to_ebitda": self._raw(ks, "enterpriseToEbitda"),
            "ev_to_revenue": self._raw(ks, "enterpriseToRevenue"),
            "trailing_eps": self._raw(ks, "trailingEps"),
            "forward_eps": self._raw(ks, "forwardEps"),
            "dividend_yield": self._raw(sd, "dividendYield"),
            "beta": self._raw(ks, "beta"),
            "shares_outstanding": self._raw(ks, "sharesOutstanding"),
            "float_shares": self._raw(ks, "floatShares"),
            "short_ratio": self._raw(ks, "shortRatio"),
            "short_pct_float": self._raw(ks, "shortPercentOfFloat"),
            "52w_high": self._raw(sd, "fiftyTwoWeekHigh"),
            "52w_low": self._raw(sd, "fiftyTwoWeekLow"),
            "50d_avg": self._raw(sd, "fiftyDayAverage"),
            "200d_avg": self._raw(sd, "twoHundredDayAverage"),
        }

    def _parse_financial_data(self, fd: dict) -> dict:
        return {
            "current_price": self._raw(fd, "currentPrice"),
            "target_high": self._raw(fd, "targetHighPrice"),
            "target_low": self._raw(fd, "targetLowPrice"),
            "target_mean": self._raw(fd, "targetMeanPrice"),
            "target_median": self._raw(fd, "targetMedianPrice"),
            "recommendation": fd.get("recommendationKey", ""),
            "num_analysts": self._raw(fd, "numberOfAnalystOpinions"),
            "total_revenue": self._raw(fd, "totalRevenue"),
            "revenue_growth": self._raw(fd, "revenueGrowth"),
            "gross_margins": self._raw(fd, "grossMargins"),
            "ebitda_margins": self._raw(fd, "ebitdaMargins"),
            "operating_margins": self._raw(fd, "operatingMargins"),
            "profit_margins": self._raw(fd, "profitMargins"),
            "total_debt": self._raw(fd, "totalDebt"),
            "total_cash": self._raw(fd, "totalCash"),
            "debt_to_equity": self._raw(fd, "debtToEquity"),
            "current_ratio": self._raw(fd, "currentRatio"),
            "return_on_equity": self._raw(fd, "returnOnEquity"),
            "return_on_assets": self._raw(fd, "returnOnAssets"),
            "free_cash_flow": self._raw(fd, "freeCashflow"),
            "operating_cash_flow": self._raw(fd, "operatingCashflow"),
            "ebitda": self._raw(fd, "ebitda"),
        }

    def _parse_statements(self, stmts: list) -> list[dict]:
        """Parse income/balance/cashflow statements into clean dicts."""
        parsed = []
        for s in stmts[:4]:  # last 4 years
            row = {}
            for k, v in s.items():
                if k == "maxAge":
                    continue
                if isinstance(v, dict) and "raw" in v:
                    row[k] = v["raw"]
                elif isinstance(v, (int, float)):
                    row[k] = v
                elif isinstance(v, str):
                    row[k] = v
            parsed.append(row)
        return parsed

    def _parse_earnings_history(self, history: list) -> list[dict]:
        """Parse quarterly EPS beats/misses."""
        parsed = []
        for h in history[:8]:
            parsed.append({
                "quarter": self._raw(h, "quarter", {}),
                "date": self._raw(h, "quarterDate"),
                "eps_estimate": self._raw(h, "epsEstimate"),
                "eps_actual": self._raw(h, "epsActual"),
                "eps_difference": self._raw(h, "epsDifference"),
                "surprise_pct": self._raw(h, "surprisePercent"),
            })
        return parsed

    def _parse_earnings_trend(self, trend: list) -> list[dict]:
        """Parse forward earnings estimates."""
        parsed = []
        for t in trend:
            earnings = t.get("earningsEstimate", {})
            revenue = t.get("revenueEstimate", {})
            parsed.append({
                "period": t.get("period", ""),
                "end_date": t.get("endDate", ""),
                "eps_avg": self._raw(earnings, "avg"),
                "eps_low": self._raw(earnings, "low"),
                "eps_high": self._raw(earnings, "high"),
                "eps_growth": self._raw(earnings, "growth"),
                "num_analysts": self._raw(earnings, "numberOfAnalysts"),
                "revenue_avg": self._raw(revenue, "avg"),
                "revenue_low": self._raw(revenue, "low"),
                "revenue_high": self._raw(revenue, "high"),
                "revenue_growth": self._raw(revenue, "growth"),
            })
        return parsed

    def _parse_recommendations(self, trend: list) -> list[dict]:
        parsed = []
        for t in trend[:3]:
            parsed.append({
                "period": t.get("period", ""),
                "strong_buy": t.get("strongBuy", 0),
                "buy": t.get("buy", 0),
                "hold": t.get("hold", 0),
                "sell": t.get("sell", 0),
                "strong_sell": t.get("strongSell", 0),
            })
        return parsed
