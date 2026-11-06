export type Exchange = "delta" | "coinswitch";
export type OptionType = "call" | "put";
export type SourceKind = "live" | "simulated" | "unavailable";

export interface QuoteDto {
  exchange: Exchange;
  symbol: string;
  bid: number | null;
  ask: number | null;
  bid_size: number | null;
  ask_size: number | null;
  ltp: number | null;
  mark_price: number | null;
  mark_iv: number | null;
  oi: number | null;
  volume: number | null;
  mid: number | null;
  ts: string;
  source: "live" | "simulated";
}

export interface SpecDto {
  exchange: Exchange;
  symbol: string;
  underlying: string;
  option_type: OptionType;
  strike: number;
  expiry: string;
  contract_value: number;
  contract_unit: string;
  settlement_currency: string;
  price_currency: string;
  tick_size: number;
  taker_fee_rate: number;
  min_qty: number;
  qty_step: number;
  expiry_assumed: boolean;
}

export interface LegDto {
  spec: SpecDto | null;
  quote: QuoteDto | null;
  spread_bps: number | null;
  source: "live" | "simulated" | null;
}

export interface DiffCell {
  delta: number | null;
  coinswitch: number | null;
  diff: number | null;
}

export interface DirectionDto {
  name: string;
  buy_exchange: Exchange;
  sell_exchange: Exchange;
  buy_symbol: string;
  sell_symbol: string;
  buy_price: number;
  sell_price: number;
  gross_spread: number;
  fees: number;
  slippage: number;
  net_spread: number;
  net_edge_bps: number;
  cash_per_contract: number | null;
  executable_qty: number;
  live_legs: boolean;
  equivalent?: boolean;
  profitable?: boolean;
}

export interface EquivalenceDto {
  equivalent: boolean;
  contract_value_match: boolean;
  settlement_match: boolean;
  settlement_normalized: boolean;
  notes: string[];
}

export interface RowDto {
  key: string;
  underlying: string;
  option_type: OptionType;
  strike: number;
  expiry: string;
  legs: Record<Exchange, LegDto>;
  diff: Record<string, DiffCell>;
  directions: Record<string, DirectionDto>;
  best: DirectionDto | null;
  equivalence: EquivalenceDto;
}

export interface ArbDto extends DirectionDto {
  key: string;
  underlying: string;
  option_type: OptionType;
  strike: number;
  expiry: string;
}

export interface IndexDto {
  underlying: string;
  spot: number | null;
  pairs: number;
  max_edge_bps: number | null;
  sources: Record<string, SourceKind>;
}

export interface SnapshotDto {
  type?: "tick";
  as_of: string;
  mode: "paper" | "live";
  underlying: string;
  spot: number | null;
  expiries: { expiry: string; pairs: number }[];
  selected_expiry: string | null;
  option_type: OptionType | null;
  rows: RowDto[];
  arbitrage: ArbDto[];
  indexes: IndexDto[];
  sources: Record<string, SourceKind>;
  stats: {
    matched_pairs: number;
    total_contract_keys: number;
    unmatched_keys: number;
    contracts: Record<string, number>;
    refresh_count: number;
  };
  arbitrage_stats?: {
    sanity_excluded: number;
    below_threshold: number;
    ineligible: number;
  };
  risk?: { kill_switch: boolean; trading_enabled: boolean; auto_risk_exits: boolean };
}

export interface ExchangeStatusDto {
  exchange: Exchange;
  label: string;
  market_data: string;
  trading: string;
  note: string;
  last_error: string | null;
  last_ok_at: string | null;
  latency_ms: number | null;
  options_access?: string;
  access_url?: string;
  unparsed_instruments?: number;
  rest_base?: string;
}

export interface RiskDto {
  trading_enabled: boolean;
  kill_switch: boolean;
  max_order_qty: number;
  max_order_notional: number;
  max_open_positions: number;
  max_gross_notional: number;
  max_daily_loss: number;
  max_price_deviation_pct: number;
  stop_loss_pct: number;
  target_pct: number;
  auto_risk_exits: boolean;
  slippage_bps: number;
  min_edge_bps: number;
  updated_at?: string;
}

