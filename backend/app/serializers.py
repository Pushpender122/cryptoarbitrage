"""JSON shapes shared by the REST API and the WebSocket stream."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .exchanges.base import Quote, iso_utc
from .models import CopyStrategy, Fill, Order, Position, RiskSettings, VenuePosition
from .services import risk


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return iso_utc(value)


def _round(value: float | None, digits: int = 8) -> float | None:
    return None if value is None else round(float(value), digits)


def order_dict(order: Order) -> dict[str, Any]:
    qty = order.filled_qty or order.qty
    notional = (order.avg_fill_price or order.limit_price or 0.0) * qty * order.contract_value
    return {
        "id": order.id,
        "client_order_id": order.client_order_id,
        "exchange_order_id": order.exchange_order_id,
        "exchange": order.exchange,
        "symbol": order.symbol,
        "underlying": order.underlying,
        "option_type": order.option_type,
        "strike": order.strike,
        "expiry": _iso(order.expiry),
        "contract_value": order.contract_value,
        "side": order.side,
        "qty": order.qty,
        "order_type": order.order_type,
        "limit_price": order.limit_price,
        "time_in_force": order.time_in_force,
        "reduce_only": order.reduce_only,
        "status": order.status,
        "filled_qty": order.filled_qty,
        "avg_fill_price": order.avg_fill_price,
        "fees": order.fees,
        "realized_pnl": order.realized_pnl,
        "notional": _round(notional, 4),
        "mode": order.mode,
        "source": order.source,
        "parent_order_id": order.parent_order_id,
        "strategy_id": order.strategy_id,
        "error": order.error,
        "exchange_state": order.exchange_state,
        "last_synced_at": _iso(order.last_synced_at),
        "created_at": _iso(order.created_at),
        "updated_at": _iso(order.updated_at),
    }


def position_dict(position: Position, quote: Quote | None) -> dict[str, Any]:
    mark = quote.mid if quote is not None and quote.mid is not None else position.avg_price
    direction = 1.0 if position.qty > 0 else -1.0
    unrealized = (mark - position.avg_price) * position.qty * position.contract_value
    pnl_pct = (
        (mark - position.avg_price) / position.avg_price * 100.0 * direction
        if position.avg_price
        else None
    )
    return {
        "id": position.id,
        "exchange": position.exchange,
        "symbol": position.symbol,
        "underlying": position.underlying,
        "option_type": position.option_type,
        "strike": position.strike,
        "expiry": _iso(position.expiry),
        "qty": position.qty,
        "avg_price": position.avg_price,
        "mark": _round(mark, 6),
        "contract_value": position.contract_value,
        "notional": _round(abs(position.qty) * mark * position.contract_value, 4),
        "unrealized_pnl": _round(unrealized, 4),
        "pnl_pct": _round(pnl_pct, 2),
        "realized_pnl": _round(position.realized_pnl, 4),
        "fees_paid": _round(position.fees_paid, 4),
        "stop_price": position.stop_price,
        "target_price": position.target_price,
        "source": quote.source if quote else None,
        "opened_at": _iso(position.opened_at),
        "updated_at": _iso(position.updated_at),
    }


def risk_dict(settings: RiskSettings) -> dict[str, Any]:
    return {
        "trading_enabled": settings.trading_enabled,
        "kill_switch": settings.kill_switch,
        "max_order_qty": settings.max_order_qty,
        "max_order_notional": settings.max_order_notional,
        "max_open_positions": settings.max_open_positions,
        "max_gross_notional": settings.max_gross_notional,
        "max_daily_loss": settings.max_daily_loss,
        "max_price_deviation_pct": settings.max_price_deviation_pct,
        "stop_loss_pct": settings.stop_loss_pct,
        "target_pct": settings.target_pct,
        "auto_risk_exits": settings.auto_risk_exits,
        "slippage_bps": settings.slippage_bps,
        "min_edge_bps": settings.min_edge_bps,
        "updated_at": _iso(settings.updated_at),
    }


def strategy_dict(strategy: CopyStrategy) -> dict[str, Any]:
    return {
        "id": strategy.id,
        "name": strategy.name,
        "source_exchange": strategy.source_exchange,
        "target_exchange": strategy.target_exchange,
        "size_ratio": strategy.size_ratio,
        "max_qty": strategy.max_qty,
        "underlyings": [u for u in (strategy.underlyings or "").split(",") if u],
        "mirror_exits": strategy.mirror_exits,
        "enabled": strategy.enabled,
        "copied_count": strategy.copied_count,
        "last_error": strategy.last_error,
        "last_event_at": _iso(strategy.last_event_at),
        "created_at": _iso(strategy.created_at),
    }


def portfolio_summary(db, quote_for, settings: RiskSettings) -> dict[str, Any]:
    from sqlalchemy import select

    from .models import Position

    positions = list(db.execute(select(Position).where(Position.qty != 0)).scalars().all())
    unrealized = 0.0
    for position in positions:
        quote = quote_for(position.exchange, position.symbol)
        mark = quote.mid if quote is not None and quote.mid is not None else position.avg_price
        unrealized += (mark - position.avg_price) * position.qty * position.contract_value
    return {
        "open_positions": len(positions),
        "unrealized_pnl": _round(unrealized, 4),
        "gross_notional": _round(
            risk.gross_notional(db, quote_for), 4
        ),
        "daily_pnl": _round(risk.daily_pnl(db, quote_for), 4),
        "max_daily_loss": settings.max_daily_loss,
    }


def fill_dict(fill: Fill) -> dict[str, Any]:
    return {
        "id": fill.id,
        "exchange": fill.exchange,
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "exchange_order_id": fill.exchange_order_id,
        "symbol": fill.symbol,
        "side": fill.side,
        "qty": fill.qty,
        "price": fill.price,
        "fee": _round(fill.fee, 8),
        "is_maker": fill.is_maker,
        "filled_at": _iso(fill.filled_at),
    }


def venue_position_dict(row: VenuePosition) -> dict[str, Any]:
    mark = row.mark_price if row.mark_price is not None else row.entry_price
    direction = 1.0 if row.size > 0 else -1.0
    pnl_pct = (
        (mark - row.entry_price) / row.entry_price * 100.0 * direction if row.entry_price else None
    )
    return {
        "id": row.id,
        "exchange": row.exchange,
        "symbol": row.symbol,
        "underlying": row.underlying,
        "option_type": row.option_type,
        "strike": row.strike,
        "expiry": _iso(row.expiry),
        "size": row.size,
        "entry_price": row.entry_price,
        "mark": _round(mark, 6),
        "contract_value": row.contract_value,
        "notional": _round(abs(row.size) * mark * row.contract_value, 4),
        "unrealized_pnl": _round(row.unrealized_pnl, 4),
        "realized_pnl": _round(row.realized_pnl, 4),
        "pnl_pct": _round(pnl_pct, 2),
        "margin": _round(row.margin, 4),
        "liquidation_price": row.liquidation_price,
        "venue_updated_at": _iso(row.venue_updated_at),
        "synced_at": _iso(row.synced_at),
        "source": "venue",
    }


def venue_summary(rows: list[VenuePosition]) -> dict[str, Any]:
    return {
        "open_positions": len(rows),
        "unrealized_pnl": _round(sum(row.unrealized_pnl for row in rows), 4),
        "realized_pnl": _round(sum(row.realized_pnl for row in rows), 4),
        "gross_notional": _round(
            sum(
                abs(row.size)
                * (row.mark_price if row.mark_price is not None else row.entry_price)
                * row.contract_value
                for row in rows
            ),
            4,
        ),
        "source": "venue",
    }
