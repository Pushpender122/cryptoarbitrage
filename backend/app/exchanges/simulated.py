"""Simulated order book standing in for an exchange until its API is available.

This module never claims to be live: every Quote it produces has
`source="simulated"`, every ContractSpec it copies is marked `expiry_assumed`
in reports, and the API surfaces `data_sources` so the UI can badge the leg.
It mirrors the *contract keys* of a real reference venue so the comparison
engine exercises the exact same matching path as production.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from ..config import Settings
from ..pricing import black76_price
from .base import ContractSpec, MarketDataProvider, Quote

BASE_VOL = {"BTC": 0.54, "ETH": 0.63}


def round_to_tick(value: float, tick: float) -> float:
    if tick <= 0:
        return value
    return round(round(value / tick) * tick, 10)


def moneyness_vol(forward: float, strike: float, t_years: float, base_vol: float, kind: str) -> float:
    """Simple skew surface: OTM puts bid up vol, wings bid up vol."""
    if forward <= 0 or strike <= 0 or t_years <= 0:
        return base_vol
    sd = max(base_vol * math.sqrt(t_years), 1e-6)
    z = math.log(strike / forward) / sd
    z = max(-4.0, min(4.0, z))
    skew = 1.0 - 0.045 * z + 0.012 * z * z
    if kind == "put" and z < 0:
        skew *= 1.0 + min(0.25, -z * 0.035)
    term = 0.92 + 0.10 * min(1.0, math.sqrt(t_years * 365) / 6.0)
    return max(0.15, base_vol * skew * term)


class SimulatedProvider(MarketDataProvider):
    """Prices a mirrored options chain with a Black-76 surface plus basis noise."""

    live_market_data = False
    supports_trading = False

    def __init__(self, cfg: Settings, *, name: str = "coinswitch", label: str = "CoinSwitch PRO", seed: int = 20261106):
        self.name = name
        self.label = label
        self.note = (
            "SIMULATED BOOK. CoinSwitch options market data is private beta (401 until "
            "allowlisted), so this leg is generated locally and labelled simulated everywhere."
        )
        self._cfg = cfg
        self._rng = random.Random(seed)
        self._basis: dict[str, float] = {}
        self._spread_bps: dict[str, float] = {}
        self._meta: dict[str, tuple[float, float]] = {}
        self._specs: list[ContractSpec] = []
        self._quotes: list[Quote] = []
        self.assumed_specs = True

    def _walk(self, key: str, sigma: float = 0.0025, clamp: float = 0.025) -> float:
        """Mean-reverting mid dislocation between the venues, kept plausible."""
        previous = self._basis.get(key, self._rng.gauss(0.0, sigma))
        value = 0.92 * previous + self._rng.gauss(0.0, sigma)
        if self._rng.random() < 0.008:
            value += self._rng.choice((-1.0, 1.0)) * self._rng.uniform(0.004, 0.012)
        value = max(-clamp, min(clamp, value))
        self._basis[key] = value
        return value

    def _spread_for(self, key: str, t_days: float, moneyness: float) -> float:
        cached = self._spread_bps.get(key)
        if cached is None:
            base = 45.0 + self._rng.uniform(0.0, 70.0)
            cached = base * (1.0 + min(1.4, abs(moneyness) * 6.0))
            self._spread_bps[key] = cached
        urgency = 1.0 + max(0.0, (2.0 - t_days)) * 0.15
        return cached * urgency

    def refresh(
        self,
        reference: list[ContractSpec],
        spots: dict[str, float],
        reference_mids: dict[str, float] | None = None,
        reference_ivs: dict[str, float] | None = None,
    ) -> tuple[list[ContractSpec], list[Quote]]:
        """Mirror `reference` contracts and price them against the same mid.

        The simulated leg is anchored to the live venue's tradable mid so the two
        venues do not differ by a systematic bias (e.g. one venue's mark sitting
        near its bid). Only the bounded basis walk creates price differences.
        """
        now = datetime.now(timezone.utc)
        specs: list[ContractSpec] = []
        quotes: list[Quote] = []
        reference_mids = reference_mids or {}
        reference_ivs = reference_ivs or {}
        for ref in reference:
            t_years = (ref.expiry - now).total_seconds() / (365.25 * 86400)
            if t_years <= 0 or t_years > 2.5:
                continue
            forward = spots.get(ref.underlying)
            if not forward or forward <= 0:
                forward = self._cfg.btc_spot_fallback if ref.underlying == "BTC" else self._cfg.eth_spot_fallback
            key = ref.key_string()
            base_vol = BASE_VOL.get(ref.underlying, 0.6)
            anchored_iv = reference_ivs.get(key)
            if anchored_iv is not None and 0.01 < anchored_iv < 5.0:
                vol = anchored_iv
            else:
                vol = moneyness_vol(forward, ref.strike, t_years, base_vol, ref.option_type)
            anchored_mid = reference_mids.get(key)
            if anchored_mid is not None and anchored_mid > 0:
                theoretical = anchored_mid
            else:
                theoretical = black76_price(forward, ref.strike, t_years, vol, ref.option_type)
            if theoretical <= 0:
                continue

            basis = self._walk(key)
            mark = max(ref.tick_size, theoretical * (1.0 + basis))
            moneyness = math.log(ref.strike / forward) if ref.strike > 0 else 0.0
            half_spread = self._spread_for(key, t_years * 365.25, moneyness) / 2.0 / 10000.0
            bid = round_to_tick(mark * (1.0 - half_spread), ref.tick_size)
            ask = round_to_tick(mark * (1.0 + half_spread), ref.tick_size)
            bid = max(ref.tick_size, bid)
            if ask <= bid:
                ask = round_to_tick(bid + ref.tick_size, ref.tick_size)
            ltp = round_to_tick(mark * (1.0 + self._rng.gauss(0.0, 0.0008)), ref.tick_size)

            stored_vol, stored_oi = self._meta.get(key, (0.0, float(self._rng.randint(150, 9000))))
            volume = stored_vol + self._rng.uniform(0.0, 4.0)
            oi = stored_oi + self._rng.gauss(0.0, 3.0)
            self._meta[key] = (volume, max(0.0, oi))

            specs.append(
                ContractSpec(
                    exchange=self.name,
                    symbol=f"{ref.underlying}-{ref.expiry:%y%m%d}-{ref.strike:g}-{'C' if ref.option_type == 'call' else 'P'}",
                    exchange_id=f"{ref.underlying}-{ref.expiry:%Y%m%d}-{ref.strike:g}-{ref.option_type}",
                    underlying=ref.underlying,
                    option_type=ref.option_type,
                    strike=ref.strike,
                    expiry=ref.expiry,
                    contract_value=ref.contract_value,
                    contract_unit=ref.contract_unit,
                    settlement_currency="USDT",
                    price_currency="USD",
                    tick_size=ref.tick_size,
                    taker_fee_rate=self._cfg.coinswitch_taker_fee,
                    maker_fee_rate=self._cfg.coinswitch_taker_fee,
                    min_qty=1.0,
                    qty_step=1.0,
                    expiry_assumed=True,
                )
            )
            quotes.append(
                Quote(
                    exchange=self.name,
                    symbol=specs[-1].symbol,
                    bid=bid,
                    ask=ask,
                    bid_size=float(self._rng.randint(2, 60)),
                    ask_size=float(self._rng.randint(2, 60)),
                    ltp=ltp,
                    mark_price=round_to_tick(mark, ref.tick_size),
                    mark_iv=round(vol, 6),
                    oi=round(oi),
                    volume=round(volume, 2),
                    ts=now,
                    source="simulated",
                )
            )
        self._specs = specs
        self._quotes = quotes
        self.last_ok_at = now
        return specs, quotes

    async def fetch_contracts(self) -> list[ContractSpec]:
        return list(self._specs)

    async def fetch_quotes(self) -> list[Quote]:
        return list(self._quotes)

    def status(self) -> dict:
        status = super().status()
        status.update(
            {
                "market_data": "simulated",
                "trading": "simulated",
                "options_access": "private_beta",
                "access_url": "https://api-trading.coinswitch.co/options/access",
                "assumed_specs": True,
            }
        )
        return status
