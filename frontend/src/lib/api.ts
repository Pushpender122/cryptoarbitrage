import type {
  Exchange,
  HistorySummaryDto,
  OptionType,
  OrderDto,
  OrderPayload,
  PairOrderResult,
  PositionsResponse,
  RiskDto,
  SnapshotDto,
  StatusDto,
  StrategyDto,
  VenuePositionDto,
  VenueSummaryDto,
} from "../types";

export interface SnapshotQuery {
  underlying: string;
  expiry?: string | null;
  optionType?: OptionType | null;
  minEdgeBps?: number;
}

function csrfToken(): string {
  const entry = document.cookie.split("; ").find((part) => part.startsWith("arbiter_csrf="));
  return entry ? decodeURIComponent(entry.slice("arbiter_csrf=".length)) : "";
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, { ...init, credentials: "same-origin", headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function queryString(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  status: () => request<StatusDto>("/api/status"),

  snapshot: (query: SnapshotQuery) =>
    request<SnapshotDto>(
      `/api/market/snapshot${queryString({
        underlying: query.underlying,
        expiry: query.expiry,
        option_type: query.optionType,
        min_edge_bps: query.minEdgeBps,
      })}`,
    ),

  pair: (key: string) =>
    request<{ key: string; points: { ts: string; edge_bps: number; net_spread: number }[] }>(
      `/api/market/history${queryString({ key })}`,
    ),

  createOrder: (payload: OrderPayload) =>
    request<OrderDto>("/api/orders", { method: "POST", body: JSON.stringify(payload) }),

  executePair: (key: string, qty: number, minEdgeBps?: number) =>
    request<PairOrderResult>("/api/orders/pair", {
      method: "POST",
      body: JSON.stringify({ key, qty, min_edge_bps: minEdgeBps ?? null }),
    }),

  cancelOrder: (id: number) => request<OrderDto>(`/api/orders/${id}/cancel`, { method: "POST" }),

  orders: (params: { limit?: number; exchange?: Exchange | null; source?: string | null } = {}) =>
    request<OrderDto[]>(
      `/api/orders${queryString({
        limit: params.limit ?? 100,
        exchange: params.exchange,
        source: params.source,
      })}`,
    ),

  positions: () => request<PositionsResponse>("/api/positions"),

  closePosition: (id: number) =>
    request<OrderDto>(`/api/positions/${id}/close`, { method: "POST" }),

  closeVenuePosition: (id: number) =>
    request<OrderDto>(`/api/venue-positions/${id}/close`, { method: "POST" }),

  syncPositions: () =>
    request<{
      result: { orders: number; fills: number; positions: number; errors: string[] };
      venue: VenuePositionDto[];
      venue_summary: VenueSummaryDto;
    }>("/api/positions/sync", { method: "POST" }),

  refreshOrder: (id: number) =>
    request<{ order: OrderDto; new_fill_qty: number; fills: number; errors: string[] }>(
      `/api/orders/${id}/refresh`,
      { method: "POST" },
    ),

  history: (params: { limit?: number; exchange?: Exchange | null } = {}) =>
    request<{ orders: OrderDto[] }>(
      `/api/history${queryString({ limit: params.limit ?? 200, exchange: params.exchange })}`,
    ),

  historySummary: () => request<HistorySummaryDto>("/api/history/summary"),

  risk: () => request<RiskDto>("/api/risk"),

  updateRisk: (patch: Partial<RiskDto>) =>
    request<RiskDto>("/api/risk", { method: "PUT", body: JSON.stringify(patch) }),

  strategies: () => request<StrategyDto[]>("/api/copy-strategies"),

  createStrategy: (payload: {
    name: string;
    source_exchange: Exchange;
    target_exchange: Exchange;
    size_ratio: number;
    max_qty: number;
    underlyings: string[];
    mirror_exits: boolean;
    enabled: boolean;
  }) => request<StrategyDto>("/api/copy-strategies", { method: "POST", body: JSON.stringify(payload) }),

  updateStrategy: (id: number, patch: Partial<StrategyDto>) =>
    request<StrategyDto>(`/api/copy-strategies/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  deleteStrategy: (id: number) =>
    request<{ ok: boolean }>(`/api/copy-strategies/${id}`, { method: "DELETE" }),
};
