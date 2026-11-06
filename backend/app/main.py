"""FastAPI entrypoint: wires providers, background polling, REST and SPA hosting."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api
from .config import FRONTEND_DIST, settings
from .db import SessionLocal, init_db
from .exchanges.base import iso_utc
from .exchanges.registry import build_providers
from .services import risk
from .services.copytrading import CopyEngine
from .services.market import MarketService
from .services.orders import OrderService
from .services.venue_sync import VenueSync
from .stream import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("terminal")


async def _poller(app: FastAPI) -> None:
    market: MarketService = app.state.market
    orders: OrderService = app.state.orders
    venue_sync: VenueSync = app.state.venue_sync
    hub: Hub = app.state.hub
    while True:
        try:
            with SessionLocal() as db:
                risk_settings = risk.get_settings(db)
                slippage_bps = risk_settings.slippage_bps
                auto_exits = risk_settings.auto_risk_exits
            await market.refresh(slippage_bps=slippage_bps)
            with SessionLocal() as db:
                await orders.reconcile_open_orders(db)
                if not settings.paper:
                    sync_result = await venue_sync.sync(db)
                    if sync_result.get("orders") or sync_result.get("fills") or sync_result.get("positions"):
                        await hub.portfolio()
                if auto_exits and settings.paper:
                    risk_settings = risk.get_settings(db)
                    for position_id, source in risk.evaluate_exits(db, market.quote_for, risk_settings):
                        try:
                            await orders.close_position(db, position_id, source=source)
                            logger.info("risk exit %s for position %s", source, position_id)
                        except Exception as exc:  # noqa: BLE001 - keep the loop alive
                            logger.warning("risk exit failed for position %s: %s", position_id, exc)
                risk_settings = risk.get_settings(db)
            await hub.send_ticks(market, risk_settings, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a poll failure must not stop the terminal
            logger.exception("market poll failed")
        await asyncio.sleep(max(0.25, settings.poll_ms / 1000.0))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    providers, simulator = build_providers(settings)
    market = MarketService(settings, providers, simulator)
    order_service = OrderService(market, providers, settings)
    order_service.copy_engine = CopyEngine(market, order_service)
    venue_sync = VenueSync(market, providers, settings, order_service)

    app.state.market = market
    app.state.orders = order_service
    app.state.venue_sync = venue_sync
    app.state.hub = Hub()
    app.state.providers = providers

    await market.refresh(slippage_bps=15.0, force=True)
    task = asyncio.create_task(_poller(app))
    logger.info(
        "terminal ready: mode=%s exchanges=%s coinswitch=%s",
        "paper" if settings.paper else "live",
        list(providers),
        "live" if settings.coinswitch_live_ready else "simulated",
    )
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for provider in providers.values():
            await provider.shutdown()


app = FastAPI(title="Crypto Options Arbitrage Terminal", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api.router)


@app.websocket("/ws")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    hub: Hub = app.state.hub
    market: MarketService = app.state.market
    params = {
        "underlying": websocket.query_params.get("underlying", "BTC").upper(),
        "expiry": websocket.query_params.get("expiry"),
        "option_type": websocket.query_params.get("option_type"),
        "min_edge_bps": float(websocket.query_params.get("min_edge_bps", 25)),
    }
    hub.add(websocket, params)
    try:
        with SessionLocal() as db:
            risk_settings = risk.get_settings(db)
        await websocket.send_json(
            {
                "type": "tick",
                "mode": "paper" if settings.paper else "live",
                **market.snapshot(
                    underlying=params["underlying"],
                    expiry=params["expiry"],
                    option_type=params["option_type"],
                    min_edge_bps=params["min_edge_bps"],
                    slippage_bps=risk_settings.slippage_bps,
                ),
            }
        )
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "config":
                update = {k: v for k, v in message.items() if k in params}
                if "underlying" in update and isinstance(update["underlying"], str):
                    update["underlying"] = update["underlying"].upper()
                if "min_edge_bps" in update:
                    try:
                        update["min_edge_bps"] = float(update["min_edge_bps"])
                    except (TypeError, ValueError):
                        update.pop("min_edge_bps")
                hub.update(websocket, update)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.debug("websocket closed", exc_info=True)
    finally:
        hub.remove(websocket)


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/")
    async def placeholder():
        return {
            "service": "options-terminal",
            "message": "Frontend not built. Run `npm --prefix frontend install && npm --prefix frontend run build`.",
            "time": iso_utc(datetime.now(timezone.utc)),
        }
