"""ORM models for the trading terminal."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    exchange: Mapped[str] = mapped_column(String(24), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    underlying: Mapped[str] = mapped_column(String(12), index=True)
    option_type: Mapped[str] = mapped_column(String(8))
    strike: Mapped[float] = mapped_column(Float)
    expiry: Mapped[datetime] = mapped_column(DateTime)
    contract_value: Mapped[float] = mapped_column(Float, default=1.0)

    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(12), default="limit")
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(8), default="gtc")
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)

    mode: Mapped[str] = mapped_column(String(8), default="paper")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    parent_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    exchange_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_position_exchange_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(24), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    underlying: Mapped[str] = mapped_column(String(12))
    option_type: Mapped[str] = mapped_column(String(8))
    strike: Mapped[float] = mapped_column(Float)
    expiry: Mapped[datetime] = mapped_column(DateTime)
    contract_value: Mapped[float] = mapped_column(Float, default=1.0)

    qty: Mapped[float] = mapped_column(Float, default=0.0)  # signed contracts
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Fill(Base):
    """Venue execution, stored once per venue fill id for idempotent reconciliation."""

    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("exchange", "fill_id", name="uq_fill_exchange_fill_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(24), index=True)
    fill_id: Mapped[str] = mapped_column(String(128))
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    is_maker: Mapped[bool] = mapped_column(Boolean, default=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VenuePosition(Base):
    """Position snapshot reported by the exchange. Authoritative in live mode."""

    __tablename__ = "venue_positions"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_venue_position_exchange_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(24), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    underlying: Mapped[str | None] = mapped_column(String(12), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    contract_value: Mapped[float] = mapped_column(Float, default=1.0)

    size: Mapped[float] = mapped_column(Float, default=0.0)  # signed
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    venue_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)

    max_order_qty: Mapped[float] = mapped_column(Float, default=50.0)          # contracts
    max_order_notional: Mapped[float] = mapped_column(Float, default=25000.0)  # quote ccy
    max_open_positions: Mapped[int] = mapped_column(Integer, default=20)
    max_gross_notional: Mapped[float] = mapped_column(Float, default=100000.0)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=5000.0)
    max_price_deviation_pct: Mapped[float] = mapped_column(Float, default=15.0)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=35.0)
    target_pct: Mapped[float] = mapped_column(Float, default=75.0)
    auto_risk_exits: Mapped[bool] = mapped_column(Boolean, default=True)
    slippage_bps: Mapped[float] = mapped_column(Float, default=15.0)
    min_edge_bps: Mapped[float] = mapped_column(Float, default=25.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CopyStrategy(Base):
    __tablename__ = "copy_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    source_exchange: Mapped[str] = mapped_column(String(24))
    target_exchange: Mapped[str] = mapped_column(String(24))
    size_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    max_qty: Mapped[float] = mapped_column(Float, default=10.0)
    underlyings: Mapped[str] = mapped_column(String(32), default="BTC,ETH")
    mirror_exits: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    copied_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
