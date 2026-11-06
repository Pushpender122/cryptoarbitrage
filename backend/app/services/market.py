"""Market data aggregation: poll venues, mirror the simulated leg, serve snapshots."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings
from ..exchanges.base import ContractSpec, MarketDataProvider, Quote, iso_utc
from ..exchanges.simulated import SimulatedProvider
from .matching import arbitrage_summary, collect_arbitrage, compare_pair

logger = logging.getLogger(__name__)


class MarketService:
    """Holds the latest contract/quote picture for every connected venue."""

    def __init__(
        self,
        cfg: Settings,
        providers: dict[str, MarketDataProvider],
        simulator: SimulatedProvider | None,
    ):
        self.cfg = cfg
        self.providers = providers
        self.simulator = simulator
        self.contracts: dict[str, dict[str, ContractSpec]] = {name: {} for name in providers}
        self.quotes: dict[str, dict[str, Quote]] = {name: {} for name in providers}
        self.spots: dict[str, float] = {}
        self.contract_ts: dict[str, datetime] = {}
        self.updated_at: datetime | None = None
        self.refresh_count = 0
        self._rows_cache: dict[tuple[str, float], list[dict[str, Any]]] = {}
        self._row_map: dict[str, dict[str, Any]] = {}
        self.unmatched: dict[str, int] = {name: 0 for name in providers}
        self.edge_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=240))

    # ------------------------------------------------------------------ refresh
    async def refresh(self, *, slippage_bps: float = 15.0, force: bool = False) -> None:
        now = datetime.now(timezone.utc)
        contract_ttl = 90.0

        for name, provider in self.providers.items():
            if provider is self.simulator:
                continue
            last = self.contract_ts.get(name)
            stale = last is None or (now - last).total_seconds() > contract_ttl
            if not (force or stale or not self.contracts[name]):
                continue
            try:
                specs = await provider.fetch_contracts()
                self.contracts[name] = {spec.symbol: spec for spec in specs}
                self.contract_ts[name] = now
            except Exception as exc:  # noqa: BLE001 - reported through provider.status()
                provider.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("%s contract fetch failed: %s", name, exc)

        live = [(name, provider) for name, provider in self.providers.items() if provider is not self.simulator]
        results = await asyncio.gather(
            *(self._safe_quotes(provider) for _, provider in live), return_exceptions=True
        )
        for (name, provider), result in zip(live, results):
            if isinstance(result, BaseException):
                provider.last_error = f"{type(result).__name__}: {result}"
                logger.warning("%s quote fetch failed: %s", name, result)
                continue
            self.quotes[name] = {quote.symbol: quote for quote in result}
            provider.last_error = None

        for name, provider in live:
            try:
                self.spots.update(provider.spots())
            except Exception:  # noqa: BLE001 - spot is best effort
                pass
        for underlying in self.cfg.underlyings:
            self.spots.setdefault(
                underlying,
                self.cfg.btc_spot_fallback if underlying == "BTC" else self.cfg.eth_spot_fallback,
            )

        if self.simulator is not None:
            reference = self._reference_specs()
            mids, ivs = self._reference_anchors(reference)
            specs, quotes = self.simulator.refresh(reference, self.spots, mids, ivs)
            self.contracts[self.simulator.name] = {spec.symbol: spec for spec in specs}
            self.quotes[self.simulator.name] = {quote.symbol: quote for quote in quotes}

        self.updated_at = now
        self.refresh_count += 1
        self._rows_cache.clear()
        self._row_map = {}
        self._record_history(slippage_bps)

    async def _safe_quotes(self, provider: MarketDataProvider) -> list[Quote]:
        return await provider.fetch_quotes()

    def _reference_specs(self) -> list[ContractSpec]:
        for name in ("delta", "coinswitch"):
            if name == (self.simulator.name if self.simulator else None):
                continue
            specs = list(self.contracts.get(name, {}).values())
            if specs and any(quote.source == "live" for quote in self.quotes.get(name, {}).values()):
                return specs
        return []

    def _reference_anchors(
        self, reference: list[ContractSpec]
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Live mid prices and implied vols used to anchor the simulated leg."""
        mids: dict[str, float] = {}
        ivs: dict[str, float] = {}
        for name in self.providers:
            if name == (self.simulator.name if self.simulator else None):
                continue
            quotes = self.quotes.get(name, {})
            for spec in reference:
                quote = quotes.get(spec.symbol)
                if quote is None:
                    continue
                key = spec.key_string()
                if quote.mid is not None and quote.mid > 0:
                    mids[key] = quote.mid
                if quote.mark_iv is not None and 0.01 < quote.mark_iv < 5.0:
                    ivs[key] = quote.mark_iv
        return mids, ivs

    def _record_history(self, slippage_bps: float) -> None:
        for underlying in self.cfg.underlyings:
            for row in self.rows(underlying, slippage_bps):
                best = row.get("best")
                if not best:
                    continue
                self.edge_history[row["key"]].append(
                    {
                        "ts": iso_utc(self.updated_at or datetime.now(timezone.utc)),
                        "edge_bps": round(best["net_edge_bps"], 3),
                        "net_spread": round(best["net_spread"], 6),
                        "buy_exchange": best["buy_exchange"],
                        "sell_exchange": best["sell_exchange"],
                    }
                )

    # ------------------------------------------------------------------ lookups
    def spec(self, exchange: str, symbol: str) -> ContractSpec | None:
        return self.contracts.get(exchange, {}).get(symbol)

    def quote(self, exchange: str, symbol: str) -> Quote | None:
        return self.quotes.get(exchange, {}).get(symbol)

    def quote_for(self, exchange: str, symbol: str) -> Quote | None:
        return self.quote(exchange, symbol)

    def counterpart(
        self,
        exchange: str,
        *,
        underlying: str,
        option_type: str,
        strike: float,
        expiry: datetime,
    ) -> ContractSpec | None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        target = (
            underlying.upper(),
            option_type,
            round(strike, 4),
            expiry.astimezone(timezone.utc).replace(second=0, microsecond=0),
        )
        for spec in self.contracts.get(exchange, {}).values():
            if spec.match_key() == target:
                return spec
        return None

    def rows(self, underlying: str, slippage_bps: float = 15.0) -> list[dict[str, Any]]:
        cache_key = (underlying, slippage_bps)
        cached = self._rows_cache.get(cache_key)
        if cached is not None:
            return cached

        grouped: dict[str, dict[str, ContractSpec]] = defaultdict(dict)
        for name in self.providers:
            for spec in self.contracts.get(name, {}).values():
                if spec.underlying.upper() != underlying.upper():
                    continue
                grouped[spec.key_string()][name] = spec

        rows: list[dict[str, Any]] = []
        unmatched = {name: 0 for name in self.providers}
        for key, specs in grouped.items():
            if len(specs) < len(self.providers):
                for name in self.providers:
                    if name not in specs:
                        unmatched[name] += 1
                continue
            quotes: dict[str, Quote] = {}
            for name, spec in specs.items():
                quote = self.quote(name, spec.symbol)
                if quote is None:
                    unmatched[name] += 1
                    quotes = {}
                    break
                quotes[name] = quote
            if not quotes:
                continue
            row = compare_pair(key, specs, quotes, slippage_bps=slippage_bps)
            rows.append(row)
            self._row_map[row["key"]] = row

        rows.sort(key=lambda r: (r["expiry"], r["strike"], r["option_type"]))
        self._rows_cache[cache_key] = rows
        self.unmatched = unmatched
        return rows

    def row(self, key: str, slippage_bps: float = 15.0) -> dict[str, Any] | None:
        existing = self._row_map.get(key)
        if existing is not None:
            return existing
        for underlying in self.cfg.underlyings:
            for row in self.rows(underlying, slippage_bps):
                if row["key"] == key:
                    return row
        return None

    def expiries(self, underlying: str, slippage_bps: float = 15.0) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for row in self.rows(underlying, slippage_bps):
            counts[row["expiry"]] += 1
        return [{"expiry": expiry, "pairs": count} for expiry, count in sorted(counts.items())]

    def history(self, key: str) -> list[dict[str, Any]]:
        return list(self.edge_history.get(key, ()))

    # ----------------------------------------------------------------- snapshot
    def indexes(self, slippage_bps: float = 15.0, min_edge_bps: float = 25.0) -> list[dict[str, Any]]:
        out = []
        for underlying in self.cfg.underlyings:
            rows = self.rows(underlying, slippage_bps)
            opportunity = collect_arbitrage(rows, min_edge_bps=min_edge_bps, limit=1)
            sources: dict[str, str] = {}
            for name in self.providers:
                quotes = [
                    quote
                    for spec in self.contracts.get(name, {}).values()
                    if spec.underlying.upper() == underlying
                    for quote in [self.quote(name, spec.symbol)]
                    if quote is not None
                ]
                if any(quote.source == "live" for quote in quotes):
                    sources[name] = "live"
                elif quotes:
                    sources[name] = "simulated"
                else:
                    sources[name] = "unavailable"
            out.append(
                {
                    "underlying": underlying,
                    "spot": round(self.spots.get(underlying, 0.0), 4) or None,
                    "pairs": len(rows),
                    "max_edge_bps": round(opportunity[0]["net_edge_bps"], 2) if opportunity else None,
                    "sources": sources,
                }
            )
        return out

    def snapshot(
        self,
        *,
        underlying: str = "BTC",
        expiry: str | None = None,
        option_type: str | None = None,
        min_edge_bps: float = 25.0,
        slippage_bps: float = 15.0,
    ) -> dict[str, Any]:
        underlying = underlying.upper()
        rows_all = self.rows(underlying, slippage_bps)
        arbitrage = arbitrage_summary(rows_all, min_edge_bps=min_edge_bps)
        expiries = self.expiries(underlying, slippage_bps)
        available = [item["expiry"] for item in expiries]
        selected = expiry if expiry in available else (available[0] if available else None)
        rows = [
            row
            for row in rows_all
            if (selected is None or row["expiry"] == selected)
            and (option_type is None or row["option_type"] == option_type)
        ]
        sources = {}
        for name in self.providers:
            quotes = [self.quote(name, spec.symbol) for spec in self.contracts.get(name, {}).values()]
            quotes = [quote for quote in quotes if quote is not None]
            sources[name] = (
                "live"
                if any(quote.source == "live" for quote in quotes)
                else "simulated"
                if quotes
                else "unavailable"
            )
        matched_keys = {row["key"] for row in rows_all}
        total_keys: set[str] = set()
        per_exchange_symbols: dict[str, int] = {}
        for name in self.providers:
            symbols = [
                spec
                for spec in self.contracts.get(name, {}).values()
                if spec.underlying.upper() == underlying
            ]
            per_exchange_symbols[name] = len(symbols)
            total_keys.update(spec.key_string() for spec in symbols)
        return {
            "as_of": iso_utc(self.updated_at or datetime.now(timezone.utc)),
            "underlying": underlying,
            "spot": round(self.spots.get(underlying, 0.0), 4) or None,
            "expiries": expiries,
            "selected_expiry": selected,
            "option_type": option_type,
            "rows": rows,
            "arbitrage": arbitrage["opportunities"],
            "arbitrage_stats": {
                "sanity_excluded": arbitrage["sanity_excluded"],
                "below_threshold": arbitrage["below_threshold"],
                "ineligible": arbitrage["ineligible"],
            },
            "indexes": self.indexes(slippage_bps, min_edge_bps),
            "sources": sources,
            "stats": {
                "matched_pairs": len(matched_keys),
                "total_contract_keys": len(total_keys),
                "unmatched_keys": len(total_keys - matched_keys),
                "contracts": per_exchange_symbols,
                "refresh_count": self.refresh_count,
            },
        }

    def stale(self, max_age_seconds: float = 10.0) -> bool:
        if self.updated_at is None:
            return True
        return (datetime.now(timezone.utc) - self.updated_at) > timedelta(seconds=max_age_seconds)
