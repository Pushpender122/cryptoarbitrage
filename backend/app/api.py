"""REST API for the options terminal."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import serializers
from .config import settings
from .db import get_db
from .exchanges.base import ProviderError, iso_utc
from .models import CopyStrategy, Fill, Order, Position, RiskSettings, VenuePosition
from .services import risk
from .services.orders import NewOrder, OrderError

router = APIRouter(prefix="/api")

Exchange = Literal["delta", "coinswitch"]
Side = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]
TIF = Literal["gtc", "ioc"]


# --------------------------------------------------------------------- payloads
class OrderIn(BaseModel):
    exchange: Exchange
    symbol: str
    side: Side
    qty: float = Field(gt=0)
    order_type: OrderType = "limit"
    limit_price: float | None = Field(default=None, gt=0)
    time_in_force: TIF = "gtc"
    reduce_only: bool = False
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)


class PairOrderIn(BaseModel):
    key: str
    qty: float = Field(gt=0)
    min_edge_bps: float | None = Field(default=None, ge=0)


class RiskIn(BaseModel):
    trading_enabled: bool | None = None
    kill_switch: bool | None = None
    max_order_qty: float | None = Field(default=None, gt=0)
    max_order_notional: float | None = Field(default=None, gt=0)
    max_open_positions: int | None = Field(default=None, ge=1)
    max_gross_notional: float | None = Field(default=None, gt=0)
    max_daily_loss: float | None = Field(default=None, gt=0)
    max_price_deviation_pct: float | None = Field(default=None, gt=0, le=100)
    stop_loss_pct: float | None = Field(default=None, gt=0, le=100)
    target_pct: float | None = Field(default=None, gt=0, le=1000)
    auto_risk_exits: bool | None = None
    slippage_bps: float | None = Field(default=None, ge=0, le=500)
    min_edge_bps: float | None = Field(default=None, ge=0, le=5000)


class CopyIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    source_exchange: Exchange
    target_exchange: Exchange
    size_ratio: float = Field(default=1.0, gt=0, le=10)
    max_qty: float = Field(default=10.0, gt=0)
    underlyings: list[str] = Field(default_factory=lambda: ["BTC", "ETH"])
    mirror_exits: bool = True
    enabled: bool = False


class CopyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    size_ratio: float | None = Field(default=None, gt=0, le=10)
    max_qty: float | None = Field(default=None, gt=0)
    underlyings: list[str] | None = None
    mirror_exits: bool | None = None
    enabled: bool | None = None


def _market(request: Request):
    return request.app.state.market


def _orders(request: Request):
    return request.app.state.orders


def _hub(request: Request):
    return request.app.state.hub


def _venue_sync(request: Request):
    return request.app.state.venue_sync


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, (OrderError, risk.RiskError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ----------------------------------------------------------------------- status
@router.get("/health")
def health():
    return {"ok": True, "time": iso_utc(datetime.now(timezone.utc))}


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)):
    market = _market(request)
    risk_settings = risk.get_settings(db)
    assumptions = [
        "Premiums are compared per unit of the underlying; cash per contract uses the venue's contract_value.",
        "Fees use venue taker rates. CoinSwitch taker fee is an assumption until your account's real specs are known.",
        "A slippage buffer is applied to both legs of every arbitrage calculation.",
        "Delta options are USD-settled; CoinSwitch options settlement is unverified, so USD/USDT is normalised and flagged.",
        "This terminal has no authentication — run it behind a private network or reverse proxy.",
    ]
    if not settings.coinswitch_live_ready:
        assumptions.insert(
            0,
            "CoinSwitch options market data is private beta (401 until allowlisted). Its leg is a simulated book and is labelled 'simulated' everywhere.",
        )
    if not settings.paper:
        assumptions.append(
            "Live mode polls each exchange for order status, fills and positions; venue state is authoritative and local automatic stop/target exits are disabled for live positions."
        )
    return {
        "mode": "paper" if settings.paper else "live",
        "server_time": iso_utc(datetime.now(timezone.utc)),
        "poll_ms": settings.poll_ms,
        "exchanges": [provider.status() for provider in market.providers.values()],
        "market": {
            "updated_at": iso_utc(market.updated_at) if market.updated_at else None,
            "refresh_count": market.refresh_count,
            "stale": market.stale(),
        },
        "risk": serializers.risk_dict(risk_settings),
        "underlyings": list(settings.underlyings),
        "live_sync": _venue_sync(request).status(),
        "assumptions": assumptions,
        "access": {
            "coinswitch_options": "allowlisted" if settings.coinswitch_live_ready else "private_beta",
            "coinswitch_request_url": "https://api-trading.coinswitch.co/options/access",
            "delta_docs_url": "https://docs.delta.exchange",
        },
    }


# ----------------------------------------------------------------------- market
@router.get("/market/snapshot")
def market_snapshot(
    request: Request,
    underlying: str = Query(default="BTC"),
    expiry: str | None = Query(default=None),
    option_type: Literal["call", "put"] | None = Query(default=None),
    min_edge_bps: float | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    risk_settings = risk.get_settings(db)
    return {
        **_market(request).snapshot(
            underlying=underlying,
            expiry=expiry,
            option_type=option_type,
            min_edge_bps=risk_settings.min_edge_bps if min_edge_bps is None else min_edge_bps,
            slippage_bps=risk_settings.slippage_bps,
        ),
        "mode": "paper" if settings.paper else "live",
    }


@router.get("/market/pair")
def market_pair(request: Request, key: str, db: Session = Depends(get_db)):
    risk_settings = risk.get_settings(db)
    row = _market(request).row(key)
    if row is None:
        raise HTTPException(status_code=404, detail="Contract pair not found")
    return row


@router.get("/market/history")
def market_history(request: Request, key: str):
    return {"key": key, "points": _market(request).history(key)}


# ----------------------------------------------------------------------- orders
@router.post("/orders")
async def create_order(payload: OrderIn, request: Request, db: Session = Depends(get_db)):
    try:
        order = await _orders(request).place(
            db,
            NewOrder(
                exchange=payload.exchange,
                symbol=payload.symbol,
                side=payload.side,
                qty=payload.qty,
                order_type=payload.order_type,
                limit_price=payload.limit_price,
                time_in_force=payload.time_in_force,
                reduce_only=payload.reduce_only,
                stop_price=payload.stop_price,
                target_price=payload.target_price,
                source="manual",
            ),
        )
    except (OrderError, risk.RiskError, ProviderError) as exc:
        raise _handle(exc) from exc
    await _hub(request).portfolio()
    return serializers.order_dict(order)


@router.get("/orders")
def list_orders(
    request: Request,
    exchange: Exchange | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(Order).order_by(Order.id.desc()).limit(limit)
    if exchange:
        stmt = stmt.where(Order.exchange == exchange)
    if status_filter:
        stmt = stmt.where(Order.status == status_filter)
    if source:
        stmt = stmt.where(Order.source == source)
    return [serializers.order_dict(order) for order in db.execute(stmt).scalars().all()]


@router.post("/orders/pair")
async def create_pair_order(payload: PairOrderIn, request: Request, db: Session = Depends(get_db)):
    try:
        result = await _orders(request).execute_pair(
            db, payload.key, payload.qty, min_edge_bps=payload.min_edge_bps
        )
    except (OrderError, risk.RiskError, ProviderError) as exc:
        raise _handle(exc) from exc
    await _hub(request).portfolio()
    return {
        "key": result["key"],
        "direction": result["direction"],
        "expected_net_spread": result["expected_net_spread"],
        "expected_edge_bps": result["expected_edge_bps"],
        "orders": [serializers.order_dict(order) for order in result["orders"]],
        "warning": result["warning"],
    }


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        order = await _orders(request).cancel(db, order_id)
    except (OrderError, ProviderError) as exc:
        raise _handle(exc) from exc
    await _hub(request).portfolio()
    return serializers.order_dict(order)


# -------------------------------------------------------------------- positions
@router.get("/positions")
def list_positions(request: Request, db: Session = Depends(get_db)):
    market = _market(request)
    risk_settings = risk.get_settings(db)
    paper_positions = list(
        db.execute(select(Position).where(Position.qty != 0).order_by(Position.exchange)).scalars().all()
    )
    venue_rows = list(
        db.execute(
            select(VenuePosition).where(VenuePosition.size != 0).order_by(VenuePosition.exchange)
        )
        .scalars()
        .all()
    )
    sync_state = _venue_sync(request).status()
    if settings.paper:
        note = (
            "Paper ledger: fills are simulated locally against live quotes. "
            "Set TRADING_MODE=live to route orders and reconcile against the venues."
        )
    elif sync_state.get("enabled"):
        note = (
            "Live mode: positions, size and P&L are read from the exchange every "
            f"{settings.live_positions_sync_ms} ms. Local stop/target exits do not apply to venue positions."
        )
    else:
        note = (
            "Live mode is on but no venue has trading credentials, so nothing can be reconciled. "
            "Add an exchange API key (and CoinSwitch options allowlisting) to see venue positions."
        )
    return {
        "mode": "paper" if settings.paper else "live",
        "positions": [
            serializers.position_dict(position, market.quote(position.exchange, position.symbol))
            for position in paper_positions
        ],
        "summary": serializers.portfolio_summary(db, market.quote_for, risk_settings),
        "venue": [serializers.venue_position_dict(row) for row in venue_rows],
        "venue_summary": serializers.venue_summary(venue_rows),
        "synced_at": sync_state.get("last_run"),
        "venue_note": note,
    }


@router.post("/positions/{position_id}/close")
async def close_position(position_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        order = await _orders(request).close_position(db, position_id, source="manual")
    except (OrderError, risk.RiskError, ProviderError) as exc:
        raise _handle(exc) from exc
    await _hub(request).portfolio()
    return serializers.order_dict(order)


@router.post("/venue-positions/{position_id}/close")
async def close_venue_position(
    position_id: int, request: Request, db: Session = Depends(get_db)
):
    """Send a reduce-only market order that flattens a venue-reported position."""
    try:
        order = await _orders(request).close_venue_position(db, position_id, source="manual")
    except (OrderError, risk.RiskError, ProviderError) as exc:
        raise _handle(exc) from exc
    await _hub(request).portfolio()
    return serializers.order_dict(order)


@router.post("/positions/sync")
async def sync_positions(request: Request, db: Session = Depends(get_db)):
    """Force an immediate venue pull (orders, fills and positions)."""
    result = await _venue_sync(request).sync(db, force=True)
    rows = list(
        db.execute(select(VenuePosition).where(VenuePosition.size != 0)).scalars().all()
    )
    if result.get("orders") or result.get("fills") or result.get("positions"):
        await _hub(request).portfolio()
    return {
        "result": result,
        "venue": [serializers.venue_position_dict(row) for row in rows],
        "venue_summary": serializers.venue_summary(rows),
    }


@router.post("/orders/{order_id}/refresh")
async def refresh_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    """Reconcile one order against the venue on demand."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.mode != "live":
        return {
            "order": serializers.order_dict(order),
            "new_fill_qty": 0.0,
            "fills": 0,
            "errors": [],
        }
    result = await _venue_sync(request).sync_order(db, order)
    await _hub(request).portfolio()
    return {**result, "order": serializers.order_dict(result["order"])}


