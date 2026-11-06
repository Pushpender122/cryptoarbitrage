from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Position, RiskSettings
from app.services.risk import RiskError, pretrade_check


def _database(kill_switch=True):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(RiskSettings(id=1, kill_switch=kill_switch))
    db.add(
        Position(
            exchange="delta",
            symbol="BTC-TEST",
            underlying="BTC",
            option_type="call",
            strike=100000,
            expiry=datetime(2027, 1, 1),
            contract_value=1,
            qty=2,
            avg_price=100,
        )
    )
    db.commit()
    return engine, db


def _spec():
    return type(
        "Spec",
        (),
        {"exchange": "delta", "symbol": "BTC-TEST", "contract_value": 1},
    )()


def test_reduce_only_close_is_allowed_with_kill_switch_on():
    engine, db = _database(kill_switch=True)
    try:
        result = pretrade_check(
            db, _spec(), "sell", 2, 100, quote_for=lambda *_: None, reduce_only=True
        )
        assert result.kill_switch is True
    finally:
        db.close()
        engine.dispose()


def test_kill_switch_blocks_new_risk_but_not_reduce_only():
    engine, db = _database(kill_switch=True)
    try:
        with pytest.raises(RiskError, match="Kill switch"):
            pretrade_check(db, _spec(), "buy", 1, 100, quote_for=lambda *_: None)
    finally:
        db.close()
        engine.dispose()


def test_reduce_only_rejects_quantity_larger_than_position():
    engine, db = _database(kill_switch=True)
    try:
        with pytest.raises(RiskError, match="exceeds the open position"):
            pretrade_check(
                db, _spec(), "sell", 3, 100, quote_for=lambda *_: None, reduce_only=True
            )
    finally:
        db.close()
        engine.dispose()


def test_reduce_only_rejects_wrong_side():
    engine, db = _database(kill_switch=True)
    try:
        with pytest.raises(RiskError, match="must be sell"):
            pretrade_check(
                db, _spec(), "buy", 1, 100, quote_for=lambda *_: None, reduce_only=True
            )
    finally:
        db.close()
        engine.dispose()


def test_order_that_flips_position_is_risk_increasing():
    engine, db = _database(kill_switch=False)
    try:
        settings = db.get(RiskSettings, 1)
        settings.max_open_positions = 1
        db.commit()
        with pytest.raises(RiskError, match="Open position limit reached"):
            pretrade_check(db, _spec(), "sell", 3, 100, quote_for=lambda *_: None)
    finally:
        db.close()
        engine.dispose()
