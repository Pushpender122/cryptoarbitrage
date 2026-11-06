from app.services.copytrading import CopyEngine


def test_copy_quantity_respects_fractional_lot_step():
    assert CopyEngine._sized_qty(2.75, 1.0, 10.0, 0.1, 0.1) == 2.7


def test_copy_quantity_rounds_down_instead_of_rounding_up():
    assert CopyEngine._sized_qty(2.8, 1.0, 10.0, 1.0, 1.0) == 2.0


def test_copy_quantity_never_exceeds_strategy_cap():
    assert CopyEngine._sized_qty(8.0, 1.0, 2.5, 0.1, 0.1) == 2.5


def test_copy_quantity_rejects_below_minimum():
    assert CopyEngine._sized_qty(0.09, 1.0, 10.0, 0.1, 0.1) == 0.0
