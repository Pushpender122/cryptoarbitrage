import { useMemo, useState } from "react";
import type { HistorySummaryDto, OrderDto } from "../types";
import {
  EXCHANGE_LABEL,
  classNames,
  fmtDateTime,
  fmtMoney,
  fmtNumber,
  fmtPrice,
  fmtQty,
  optionLabel,
} from "../lib/format";
import { Badge, Button, EmptyState, Panel, Stat } from "./ui";

const FILTERS = [
  { value: "all", label: "All" },
  { value: "manual", label: "Manual" },
  { value: "arbitrage", label: "Arbitrage" },
  { value: "copy", label: "Copy" },
  { value: "risk", label: "Risk exits" },
] as const;

function statusTone(status: string) {
  if (status === "filled") return "up" as const;
  if (status === "open" || status === "accepted" || status === "pending") return "warn" as const;
  if (status === "rejected" || status === "cancelled") return "down" as const;
  return "muted" as const;
}

export function HistoryPanel({
  className,
  orders,
  summary,
  onCancel,
}: {
  className?: string;
  orders: OrderDto[];
  summary: HistorySummaryDto | null;
  onCancel: (id: number) => void;
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["value"]>("all");

  const filtered = useMemo(() => {
    if (filter === "all") return orders;
    if (filter === "risk") return orders.filter((order) => order.source.startsWith("risk"));
    return orders.filter((order) => order.source === filter);
  }, [orders, filter]);

  return (
    <Panel
      className={className}
      title="Order & trade history"
      subtitle={`${summary?.filled_orders ?? 0} filled · ${summary?.open_orders ?? 0} resting · ${summary?.rejected_orders ?? 0} rejected`}
      actions={
        <div className="flex flex-wrap items-center gap-1">
          {FILTERS.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => setFilter(item.value)}
              className={classNames(
                "rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.06em]",
                filter === item.value
                  ? "border-delta/50 bg-delta/10 text-delta"
                  : "border-line text-muted hover:text-strong",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      }
      bodyClassName="flex min-h-0 flex-col gap-3 p-0"
    >
      <div className="grid grid-cols-2 gap-3 border-b border-line px-3 py-2 sm:grid-cols-4">
        <Stat
          label="Realized P&L"
          value={fmtMoney(summary?.realized_pnl ?? 0)}
          tone={(summary?.realized_pnl ?? 0) >= 0 ? "up" : "down"}
        />
        <Stat label="Fees paid" value={fmtMoney(summary?.fees_paid ?? 0)} />
        <Stat
          label="Win rate"
          value={summary?.win_rate === null || summary?.win_rate === undefined ? "—" : `${fmtNumber(summary.win_rate, 1)}%`}
          hint={`${summary?.wins ?? 0}W / ${summary?.losses ?? 0}L`}
        />
        <Stat
          label="By venue"
          value={`${fmtMoney(summary?.by_exchange.delta?.realized_pnl ?? 0)} / ${fmtMoney(summary?.by_exchange.coinswitch?.realized_pnl ?? 0)}`}
          hint="Delta / CoinSwitch"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-auto" style={{ maxHeight: "min(38vh, 400px)" }}>
        {filtered.length === 0 ? (
          <div className="px-3">
            <EmptyState>No orders in this view yet.</EmptyState>
          </div>
        ) : (
          <table className="w-full border-collapse text-[11px]">
            <thead className="sticky top-0 z-10 bg-panel-2 text-[9px] uppercase tracking-[0.1em] text-muted">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Time</th>
                <th className="px-2 py-2 text-left font-semibold">Contract</th>
                <th className="px-2 py-2 text-left font-semibold">Side</th>
                <th className="px-2 py-2 text-right font-semibold">Qty</th>
                <th className="px-2 py-2 text-right font-semibold">Fill</th>
                <th className="px-2 py-2 text-right font-semibold">Fees</th>
                <th className="px-2 py-2 text-right font-semibold">P&L</th>
                <th className="px-2 py-2 text-left font-semibold">Status</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((order) => (
                <tr key={order.id} className="border-t border-line/60 hover:bg-panel-2/60">
                  <td className="px-3 py-1.5 font-mono text-[10px] text-muted">{fmtDateTime(order.created_at)}</td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[11px] text-strong">
                        {order.underlying} {fmtNumber(order.strike, 0)}
                      </span>
                      <Badge tone={order.option_type === "call" ? "up" : "down"}>{optionLabel(order.option_type)}</Badge>
                      <span className="text-[9px] text-muted">{EXCHANGE_LABEL[order.exchange]}</span>
                    </div>
                    <span className="font-mono text-[9px] text-muted/70">{order.symbol}</span>
                  </td>
                  <td
                    className={classNames(
                      "px-2 py-1.5 text-[10px] font-semibold uppercase",
                      order.side === "buy" ? "text-up" : "text-down",
                    )}
                  >
                    {order.side}
                  </td>
                  <td className="tnum px-2 py-1.5 text-right font-mono text-strong/85">{fmtQty(order.qty)}</td>
                  <td className="tnum px-2 py-1.5 text-right font-mono text-strong/85">
                    {fmtPrice(order.avg_fill_price ?? order.limit_price)}
                    <div className="text-[9px] text-muted">{order.order_type}</div>
                  </td>
                  <td className="tnum px-2 py-1.5 text-right font-mono text-muted">{fmtMoney(order.fees)}</td>
                  <td
                    className={classNames(
                      "tnum px-2 py-1.5 text-right font-mono",
                      order.realized_pnl > 0 ? "text-up" : order.realized_pnl < 0 ? "text-down" : "text-muted",
                    )}
                  >
                    {order.realized_pnl ? fmtMoney(order.realized_pnl) : "—"}
                  </td>
                  <td className="px-2 py-1.5">
                    <Badge tone={statusTone(order.status)}>{order.status}</Badge>
                    <div className="mt-0.5 text-[9px] uppercase tracking-[0.06em] text-muted">
                      {order.source}
                      {order.parent_order_id ? ` · pair ${order.parent_order_id}` : ""}
                    </div>
                    {order.error ? <div className="mt-0.5 text-[9px] text-down">{order.error}</div> : null}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    {order.status === "open" ? (
                      <Button size="sm" variant="ghost" onClick={() => onCancel(order.id)}>
                        Cancel
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
