"""Provider wiring: real adapters where possible, simulated only where not."""
from __future__ import annotations

from ..config import Settings
from .base import MarketDataProvider
from .coinswitch import CoinSwitchProvider
from .delta import DeltaProvider
from .simulated import SimulatedProvider


def build_providers(cfg: Settings) -> tuple[dict[str, MarketDataProvider], SimulatedProvider | None]:
    providers: dict[str, MarketDataProvider] = {"delta": DeltaProvider(cfg)}
    simulator: SimulatedProvider | None = None

    if cfg.coinswitch_live_ready:
        providers["coinswitch"] = CoinSwitchProvider(cfg)
    elif cfg.coinswitch_simulated is False:
        providers["coinswitch"] = CoinSwitchProvider(cfg)
    else:
        simulator = SimulatedProvider(cfg)
        providers["coinswitch"] = simulator

    return providers, simulator
