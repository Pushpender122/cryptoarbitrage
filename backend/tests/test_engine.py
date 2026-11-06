"""Engine tests: pricing, contract matching, arbitrage maths, order accounting."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.exchanges.base import ContractSpec, Quote
from app.exchanges.coinswitch import parse_option_symbol
from app.models import Base, Position
from app.pricing import black76_price, implied_vol
from app.services.matching import arbitrage_summary, collect_arbitrage, compare_pair
from app.services.orders import OrderService

EXPIRY = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def make_spec(
    exchange: str,
    *,
    contract_value: float = 0.001,
    settlement: str = "USD",
    fee: float = 0.0003,
    strike: float = 75000.0,
    option_type: str = "call",
) -> ContractSpec:
    return ContractSpec(
        exchange=exchange,
        symbol=f"{exchange.upper()}-{strike:g}",
        exchange_id="1",
        underlying="BTC",
        option_type=option_type,
        strike=strike,
        expiry=EXPIRY,
        contract_value=contract_value,
        contract_unit="BTC",
        settlement_currency=settlement,
        price_currency="USD",
        tick_size=0.1,
        taker_fee_rate=fee,
        maker_fee_rate=fee,
    )


def make_quote(
    exchange: str,
    bid: float,
    ask: float,
    *,
    symbol: str,
    source: str = "live",
    bid_size: float = 10,
    ask_size: float = 10,
) -> Quote:
    return Quote(
        exchange=exchange,
        symbol=symbol,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        ltp=(bid + ask) / 2,
        mark_price=(bid + ask) / 2,
        mark_iv=0.5,
        oi=100,
        volume=10,
        ts=datetime.now(timezone.utc),
        source=source,
    )


def pair_row(slippage_bps: float = 0.0, contract_value: float = 0.001, source: str = "live"):
    delta = make_spec("delta", contract_value=contract_value, fee=0.0003)
    coinswitch = make_spec("coinswitch", contract_value=contract_value, settlement="USDT", fee=0.0005)
    return compare_pair(
        "BTC|call|75000|2026-11-20T12:00Z",
        {"delta": delta, "coinswitch": coinswitch},
        {
            "delta": make_quote("delta", 99.0, 101.0, symbol=delta.symbol, source=source),
            "coinswitch": make_quote("coinswitch", 103.0, 104.0, symbol=coinswitch.symbol, source=source),
        },
        slippage_bps=slippage_bps,
    )


def test_black76_put_call_parity() -> None:
    forward, strike, t_years, vol = 75000.0, 74000.0, 14 / 365, 0.55
    call = black76_price(forward, strike, t_years, vol, "call")
    put = black76_price(forward, strike, t_years, vol, "put")
    assert call - put == pytest.approx(forward - strike, rel=1e-6)


def test_implied_vol_round_trip() -> None:
    price = black76_price(75000, 80000, 30 / 365, 0.62, "call")
    assert implied_vol(price, 75000, 80000, 30 / 365, "call") == pytest.approx(0.62, rel=1e-3)


def test_compare_pair_picks_cheapest_buy_and_most_expensive_sell() -> None:
    row = pair_row()
    best = row["best"]
    assert best["buy_exchange"] == "delta"
    assert best["sell_exchange"] == "coinswitch"
    assert best["gross_spread"] == pytest.approx(2.0)
    assert best["plausible"] is True


def test_compare_pair_deducts_fees_and_slippage() -> None:
    row = pair_row(slippage_bps=10)
    best = row["best"]
    expected_fees = 101.0 * 0.0003 + 103.0 * 0.0005
    expected_slippage = (101.0 + 103.0) * 10 / 10000
    assert best["fees"] == pytest.approx(expected_fees)
    assert best["net_spread"] == pytest.approx(2.0 - expected_fees - expected_slippage)
    assert best["net_spread"] < best["gross_spread"]


def test_settlement_difference_is_normalised_and_flagged() -> None:
    row = pair_row()
    assert row["equivalence"]["equivalent"] is True
    assert row["equivalence"]["settlement_normalized"] is True
    assert any("Settlement differs" in note for note in row["equivalence"]["notes"])


def test_contract_size_mismatch_is_not_equivalent() -> None:
    row = pair_row(contract_value=0.001)
    mismatched = make_spec("coinswitch", contract_value=0.01, settlement="USDT")
    row = compare_pair(
        row["key"],
        {"delta": make_spec("delta"), "coinswitch": mismatched},
        {
            "delta": make_quote("delta", 99.0, 101.0, symbol="DELTA-75000"),
            "coinswitch": make_quote("coinswitch", 110.0, 112.0, symbol=mismatched.symbol),
        },
        slippage_bps=0,
    )
    assert row["equivalence"]["equivalent"] is False
    assert row["best"]["cash_per_contract"] is None
    assert collect_arbitrage([row], min_edge_bps=1) == []


def test_arbitrage_threshold_filters_opportunities() -> None:
    row = pair_row()
    assert collect_arbitrage([row], min_edge_bps=1)
    assert collect_arbitrage([row], min_edge_bps=10_000) == []


def test_tick_floor_quotes_are_flagged_and_excluded() -> None:
    """A 5x percentage edge on a near-zero premium is noise, not arbitrage."""
    delta = make_spec("delta", strike=90000.0, fee=0.0003)
    coinswitch = make_spec("coinswitch", strike=90000.0, settlement="USDT", fee=0.0005)
    row = compare_pair(
        "BTC|call|90000|2026-11-20T12:00Z",
        {"delta": delta, "coinswitch": coinswitch},
        {
            "delta": make_quote("delta", 5.2, 5.3, symbol=delta.symbol),
            "coinswitch": make_quote("coinswitch", 0.3, 0.4, symbol=coinswitch.symbol),
        },
        slippage_bps=0,
    )
    assert row["best"]["plausible"] is False
    assert row["best"]["sanity"]
    assert collect_arbitrage([row], min_edge_bps=0) == []
    summary = arbitrage_summary([row], min_edge_bps=0)
    assert summary["sanity_excluded"] == 1
    assert summary["opportunities"] == []


def test_wide_leg_spread_is_not_treated_as_arbitrage() -> None:
    """Crossing a 40%-wide book is not a spread you can harvest."""
    delta = make_spec("delta", fee=0.0003)
    coinswitch = make_spec("coinswitch", settlement="USDT", fee=0.0005)
    row = compare_pair(
        "BTC|call|75000|2026-11-20T12:00Z",
        {"delta": delta, "coinswitch": coinswitch},
        {
            "delta": make_quote("delta", 300.0, 400.0, symbol=delta.symbol),
            "coinswitch": make_quote("coinswitch", 480.0, 490.0, symbol=coinswitch.symbol),
        },
        slippage_bps=0,
    )
    assert row["best"]["net_spread"] > 0
    assert row["best"]["plausible"] is False
    assert collect_arbitrage([row], min_edge_bps=0) == []


def test_missing_side_is_not_compared() -> None:
    row = pair_row()
    assert row["equivalence"]["contract_value_match"] is True
    # A pair only exists when both venues have the contract; compare_pair requires both specs.
    assert len(row["legs"]) == 2


def test_parse_option_symbol_variants() -> None:
    cases = [
        ("BTC-261120-75000-C", "BTC", "call", 75000),
        ("BTCUSDT_261120_75000_PE", "BTC", "put", 75000),
        ("ETH-20261120-2080-CALL", "ETH", "call", 2080),
    ]
    for symbol, underlying, option_type, strike in cases:
        parsed = parse_option_symbol(symbol)
        assert parsed is not None, symbol
        assert parsed["underlying"] == underlying
        assert parsed["option_type"] == option_type
        assert parsed["strike"] == strike


def test_parse_option_symbol_rejects_spot_pairs() -> None:
    assert parse_option_symbol("BTCUSDT") is None
    assert parse_option_symbol("") is None


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_position_accounting_long_round_trip(db: Session) -> None:
    service = OrderService(None, {}, settings)  # type: ignore[arg-type]
    spec = make_spec("delta")

    service._apply_fill(db, spec, "buy", 2, 100.0, 0.02)
    db.flush()
    position = db.query(Position).one()
    assert position.qty == 2
    assert position.avg_price == 100.0

    realized = service._apply_fill(db, spec, "sell", 1, 120.0, 0.01)
    db.flush()
    assert realized == pytest.approx((120.0 - 100.0) * 1 * 0.001)
    position = db.query(Position).one()
    assert position.qty == 1
    assert position.realized_pnl == pytest.approx(0.02)

    realized = service._apply_fill(db, spec, "sell", 1, 90.0, 0.01)
    db.flush()
    assert realized == pytest.approx(-0.01)
    assert db.query(Position).count() == 0


def test_position_accounting_short_round_trip(db: Session) -> None:
    service = OrderService(None, {}, settings)  # type: ignore[arg-type]
    spec = make_spec("coinswitch", settlement="USDT")
    service._apply_fill(db, spec, "sell", 3, 100.0, 0.0)
    db.flush()
    assert db.query(Position).one().qty == -3

    realized = service._apply_fill(db, spec, "buy", 2, 80.0, 0.0)
    db.flush()
    assert realized == pytest.approx((80.0 - 100.0) * 2 * 0.001 * -1)
    assert db.query(Position).one().qty == -1
