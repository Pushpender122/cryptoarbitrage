"""Delta Exchange India adapter (REST v2).

Market data is public. Trading needs an API key/secret created at
delta.exchange with "Trading" permission and (usually) an IP allowlist.

Signing: hex HMAC-SHA256 of `METHOD + timestamp + path + query + body`,
sent in the `api-key`, `timestamp` and `signature` headers.
Docs: https://docs.delta.exchange
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import Settings
from .base import (
    ContractSpec,
    MarketDataProvider,
    OrderAck,
    OrderRequest,
    ProviderError,
    Quote,
    VenueFill,
    VenueOrderStatus,
    VenuePositionSnapshot,
)

PRODUCTS_PATH = "/v2/products"
TICKERS_PATH = "/v2/tickers"
ORDERS_PATH = "/v2/orders"
FILLS_PATH = "/v2/fills"
POSITIONS_PATH = "/v2/positions/margined"

CONTRACT_TYPES = "call_options,put_options"


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_epoch(value: Any) -> datetime | None:
    """Delta timestamps are strings; v2 uses microseconds, older fields use seconds/ms."""
    if value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number > 10**16:  # nanoseconds
        number //= 1_000_000
    elif number > 10**14:  # microseconds
        number //= 1_000
    elif number < 10**11:  # seconds
        number *= 1_000
    return datetime.fromtimestamp(number / 1000, tz=timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DeltaProvider(MarketDataProvider):
    name = "delta"
    label = "Delta Exchange"
    live_market_data = True

    def __init__(self, cfg: Settings):
        self._cfg = cfg
        self.supports_trading = cfg.delta_live_ready
        self.note = (
            "Public v2 market data. Order routing uses your API key; keep it scoped to "
            "Trading permission and an IP allowlist."
        )
        self._client = httpx.AsyncClient(base_url=cfg.delta_base_url, timeout=20.0)
        self._symbols: set[str] = set()
        self._spots: dict[str, float] = {}

    async def shutdown(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- transport
    def _headers(self, method: str, path: str, query: str, payload: str) -> dict[str, str]:
        if not self._cfg.delta_live_ready:
            raise ProviderError("Delta API key/secret not configured", code="no_credentials")
        timestamp = str(int(time.time()))
        message = f"{method}{timestamp}{path}{query}{payload}"
        signature = hmac.new(
            self._cfg.delta_api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": self._cfg.delta_api_key,
            "timestamp": timestamp,
            "signature": signature,
            "Content-Type": "application/json",
            "User-Agent": "options-terminal/1.0",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        payload = "" if body is None else httpx._content.json_dumps(body)  # noqa: SLF001
        headers = (
            self._headers(method, path, query, payload)
            if auth
            else {"Content-Type": "application/json", "User-Agent": "options-terminal/1.0"}
        )
        started = time.perf_counter()
        try:
            response = await self._client.request(
                method,
                f"{path}{query}",
                headers=headers,
                content=payload or None,
            )
        except httpx.HTTPError as exc:
            self.last_error = f"transport: {exc}"
            raise ProviderError(f"Delta transport error: {exc}", retryable=True) from exc
        self.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"Delta returned non-JSON ({response.status_code})", retryable=response.status_code < 500 or response.status_code >= 500) from exc
        if response.status_code >= 400 or data.get("success") is False:
            message = data.get("error") or {}
            detail = message.get("context") or message.get("code") or response.text[:200]
            raise ProviderError(f"Delta error {response.status_code}: {detail}", retryable=response.status_code >= 500)
        self.last_ok_at = datetime.now(timezone.utc)
        self.last_error = None
        return data

    # -------------------------------------------------------------- market data
    async def fetch_contracts(self) -> list[ContractSpec]:
        data = await self._request(
            "GET", PRODUCTS_PATH, params={"contract_types": CONTRACT_TYPES, "states": "live"}
        )
        now = datetime.now(timezone.utc)
        specs: list[ContractSpec] = []
        for product in data.get("result") or []:
            underlying = ((product.get("underlying_asset") or {}).get("symbol") or "").upper()
            if underlying not in self._cfg.underlyings:
                continue
            contract_type = product.get("contract_type") or ""
            option_type = "call" if contract_type.startswith("call") else "put"
            strike = _f(product.get("strike_price"))
            expiry = _parse_time(product.get("settlement_time"))
            if not strike or not expiry or expiry <= now:
                continue
            settling = product.get("settling_asset") or {}
            quoting = product.get("quoting_asset") or {}
            specs.append(
                ContractSpec(
                    exchange=self.name,
                    symbol=product["symbol"],
                    exchange_id=str(product.get("id")),
                    underlying=underlying,
                    option_type=option_type,
                    strike=strike,
                    expiry=expiry,
                    contract_value=_f(product.get("contract_value")) or 1.0,
                    contract_unit=product.get("contract_unit_currency") or underlying,
                    settlement_currency=(settling.get("symbol") or "USD").upper(),
                    price_currency=(quoting.get("symbol") or settling.get("symbol") or "USD").upper(),
                    tick_size=_f(product.get("tick_size")) or 0.1,
                    taker_fee_rate=_f(product.get("taker_commission_rate")) or 0.0003,
                    maker_fee_rate=_f(product.get("maker_commission_rate")) or 0.0003,
                    min_qty=1.0,
                    qty_step=1.0,
                )
            )
        self._symbols = {spec.symbol for spec in specs}
        return specs

    async def fetch_quotes(self) -> list[Quote]:
        data = await self._request("GET", TICKERS_PATH, params={"contract_types": CONTRACT_TYPES})
        now = datetime.now(timezone.utc)
        quotes: list[Quote] = []
        spot_samples: dict[str, list[float]] = {}
        for ticker in data.get("result") or []:
            symbol = ticker.get("symbol") or ""
            if self._symbols and symbol not in self._symbols:
                continue
            book = ticker.get("quotes") or {}
            bid = _f(book.get("best_bid"))
            ask = _f(book.get("best_ask"))
            mark = _f(ticker.get("mark_price"))
            last = _f(ticker.get("close")) or _f(ticker.get("ltp"))
            quotes.append(
                Quote(
                    exchange=self.name,
                    symbol=symbol,
                    bid=bid,
                    ask=ask,
                    bid_size=_f(book.get("bid_size")),
                    ask_size=_f(book.get("ask_size")),
                    ltp=last,
                    mark_price=mark,
                    mark_iv=_f(book.get("mark_iv")),
                    oi=_f(ticker.get("oi_contracts")),
                    volume=_f(ticker.get("volume")),
                    ts=_parse_time(ticker.get("time")) or now,
                    source="live",
                )
            )
            spot = _f(ticker.get("spot_price"))
            if spot and "-" in symbol:
                underlying = symbol.split("-")[1].upper()
                spot_samples.setdefault(underlying, []).append(spot)
        if spot_samples:
            self._spots = {
                name: sorted(values)[len(values) // 2] for name, values in spot_samples.items()
            }
        return quotes

    def spots(self) -> dict[str, float]:
        return dict(self._spots)

    # ------------------------------------------------------------------ trading
    async def place_order(self, request: OrderRequest) -> OrderAck:
        body: dict[str, Any] = {
            "product_id": int(request.exchange_id),
            "side": request.side,
            "size": int(round(request.qty)),
            "order_type": "market_order" if request.order_type == "market" else "limit_order",
            "time_in_force": (request.time_in_force or "gtc").lower(),
            "reduce_only": bool(request.reduce_only),
        }
        if request.order_type == "limit" and request.limit_price is not None:
            body["limit_price"] = f"{request.limit_price:.8f}".rstrip("0").rstrip(".")
        if request.client_order_id:
            body["client_order_id"] = request.client_order_id[:32]
        data = await self._request("POST", ORDERS_PATH, body=body, auth=True)
        result = data.get("result") or {}
        return OrderAck(
            exchange_order_id=str(result.get("id")) if result.get("id") else None,
            status="accepted",
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> None:
        await self._request(
            "DELETE", ORDERS_PATH, body={"id": int(exchange_order_id)}, auth=True
        )

    async def fetch_order(
        self,
        exchange_order_id: str,
        symbol: str,
        *,
        client_order_id: str | None = None,
    ) -> VenueOrderStatus:
        if exchange_order_id:
            path = f"{ORDERS_PATH}/{exchange_order_id}"
        elif client_order_id:
            path = f"{ORDERS_PATH}/client_order_id/{client_order_id}"
        else:
            raise ProviderError("Delta needs an order id or client order id to poll status")
        data = await self._request("GET", path, auth=True)
        result = data.get("result") or {}
        size = _f(result.get("size")) or 0.0
        unfilled = _f(result.get("unfilled_size"))
        state = (result.get("state") or "").lower()
        if unfilled is not None:
            filled = max(0.0, size - unfilled)
        else:
            filled = size if state == "closed" else 0.0
        if state == "closed":
            status = "filled"
        elif state == "cancelled":
            status = "cancelled"
        elif state == "rejected":
            status = "rejected"
        elif state in ("open", "pending"):
            status = "partially_filled" if 0 < filled < size else "open"
        else:
            status = "unknown"
        fees = _f(result.get("paid_commission"))
        if fees is None:
            fees = _f(result.get("commission")) or 0.0
        return VenueOrderStatus(
            exchange_order_id=str(result.get("id") or exchange_order_id or ""),
            status=status,
            filled_qty=filled,
            avg_fill_price=_f(result.get("average_fill_price")),
            fees=fees or 0.0,
            venue_state=result.get("state"),
            reject_reason=result.get("cancellation_reason") if state == "rejected" else None,
            updated_at=_parse_epoch(result.get("updated_at")),
        )

    async def fetch_fills(
        self,
        exchange_order_id: str,
        symbol: str,
        *,
        product_id: str | None = None,
    ) -> list[VenueFill]:
        if not product_id:
            return []
        data = await self._request(
            "GET",
            FILLS_PATH,
            params={"product_ids": product_id, "page_size": 100},
            auth=True,
        )
        fills: list[VenueFill] = []
        for row in data.get("result") or []:
            if str(row.get("order_id")) != str(exchange_order_id):
                continue
            quantity = _f(row.get("size")) or 0.0
            price = _f(row.get("price")) or 0.0
            if quantity <= 0:
                continue
            fills.append(
                VenueFill(
                    exchange=self.name,
                    fill_id=str(row.get("id")),
                    exchange_order_id=str(row.get("order_id")),
                    symbol=row.get("product_symbol") or symbol,
                    side=(row.get("side") or "").lower(),
                    qty=quantity,
                    price=price,
                    fee=_f(row.get("commission")) or 0.0,
                    is_maker=(row.get("role") or "").lower() == "maker",
                    ts=_parse_epoch(row.get("created_at")) or datetime.now(timezone.utc),
                )
            )
        return fills

    async def fetch_position_snapshots(self) -> list[VenuePositionSnapshot]:
        data = await self._request("GET", POSITIONS_PATH, auth=True)
        snapshots: list[VenuePositionSnapshot] = []
        for row in data.get("result") or []:
            symbol = row.get("product_symbol") or ""
            size = _f(row.get("size")) or 0.0
            if not symbol or size == 0:
                continue
            if not symbol.startswith(("C-", "P-")):
                continue  # only options belong in this terminal
            snapshots.append(
                VenuePositionSnapshot(
                    exchange=self.name,
                    symbol=symbol,
                    size=size,
                    entry_price=_f(row.get("entry_price")) or 0.0,
                    mark_price=_f(row.get("mark_price")),
                    unrealized_pnl=_f(row.get("unrealized_pnl")) or 0.0,
                    realized_pnl=_f(row.get("realized_pnl")) or 0.0,
                    margin=_f(row.get("margin")),
                    liquidation_price=_f(row.get("liquidation_price")),
                    venue_updated_at=_parse_epoch(row.get("updated_at")),
                    raw=row,
                )
            )
        return snapshots

    def status(self) -> dict[str, Any]:
        status = super().status()
        status["market_data"] = "live"
        status["trading"] = "enabled" if self.supports_trading else "keys_required"
        status["rest_base"] = self._cfg.delta_base_url
        return status
