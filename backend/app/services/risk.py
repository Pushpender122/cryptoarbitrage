"""Pre-trade risk gates, exposure maths and automatic stop/target exits."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..exchanges.base import ContractSpec, Quote
from ..models import Order, Position, RiskSettings, VenuePosition

QuoteLookup = Callable[[str, str], Quote | None]


class RiskError(Exception):
    """Raised when a pre-trade risk gate rejects an order."""


def get_settings(db: Session) -> RiskSettings:
    settings = db.get(RiskSettings, 1)
    if settings is None:
        settings = RiskSettings(id=1)
        db.add(settings)
        db.commit()
    return settings


def start_of_day() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def open_positions(db: Session) -> list[Position]:
    return list(db.execute(select(Position).where(Position.qty != 0)).scalars().all())


def position_mark(position: Position, quote: Quote | None) -> float:
    if quote is not None and quote.mid is not None:
        return quote.mid
    return position.avg_price


def unrealized_pnl(position: Position, quote: Quote | None) -> float:
    mark = position_mark(position, quote)
    return (mark - position.avg_price) * position.qty * position.contract_value


def venue_position_rows(db: Session) -> list[VenuePosition]:
    return list(db.execute(select(VenuePosition).where(VenuePosition.size != 0)).scalars().all())


def venue_mark(row: VenuePosition) -> float:
    return row.mark_price if row.mark_price is not None else row.entry_price


def gross_notional(db: Session, quote_for: QuoteLookup, *, live: bool = False) -> float:
    if live:
        return sum(abs(row.size) * venue_mark(row) * row.contract_value for row in venue_position_rows(db))
    total = 0.0
    for position in open_positions(db):
        quote = quote_for(position.exchange, position.symbol)
        total += abs(position.qty) * position_mark(position, quote) * position.contract_value
    return total


def daily_pnl(db: Session, quote_for: QuoteLookup, *, live: bool = False) -> float:
    if live:
        # Venue-reported P&L is authoritative for live positions and trades.
        return float(sum(row.realized_pnl + row.unrealized_pnl for row in venue_position_rows(db)))
    realized = (
        db.execute(
            select(func.coalesce(func.sum(Order.realized_pnl), 0.0)).where(Order.created_at >= start_of_day())
        ).scalar_one()
        or 0.0
    )
    unrealized = sum(unrealized_pnl(position, quote_for(position.exchange, position.symbol)) for position in open_positions(db))
    return float(realized) + float(unrealized)


def pretrade_check(
    db: Session,
    spec: ContractSpec,
    side: str,
    qty: float,
    price: float,
    *,
    quote_for: QuoteLookup,
    reduce_only: bool = False,
    check_price_deviation: bool = True,
    live: bool = False,
) -> RiskSettings:
    """Raise RiskError with an operator-readable reason, or return the settings used."""
    settings = get_settings(db)
    if settings.kill_switch and not reduce_only:
        raise RiskError("Kill switch is on — new risk-increasing orders are blocked.")
    if not settings.trading_enabled and not reduce_only:
        raise RiskError("Trading is disabled in risk settings.")
    if qty <= 0:
        raise RiskError("Quantity must be positive.")

    notional = qty * price * spec.contract_value
    if not reduce_only:
        if qty > settings.max_order_qty:
            raise RiskError(
                f"Quantity {qty:g} exceeds the per-order limit of {settings.max_order_qty:g} contracts."
            )
        if notional > settings.max_order_notional:
            raise RiskError(
                f"Order notional {notional:,.2f} exceeds the per-order limit of {settings.max_order_notional:,.2f}."
            )

    quote = quote_for(spec.exchange, spec.symbol)
    if check_price_deviation and quote is not None and quote.mid:
        deviation = abs(price - quote.mid) / quote.mid * 100.0
        if deviation > settings.max_price_deviation_pct:
            raise RiskError(
                f"Price {price:g} is {deviation:.1f}% away from mid {quote.mid:g} "
                f"(limit {settings.max_price_deviation_pct:g}%)."
            )

    if live:
        venue_row = db.execute(
            select(VenuePosition).where(
                VenuePosition.exchange == spec.exchange, VenuePosition.symbol == spec.symbol
            )
        ).scalar_one_or_none()
        existing_qty = venue_row.size if venue_row is not None else 0.0
    else:
        existing = db.execute(
            select(Position).where(Position.exchange == spec.exchange, Position.symbol == spec.symbol)
        ).scalar_one_or_none()
        existing_qty = existing.qty if existing is not None else 0.0
    if reduce_only:
        if existing_qty == 0:
            raise RiskError("Reduce-only order rejected: there is no open position to reduce.")
        expected_side = "sell" if existing_qty > 0 else "buy"
        if side != expected_side:
            raise RiskError(f"Reduce-only order must be {expected_side} to reduce the current position.")
        if qty > abs(existing_qty) + 1e-9:
            raise RiskError(
                f"Reduce-only quantity {qty:g} exceeds the open position size {abs(existing_qty):g}."
            )
        return settings

    signed_change = qty if side == "buy" else -qty
    projected_qty = existing_qty + signed_change
    # An order that crosses through flat and opens the opposite side increases
    # absolute exposure even though it initially reduces the old position.
    risk_increasing = abs(projected_qty) > abs(existing_qty) + 1e-9

    if risk_increasing:
        count = len(venue_position_rows(db)) if live else len(open_positions(db))
        if existing_qty == 0 and count >= settings.max_open_positions:
            raise RiskError(
                f"Open position limit reached ({count}/{settings.max_open_positions})."
            )
        projected = gross_notional(db, quote_for, live=live) + notional
        if projected > settings.max_gross_notional:
            raise RiskError(
                f"Gross exposure would reach {projected:,.2f}, above the "
                f"{settings.max_gross_notional:,.2f} cap."
            )
        pnl = daily_pnl(db, quote_for, live=live)
        if pnl <= -abs(settings.max_daily_loss):
            raise RiskError(
                f"Daily loss limit hit ({pnl:,.2f} vs -{settings.max_daily_loss:,.2f}); "
                "risk-increasing orders are blocked until reset."
            )
    return settings


def evaluate_exits(db: Session, quote_for: QuoteLookup, settings: RiskSettings) -> list[tuple[int, str]]:
    """Return (position_id, reason) pairs that should be closed now."""
    if not settings.auto_risk_exits:
        return []
    exits: list[tuple[int, str]] = []
    for position in open_positions(db):
        quote = quote_for(position.exchange, position.symbol)
        if quote is None or quote.mid is None or position.avg_price <= 0:
            continue
        mark = quote.mid
        is_long = position.qty > 0
        direction = 1.0 if is_long else -1.0
        change_pct = (mark - position.avg_price) / position.avg_price * 100.0 * direction

        stop_hit = change_pct <= -abs(settings.stop_loss_pct)
        target_hit = change_pct >= abs(settings.target_pct)
        if position.stop_price:
            stop_hit = stop_hit or (mark <= position.stop_price if is_long else mark >= position.stop_price)
        if position.target_price:
            target_hit = target_hit or (mark >= position.target_price if is_long else mark <= position.target_price)

        if stop_hit:
            exits.append((position.id, "risk_stop"))
        elif target_hit:
            exits.append((position.id, "risk_target"))
    return exits
