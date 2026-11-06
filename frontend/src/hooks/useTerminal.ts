import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type {
  Exchange,
  HistorySummaryDto,
  OptionType,
  OrderDto,
  OrderPayload,
  PairOrderResult,
  PortfolioSummaryDto,
  PositionDto,
  RiskDto,
  SnapshotDto,
  StatusDto,
  StrategyDto,
  VenuePositionDto,
  VenueSummaryDto,
} from "../types";

export interface TerminalQuery {
  underlying: string;
  expiry: string | null;
  optionType: OptionType | null;
  minEdgeBps: number;
}

export interface Toast {
  id: number;
  kind: "info" | "success" | "error";
  message: string;
}

export interface ActionResult {
  ok: boolean;
  error?: string;
  warning?: string | null;
  result?: PairOrderResult;
}

const EMPTY_SUMMARY: PortfolioSummaryDto = {
  open_positions: 0,
  unrealized_pnl: 0,
  gross_notional: 0,
  daily_pnl: 0,
  max_daily_loss: 0,
};

const EMPTY_VENUE_SUMMARY: VenueSummaryDto = {
  open_positions: 0,
  unrealized_pnl: 0,
  realized_pnl: 0,
  gross_notional: 0,
  source: "venue",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useTerminal() {
  const [query, setQuery] = useState<TerminalQuery>({
    underlying: "BTC",
    expiry: null,
    optionType: null,
    minEdgeBps: 25,
  });
  const [status, setStatus] = useState<StatusDto | null>(null);
  const [snapshot, setSnapshot] = useState<SnapshotDto | null>(null);
  const [positions, setPositions] = useState<PositionDto[]>([]);
  const [summary, setSummary] = useState<PortfolioSummaryDto>(EMPTY_SUMMARY);
  const [positionMode, setPositionMode] = useState<"paper" | "live">("paper");
  const [venuePositions, setVenuePositions] = useState<VenuePositionDto[]>([]);
  const [venueSummary, setVenueSummary] = useState<VenueSummaryDto>(EMPTY_VENUE_SUMMARY);
  const [positionsSyncedAt, setPositionsSyncedAt] = useState<string | null>(null);
  const [orders, setOrders] = useState<OrderDto[]>([]);
  const [historySummary, setHistorySummary] = useState<HistorySummaryDto | null>(null);
  const [risk, setRisk] = useState<RiskDto | null>(null);
  const [strategies, setStrategies] = useState<StrategyDto[]>([]);
  const [venueNote, setVenueNote] = useState("");
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const socketRef = useRef<WebSocket | null>(null);

  const notify = useCallback((message: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current.slice(-3), { id, kind, message }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 6500);
  }, []);

  const refreshPortfolio = useCallback(async () => {
    try {
      const [positionResponse, orderList, history] = await Promise.all([
        api.positions(),
        api.orders({ limit: 120 }),
        api.historySummary(),
      ]);
      setPositions(positionResponse.positions);
      setSummary(positionResponse.summary);
      setPositionMode(positionResponse.mode);
      setVenuePositions(positionResponse.venue);
      setVenueSummary(positionResponse.venue_summary);
      setPositionsSyncedAt(positionResponse.synced_at);
      setVenueNote(positionResponse.venue_note);
      setOrders(orderList);
      setHistorySummary(history);
    } catch (error) {
      console.error("portfolio refresh failed", error);
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const next = await api.status();
      setStatus(next);
      setRisk(next.risk);
    } catch (error) {
      console.error("status refresh failed", error);
    }
  }, []);

  const refreshStrategies = useCallback(async () => {
    try {
      setStrategies(await api.strategies());
    } catch (error) {
      console.error("strategy refresh failed", error);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshStatus(), refreshPortfolio(), refreshStrategies()]);
  }, [refreshStatus, refreshPortfolio, refreshStrategies]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refreshAll();
      try {
        const initial = await api.snapshot({ underlying: query.underlying, minEdgeBps: query.minEdgeBps });
        if (!cancelled) setSnapshot(initial);
      } catch (error) {
        if (!cancelled) notify(errorMessage(error), "error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // The initial load only needs the first underlying; the socket keeps it fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let closed = false;
    let attempt = 0;
    let retry: number | undefined;

    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const params = new URLSearchParams({
        underlying: query.underlying,
        min_edge_bps: String(query.minEdgeBps),
      });
      if (query.expiry) params.set("expiry", query.expiry);
      if (query.optionType) params.set("option_type", query.optionType);

      const socket = new WebSocket(`${protocol}://${window.location.host}/ws?${params.toString()}`);
      socketRef.current = socket;

      socket.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data as string) as SnapshotDto & { type?: string };
          if (message.type === "tick") {
            setSnapshot(message);
            setLoading(false);
          } else if (message.type === "portfolio") {
            void refreshPortfolio();
          }
        } catch (error) {
          console.error("bad socket message", error);
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closed) {
          retry = window.setTimeout(connect, Math.min(5000, 400 * 2 ** attempt++));
        }
      };
      socket.onerror = () => socket.close();
    };

    connect();
    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [query.underlying, query.expiry, query.optionType, query.minEdgeBps, refreshPortfolio]);

  const updateQuery = useCallback((patch: Partial<TerminalQuery>) => {
    setQuery((previous) => ({
      ...previous,
      ...patch,
      expiry:
        patch.underlying && patch.underlying !== previous.underlying
          ? null
          : patch.expiry !== undefined
            ? patch.expiry
            : previous.expiry,
    }));
  }, []);

  const placeOrder = useCallback(
    async (payload: OrderPayload): Promise<ActionResult> => {
      try {
        const order = await api.createOrder(payload);
        notify(
          `${payload.side.toUpperCase()} ${order.filled_qty || order.qty} × ${order.symbol} — ${order.status}`,
          order.status === "rejected" ? "error" : "success",
        );
        await refreshPortfolio();
        return { ok: true };
      } catch (error) {
        const message = errorMessage(error);
        notify(message, "error");
        return { ok: false, error: message };
      }
    },
    [notify, refreshPortfolio],
  );

  const executePair = useCallback(
    async (key: string, qty: number, minEdgeBps?: number): Promise<ActionResult> => {
      try {
        const result = await api.executePair(key, qty, minEdgeBps);
        notify(
          result.warning
            ? `Pair partially executed: ${result.warning}`
            : `Pair executed: ${result.direction.replaceAll("_", " ")} @ ${result.expected_edge_bps.toFixed(1)} bps`,
          result.warning ? "error" : "success",
        );
        await refreshPortfolio();
        return { ok: true, warning: result.warning, result };
      } catch (error) {
        const message = errorMessage(error);
        notify(message, "error");
        return { ok: false, error: message };
      }
    },
    [notify, refreshPortfolio],
  );

  const cancelOrder = useCallback(
    async (id: number) => {
      try {
        await api.cancelOrder(id);
        notify("Order cancelled", "success");
        await refreshPortfolio();
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [notify, refreshPortfolio],
  );

  const closePosition = useCallback(
    async (id: number) => {
      try {
        await api.closePosition(id);
        notify("Position closed", "success");
        await refreshPortfolio();
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [notify, refreshPortfolio],
  );

  const closeVenuePosition = useCallback(
    async (id: number) => {
      try {
        const order = await api.closeVenuePosition(id);
        notify(
          `Reduce-only close sent (${order.status}); the venue sync will confirm the fill`,
          order.status === "rejected" ? "error" : "success",
        );
        await refreshPortfolio();
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [notify, refreshPortfolio],
  );

  const syncPositions = useCallback(async () => {
    try {
      const response = await api.syncPositions();
      setVenuePositions(response.venue);
      setVenueSummary(response.venue_summary);
      setPositionsSyncedAt(new Date().toISOString());
      const counts = response.result;
      notify(
        `Venue sync: ${counts.orders} orders, ${counts.fills} new fills, ${counts.positions} positions` +
          (counts.errors.length ? ` · ${counts.errors[0]}` : ""),
        counts.errors.length ? "error" : "success",
      );
      await refreshPortfolio();
    } catch (error) {
      notify(errorMessage(error), "error");
    }
  }, [notify, refreshPortfolio]);

  const saveRisk = useCallback(
    async (patch: Partial<RiskDto>) => {
      try {
        const updated = await api.updateRisk(patch);
        setRisk(updated);
        if (patch.min_edge_bps !== undefined) updateQuery({ minEdgeBps: updated.min_edge_bps });
        notify("Risk settings saved", "success");
        await refreshStatus();
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [notify, refreshStatus, updateQuery],
  );

  const toggleKillSwitch = useCallback(async () => {
    if (!risk) return;
    await saveRisk({ kill_switch: !risk.kill_switch });
  }, [risk, saveRisk]);

  const createStrategy = useCallback(
    async (payload: {
      name: string;
      source_exchange: Exchange;
      target_exchange: Exchange;
      size_ratio: number;
      max_qty: number;
      underlyings: string[];
      mirror_exits: boolean;
      enabled: boolean;
    }): Promise<boolean> => {
      try {
        await api.createStrategy(payload);
        notify("Copy strategy created", "success");
        await refreshStrategies();
        return true;
      } catch (error) {
        notify(errorMessage(error), "error");
        return false;
      }
    },
    [notify, refreshStrategies],
  );

  const toggleStrategy = useCallback(
    async (id: number, enabled: boolean) => {
      try {
        await api.updateStrategy(id, { enabled });
        await refreshStrategies();
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [notify, refreshStrategies],
  );

  const deleteStrategy = useCallback(
    async (id: number) => {
      try {
        await api.deleteStrategy(id);
        notify("Copy strategy deleted", "success");
        await refreshStrategies();
      } catch (error) {
        notify(errorMessage(error), "error");
      }
    },
    [notify, refreshStrategies],
  );

  return {
    query,
    updateQuery,
    status,
    snapshot,
    positions,
    summary,
    positionMode,
    venuePositions,
    venueSummary,
    positionsSyncedAt,
    orders,
    historySummary,
    risk,
    strategies,
    venueNote,
    connected,
    loading,
    toasts,
    notify,
    refreshAll,
    refreshPortfolio,
    placeOrder,
    executePair,
    cancelOrder,
    closePosition,
    closeVenuePosition,
    syncPositions,
    saveRisk,
    toggleKillSwitch,
    createStrategy,
    toggleStrategy,
    deleteStrategy,
  };
}
