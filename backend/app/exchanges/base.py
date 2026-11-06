"""Exchange adapter interfaces and shared value types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class ProviderError(Exception):
    """Raised when an exchange call fails. `code` is exchange-specific."""

    def __init__(self, message: str, *, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ContractSpec:
    """A tradable option contract as described by one exchange."""

    exchange: str
    symbol: str
    exchange_id: str
    underlying: str
    option_type: str  # "call" | "put"
    strike: float
    expiry: datetime  # aware, UTC
    contract_value: float  # units of underlying per contract
    contract_unit: str
    settlement_currency: str
    price_currency: str
    tick_size: float
    taker_fee_rate: float
    maker_fee_rate: float
    min_qty: float = 1.0
    qty_step: float = 1.0
    expiry_assumed: bool = False

    def match_key(self) -> tuple:
        """Fields that must agree for two contracts to be comparable."""
        return (
            self.underlying,
            self.option_type,
            round(self.strike, 4),
            self.expiry.replace(second=0, microsecond=0),
        )

    def key_string(self) -> str:
        u, o, s, e = self.match_key()
        return f"{u}|{o}|{s:g}|{e:%Y-%m-%dT%H:%M}Z"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "underlying": self.underlying,
            "option_type": self.option_type,
            "strike": self.strike,
            "expiry": iso_utc(self.expiry),
            "contract_value": self.contract_value,
            "contract_unit": self.contract_unit,
            "settlement_currency": self.settlement_currency,
            "price_currency": self.price_currency,
            "tick_size": self.tick_size,
            "taker_fee_rate": self.taker_fee_rate,
            "min_qty": self.min_qty,
            "qty_step": self.qty_step,
            "expiry_assumed": self.expiry_assumed,
        }


@dataclass(slots=True)
class Quote:
    exchange: str
    symbol: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    ltp: float | None
    mark_price: float | None
    mark_iv: float | None
    oi: float | None
    volume: float | None
    ts: datetime
    source: str = "live"  # "live" | "simulated"

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.mark_price or self.ltp

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "ltp": self.ltp,
            "mark_price": self.mark_price,
            "mark_iv": self.mark_iv,
            "oi": self.oi,
            "volume": self.volume,
            "mid": self.mid,
            "ts": iso_utc(self.ts),
            "source": self.source,
        }


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    exchange_id: str
    side: str  # "buy" | "sell"
    qty: float
    order_type: str = "limit"  # "limit" | "market"
    limit_price: float | None = None
    time_in_force: str = "gtc"
    reduce_only: bool = False
    client_order_id: str | None = None


@dataclass(slots=True)
class OrderAck:
    exchange_order_id: str | None
    status: str  # accepted | open | filled | rejected
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    fees: float = 0.0
    error: str | None = None


@dataclass(slots=True)
class VenueOrderStatus:
    """Normalised order state as reported by the venue."""

    exchange_order_id: str
    status: str  # open | partially_filled | filled | cancelled | rejected | unknown
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    fees: float = 0.0
    venue_state: str | None = None
    reject_reason: str | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class VenueFill:
    """One execution reported by the venue. `fill_id` must be stable for de-duplication."""

    exchange: str
    fill_id: str
    exchange_order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    is_maker: bool
    ts: datetime


@dataclass(slots=True)
class VenuePositionSnapshot:
    """A venue-reported position. `size` is signed: positive long, negative short."""

    exchange: str
    symbol: str
    size: float
    entry_price: float
    mark_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin: float | None = None
    liquidation_price: float | None = None
    venue_updated_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class MarketDataProvider(ABC):
    """One exchange. Market data may be public while trading still needs keys."""

    name: str = "unknown"
    label: str = "Unknown"
    live_market_data: bool = False
    supports_trading: bool = False
    note: str = ""

    last_error: str | None = None
    last_ok_at: datetime | None = None
    latency_ms: float | None = None

    @abstractmethod
    async def fetch_contracts(self) -> list[ContractSpec]:
        ...

    @abstractmethod
    async def fetch_quotes(self) -> list[Quote]:
        ...

    async def place_order(self, request: OrderRequest) -> OrderAck:
        raise ProviderError(f"{self.label} order routing is not configured")

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> None:
        raise ProviderError(f"{self.label} order cancellation is not configured")

    async def fetch_order(
        self,
        exchange_order_id: str,
        symbol: str,
        *,
        client_order_id: str | None = None,
    ) -> VenueOrderStatus:
        raise ProviderError(f"{self.label} order status is not available")

    async def fetch_fills(
        self,
        exchange_order_id: str,
        symbol: str,
        *,
        product_id: str | None = None,
    ) -> list[VenueFill]:
        return []

    async def fetch_position_snapshots(self) -> list[VenuePositionSnapshot]:
        return []

    def spots(self) -> dict[str, float]:
        """Latest underlying index price per underlying, when the venue exposes it."""
        return {}

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {
            "exchange": self.name,
            "label": self.label,
            "market_data": "live" if self.live_market_data else "unavailable",
            "trading": "enabled" if self.supports_trading else "keys_required",
            "note": self.note,
            "last_error": self.last_error,
            "last_ok_at": iso_utc(self.last_ok_at) if self.last_ok_at else None,
            "latency_ms": self.latency_ms,
        }
