"""Venue reconciliation: poll order state, record fills, mirror positions.

In live mode the exchange is the source of truth. This service:

* polls every live order that is still working and copies the venue's status,
  filled quantity, average price and fees onto the local order;
* records each venue execution once (unique per exchange + fill id), so a
  retried poll, a restart, or an overlapping loop cannot double-count fills;
* replays the *delta* of newly filled quantity into copy-trading rules;
* replaces local position rows with snapshots reported by the venue, deleting
  rows for positions the venue no longer holds.

Paper mode never calls into here: the local ledger is only authoritative for
simulated fills.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..exchanges.base import MarketDataProvider, ProviderError, VenueFill
from ..models import Fill, Order, VenuePosition, utcnow
from .market import MarketService
from .orders import OrderService

logger = logging.getLogger(__name__)

OPEN_ORDER_STATUSES = ("pending", "accepted", "open", "partially_filled")


class VenueSync:
    def __init__(
        self,
        market: MarketService,
        providers: dict[str, MarketDataProvider],
        cfg: Settings,
        orders: OrderService,
    ):
        self.market = market
        self.providers = providers
        self.cfg = cfg
        self.orders = orders
        self.last_run = None
        self.last_result: dict[str, Any] = {
            "orders": 0,
            "fills": 0,
            "positions": 0,
            "errors": [],
        }
        self._last_sync_mono = 0.0
        self._last_positions_mono = 0.0

    # ------------------------------------------------------------------ public
    @property
    def enabled(self) -> bool:
        return not self.cfg.paper and any(p.supports_trading for p in self.providers.values())

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "order_interval_ms": self.cfg.live_sync_ms,
            "positions_interval_ms": self.cfg.live_positions_sync_ms,
            "last_run": _iso(self.last_run),
            "last_result": self.last_result,
        }

    async def sync(self, db: Session, *, force: bool = False) -> dict[str, Any]:
        """Poll every working live order and (periodically) all venue positions."""
        if self.cfg.paper:
            return {"enabled": False, "orders": 0, "fills": 0, "positions": 0, "errors": []}

        now = time.monotonic()
        if not force and (now - self._last_sync_mono) * 1000.0 < self.cfg.live_sync_ms:
            return self.last_result

        result: dict[str, Any] = {"orders": 0, "fills": 0, "positions": 0, "errors": []}
        await self._sync_orders(db, result)
        if force or (now - self._last_positions_mono) * 1000.0 >= self.cfg.live_positions_sync_ms:
            await self._sync_positions(db, result)
            self._last_positions_mono = now

        self._last_sync_mono = now
        self.last_run = utcnow()
        self.last_result = result
        if result["errors"]:
            logger.warning("venue sync errors: %s", "; ".join(result["errors"][:3]))
        return result

    async def sync_order(self, db: Session, order: Order) -> dict[str, Any]:
        """Force a single order to be reconciled, for the on-demand refresh button."""
        result: dict[str, Any] = {"orders": 0, "fills": 0, "positions": 0, "errors": []}
        delta = await self._sync_one(db, order, result)
        if delta > 0:
            await self._mirror(db, order, delta)
        db.refresh(order)
        return {
            "order": order,
            "new_fill_qty": delta,
            "fills": result["fills"],
            "errors": result["errors"],
        }

    # ------------------------------------------------------------------- orders
    async def _sync_orders(self, db: Session, result: dict[str, Any]) -> None:
        orders = list(
            db.execute(
                select(Order).where(
                    Order.mode == "live",
                    Order.status.in_(OPEN_ORDER_STATUSES),
                )
            )
            .scalars()
            .all()
        )
        for order in orders:
            delta = await self._sync_one(db, order, result)
            if delta > 0:
                await self._mirror(db, order, delta)

    async def _sync_one(self, db: Session, order: Order, result: dict[str, Any]) -> float:
        provider = self.providers.get(order.exchange)
        if provider is None or not provider.supports_trading:
            return 0.0
        if not order.exchange_order_id and not order.client_order_id:
            return 0.0

        try:
            status = await provider.fetch_order(
                order.exchange_order_id or "",
                order.symbol,
                client_order_id=order.client_order_id,
            )
        except ProviderError as exc:
            result["errors"].append(f"{order.exchange} {order.symbol}: {exc}")
            order.last_synced_at = utcnow()
            db.commit()
            return 0.0
        except Exception as exc:  # noqa: BLE001 - one venue error must not stop the loop
            result["errors"].append(f"{order.exchange} {order.symbol}: {exc}")
            logger.warning("order %s status poll failed: %s", order.id, exc)
            db.commit()
            return 0.0

        previous_filled = order.filled_qty
        if status.filled_qty > 0:
            try:
                fills = await provider.fetch_fills(
                    order.exchange_order_id or "",
                    order.symbol,
                    product_id=self._product_id(order),
                )
            except Exception as exc:  # noqa: BLE001 - cumulative state still applies
                result["errors"].append(f"{order.exchange} {order.symbol} fills: {exc}")
                fills = []
            for fill in fills:
                if self._record_fill(db, order, fill):
                    result["fills"] += 1

        order.filled_qty = max(order.filled_qty, status.filled_qty)
        if status.avg_fill_price is not None:
            order.avg_fill_price = status.avg_fill_price
        order.fees = max(order.fees, status.fees)
        if status.venue_state:
            order.exchange_state = status.venue_state
        if status.status != "unknown":
            order.status = status.status
        if status.reject_reason:
            order.error = status.reject_reason
        order.last_synced_at = utcnow()
        if status.status != "unknown" or status.filled_qty != previous_filled:
            order.updated_at = utcnow()
        result["orders"] += 1
        db.commit()
        return max(0.0, order.filled_qty - previous_filled)

    def _product_id(self, order: Order) -> str | None:
        spec = self.market.spec(order.exchange, order.symbol)
        return spec.exchange_id if spec is not None else None

    def _record_fill(self, db: Session, order: Order, fill: VenueFill) -> bool:
        existing = db.execute(
            select(Fill).where(Fill.exchange == fill.exchange, Fill.fill_id == fill.fill_id)
        ).scalar_one_or_none()
        if existing is not None:
            return False
        db.add(
            Fill(
                exchange=fill.exchange,
                fill_id=fill.fill_id,
                order_id=order.id,
                exchange_order_id=fill.exchange_order_id,
                symbol=fill.symbol or order.symbol,
                side=fill.side or order.side,
                qty=fill.qty,
                price=fill.price,
                fee=fill.fee,
                is_maker=fill.is_maker,
                filled_at=fill.ts.astimezone(timezone.utc).replace(tzinfo=None),
            )
        )
        db.flush()
        return True

    async def _mirror(self, db: Session, order: Order, filled_qty: float) -> None:
        copy_engine = self.orders.copy_engine
        if copy_engine is None:
            return
        try:
            await copy_engine.on_order_filled(db, order, filled_qty=filled_qty)
        except Exception as exc:  # noqa: BLE001 - copy failures must not break reconciliation
            logger.warning("copy trading failed after venue fill on order %s: %s", order.id, exc)

    # ---------------------------------------------------------------- positions
    async def _sync_positions(self, db: Session, result: dict[str, Any]) -> None:
        for name, provider in self.providers.items():
            if not provider.supports_trading:
                continue
            try:
                snapshots = await provider.fetch_position_snapshots()
            except Exception as exc:  # noqa: BLE001 - keep other venues syncing
                result["errors"].append(f"{name} positions: {exc}")
                logger.warning("%s position sync failed: %s", name, exc)
                continue

            seen: set[str] = set()
            for snapshot in snapshots:
                if not snapshot.symbol or snapshot.size == 0:
                    continue
                row = db.execute(
                    select(VenuePosition).where(
                        VenuePosition.exchange == name,
                        VenuePosition.symbol == snapshot.symbol,
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = VenuePosition(exchange=name, symbol=snapshot.symbol)
                    db.add(row)
                spec = self.market.spec(name, snapshot.symbol)
                if spec is not None:
                    row.underlying = spec.underlying
                    row.option_type = spec.option_type
                    row.strike = spec.strike
                    row.expiry = spec.expiry.astimezone(timezone.utc).replace(tzinfo=None)
                    row.contract_value = spec.contract_value
                row.size = snapshot.size
                row.entry_price = snapshot.entry_price
                row.mark_price = snapshot.mark_price
                row.unrealized_pnl = snapshot.unrealized_pnl
                row.realized_pnl = snapshot.realized_pnl
                row.margin = snapshot.margin
                row.liquidation_price = snapshot.liquidation_price
                row.venue_updated_at = snapshot.venue_updated_at
                row.synced_at = utcnow()
                row.raw = json.dumps(snapshot.raw, default=str)[:4000] if snapshot.raw else None
                seen.add(snapshot.symbol)
                result["positions"] += 1

            stale = list(
                db.execute(select(VenuePosition).where(VenuePosition.exchange == name))
                .scalars()
                .all()
            )
            for row in stale:
                if row.symbol not in seen:
                    db.delete(row)
        db.commit()


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
