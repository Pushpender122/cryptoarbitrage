"""Copy-trading rules: mirror fills from a source venue onto a target venue."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CopyStrategy, Order, Position, VenuePosition, utcnow
from .market import MarketService
from .orders import NewOrder, OrderError, OrderService

logger = logging.getLogger(__name__)


class CopyEngine:
    def __init__(self, market: MarketService, order_service: OrderService):
        self.market = market
        self.order_service = order_service

    @staticmethod
    def _underlyings(strategy: CopyStrategy) -> set[str]:
        return {value.strip().upper() for value in (strategy.underlyings or "").split(",") if value.strip()}

    async def on_order_filled(
        self, db: Session, order: Order, filled_qty: float | None = None
    ) -> None:
        """Mirror a fill according to enabled strategies.

        `filled_qty` is the *newly* filled quantity. Live reconciliation passes the
        delta since the previous poll, so a partially filled order is mirrored in
        pieces instead of re-mirroring its cumulative size.
        """
        qty = order.filled_qty if filled_qty is None else filled_qty
        if order.source in ("copy", "risk_stop", "risk_target") or qty <= 0:
            return

        strategies = list(
            db.execute(select(CopyStrategy).where(CopyStrategy.enabled.is_(True))).scalars().all()
        )
        for strategy in strategies:
            if strategy.source_exchange != order.exchange:
                continue
            if order.underlying.upper() not in self._underlyings(strategy):
                continue
            if strategy.target_exchange == order.exchange:
                continue
            if order.reduce_only:
                if strategy.mirror_exits:
                    await self._mirror_exit(db, strategy, order, qty)
                continue
            await self._mirror_entry(db, strategy, order, qty)
        db.commit()

    async def _mirror_entry(
        self, db: Session, strategy: CopyStrategy, order: Order, filled_qty: float
    ) -> None:
        spec = self.market.counterpart(
            strategy.target_exchange,
            underlying=order.underlying,
            option_type=order.option_type,
            strike=order.strike,
            expiry=order.expiry,
        )
        if spec is None:
            strategy.last_error = "No equivalent contract on the target venue."
            return
        qty = self._sized_qty(filled_qty, strategy.size_ratio, strategy.max_qty, spec.min_qty, spec.qty_step)
        if qty <= 0:
            strategy.last_error = "Mirrored quantity rounded to zero."
            return
        quote = self.market.quote(strategy.target_exchange, spec.symbol)
        if quote is None or quote.mid is None:
            strategy.last_error = "Target venue has no quote for the mirrored contract."
            return
        try:
            await self.order_service.place(
                db,
                NewOrder(
                    exchange=strategy.target_exchange,
                    symbol=spec.symbol,
                    side=order.side,
                    qty=qty,
                    order_type="market",
                    source="copy",
                    parent_order_id=order.id,
                    strategy_id=strategy.id,
                ),
            )
            strategy.copied_count += 1
            strategy.last_event_at = utcnow()
            strategy.last_error = None
        except (OrderError, Exception) as exc:  # noqa: BLE001 - keep the strategy alive
            strategy.last_error = str(exc)
            logger.warning("copy entry failed for strategy %s: %s", strategy.id, exc)

    async def _mirror_exit(
        self, db: Session, strategy: CopyStrategy, order: Order, filled_qty: float
    ) -> None:
        spec = self.market.counterpart(
            strategy.target_exchange,
            underlying=order.underlying,
            option_type=order.option_type,
            strike=order.strike,
            expiry=order.expiry,
        )
        if spec is None:
            return
        target_qty = self._sized_qty(filled_qty, strategy.size_ratio, strategy.max_qty, spec.min_qty, spec.qty_step)
        if target_qty <= 0:
            return

        position = db.execute(
            select(Position).where(
                Position.exchange == strategy.target_exchange, Position.symbol == spec.symbol
            )
        ).scalar_one_or_none()
        if position is not None and position.qty != 0:
            qty = min(abs(position.qty), target_qty)
            side = "sell" if position.qty > 0 else "buy"
        else:
            # Live desks hold venue positions, not paper rows.
            venue = db.execute(
                select(VenuePosition).where(
                    VenuePosition.exchange == strategy.target_exchange,
                    VenuePosition.symbol == spec.symbol,
                )
            ).scalar_one_or_none()
            if venue is None or venue.size == 0:
                return
            qty = min(abs(venue.size), target_qty)
            side = "sell" if venue.size > 0 else "buy"

        try:
            await self.order_service.place(
                db,
                NewOrder(
                    exchange=strategy.target_exchange,
                    symbol=spec.symbol,
                    side=side,
                    qty=qty,
                    order_type="market",
                    reduce_only=True,
                    source="copy",
                    parent_order_id=order.id,
                    strategy_id=strategy.id,
                ),
            )
            strategy.copied_count += 1
            strategy.last_event_at = utcnow()
            strategy.last_error = None
        except (OrderError, Exception) as exc:  # noqa: BLE001
            strategy.last_error = str(exc)
            logger.warning("copy exit failed for strategy %s: %s", strategy.id, exc)

    @staticmethod
    def _sized_qty(
        source_qty: float,
        ratio: float,
        max_qty: float,
        min_qty: float,
        qty_step: float = 1.0,
    ) -> float:
        """Scale/cap a copied fill and round DOWN to the target venue lot step.

        Rounding down avoids exceeding the configured copy size or source fill.
        """
        try:
            requested = min(
                Decimal(str(max_qty)),
                Decimal(str(source_qty)) * Decimal(str(ratio)),
            )
            step = Decimal(str(qty_step))
            minimum = Decimal(str(min_qty))
        except (InvalidOperation, ValueError):
            return 0.0
        if requested <= 0 or step <= 0 or minimum <= 0:
            return 0.0
        lots = (requested / step).to_integral_value(rounding=ROUND_FLOOR)
        sized = lots * step
        if sized < minimum:
            return 0.0
        return float(sized)