@router.get("/fills")
def list_fills(
    order_id: int | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(Fill).order_by(Fill.id.desc()).limit(limit)
    if order_id is not None:
        stmt = stmt.where(Fill.order_id == order_id)
    return {"fills": [serializers.fill_dict(fill) for fill in db.execute(stmt).scalars().all()]}


# ---------------------------------------------------------------------- history
@router.get("/history")
def history(
    request: Request,
    exchange: Exchange | None = None,
    source: str | None = None,
    limit: int = Query(default=200, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(Order).order_by(Order.id.desc()).limit(limit)
    if exchange:
        stmt = stmt.where(Order.exchange == exchange)
    if source:
        stmt = stmt.where(Order.source == source)
    return {"orders": [serializers.order_dict(order) for order in db.execute(stmt).scalars().all()]}


@router.get("/history/summary")
def history_summary(db: Session = Depends(get_db)):
    orders = list(db.execute(select(Order)).scalars().all())
    filled = [order for order in orders if order.status == "filled"]
    fees = sum(order.fees for order in filled)
    realized = sum(order.realized_pnl for order in filled)
    wins = [order for order in filled if order.realized_pnl > 0]
    losses = [order for order in filled if order.realized_pnl < 0]

    def bucket(source: str | None = None, exchange: str | None = None) -> dict:
        subset = [
            order
            for order in filled
            if (source is None or order.source == source) and (exchange is None or order.exchange == exchange)
        ]
        return {
            "orders": len(subset),
            "fees": round(sum(order.fees for order in subset), 4),
            "realized_pnl": round(sum(order.realized_pnl for order in subset), 4),
        }

    return {
        "total_orders": len(orders),
        "filled_orders": len(filled),
        "open_orders": len([order for order in orders if order.status == "open"]),
        "rejected_orders": len([order for order in orders if order.status == "rejected"]),
        "fees_paid": round(fees, 4),
        "realized_pnl": round(realized, 4),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(filled) * 100, 1) if filled else None,
        "by_exchange": {exchange: bucket(exchange=exchange) for exchange in ("delta", "coinswitch")},
        "by_source": {
            source: bucket(source=source) for source in ("manual", "arbitrage", "copy", "risk_stop", "risk_target")
        },
    }


# ------------------------------------------------------------------------- risk
@router.get("/risk")
def get_risk(db: Session = Depends(get_db)):
    return serializers.risk_dict(risk.get_settings(db))


@router.put("/risk")
async def update_risk(payload: RiskIn, request: Request, db: Session = Depends(get_db)):
    risk_settings = risk.get_settings(db)
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(risk_settings, key, value)
    db.commit()
    db.refresh(risk_settings)
    await _hub(request).portfolio()
    return serializers.risk_dict(risk_settings)


# --------------------------------------------------------------- copy trading
@router.get("/copy-strategies")
def list_strategies(db: Session = Depends(get_db)):
    strategies = db.execute(select(CopyStrategy).order_by(CopyStrategy.id)).scalars().all()
    return [serializers.strategy_dict(strategy) for strategy in strategies]


@router.post("/copy-strategies")
def create_strategy(payload: CopyIn, db: Session = Depends(get_db)):
    if payload.source_exchange == payload.target_exchange:
        raise HTTPException(status_code=400, detail="Source and target exchanges must differ.")
    strategy = CopyStrategy(
        name=payload.name,
        source_exchange=payload.source_exchange,
        target_exchange=payload.target_exchange,
        size_ratio=payload.size_ratio,
        max_qty=payload.max_qty,
        underlyings=",".join(sorted({u.strip().upper() for u in payload.underlyings if u.strip()})) or "BTC,ETH",
        mirror_exits=payload.mirror_exits,
        enabled=payload.enabled,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return serializers.strategy_dict(strategy)


@router.patch("/copy-strategies/{strategy_id}")
def update_strategy(strategy_id: int, payload: CopyPatch, db: Session = Depends(get_db)):
    strategy = db.get(CopyStrategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    changes = payload.model_dump(exclude_unset=True)
    if "underlyings" in changes and changes["underlyings"] is not None:
        changes["underlyings"] = ",".join(
            sorted({u.strip().upper() for u in changes["underlyings"] if u.strip()})
        )
    for key, value in changes.items():
        setattr(strategy, key, value)
    db.commit()
    db.refresh(strategy)
    return serializers.strategy_dict(strategy)


@router.delete("/copy-strategies/{strategy_id}")
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)):
    strategy = db.get(CopyStrategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    db.delete(strategy)
    db.commit()
    return {"ok": True}