export interface StatusDto {
  mode: "paper" | "live";
  server_time: string;
  poll_ms: number;
  exchanges: ExchangeStatusDto[];
  market: { updated_at: string | null; refresh_count: number; stale: boolean };
  risk: RiskDto;
  underlyings: string[];
  live_sync: LiveSyncDto;
  assumptions: string[];
  access: {
    coinswitch_options: string;
    coinswitch_request_url: string;
    delta_docs_url: string;
  };
}

export interface OrderDto {
  id: number;
  client_order_id: string;
  exchange_order_id: string | null;
  exchange: Exchange;
  symbol: string;
  underlying: string;
  option_type: OptionType;
  strike: number;
  expiry: string;
  contract_value: number;
  side: "buy" | "sell";
  qty: number;
  order_type: "limit" | "market";
  limit_price: number | null;
  time_in_force: string;
  reduce_only: boolean;
  status: string;
  filled_qty: number;
  avg_fill_price: number | null;
  fees: number;
  realized_pnl: number;
  notional: number;
  mode: "paper" | "live";
  source: string;
  parent_order_id: number | null;
  strategy_id: number | null;
  error: string | null;
  exchange_state: string | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PositionDto {
  id: number;
  exchange: Exchange;
  symbol: string;
  underlying: string;
  option_type: OptionType;
  strike: number;
  expiry: string;
  qty: number;
  avg_price: number;
  mark: number;
  contract_value: number;
  notional: number;
  unrealized_pnl: number;
  pnl_pct: number | null;
  realized_pnl: number;
  fees_paid: number;
  stop_price: number | null;
  target_price: number | null;
  source: "live" | "simulated" | null;
  opened_at: string;
}

export interface PortfolioSummaryDto {
  open_positions: number;
  unrealized_pnl: number;
  gross_notional: number;
  daily_pnl: number;
  max_daily_loss: number;
}

export interface VenuePositionDto {
  id: number;
  exchange: Exchange;
  symbol: string;
  underlying: string | null;
  option_type: OptionType | null;
  strike: number | null;
  expiry: string | null;
  size: number;
  entry_price: number;
  mark: number;
  contract_value: number;
  notional: number;
  unrealized_pnl: number;
  realized_pnl: number;
  pnl_pct: number | null;
  margin: number | null;
  liquidation_price: number | null;
  venue_updated_at: string | null;
  synced_at: string | null;
  source: "venue";
}

export interface VenueSummaryDto {
  open_positions: number;
  unrealized_pnl: number;
  realized_pnl: number;
  gross_notional: number;
  source: "venue";
}

export interface LiveSyncDto {
  enabled: boolean;
  order_interval_ms: number;
  positions_interval_ms: number;
  last_run: string | null;
  last_result: { orders: number; fills: number; positions: number; errors: string[] };
}

export interface PositionsResponse {
  mode: "paper" | "live";
  positions: PositionDto[];
  summary: PortfolioSummaryDto;
  venue: VenuePositionDto[];
  venue_summary: VenueSummaryDto;
  synced_at: string | null;
  venue_note: string;
}

export interface HistorySummaryDto {
  total_orders: number;
  filled_orders: number;
  open_orders: number;
  rejected_orders: number;
  fees_paid: number;
  realized_pnl: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  by_exchange: Record<string, { orders: number; fees: number; realized_pnl: number }>;
  by_source: Record<string, { orders: number; fees: number; realized_pnl: number }>;
}

export interface StrategyDto {
  id: number;
  name: string;
  source_exchange: Exchange;
  target_exchange: Exchange;
  size_ratio: number;
  max_qty: number;
  underlyings: string[];
  mirror_exits: boolean;
  enabled: boolean;
  copied_count: number;
  last_error: string | null;
  last_event_at: string | null;
  created_at: string;
}

export interface OrderPayload {
  exchange: Exchange;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  order_type: "limit" | "market";
  limit_price?: number | null;
  time_in_force?: "gtc" | "ioc";
  reduce_only?: boolean;
  stop_price?: number | null;
  target_price?: number | null;
}

export interface PairOrderResult {
  key: string;
  direction: string;
  expected_net_spread: number;
  expected_edge_bps: number;
  orders: OrderDto[];
  warning: string | null;
}
