"""Unified configuration — single source of truth from .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Telegram ───────────────────────────────────────
    telegram_bot_token: str
    telegram_chat_id: str  # Default push-alert target

    # ── AI (dual model) ────────────────────────────────
    anthropic_api_key: str
    claude_model_deep: str = "claude-opus-4-6"
    claude_model_fast: str = "claude-sonnet-4-20250514"

    # ── Elfa AI ────────────────────────────────────────
    elfa_api_key: str

    # ── CoinGlass ──────────────────────────────────────
    coinglass_api_key: str

    # ── CoinGecko ─────────────────────────────────────
    coingecko_api_key: str = ""

    # ── TradingView Webhooks ───────────────────────────
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8443
    webhook_secret: str = "change_me"
    webhook_url: str = ""

    # ── Binance ────────────────────────────────────────
    binance_base_url: str = "https://api.binance.com"

    # ── Liquidation-bot tuning ─────────────────────────
    heatmap_poll_seconds: int = 180
    ghost_poll_seconds: int = 900
    heatmap_delta_pct: float = 0.01
    heatmap_min_liq_usd: float = 50_000_000
    ghost_oi_change_threshold: float = 0.10
    social_ma_window_days: int = 7

    # ── Rate limits (requests per minute) ──────────────
    coinglass_hobbyist_rpm: int = 120
    coinglass_prime_rpm: int = 300
    elfa_rpm: int = 100
    coingecko_rpm: int = 30

    # ── Database ───────────────────────────────────────
    db_path: str = "./data/unified.db"

    # ── Stablecoins to exclude from ghost screening ────
    excluded_symbols: list[str] = [
        "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP",
        "FDUSD", "PYUSD", "USDD", "GUSD",
    ]

    # ── Per-user rate limiting ─────────────────────────
    claude_calls_per_user_per_hour: int = 999_999  # effectively unlimited
