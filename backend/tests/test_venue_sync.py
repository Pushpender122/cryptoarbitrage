"""Venue reconciliation: partial fills must be applied exactly once."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.exchanges.base import (
    ContractSpec,
    VenueFill,
    VenueOrderStatus,
    VenuePositionSnapshot,
)
from app.exchanges.coinswitch import CoinSwitchProvider
from app.exchanges.delta import DeltaProvider
from app.models import Base, Fill, Order, VenuePosition
from app.services import risk
from app.services.orders import OrderService
from app.services.venue_sync import VenueSync

SYMBOL = "C-BTC-75000-071124"
EXPIRY = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
SPEC = ContractSpec(
    exchange="delta",
    symbol=SYMBOL,
    exchange_id="123",
    underlying="BTC",
    option_type="call",
    strike=75000,
    expiry=EXPIRY,
    contract_value=0.001,
    contract_unit="BTC",
    settlement_currency="USD",
    price_currency="USD",
    tick_size=0.5,
    taker_fee_rate=0.0001,
    maker_fee_rate=0.0001,
)


class StubMarket:
    def __init__(self, specs: dict | None = None):
        self._specs = specs or {}

    def spec(self, exchange: str, symbol: str):
        return self._specs.get((exchange, symbol))


class StubProvider:
    name = "delta"
    label = "Delta"
    supports_trading = True

    def __init__(self):
        self.order: VenueOrderStatus | None = None
        self.fills: list[VenueFill] = []
        self.positions: list[VenuePositionSnapshot] = []

    async def fetch_order(self, exchange_order_id, symbol, *, client_order_id=None):
        if self.order is None:
            raise AssertionError("test did not configure an order status")
        return self.order

    async def fetch_fills(self, exchange_order_id, symbol, *, product_id=None):
        return list(self.fills)

    async def fetch_position_snapshots(self):
        return list(self.positions)


class Recorder:
    def __init__(self):
        self.calls: list[float] = []

    async def on_order_filled(self, db, order, filled_qty=None):
        self.calls.append(filled_qty)


def make_order(**overrides) -> Order:
    data = dict(
        client_order_id="coid-1",
        exchange_order_id="V1",
        exchange="delta",
        symbol=SYMBOL,
        underlying="BTC",
        option_type="call",
        strike=75000,
        expiry=datetime(2026, 11, 7, 12, 0),
        contract_value=0.001,
        side="buy",
        qty=5,
        status="open",
        mode="live",
        source="manual",
    )
    data.update(overrides)
    return Order(**data)


def fill(fill_id: str, qty: float, price: float) -> VenueFill:
    return VenueFill(
        exchange="delta",
        fill_id=fill_id,
        exchange_order_id="V1",
        symbol=SYMBOL,
        side="buy",
        qty=qty,
        price=price,
        fee=0.01,
        is_maker=False,
        ts=datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    cfg = replace(Settings(), trading_mode="live", live_sync_ms=0, live_positions_sync_ms=0)
    provider = StubProvider()
    market = StubMarket({("delta", SYMBOL): SPEC})
    orders = OrderService(market, {"delta": provider}, cfg)
    recorder = Recorder()
    orders.copy_engine = recorder
    sync = VenueSync(market, {"delta": provider}, cfg, orders)
    db = session_factory()
    order = make_order()
    db.add(order)
    db.commit()
    try:
        yield db, provider, recorder, sync, order
    finally:
        db.close()


@pytest.mark.asyncio
async def test_partial_fills_apply_once_and_copy_only_the_delta(env):
    db, provider, recorder, sync, order = env
    provider.order = VenueOrderStatus(
        exchange_order_id="V1",
        status="partially_filled",
        filled_qty=2,
        avg_fill_price=100.0,
        fees=0.02,
        venue_state="open",
    )
    provider.fills = [fill("f1", 1, 100), fill("f2", 1, 101)]

    result = await sync.sync(db, force=True)
    db.refresh(order)
    assert (result["orders"], result["fills"]) == (1, 2)
    assert order.status == "partially_filled"
    assert order.filled_qty == 2
    assert order.avg_fill_price == 100.0
    assert order.fees == 0.02
    assert order.exchange_state == "open"
    assert order.last_synced_at is not None
    assert len(db.execute(select(Fill)).scalars().all()) == 2
    assert recorder.calls == [2]

    # A repeated poll returns the same fills; nothing may be double-counted.
    second = await sync.sync(db, force=True)
    assert second["fills"] == 0
    assert len(db.execute(select(Fill)).scalars().all()) == 2
    assert recorder.calls == [2]

    # Completion adds only the three new fills to the ledger and copy rules.
    provider.order = VenueOrderStatus(
        exchange_order_id="V1",
        status="filled",
        filled_qty=5,
        avg_fill_price=100.4,
        fees=0.05,
        venue_state="closed",
    )
    provider.fills = [fill(f"f{index}", 1, 100 + index) for index in range(1, 6)]
    third = await sync.sync(db, force=True)
    db.refresh(order)
    assert third["fills"] == 3
    assert order.status == "filled"
    assert order.filled_qty == 5
    assert order.fees == 0.05
    assert len(db.execute(select(Fill)).scalars().all()) == 5
    assert recorder.calls == [2, 3]


@pytest.mark.asyncio
async def test_cancelled_order_keeps_partial_fills(env):
    db, provider, _, sync, order = env
    provider.order = VenueOrderStatus(
        exchange_order_id="V1",
        status="cancelled",
        filled_qty=1,
        fees=0.01,
        venue_state="cancelled",
    )
    provider.fills = [fill("c1", 1, 99)]

    await sync.sync(db, force=True)
    db.refresh(order)
    assert order.status == "cancelled"
    assert order.filled_qty == 1
    assert order.exchange_state == "cancelled"


@pytest.mark.asyncio
async def test_sync_order_refreshes_a_single_order(env):
    db, provider, _, sync, order = env
    provider.order = VenueOrderStatus(
        exchange_order_id="V1",
        status="partially_filled",
        filled_qty=2,
        avg_fill_price=101.0,
        fees=0.02,
        venue_state="open",
    )
    provider.fills = [fill("f1", 2, 101)]

    result = await sync.sync_order(db, order)
    assert result["new_fill_qty"] == 2
    assert result["order"].filled_qty == 2
    assert result["order"].status == "partially_filled"


@pytest.mark.asyncio
async def test_positions_are_replaced_from_the_venue_and_drive_risk(env):
    db, provider, _, sync, _ = env
    provider.positions = [
        VenuePositionSnapshot(
            exchange="delta",
            symbol=SYMBOL,
            size=3,
            entry_price=90,
            mark_price=95,
            unrealized_pnl=1.5,
            realized_pnl=0.2,
            margin=12,
        )
    ]
    await sync.sync(db, force=True)
    row = db.execute(select(VenuePosition)).scalar_one()
    assert row.size == 3
    assert row.entry_price == 90
    assert row.mark_price == 95
    assert row.underlying == "BTC"
    assert row.strike == 75000
    assert row.margin == 12

    assert risk.gross_notional(db, lambda *_: None, live=True) == pytest.approx(3 * 95 * 0.001)
    assert risk.daily_pnl(db, lambda *_: None, live=True) == pytest.approx(1.7)

    settings = risk.get_settings(db)
    settings.max_open_positions = 1
    db.commit()
    with pytest.raises(risk.RiskError):
        risk.pretrade_check(
            db, SPEC, "buy", 1, 95, quote_for=lambda *_: None, live=True
        )

    # The venue flips the position; the mirror must follow the signed size.
    provider.positions = [
        VenuePositionSnapshot(
            exchange="delta", symbol=SYMBOL, size=-2, entry_price=95, mark_price=94
        )
    ]
    await sync.sync(db, force=True)
    db.refresh(row)
    assert row.size == -2
    assert db.execute(select(VenuePosition)).scalars().all() == [row]

    # Closed on the venue: the local mirror must not keep a stale position.
    provider.positions = []
    await sync.sync(db, force=True)
    assert db.execute(select(VenuePosition)).scalars().all() == []


@pytest.mark.asyncio
async def test_delta_parses_status_fills_and_positions():
    provider = object.__new__(DeltaProvider)
    micro = str(int(datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000))

    async def fake_request(method, path, **kwargs):
        if path.startswith("/v2/orders/"):
            return {
                "result": {
                    "id": 9001,
                    "state": "open",
                    "size": 5,
                    "unfilled_size": 2,
                    "average_fill_price": "101.5",
                    "paid_commission": "0.03",
                    "updated_at": micro,
                }
            }
        if path == "/v2/fills":
            assert kwargs["params"]["product_ids"] == "123"
            return {
                "result": [
                    {
                        "id": 1,
                        "order_id": 9001,
                        "product_symbol": SYMBOL,
                        "side": "buy",
                        "size": "2",
                        "price": "101",
                        "commission": "0.02",
                        "role": "maker",
                        "created_at": micro,
                    },
                    {
                        "id": 2,
                        "order_id": 777,
                        "product_symbol": SYMBOL,
                        "side": "buy",
                        "size": "1",
                        "price": "100",
                        "role": "taker",
                        "created_at": micro,
                    },
                ]
            }
        if path == "/v2/positions/margined":
            return {
                "result": [
                    {
                        "product_symbol": SYMBOL,
                        "size": "-3",
                        "entry_price": "90",
                        "mark_price": "95",
                        "unrealized_pnl": "-1.5",
                        "realized_pnl": "0.4",
                        "margin": "12",
                        "liquidation_price": "120",
                        "updated_at": micro,
                    },
                    {"product_symbol": "BTCUSD", "size": "1", "entry_price": "70000"},
                ]
            }
        raise AssertionError(f"unexpected path {path}")

    provider._request = fake_request  # type: ignore[attr-defined]
    status = await provider.fetch_order("9001", SYMBOL)
    assert status.status == "partially_filled"
    assert status.filled_qty == 3
    assert status.avg_fill_price == 101.5
    assert status.fees == 0.03
    assert status.venue_state == "open"
    assert status.updated_at is not None and status.updated_at.year == 2026

    fills = await provider.fetch_fills("9001", SYMBOL, product_id="123")
    assert [item.fill_id for item in fills] == ["1"]
    assert fills[0].is_maker is True
    assert fills[0].qty == 2
    assert fills[0].side == "buy"

    snapshots = await provider.fetch_position_snapshots()
    assert len(snapshots) == 1  # futures positions are ignored
    assert snapshots[0].size == -3
    assert snapshots[0].realized_pnl == 0.4
    assert snapshots[0].liquidation_price == 120


@pytest.mark.asyncio
async def test_coinswitch_parses_status_fills_and_positions():
    provider = object.__new__(CoinSwitchProvider)

    async def fake_request(method, path, **kwargs):
        if path.startswith("/v5/order/realtime"):
            assert "category=option" in path
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "o1",
                            "orderStatus": "PartiallyFilled",
                            "cumExecQty": "2",
                            "cumExecFee": "0.01",
                            "avgPrice": "101",
                            "updatedTime": "1756478116611",
                            "rejectReason": "EC_NoError",
                        }
                    ]
                },
            }
        if path.startswith("/v5/execution/list"):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "execId": "e1",
                            "orderId": "o1",
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "execPrice": "101",
                            "execQty": "2",
                            "execFee": "0.01",
                            "isMaker": True,
                            "execTime": "1756478116611",
                        }
                    ]
                },
            }
        if path.startswith("/v5/position/list"):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "side": "Sell",
                            "size": "2",
                            "avgPrice": "100",
                            "markPrice": "99",
                            "unrealisedPnl": "0.5",
                            "cumRealisedPnl": "-0.1",
                            "positionIM": "20",
                            "updatedTime": "1756478116611",
                        }
                    ]
                },
            }
        raise AssertionError(path)

    provider._request = fake_request  # type: ignore[attr-defined]
    status = await provider.fetch_order("o1", "BTCUSDT")
    assert status.status == "partially_filled"
    assert status.filled_qty == 2
    assert status.fees == 0.01

    fills = await provider.fetch_fills("o1", "BTCUSDT")
    assert fills[0].fill_id == "e1"
    assert fills[0].is_maker is True
    assert fills[0].qty == 2

    snapshots = await provider.fetch_position_snapshots()
    assert snapshots[0].size == -2
    assert snapshots[0].unrealized_pnl == 0.5
    assert snapshots[0].margin == 20
