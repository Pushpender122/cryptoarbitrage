"""CoinSwitch PRO adapter (HFT API surface, `category=option`).

Options trading and options market data are a *private beta* allowlisted per
API key: unauthenticated or non-allowlisted calls return HTTP 401 with
`Signature validation failed`. See https://api-trading.coinswitch.co/options/access

Auth: Ed25519 signature over `METHOD + URL-decoded path + epoch_ms`, sent as
X-AUTH-APIKEY / X-AUTH-SIGNATURE / X-AUTH-EPOCH (hex-encoded key and signature).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

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

SESSION_PATH = "/v5/session"
INSTRUMENTS_PATH = "/v5/market/instruments-info"
TICKERS_PATH = "/v5/market/tickers"
ORDER_CREATE_PATH = "/v5/order/create"
ORDER_CANCEL_PATH = "/v5/order/cancel"
ORDER_REALTIME_PATH = "/v5/order/realtime"
EXECUTION_PATH = "/v5/execution/list"
POSITION_PATH = "/v5/position/list"

STATUS_MAP = {
    "New": "open",
    "PartiallyFilled": "partially_filled",
    "Filled": "filled",
    "Cancelled": "cancelled",
    "Rejected": "rejected",
}


def _from_ms(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number < 10**11:
        number *= 1000
    return datetime.fromtimestamp(number / 1000, tz=timezone.utc)

KIND_TOKENS = {"C": "call", "CE": "call", "CALL": "call", "P": "put", "PE": "put", "PUT": "put"}


def _parse_expiry_token(token: str) -> datetime | None:
    """Parse yymmdd / yyyymmdd / yyyy-mm-dd tokens as an assumed 12:00Z expiry."""
    if re.fullmatch(r"\d{6}", token):
        yy, mm, dd = token[:2], token[2:4], token[4:6]
        year = 2000 + int(yy)
    elif re.fullmatch(r"\d{8}", token):
        year, mm, dd = int(token[:4]), token[4:6], token[6:8]
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        year, mm, dd = int(token[:4]), token[5:7], token[8:10]
    else:
        return None
    try:
        return datetime(year, int(mm), int(dd), 12, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_option_symbol(symbol: str) -> dict[str, Any] | None:
    """Best-effort parse of an option symbol.

    CoinSwitch does not publish the options symbol grammar, so this handles the
    conventions used by its futures surface and peers (BASE-DATE-STRIKE-C/PE,
    BTCUSDT-YYMMDD-110000-C, ...). Instruments whose metadata carries explicit
    strike/expiry fields should be preferred over this parser.
    """
    if not symbol:
        return None
    tokens = [t for t in re.split(r"[-_/ ]+", symbol.strip()) if t]
    if not tokens:
        return None

    underlying = re.sub(r"(USDT|USD|INR|PERP)$", "", tokens[0].upper())
    kind = None
    expiry = None
    strike = None
    for token in tokens[1:]:
        upper = token.upper()
        if kind is None and upper in KIND_TOKENS:
            kind = KIND_TOKENS[upper]
            continue
        if expiry is None:
            parsed = _parse_expiry_token(token)
            if parsed is not None:
                expiry = parsed
                continue
        if strike is None:
            try:
                value = float(token)
            except ValueError:
                continue
            if value > 0:
                strike = value
    if not (underlying and kind and expiry and strike):
        return None
    return {"underlying": underlying, "option_type": kind, "expiry": expiry, "strike": strike}


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CoinSwitchProvider(MarketDataProvider):
    name = "coinswitch"
    label = "CoinSwitch PRO"
    note = (
        "Options API is private beta (allowlisted per key). Until access is granted, "
        "this leg runs on a simulated book that is labelled as such in the UI."
    )

    def __init__(self, cfg: Settings):
        self._cfg = cfg
        self.live_market_data = cfg.coinswitch_live_ready
        self.supports_trading = cfg.coinswitch_live_ready
        self._client: httpx.AsyncClient | None = None
        self._instrument_cache: dict[str, dict[str, Any]] = {}
        self._instrument_fetched_at: datetime | None = None
        self.unparsed_instruments = 0

    # ---------------------------------------------------------------- transport
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._cfg.coinswitch_base_url, timeout=20.0)
        return self._client

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _sign(self, method: str, path: str) -> dict[str, str]:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        epoch = str(int(time.time() * 1000))
        try:
            secret = bytes.fromhex(self._cfg.coinswitch_api_secret)
            if len(secret) != 32:
                raise ValueError("Ed25519 private key must decode to exactly 32 bytes")
            private_key = Ed25519PrivateKey.from_private_bytes(secret)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            raise ProviderError(f"CoinSwitch Ed25519 secret must be a 32-byte hex private key: {exc}") from exc
        from urllib.parse import unquote_plus

        decoded_path = unquote_plus(path)
        message = f"{method.upper()}{decoded_path}{epoch}".encode()
        signature = private_key.sign(message).hex()
        return {
            "X-AUTH-APIKEY": self._cfg.coinswitch_api_key,
            "X-AUTH-SIGNATURE": signature,
            "X-AUTH-EPOCH": epoch,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, *, body: dict | None = None) -> dict:
        if not (self._cfg.coinswitch_api_key and self._cfg.coinswitch_api_secret):
            raise ProviderError("CoinSwitch API key/secret not configured", code="no_credentials")
        headers = self._sign(method, path)
        started = time.perf_counter()
        try:
            response = await self.client.request(method, path, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError(f"CoinSwitch transport error: {exc}", retryable=True) from exc
        self.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if response.status_code == 401:
            raise ProviderError(
                "CoinSwitch rejected the signature or the key is not allowlisted for options",
                code="unauthorized",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"CoinSwitch returned non-JSON ({response.status_code})", retryable=True) from exc
        if response.status_code >= 400 or payload.get("retCode") not in (0, None):
            raise ProviderError(
                f"CoinSwitch error {payload.get('retCode')}: {payload.get('retMsg') or response.text[:200]}",
                code=str(payload.get("retCode")),
                retryable=response.status_code >= 500,
            )
        return payload

    # -------------------------------------------------------------- market data
    async def fetch_contracts(self) -> list[ContractSpec]:
        instruments = await self._instruments()
        specs: list[ContractSpec] = []
        self.unparsed_instruments = 0
        for item in instruments.values():
            spec = self._spec_from_instrument(item)
            if spec is not None:
                specs.append(spec)
        return specs

    async def _instruments(self) -> dict[str, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        if self._instrument_fetched_at and now - self._instrument_fetched_at < timedelta(minutes=10):
            return self._instrument_cache
        payload = await self._request("GET", f"{INSTRUMENTS_PATH}?category=option")
        rows = (payload.get("result") or {}).get("list") or []
        cache: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = row.get("symbol")
            if symbol:
                cache[symbol] = row
        self._instrument_cache = cache
        self._instrument_fetched_at = now
        self.last_ok_at = now
        self.last_error = None
        return cache

    def _spec_from_instrument(self, row: dict[str, Any]) -> ContractSpec | None:
        symbol = row.get("symbol", "")
        parsed = parse_option_symbol(symbol) or {}
        underlying = (row.get("baseCoin") or parsed.get("underlying") or "").upper()
        option_type = parsed.get("option_type")
        options_type = str(row.get("optionsType") or "").lower()
        if options_type in {"call", "put"}:
            option_type = options_type
        expiry = parsed.get("expiry")
        delivery = row.get("deliveryTime") or row.get("expiryTime")
        expiry_assumed = True
        if isinstance(delivery, (int, float)) and delivery > 0:
            expiry = datetime.fromtimestamp(delivery / 1000, tz=timezone.utc)
            expiry_assumed = False
        strike = _to_float(row.get("strikePrice")) or parsed.get("strike")
        contract_value = _to_float(row.get("contractValue")) or 0.0
        if not contract_value:
            contract_value = _to_float(row.get("lotSize")) or 1.0
        if not (underlying and option_type and expiry and strike):
            self.unparsed_instruments += 1
            return None
        if underlying not in self._cfg.underlyings:
            return None
        tick = _to_float((row.get("priceFilter") or {}).get("tickSize")) or 0.1
        lot = row.get("lotSizeFilter") or {}
        return ContractSpec(
            exchange=self.name,
            symbol=symbol,
            exchange_id=symbol,
            underlying=underlying,
            option_type=option_type,
            strike=float(strike),
            expiry=expiry,
            contract_value=contract_value,
            contract_unit=underlying,
            settlement_currency=(row.get("settleCoin") or "USDT").upper(),
            price_currency=(row.get("quoteCoin") or "USDT").upper(),
            tick_size=tick,
            taker_fee_rate=self._cfg.coinswitch_taker_fee,
            maker_fee_rate=self._cfg.coinswitch_taker_fee,
            min_qty=_to_float(lot.get("minOrderQty")) or 1.0,
            qty_step=_to_float(lot.get("qtyStep")) or 1.0,
            expiry_assumed=expiry_assumed,
        )

    async def fetch_quotes(self) -> list[Quote]:
        payload = await self._request("GET", f"{TICKERS_PATH}?category=option")
        result = payload.get("result") or {}
        rows = result.get("list") if isinstance(result, dict) else result
        now = datetime.now(timezone.utc)
        quotes: list[Quote] = []
        for row in rows or []:
            symbol = row.get("symbol")
            if not symbol:
                continue
            quotes.append(
                Quote(
                    exchange=self.name,
                    symbol=symbol,
                    bid=_to_float(row.get("bid1Price") or row.get("bidPrice")),
                    ask=_to_float(row.get("ask1Price") or row.get("askPrice")),
                    bid_size=_to_float(row.get("bid1Size") or row.get("bidSize")),
                    ask_size=_to_float(row.get("ask1Size") or row.get("askSize")),
                    ltp=_to_float(row.get("lastPrice") or row.get("ltp")),
                    mark_price=_to_float(row.get("markPrice")),
                    mark_iv=_to_float(row.get("markIv") or row.get("iv")),
                    oi=_to_float(row.get("openInterest")),
                    volume=_to_float(row.get("volume24h") or row.get("turnover24h")),
                    ts=now,
                    source="live",
                )
            )
        self.last_ok_at = now
        self.last_error = None
        return quotes

    # ------------------------------------------------------------------ trading
    async def place_order(self, request: OrderRequest) -> OrderAck:
        body = {
            "category": "option",
            "symbol": request.symbol,
            "side": "Buy" if request.side == "buy" else "Sell",
            "orderType": "Market" if request.order_type == "market" else "Limit",
            "qty": self._fmt(request.qty),
            "positionIdx": 0,
            "timeInForce": (request.time_in_force or "gtc").upper(),
        }
        if request.order_type == "limit" and request.limit_price is not None:
            body["price"] = self._fmt(request.limit_price)
        if request.client_order_id:
            body["orderLinkId"] = request.client_order_id
        payload = await self._request("POST", ORDER_CREATE_PATH, body=body)
        result = payload.get("result") or {}
        return OrderAck(
            exchange_order_id=str(result.get("orderId") or "") or None,
            status="accepted",
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> None:
        await self._request(
            "POST",
            ORDER_CANCEL_PATH,
            body={"category": "option", "symbol": symbol, "orderId": exchange_order_id},
        )

    async def fetch_order(
        self,
        exchange_order_id: str,
        symbol: str,
        *,
        client_order_id: str | None = None,
    ) -> VenueOrderStatus:
        from urllib.parse import urlencode

        params: dict[str, str] = {"category": "option"}
        if exchange_order_id:
            params["orderId"] = exchange_order_id
        elif client_order_id:
            params["orderLinkId"] = client_order_id
        else:
            raise ProviderError("CoinSwitch needs an order id or order link id to poll status")
        payload = await self._request("GET", f"{ORDER_REALTIME_PATH}?{urlencode(params)}")
        rows = (payload.get("result") or {}).get("list") or []
        row = next((item for item in rows if item.get("orderId") == exchange_order_id), rows[0] if rows else None)
        if row is None:
            raise ProviderError("Order not found in CoinSwitch realtime orders", code="not_found")
        filled = _to_float(row.get("cumExecQty")) or 0.0
        status = STATUS_MAP.get(str(row.get("orderStatus")), "unknown")
        if status == "open" and filled > 0:
            status = "partially_filled"
        reason = row.get("rejectReason")
        return VenueOrderStatus(
            exchange_order_id=str(row.get("orderId") or exchange_order_id),
            status=status,
            filled_qty=filled,
            avg_fill_price=_to_float(row.get("avgPrice")) or None,
            fees=_to_float(row.get("cumExecFee")) or 0.0,
            venue_state=str(row.get("orderStatus") or ""),
            reject_reason=reason if reason and reason != "EC_NoError" else None,
            updated_at=_from_ms(row.get("updatedTime")),
        )

    async def fetch_fills(
        self,
        exchange_order_id: str,
        symbol: str,
        *,
        product_id: str | None = None,
    ) -> list[VenueFill]:
        from urllib.parse import urlencode

        query = urlencode({"category": "option", "orderId": exchange_order_id})
        payload = await self._request("GET", f"{EXECUTION_PATH}?{query}")
        rows = (payload.get("result") or {}).get("list") or []
        fills: list[VenueFill] = []
        for row in rows:
            quantity = _to_float(row.get("execQty")) or 0.0
            price = _to_float(row.get("execPrice")) or 0.0
            if quantity <= 0 or not row.get("execId"):
                continue
            fills.append(
                VenueFill(
                    exchange=self.name,
                    fill_id=str(row["execId"]),
                    exchange_order_id=str(row.get("orderId") or exchange_order_id),
                    symbol=str(row.get("symbol") or symbol),
                    side=str(row.get("side") or "").lower(),
                    qty=quantity,
                    price=price,
                    fee=_to_float(row.get("execFee")) or 0.0,
                    is_maker=bool(row.get("isMaker")),
                    ts=_from_ms(row.get("execTime")) or datetime.now(timezone.utc),
                )
            )
        return fills

    async def fetch_position_snapshots(self) -> list[VenuePositionSnapshot]:
        payload = await self._request("GET", f"{POSITION_PATH}?category=option")
        rows = (payload.get("result") or {}).get("list") or []
        snapshots: list[VenuePositionSnapshot] = []
        for row in rows:
            quantity = _to_float(row.get("size")) or 0.0
            if quantity == 0:
                continue
            side = str(row.get("side") or "").lower()
            signed = -quantity if side == "sell" else quantity
            snapshots.append(
                VenuePositionSnapshot(
                    exchange=self.name,
                    symbol=str(row.get("symbol") or ""),
                    size=signed,
                    entry_price=_to_float(row.get("avgPrice")) or 0.0,
                    mark_price=_to_float(row.get("markPrice")),
                    unrealized_pnl=_to_float(row.get("unrealisedPnl")) or 0.0,
                    realized_pnl=_to_float(row.get("cumRealisedPnl")) or 0.0,
                    margin=_to_float(row.get("positionIM")),
                    liquidation_price=None,
                    venue_updated_at=_from_ms(row.get("updatedTime")),
                    raw=row,
                )
            )
        return snapshots

    @staticmethod
    def _fmt(value: float) -> str:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    def status(self) -> dict[str, Any]:
        status = super().status()
        status.update(
            {
                "market_data": "live" if self.live_market_data else "unavailable",
                "trading": "enabled" if self.supports_trading else "allowlist_required",
                "options_access": "allowlisted" if self._cfg.coinswitch_options_allowlisted else "private_beta",
                "unparsed_instruments": self.unparsed_instruments,
                "access_url": "https://api-trading.coinswitch.co/options/access",
            }
        )
        return status


async def probe(provider: CoinSwitchProvider) -> None:
    """Connectivity smoke test used by scripts, not by the app."""
    try:
        await asyncio.gather(provider.fetch_contracts(), provider.fetch_quotes())
    except ProviderError as exc:
        provider.last_error = str(exc)
