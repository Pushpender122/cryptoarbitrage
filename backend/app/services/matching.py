"""Option contract comparison and arbitrage maths.

All functions here are pure so the engine can be unit-tested without exchanges.
Prices are quoted per unit of the underlying (Delta quotes BTC options in USD per
BTC); cash per contract is `price * contract_value`.
"""
from __future__ import annotations

from typing import Any

from ..exchanges.base import ContractSpec, Quote, iso_utc

SETTLEMENT_GROUPS = {"USD": "USD", "USDT": "USD", "USDC": "USD", "INR": "INR"}
EXCHANGES = ("delta", "coinswitch")

# Cross-venue option spreads beyond this are almost always stale, illiquid or
# tick-floor quotes rather than executable arbitrage. Flagged, never hidden.
MAX_PLAUSIBLE_EDGE_BPS = 800.0
MAX_LEG_SPREAD_BPS = 1500.0
MIN_PREMIUM_TICKS = 10.0


def settlement_group(currency: str) -> str:
    return SETTLEMENT_GROUPS.get((currency or "").upper(), (currency or "").upper())


def _spread_bps(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 10000


def compare_pair(
    key: str,
    specs: dict[str, ContractSpec],
    quotes: dict[str, Quote],
    *,
    slippage_bps: float,
) -> dict[str, Any]:
    """Compare one matched strike/expiry/type across both venues."""
    reference = specs.get("delta") or specs.get("coinswitch")
    assert reference is not None
    row: dict[str, Any] = {
        "key": key,
        "underlying": reference.underlying,
        "option_type": reference.option_type,
        "strike": reference.strike,
        "expiry": iso_utc(reference.expiry),
        "legs": {},
        "diff": {},
        "directions": {},
        "best": None,
        "equivalence": {"equivalent": False, "notes": []},
    }

    for exchange in EXCHANGES:
        spec = specs.get(exchange)
        quote = quotes.get(exchange)
        row["legs"][exchange] = {
            "spec": spec.to_dict() if spec else None,
            "quote": quote.to_dict() if quote else None,
            "spread_bps": _spread_bps(quote.bid, quote.ask) if quote else None,
            "source": quote.source if quote else None,
        }

    delta_spec, cs_spec = specs.get("delta"), specs.get("coinswitch")
    delta_quote, cs_quote = quotes.get("delta"), quotes.get("coinswitch")
    notes: list[str] = []

    contract_value_match = bool(
        delta_spec
        and cs_spec
        and abs(delta_spec.contract_value - cs_spec.contract_value)
        <= max(1e-12, 1e-6 * max(delta_spec.contract_value, cs_spec.contract_value))
    )
    settlement_normalized = False
    settlement_match = False
    if delta_spec and cs_spec:
        if delta_spec.settlement_currency.upper() == cs_spec.settlement_currency.upper():
            settlement_match = True
        elif settlement_group(delta_spec.settlement_currency) == settlement_group(cs_spec.settlement_currency):
            settlement_match = True
            settlement_normalized = True
            notes.append(
                f"Settlement differs ({delta_spec.settlement_currency} vs "
                f"{cs_spec.settlement_currency}); treated as USD-equivalent. Verify FX/depeg risk."
            )
        else:
            notes.append(
                f"Settlement currency mismatch: {delta_spec.settlement_currency} vs "
                f"{cs_spec.settlement_currency}. Contracts are not equivalent."
            )
        if not contract_value_match:
            notes.append(
                f"Contract size differs: {delta_spec.contract_value:g} {delta_spec.contract_unit} "
                f"vs {cs_spec.contract_value:g} {cs_spec.contract_unit} per contract."
            )
        for exchange, spec, quote in (
            ("Delta", delta_spec, delta_quote),
            ("CoinSwitch", cs_spec, cs_quote),
        ):
            if spec.expiry_assumed:
                notes.append(f"{exchange} expiry was inferred from the symbol; confirm expiry time.")
            if quote is not None and quote.source == "simulated":
                notes.append(f"{exchange} leg is a simulated book, not market data.")

    equivalent = bool(contract_value_match and settlement_match and len(EXCHANGES) == len(specs))

    # Price differences (exchange A minus exchange B).
    fields = {
        "ltp": lambda q: q.ltp,
        "bid": lambda q: q.bid,
        "ask": lambda q: q.ask,
        "mid": lambda q: q.mid,
        "mark_price": lambda q: q.mark_price,
        "mark_iv": lambda q: q.mark_iv,
        "oi": lambda q: q.oi,
        "volume": lambda q: q.volume,
    }
    for name, getter in fields.items():
        d_value = getter(delta_quote) if delta_quote else None
        c_value = getter(cs_quote) if cs_quote else None
        diff = None
        if d_value is not None and c_value is not None:
            diff = d_value - c_value
        row["diff"][name] = {"delta": d_value, "coinswitch": c_value, "diff": diff}

    # Both arbitrage directions, including fees and a slippage buffer.
    directions: dict[str, dict[str, Any]] = {}
    for name, buy_ex, sell_ex in (
        ("buy_delta_sell_coinswitch", "delta", "coinswitch"),
        ("buy_coinswitch_sell_delta", "coinswitch", "delta"),
    ):
        buy_quote, sell_quote = quotes.get(buy_ex), quotes.get(sell_ex)
        buy_spec, sell_spec = specs.get(buy_ex), specs.get(sell_ex)
        if not (buy_quote and sell_quote and buy_spec and sell_spec):
            continue
        buy_price, sell_price = buy_quote.ask, sell_quote.bid
        if not buy_price or not sell_price:
            continue
        gross = sell_price - buy_price
        fees = buy_price * buy_spec.taker_fee_rate + sell_price * sell_spec.taker_fee_rate
        slippage = (buy_price + sell_price) * slippage_bps / 10000.0
        net = gross - fees - slippage
        contract_value = buy_spec.contract_value if contract_value_match else None
        executable = min(
            buy_quote.ask_size or 0.0,
            sell_quote.bid_size or 0.0,
        )
        directions[name] = {
            "name": name,
            "buy_exchange": buy_ex,
            "sell_exchange": sell_ex,
            "buy_symbol": buy_spec.symbol,
            "sell_symbol": sell_spec.symbol,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "gross_spread": gross,
            "fees": fees,
            "slippage": slippage,
            "net_spread": net,
            "net_edge_bps": (net / buy_price * 10000.0) if buy_price else 0.0,
            "cash_per_contract": net * contract_value if contract_value else None,
            "executable_qty": executable,
            "live_legs": buy_quote.source == "live" and sell_quote.source == "live",
        }

    best = None
    if directions:
        best = max(directions.values(), key=lambda d: d["net_spread"])
        best = dict(best)
        buy_spec = specs.get(best["buy_exchange"])
        sell_spec = specs.get(best["sell_exchange"])
        tick = max(
            buy_spec.tick_size if buy_spec else 0.0,
            sell_spec.tick_size if sell_spec else 0.0,
        )
        sanity: list[str] = []
        if tick > 0 and min(best["buy_price"], best["sell_price"]) < MIN_PREMIUM_TICKS * tick:
            sanity.append(
                f"A premium is within {MIN_PREMIUM_TICKS:g} ticks of the minimum tick; "
                "a percentage edge here is noise, not a tradable spread."
            )
        if best["net_edge_bps"] > MAX_PLAUSIBLE_EDGE_BPS:
            sanity.append(
                f"Edge above {MAX_PLAUSIBLE_EDGE_BPS / 100:g}% usually means a stale or illiquid "
                "quote rather than a real cross-venue spread."
            )
        for exchange in (best["buy_exchange"], best["sell_exchange"]):
            leg_spread = row["legs"].get(exchange, {}).get("spread_bps")
            if leg_spread is not None and leg_spread > MAX_LEG_SPREAD_BPS:
                sanity.append(
                    f"The {exchange} book is {leg_spread / 100:.1f}% wide; crossing a spread that "
                    "wide is market noise, not arbitrage."
                )
                break
        best["equivalent"] = equivalent
        best["profitable"] = best["net_spread"] > 0
        best["sanity"] = sanity
        best["plausible"] = not sanity
    row["directions"] = directions
    row["best"] = best
    row["equivalence"] = {
        "equivalent": equivalent,
        "contract_value_match": contract_value_match,
        "settlement_match": settlement_match,
        "settlement_normalized": settlement_normalized,
        "notes": list(dict.fromkeys(notes)),
    }
    return row


def arbitrage_summary(
    rows: list[dict[str, Any]], *, min_edge_bps: float, limit: int = 25
) -> dict[str, Any]:
    """Rank opportunities and report why the rest were dropped."""
    opportunities: list[dict[str, Any]] = []
    sanity_excluded = 0
    below_threshold = 0
    ineligible = 0
    for row in rows:
        best = row.get("best")
        if not best or not row["equivalence"]["equivalent"]:
            ineligible += 1
            continue
        if best["net_edge_bps"] < min_edge_bps or best["executable_qty"] <= 0:
            below_threshold += 1
            continue
        if not best.get("plausible", True):
            sanity_excluded += 1
            continue
        opportunities.append(
            {
                "key": row["key"],
                "underlying": row["underlying"],
                "option_type": row["option_type"],
                "strike": row["strike"],
                "expiry": row["expiry"],
                **best,
            }
        )
    opportunities.sort(key=lambda o: o["net_edge_bps"], reverse=True)
    return {
        "opportunities": opportunities[:limit],
        "sanity_excluded": sanity_excluded,
        "below_threshold": below_threshold,
        "ineligible": ineligible,
    }


def collect_arbitrage(
    rows: list[dict[str, Any]], *, min_edge_bps: float, limit: int = 25
) -> list[dict[str, Any]]:
    return arbitrage_summary(rows, min_edge_bps=min_edge_bps, limit=limit)["opportunities"]
