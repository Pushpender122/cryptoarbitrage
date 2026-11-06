"""Runtime settings, loaded once from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"

load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _tri(name: str, default: bool | None) -> bool | None:
    raw = _str(name).lower()
    if raw in {"auto", ""}:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Settings:
    database_url: str = _str("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'terminal.db'}")

    trading_mode: str = _str("TRADING_MODE", "paper").lower()
    poll_ms: int = int(_float("MARKET_POLL_MS", 1500))
    live_sync_ms: int = int(_float("LIVE_SYNC_MS", 3000))
    live_positions_sync_ms: int = int(_float("LIVE_POSITIONS_SYNC_MS", 5000))

    delta_base_url: str = _str("DELTA_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
    delta_api_key: str = _str("DELTA_API_KEY")
    delta_api_secret: str = _str("DELTA_API_SECRET")

    coinswitch_base_url: str = _str("COINSWITCH_BASE_URL", "https://dma.coinswitch.co").rstrip("/")
    coinswitch_api_key: str = _str("COINSWITCH_API_KEY")
    coinswitch_api_secret: str = _str("COINSWITCH_API_SECRET")
    coinswitch_options_allowlisted: bool = _bool("COINSWITCH_OPTIONS_ALLOWLISTED", False)
    coinswitch_simulated: bool | None = _tri("COINSWITCH_SIMULATED", None)

    btc_spot_fallback: float = _float("DEMO_BTC_SPOT", 74500.0)
    eth_spot_fallback: float = _float("DEMO_ETH_SPOT", 2080.0)
    coinswitch_taker_fee: float = _float("COINSWITCH_TAKER_FEE", 0.0005)

    underlyings: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip().upper() for s in _str("UNDERLYINGS", "BTC,ETH").split(",") if s.strip()
        )
    )
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    # Single-operator web authentication.
    admin_password: str = _str("ADMIN_PASSWORD")
    session_secret: str = _str("SESSION_SECRET")
    session_ttl_seconds: int = max(300, int(_float("SESSION_TTL_SECONDS", 43200)))
    session_cookie_secure: bool = _bool("SESSION_COOKIE_SECURE", False)

    @property
    def delta_live_ready(self) -> bool:
        return bool(self.delta_api_key and self.delta_api_secret)

    @property
    def coinswitch_live_ready(self) -> bool:
        return bool(
            self.coinswitch_api_key
            and self.coinswitch_api_secret
            and self.coinswitch_options_allowlisted
        )

    @property
    def paper(self) -> bool:
        return self.trading_mode != "live"


settings = Settings()
