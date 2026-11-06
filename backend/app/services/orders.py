"""Unified order routing: risk gates, paper fills, position accounting, paired legs."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..exchanges.base import ContractSpec, MarketDataProvider, OrderRequest, Quote
from ..models import Order, Position, VenuePosition, utcnow
from . import risk
from .market import MarketService

logger = logging.getLogger(__name__)


class OrderError(Exception):
    """User-facing order rejection."""


@dataclass
class NewOrder:
    exchange: str
    symbol: str
    side: str
    qty: float
    order_type: str = "limit"
    limit_price: float | None = None
    time_in_force: str = "gtc"
    reduce_only: bool = False
    source: str = "manual"
    parent_order_id: int | None = None
    strategy_id: int | None = None
    client_order_id: str | None = None
    stop_price: float | None = None
    target_price: float | None = None


class OrderService:
    def __init__(
        self,
        market: MarketService,
        providers: dict[str, MarketDataProvider],
        cfg: Settings,
    ):
        self.market = market
        self.providers = providers
        self.cfg = cfg
        self.copy_engine: Any = None  # set at startup to avoid an import cycle

    # ------------------------------------------------------------------ placing
    async def place(self, db: Session, request: NewOrder) -> Order:
        spec = self.market.spec(request.exchange, request.symbol)
        if spec is None:
            raise OrderError(f"Unknown contract {request.exchange}:{request.symbol}.")
        if request.side not in ("buy", "sell"):
            raise OrderError("Side must be buy or sell.")
        if request.order_type not in ("limit", "market"):
            raise OrderError("Order type must be limit or market.")
        if request.order_type == "limit" and not request.limit_price:
            raise OrderError("Limit orders need a limit price.")

        quote = self.market.quote(request.exchange, spec.symbol)
        if quote is None:
            raise OrderError(
                f"No market data for {spec.symbol} on {request.exchange}; refusing to route blind."
            )
        if request.order_type == "market":
            reference_price = quote.ask if request.side == "buy" else quote.bid
            if reference_price is None:
                raise OrderError("Order book has no price on the side needed to fill this order.")
        else:
            reference_price = float(request.limit_price or 0.0)

        settings = risk.pretrade_check(
            db,
            spec,
            request.side,
            request.qty,
            reference_price,
            quote_for=self.market.quote_for,
            reduce_only=request.reduce_only,
            check_price_deviation=request.order_type == "limit",
            live=not self.cfg.paper,
        )

        provider = self.providers[request.exchange]
        if not self.cfg.paper and not provider.supports_trading:
            raise OrderError(
                f"{provider.label} live trading is not configured "
                "(API keys and/or options allowlist missing)."
            )
        mode = "paper" if self.cfg.paper else "live"

        order = Order(
            client_order_id=request.client_order_id or f"coid-{uuid.uuid4().hex[:20]}",
            exchange=request.exchange,
            symbol=spec.symbol,
            underlying=spec.underlying,
            option_type=spec.option_type,
            strike=spec.strike,
            expiry=spec.expiry.astimezone(timezone.utc).replace(tzinfo=None),
            contract_value=spec.contract_value,
            side=request.side,
            qty=request.qty,
            order_type=request.order_type,
            limit_price=request.limit_price,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            status="pending",
            mode=mode,
            source=request.source,
            parent_order_id=request.parent_order_id,
            strategy_id=request.strategy_id,
        )
        db.add(order)
        db.flush()

        if mode == "paper":
            status, fill_price = self._paper_fill(quote, request, settings.slippage_bps)
            if status == "filled" and fill_price is not None:
                self._fill(db, order, spec, fill_price, request.qty, request)
            else:
                order.status = "open"
        else:
            ack = await provider.place_order(
                OrderRequest(
                    symbol=spec.symbol,
                    exchange_id=spec.exchange_id,
                    side=request.side,
                    qty=request.qty,
                    order_type=request.order_type,
                    limit_price=request.limit_price,
                    time_in_force=request.time_in_force,
                    reduce_only=request.reduce_only,
                    client_order_id=order.client_order_id,
                )
            )
            order.exchange_order_id = ack.exchange_order_id
            order.status = ack.status or "accepted"
            if ack.error:
                order.error = ack.error

        db.commit()
        db.refresh(order)

        if order.status == "filled" and self.copy_engine is not None:
            try:
                await self.copy_engine.on_order_filled(db, order)
            except Exception as exc:  # noqa: BLE001 - copy failures must not kill the fill
                logger.warning("copy trading failed after order %s: %s", order.id, exc)
        return order

    def _paper_fill(self, quote: Quote, request: NewOrder, slippage_bps: float) -> tuple[str, float | None]:
        slip = slippage_bps / 10000.0
        if request.order_type == "market":
            if request.side == "buy":
                if quote.ask is None:
                    raise OrderError("No ask price available.")
                return "filled", quote.ask * (1.0 + slip)
            if quote.bid is None:
                raise OrderError("No bid price available.")
            return "filled", quote.bid * (1.0 - slip)

        limit = float(request.limit_price or 0.0)
        if request.side == "buy":
            if quote.ask is not None and limit >= quote.ask:
                return "filled", min(limit, quote.ask)
            return "open", None
        if quote.bid is not None and limit <= quote.bid:
            return "filled", max(limit, quote.bid)
        return "open", None

    # ------------------------------------------------------------------ fills
    def _fill(
        self,
        db: Session,
        order: Order,
        spec: ContractSpec,
        price: float,
        qty: float,
        request: NewOrder | None = None,
    ) -> None:
        price = round(price / spec.tick_size) * spec.tick_size if spec.tick_size else price
        fee = price * qty * spec.contract_value * spec.taker_fee_rate
        order.filled_qty = qty
        order.avg_fill_price = price
        order.fees = round(fee, 8)
        order.status = "filled"
        order.realized_pnl = self._apply_fill(db, spec, order.side, qty, price, fee)
        order.updated_at = utcnow()

        if request is not None and (request.stop_price or request.target_price):
            position = db.execute(
                select(Position).where(
                    Position.exchange == spec.exchange, Position.symbol == spec.symbol
                )
            ).scalar_one_or_none()
            if position is not None:
                if request.stop_price:
                    position.stop_price = request.stop_price
                if request.target_price:
                    position.target_price = request.target_price

    def _apply_fill(
        self,
        db: Session,
        spec: ContractSpec,
        side: str,
        qty: float,
        price: float,
        fee: float,
    ) -> float:
        position = db.execute(
            select(Position).where(Position.exchange == spec.exchange, Position.symbol == spec.symbol)
        ).scalar_one_or_none()
        signed = qty if side == "buy" else -qty
        realized = 0.0

        if position is None:
            position = Position(
                exchange=spec.exchange,
                symbol=spec.symbol,
                underlying=spec.underlying,
                option_type=spec.option_type,
                strike=spec.strike,
                expiry=spec.expiry.astimezone(timezone.utc).replace(tzinfo=None),
                contract_value=spec.contract_value,
                qty=signed,
                avg_price=price,
                fees_paid=fee,
            )
            db.add(position)
            return 0.0

        old_qty = position.qty
        new_qty = old_qty + signed
        increasing = old_qty == 0 or (old_qty > 0) == (signed > 0)

        if increasing:
            total = abs(old_qty) + qty
            position.avg_price = (abs(old_qty) * position.avg_price + qty * price) / total if total else price
        else:
            closing = min(abs(signed), abs(old_qty))
            direction = 1.0 if old_qty > 0 else -1.0
            realized = (price - position.avg_price) * closing * spec.contract_value * direction
            position.realized_pnl += realized
            if abs(signed) > abs(old_qty):
                position.avg_price = price  # flipped through flat
            elif new_qty == 0:
                position.avg_price = 0.0

        position.qty = new_qty
        position.fees_paid += fee
        position.updated_at = utcnow()
        if abs(position.qty) < 1e-12:
            db.delete(position)
        return realized

    # ------------------------------------------------------------- maintenance
    async def reconcile_open_orders(self, db: Session) -> int:
        """Fill resting paper limit orders that the market has crossed."""
        orders = list(
            db.execute(
                select(Order).where(Order.status == "open", Order.mode == "paper")
            ).scalars().all()
        )
        filled = 0
        for order in orders:
            spec = self.market.spec(order.exchange, order.symbol)
            quote = self.market.quote(order.exchange, order.symbol)
            if spec is None or quote is None or order.limit_price is None:
                continue
            crossed = (
                quote.ask is not None and order.limit_price >= quote.ask
                if order.side == "buy"
                else quote.bid is not None and order.limit_price <= quote.bid
            )
            if not crossed:
                continue
            price = min(order.limit_price, quote.ask) if order.side == "buy" else max(order.limit_price, quote.bid)
            self._fill(db, order, spec, float(price), order.qty - order.filled_qty)
            filled += 1
        if filled:
            db.commit()
            for order in orders:
                if order.status == "filled" and self.copy_engine is not None:
                    await self.copy_engine.on_order_filled(db, order)
        return filled

    async def cancel(self, db: Session, order_id: int) -> Order:
        order = db.get(Order, order_id)
        if order is None:
            raise OrderError("Order not found.")
        if order.status not in ("open", "accepted", "pending"):
            raise OrderError(f"Order is {order.status} and cannot be cancelled.")
        if order.mode == "live" and order.exchange_order_id:
            provider = self.providers[order.exchange]
            await provider.cancel_order(order.exchange_order_id, order.symbol)
        order.status = "cancelled"
        order.updated_at = utcnow()
        db.commit()
        db.refresh(order)
        return order

    async def close_position(self, db: Session, position_id: int, *, source: str = "manual") -> Order:
        position = db.get(Position, position_id)
        if position is None or position.qty == 0:
            raise OrderError("Position not found or already flat.")
        side = "sell" if position.qty > 0 else "buy"
        return await self.place(
            db,
            NewOrder(
                exchange=position.exchange,
                symbol=position.symbol,
                side=side,
                qty=abs(position.qty),
                order_type="market",
                reduce_only=True,
                source=source,
            ),
        )

    async def close_venue_position(
        self, db: Session, position_id: int, *, source: str = "manual"
    ) -> Order:
        """Flatten a position the venue reports (live mode)."""
        position = db.get(VenuePosition, position_id)
        if position is None or position.size == 0:
            raise OrderError("Venue position not found or already flat.")
        side = "sell" if position.size > 0 else "buy"
        return await self.place(
            db,
            NewOrder(
                exchange=position.exchange,
                symbol=position.symbol,
                side=side,
                qty=abs(position.size),
                order_type="market",
                reduce_only=True,
                source=source,
            ),
        )

    # ------------------------------------------------------------------- pairs
    async def execute_pair(
        self, db: Session, key: str, qty: float, *, min_edge_bps: float | None = None
    ) -> dict[str, Any]:
        risk_settings = risk.get_settings(db)
        row = self.market.row(key, risk_settings.slippage_bps)
        if row is None:
            raise OrderError("That contract pair is no longer available on both venues.")
        if not row["equivalence"]["equivalent"]:
            raise OrderError(
                "Contracts are not equivalent — refusing to pair them: "
                + "; ".join(row["equivalence"]["notes"])
            )
        best = row["best"]
        if best is None:
            raise OrderError("No executable quote on both legs.")
        if not best.get("plausible", True):
            raise OrderError(
                "This spread fails the sanity guard: " + " ".join(best.get("sanity", []))
            )
        threshold = risk_settings.min_edge_bps if min_edge_bps is None else min_edge_bps
        if best["net_edge_bps"] < threshold:
            raise OrderError(
                f"Edge is {best['net_edge_bps']:.1f} bps, below the {threshold:.1f} bps threshold."
            )
        if qty > best["executable_qty"]:
            raise OrderError(
                f"Only {best['executable_qty']:g} contracts are available at the top of book."
            )

        sell_leg = NewOrder(
            exchange=best["sell_exchange"],
            symbol=best["sell_symbol"],
            side="sell",
            qty=qty,
            order_type="limit",
            limit_price=best["sell_price"],
            time_in_force="ioc",
            source="arbitrage",
        )
        buy_leg = NewOrder(
            exchange=best["buy_exchange"],
            symbol=best["buy_symbol"],
            side="buy",
            qty=qty,
            order_type="limit",
            limit_price=best["buy_price"],
            time_in_force="ioc",
            source="arbitrage",
        )

        # Validate both legs before touching either venue: a naked leg is the
        # expensive failure mode here. Rejections are pre-trade only.
        for leg in (sell_leg, buy_leg):
            spec = self.market.spec(leg.exchange, leg.symbol)
            if spec is None:
                raise OrderError(f"Unknown contract {leg.exchange}:{leg.symbol}.")
            provider = self.providers.get(leg.exchange)
            if not self.cfg.paper and (provider is None or not provider.supports_trading):
                raise OrderError(
                    f"{leg.exchange} cannot trade live (keys or allowlist missing); "
                    "refusing to send the other leg naked."
                )
            risk.pretrade_check(
                db,
                spec,
                leg.side,
                leg.qty,
                float(leg.limit_price or 0.0),
                quote_for=self.market.quote_for,
                reduce_only=False,
                check_price_deviation=False,
                live=not self.cfg.paper,
            )

        orders: list[Order] = []
        error: str | None = None
        try:
            # The sell leg goes first: if the venue rejects it, nothing is left naked.
            first = await self.place(db, sell_leg)
            first.parent_order_id = first.id
            db.commit()
            orders.append(first)
            buy_leg.parent_order_id = first.id
            orders.append(await self.place(db, buy_leg))
        except Exception as exc:  # noqa: BLE001 - a partial pair is reported, never hidden
            error = str(exc)
            db.rollback()
            for order in orders:
                try:
                    db.refresh(order)
                except Exception:  # noqa: BLE001
                    pass

        return {
            "key": key,
            "direction": best["name"],
            "expected_net_spread": best["net_spread"],
            "expected_edge_bps": best["net_edge_bps"],
            "orders": orders,
            "warning": error,
        }
