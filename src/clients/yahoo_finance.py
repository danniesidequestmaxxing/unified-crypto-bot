"""Stock market data client using Yahoo Finance chart API.

Provides OHLCV candlestick data for US stocks — no API key required.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
import pandas as pd
import structlog

log = structlog.get_logger()

_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
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

        url = f"{_BASE_URL}/{symbol}"
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
        url = f"{_BASE_URL}/{symbol}"
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
