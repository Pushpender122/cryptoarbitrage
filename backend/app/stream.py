"""WebSocket fan-out: per-client market snapshots plus portfolio change pings."""
from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from .config import Settings
from .models import RiskSettings
from .services.market import MarketService

logger = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, dict] = {}
        self._lock = asyncio.Lock()

    def add(self, websocket: WebSocket, params: dict) -> None:
        self.clients[websocket] = params

    def update(self, websocket: WebSocket, params: dict) -> None:
        if websocket in self.clients:
            self.clients[websocket].update(params)

    def remove(self, websocket: WebSocket) -> None:
        self.clients.pop(websocket, None)

    async def broadcast(self, payload: dict) -> None:
        async with self._lock:
            sockets = list(self.clients.items())
        for websocket, _ in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001 - dropped sockets clean themselves up
                self.remove(websocket)

    async def portfolio(self) -> None:
        await self.broadcast({"type": "portfolio"})

    async def send_ticks(self, market: MarketService, risk_settings: RiskSettings, cfg: Settings) -> None:
        async with self._lock:
            sockets = list(self.clients.items())
        if not sockets:
            return

        combos: dict[tuple, list[WebSocket]] = {}
        for websocket, params in sockets:
            key = (
                params.get("underlying", "BTC"),
                params.get("expiry"),
                params.get("option_type"),
                float(params.get("min_edge_bps", risk_settings.min_edge_bps)),
            )
            combos.setdefault(key, []).append(websocket)

        for (underlying, expiry, option_type, min_edge), targets in combos.items():
            snapshot = market.snapshot(
                underlying=underlying,
                expiry=expiry,
                option_type=option_type,
                min_edge_bps=min_edge,
                slippage_bps=risk_settings.slippage_bps,
            )
            payload = {
                "type": "tick",
                "mode": "paper" if cfg.paper else "live",
                "risk": {
                    "kill_switch": risk_settings.kill_switch,
                    "trading_enabled": risk_settings.trading_enabled,
                    "auto_risk_exits": risk_settings.auto_risk_exits,
                },
                **snapshot,
            }
            for websocket in targets:
                try:
                    await websocket.send_json(payload)
                except Exception:  # noqa: BLE001
                    self.remove(websocket)
