from types import SimpleNamespace

import pytest

from app.services.orders import OrderError, OrderService


def _spec():
    return SimpleNamespace(min_qty=0.1, qty_step=0.1, tick_size=0.05, exchange="coinswitch")


def test_order_quantity_accepts_valid_lot_and_tick():
    OrderService._validate_contract_constraints(_spec(), 0.3, "limit", 1.25)


def test_order_quantity_rejects_below_minimum():
    with pytest.raises(OrderError, match="at least"):
        OrderService._validate_contract_constraints(_spec(), 0.05, "market", None)


def test_order_quantity_rejects_invalid_lot_step():
    with pytest.raises(OrderError, match="multiple of 0.1"):
        OrderService._validate_contract_constraints(_spec(), 0.25, "market", None)


def test_limit_order_rejects_invalid_tick_size():
    with pytest.raises(OrderError, match="multiple of 0.05"):
        OrderService._validate_contract_constraints(_spec(), 0.3, "limit", 1.23)
