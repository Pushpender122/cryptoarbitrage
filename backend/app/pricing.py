"""Black-76 pricing helpers (used by the simulated CoinSwitch book and analytics)."""
from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black76_price(forward: float, strike: float, t_years: float, vol: float, kind: str) -> float:
    """Undiscounted Black-76 price (r=0). `kind` is 'call' or 'put'."""
    intrinsic = max(forward - strike, 0.0) if kind == "call" else max(strike - forward, 0.0)
    if t_years <= 0 or vol <= 0 or forward <= 0 or strike <= 0:
        return intrinsic
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    if kind == "call":
        return forward * norm_cdf(d1) - strike * norm_cdf(d2)
    return strike * norm_cdf(-d2) - forward * norm_cdf(-d1)


def greeks(forward: float, strike: float, t_years: float, vol: float, kind: str) -> dict[str, float]:
    if t_years <= 0 or vol <= 0:
        return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * t_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    delta = norm_cdf(d1) if kind == "call" else norm_cdf(d1) - 1.0
    gamma = norm_pdf(d1) / (forward * vol * sqrt_t)
    vega = forward * norm_pdf(d1) * sqrt_t / 100.0
    theta = -forward * norm_pdf(d1) * vol / (2.0 * sqrt_t) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def implied_vol(price: float, forward: float, strike: float, t_years: float, kind: str) -> float | None:
    """Bisection IV for a quoted premium. Returns None when unsolvable."""
    if price <= 0 or t_years <= 0 or forward <= 0 or strike <= 0:
        return None
    intrinsic = max(forward - strike, 0.0) if kind == "call" else max(strike - forward, 0.0)
    if price < intrinsic:
        return None
    low, high = 0.01, 5.0
    for _ in range(80):
        mid = (low + high) / 2
        if black76_price(forward, strike, t_years, mid, kind) < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2
