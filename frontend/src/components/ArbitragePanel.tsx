import { useState } from "react";
import type { ArbDto, RiskDto } from "../types";
import type { ActionResult } from "../hooks/useTerminal";
import {
  EXCHANGE_LABEL,
  classNames,
  fmtBps,
  fmtExpiry,
  fmtMoney,
  fmtNumber,
  fmtPrice,
  fmtQty,
  optionLabel,
} from "../lib/format";
import { Badge, Button, EmptyState, Panel, inputClass } from "./ui";

interface Props {
  className?: string;
  opportunities: ArbDto[];
  risk: RiskDto | null;
  excludedCount?: number;
  onExecute: (key: string, qty: number) => Promise<ActionResult>;
}

export function ArbitragePanel({ className, opportunities, risk, excludedCount = 0, onExecute }: Props) {
  const [qty, setQty] = useState("1");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const quantity = Math.max(1, Number(qty) || 1);
  const minEdge = risk?.min_edge_bps ?? 25;

  const execute = async (opportunity: ArbDto) => {
    setBusyKey(opportunity.key);
    setMessage(null);
    const result = await onExecute(opportunity.key, quantity);
    setBusyKey(null);
    if (!result.ok) setMessage(result.error ?? "Execution rejected");
    else if (result.warning) setMessage(result.warning);
  };

  return (
    <Panel
      className={className}
      title="Arbitrage scanner"
      subtitle={`${opportunities.length} spreads above ${fmtNumber(minEdge, 0)} bps${
        excludedCount > 0 ? ` · ${excludedCount} rejected by sanity guard` : ""
      }`}
      actions={
        <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted">
          Qty
          <input
            className={classNames(inputClass, "w-14 px-1.5 py-1 text-center")}
            type="number"
            min={1}
            value={qty}
            onChange={(event) => setQty(event.target.value)}
          />
        </label>
      }
      bodyClassName="flex min-h-0 flex-col gap-2 p-0"
    >
      <div className="min-h-0 flex-1 overflow-auto" style={{ maxHeight: "min(46vh, 460px)" }}>
        {opportunities.length === 0 ? (
          <div className="p-3">
            <EmptyState>
              No spread clears frictions right now. The scanner subtracts taker fees on both legs plus a{" "}
              {fmtNumber(risk?.slippage_bps ?? 0, 0)} bps slippage buffer.
            </EmptyState>
          </div>
        ) : (
          <table className="w-full border-collapse text-[11px]">
            <thead className="sticky top-0 z-10 bg-panel-2 text-[9px] uppercase tracking-[0.1em] text-muted">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Contract</th>
                <th className="px-2 py-2 text-left font-semibold">Buy</th>
                <th className="px-2 py-2 text-left font-semibold">Sell</th>
                <th className="px-2 py-2 text-right font-semibold">Net edge</th>
                <th className="px-2 py-2 text-right font-semibold">$/contract</th>
                <th className="px-2 py-2 text-right font-semibold">Top</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {opportunities.map((opportunity) => (
                <tr key={opportunity.key} className="border-t border-line/60 hover:bg-panel-2/60">
                  <td className="px-3 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[11px] font-semibold text-strong">
                        {opportunity.underlying} {fmtNumber(opportunity.strike, 0)}
                      </span>
                      <Badge tone={opportunity.option_type === "call" ? "up" : "down"}>
                        {optionLabel(opportunity.option_type)}
                      </Badge>
                    </div>
                    <span className="text-[9px] text-muted">{fmtExpiry(opportunity.expiry)} UTC</span>
                  </td>
                  <td className="px-2 py-1.5">
                    <span className="text-delta">{EXCHANGE_LABEL[opportunity.buy_exchange]}</span>
                    <div className="tnum font-mono text-[10px] text-strong/80">@{fmtPrice(opportunity.buy_price)}</div>
                  </td>
                  <td className="px-2 py-1.5">
                    <span className={opportunity.sell_exchange === "delta" ? "text-delta" : "text-cs"}>
                      {EXCHANGE_LABEL[opportunity.sell_exchange]}
                    </span>
                    <div className="tnum font-mono text-[10px] text-strong/80">@{fmtPrice(opportunity.sell_price)}</div>
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <span className="tnum font-mono text-[12px] font-semibold text-up">
                      {fmtBps(opportunity.net_edge_bps)}
                    </span>
                    <div className="tnum font-mono text-[9px] text-muted">
                      gross {fmtPrice(opportunity.gross_spread)}
                    </div>
                  </td>
                  <td className="tnum px-2 py-1.5 text-right font-mono text-[11px] text-strong">
                    {fmtMoney(opportunity.cash_per_contract)}
                  </td>
                  <td className="tnum px-2 py-1.5 text-right font-mono text-[10px] text-muted">
                    {fmtQty(opportunity.executable_qty)}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {!opportunity.live_legs ? <Badge tone="warn">sim</Badge> : null}
                      <Button
                        size="sm"
                        variant="buy"
                        disabled={busyKey === opportunity.key}
                        onClick={() => execute(opportunity)}
                      >
                        {busyKey === opportunity.key ? "…" : "Pair"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {message ? (
        <p className="mx-3 mb-2 rounded border border-warn/30 bg-warn/10 px-2 py-1.5 text-[10px] text-warn">
          {message}
        </p>
      ) : null}

      <div className="border-t border-line px-3 py-1.5 text-[9px] leading-relaxed text-muted">
        Net = sell bid − buy ask − taker fees on both legs − slippage buffer. Contracts must match on type, strike,
        expiry, contract size and settlement group. Fees and margin vary per venue.
      </div>
    </Panel>
  );
}
