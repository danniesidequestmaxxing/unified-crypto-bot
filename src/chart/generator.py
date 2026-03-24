"""Candlestick chart generator with indicators for Telegram.

Ported from telegram-pinescript-bot chart.py.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd

from src.clients.binance import BinanceClient
from src.clients.yahoo_finance import StockClient

# Map user-friendly timeframe strings to Binance interval codes
TIMEFRAME_MAP = {
    "1M": "1m", "3M": "3m", "5M": "5m",
    "15M": "15m", "30M": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h",
    "6H": "6h", "8H": "8h", "12H": "12h",
    "1D": "1d", "3D": "3d", "1W": "1w",
}

CANDLE_COUNTS = {
    "1m": 120, "3m": 100, "5m": 100,
    "15m": 96, "30m": 80,
    "1h": 72, "2h": 60, "4h": 60,
    "6h": 48, "8h": 48, "12h": 48,
    "1d": 60, "3d": 40, "1w": 40,
}


async def fetch_klines(binance: BinanceClient, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV kline data from Binance."""
    interval = TIMEFRAME_MAP.get(timeframe.upper(), timeframe.lower())
    limit = CANDLE_COUNTS.get(interval, 60)
    raw = await binance.get_klines(symbol, interval, limit=limit)

    df = pd.DataFrame(raw, columns=[
        "timestamp", "Open", "High", "Low", "Close", "Volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["Date"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("Date", inplace=True)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = df[col].astype(float)
    return df[["Open", "High", "Low", "Close", "Volume"]]


async def fetch_stock_klines(stock_client: StockClient, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV kline data from Yahoo Finance for stocks."""
    return await stock_client.get_klines(symbol, interval=timeframe)


def _compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def generate_chart(
    df: pd.DataFrame, symbol: str, timeframe: str,
    levels: dict | None = None,
) -> bytes:
    """Render a candlestick chart with EMA, RSI, volume, and trade levels."""
    ema9 = _compute_ema(df["Close"], 9)
    ema21 = _compute_ema(df["Close"], 21)
    rsi = _compute_rsi(df["Close"], 14)

    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350", edge="inherit", wick="inherit",
        volume={"up": "#26a69a80", "down": "#ef535080"},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds", marketcolors=mc,
        facecolor="#1a1a2e", edgecolor="#1a1a2e", figcolor="#1a1a2e",
        gridcolor="#2a2a4a", gridstyle="--", gridaxis="both",
        y_on_right=True,
        rc={"font.size": 9, "axes.labelcolor": "white",
            "xtick.color": "white", "ytick.color": "white"},
    )

    add_plots = [
        mpf.make_addplot(ema9, color="#f0b90b", width=1.2, label="EMA 9"),
        mpf.make_addplot(ema21, color="#e040fb", width=1.2, label="EMA 21"),
        mpf.make_addplot(rsi, panel=2, color="#42a5f5", width=1.0,
                         ylabel="RSI", ylim=(0, 100)),
        mpf.make_addplot(pd.Series(30, index=df.index), panel=2,
                         color="#ffffff40", width=0.5, linestyle="--"),
        mpf.make_addplot(pd.Series(70, index=df.index), panel=2,
                         color="#ffffff40", width=0.5, linestyle="--"),
    ]

    hlines_dict = {"hlines": [], "colors": [], "linestyle": [], "linewidths": []}
    if levels:
        level_config = [
            ("entry", "#f0b90b", "-", 1.5),
            ("sl", "#ef5350", "--", 1.5),
            ("tp1", "#26a69a", "--", 1.0),
            ("tp2", "#26a69a", "--", 1.0),
            ("tp3", "#26a69a", "--", 1.0),
        ]
        for key, color, ls, lw in level_config:
            val = levels.get(key)
            if val is not None:
                hlines_dict["hlines"].append(val)
                hlines_dict["colors"].append(color)
                hlines_dict["linestyle"].append(ls)
                hlines_dict["linewidths"].append(lw)

    hlines_kwargs = {}
    if hlines_dict["hlines"]:
        hlines_kwargs["hlines"] = hlines_dict

    fig, axes = mpf.plot(
        df, type="candle", style=style, volume=True, volume_panel=1,
        addplot=add_plots, panel_ratios=(5, 1.5, 2), figsize=(12, 7),
        tight_layout=True, returnfig=True, **hlines_kwargs,
    )

    direction = ""
    if levels and levels.get("direction"):
        direction = f"  |  {levels['direction'].upper()}"
    axes[0].set_title(
        f"{symbol}  {timeframe}{direction}",
        color="white", fontsize=13, fontweight="bold", loc="left", pad=10,
    )
    axes[0].legend(["EMA 9", "EMA 21"], loc="upper left", fontsize=8,
                   facecolor="#1a1a2e", edgecolor="#2a2a4a", labelcolor="white")

    if levels:
        price_labels = [
            ("entry", "ENTRY", "#f0b90b"), ("sl", "SL", "#ef5350"),
            ("tp1", "TP1", "#26a69a"), ("tp2", "TP2", "#26a69a"),
            ("tp3", "TP3", "#26a69a"),
        ]
        for key, label, color in price_labels:
            val = levels.get(key)
            if val is not None:
                axes[0].annotate(
                    f" {label}: {val:,.2f}", xy=(1.0, val),
                    xycoords=("axes fraction", "data"),
                    fontsize=8, color=color, fontweight="bold", va="center",
                )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="#1a1a2e", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
