import { useMemo, useState } from "react";
import type { Exchange, RowDto, SnapshotDto } from "../types";
import type { TerminalQuery } from "../hooks/useTerminal";
import {
  classNames,
  fmtBps,
  fmtExpiry,
  fmtNumber,
  fmtPrice,
  fmtQty,
  optionLabel,
} from "../lib/format";
import { Badge, EmptyState, Panel, Segmented, SourceBadge } from "./ui";

interface Props {
  className?: string;
  snapshot: SnapshotDto | null;
  query: TerminalQuery;
  onQuery: (patch: Partial<TerminalQuery>) => void;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}

function PriceCell({
  bid,
  ask,
  bidSize,
  askSize,
  accent,
}: {
  bid: number | null;
  ask: number | null;
  bidSize: number | null;
  askSize: number | null;
  accent: "delta" | "cs";
}) {
  return (
    <div className="text-right">
      <div className="tnum flex items-baseline justify-end gap-1 font-mono text-[12px]">
        <span className={accent === "delta" ? "text-delta" : "text-cs"}>{fmtPrice(bid)}</span>
        <span className="text-muted/60">/</span>
        <span className="text-strong">{fmtPrice(ask)}</span>
      </div>
      <div className="tnum font-mono text-[9px] text-muted/70">
        {fmtQty(bidSize)} × {fmtQty(askSize)}
      </div>
    </div>
  );
}

export function ComparisonPanel({ className, snapshot, query, onQuery, selectedKey, onSelect }: Props) {
  const [search, setSearch] = useState("");
  const rows = useMemo(() => snapshot?.rows ?? [], [snapshot]);
  const filtered = useMemo(() => {
    const needle = search.trim();
    if (!needle) return rows;
    return rows.filter((row) => String(row.strike).includes(needle));
  }, [rows, search]);

  const deltaLeg = (row: RowDto) => row.legs.delta;
  const csLeg = (row: RowDto) => row.legs.coinswitch;

  return (
    <Panel
      className={className}
      title="Options comparison"
      subtitle={
        snapshot
          ? `${snapshot.stats.matched_pairs} matched · ${snapshot.stats.unmatched_keys} unmatched · ${fmtExpiry(snapshot.selected_expiry)}`
          : "connecting…"
      }
      actions={
        <div className="flex items-center gap-2">
          <Segmented
            value={query.underlying}
            onChange={(value) => onQuery({ underlying: value })}
            options={[
              { value: "BTC", label: "BTC" },
              { value: "ETH", label: "ETH" },
            ]}
          />
          <Segmented
            value={query.optionType ?? "all"}
            onChange={(value) => onQuery({ optionType: value === "all" ? null : (value as "call" | "put") })}
            options={[
              { value: "all", label: "All" },
              { value: "call", label: "CE" },
              { value: "put", label: "PE" },
            ]}
          />
        </div>
      }
      bodyClassName="flex min-h-0 flex-col gap-2 p-0"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <select
          value={query.expiry ?? snapshot?.selected_expiry ?? ""}
          onChange={(event) => onQuery({ expiry: event.target.value || null })}
          className="rounded border border-line bg-ink/60 px-2 py-1 font-mono text-[11px] text-strong outline-none focus:border-delta/60"
        >
          {(snapshot?.expiries ?? []).map((option) => (
            <option key={option.expiry} value={option.expiry}>
              {fmtExpiry(option.expiry)} UTC · {option.pairs} pairs
            </option>
          ))}
        </select>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Filter strike…"
          className="w-28 rounded border border-line bg-ink/60 px-2 py-1 font-mono text-[11px] text-strong outline-none placeholder:text-muted/50 focus:border-delta/60"
        />
        <span className="ml-auto flex items-center gap-1.5">
          <Badge tone="delta">Delta</Badge>
          <Badge tone="cs">CoinSwitch</Badge>
          <span className="text-[10px] text-muted">bid / ask · sizes</span>
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto" style={{ maxHeight: "min(58vh, 640px)" }}>
        {filtered.length === 0 ? (
          <div className="p-3">
            <EmptyState>
              {rows.length === 0
                ? "No matched contracts on this expiry yet. Both venues must list the same strike, type and expiry."
                : "No strikes match that filter."}
            </EmptyState>
          </div>
        ) : (
          <table className="w-full border-collapse text-[12px]">
            <thead className="sticky top-0 z-10 bg-panel-2 text-[9px] uppercase tracking-[0.1em] text-muted">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Strike</th>
                <th className="px-2 py-2 text-right font-semibold text-delta">Delta bid/ask</th>
                <th className="px-2 py-2 text-right font-semibold text-cs">CoinSwitch bid/ask</th>
                <th className="px-2 py-2 text-right font-semibold">Mid Δ</th>
                <th className="px-2 py-2 text-right font-semibold">Net edge</th>
                <th className="px-2 py-2 text-center font-semibold">Data</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const delta = deltaLeg(row);
                const cs = csLeg(row);
                const best = row.best;
                const selected = row.key === selectedKey;
                const midDiff = row.diff.mid?.diff ?? null;
                return (
                  <tr
                    key={row.key}
                    onClick={() => onSelect(row.key)}
                    className={classNames(
                      "cursor-pointer border-t border-line/60 transition-colors",
                      selected ? "bg-delta/10" : "hover:bg-panel-2/70",
                    )}
                  >
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className="tnum font-mono text-[12px] font-semibold text-strong">
                          {fmtNumber(row.strike, 0)}
                        </span>
                        <Badge tone={row.option_type === "call" ? "up" : "down"}>
                          {optionLabel(row.option_type)}
                        </Badge>
                      </div>
                      <span className="text-[9px] text-muted">{fmtExpiry(row.expiry)}</span>
                    </td>
                    <td className="px-2 py-1.5">
                      <PriceCell
                        bid={delta?.quote?.bid ?? null}
                        ask={delta?.quote?.ask ?? null}
                        bidSize={delta?.quote?.bid_size ?? null}
                        askSize={delta?.quote?.ask_size ?? null}
                        accent="delta"
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <PriceCell
                        bid={cs?.quote?.bid ?? null}
                        ask={cs?.quote?.ask ?? null}
                        bidSize={cs?.quote?.bid_size ?? null}
                        askSize={cs?.quote?.ask_size ?? null}
                        accent="cs"
                      />
                    </td>
                    <td
                      className={classNames(
                        "tnum px-2 py-1.5 text-right font-mono text-[12px]",
                        midDiff === null ? "text-muted" : midDiff > 0 ? "text-up" : "text-down",
                      )}
                    >
                      {midDiff === null ? "—" : `${midDiff > 0 ? "+" : ""}${fmtPrice(midDiff)}`}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      {best ? (
                        <>
                          <span
                            className={classNames(
                              "tnum font-mono text-[12px] font-semibold",
                              best.net_edge_bps > 0 ? "text-up" : "text-muted",
                            )}
                          >
                            {fmtBps(best.net_edge_bps)}
                          </span>
                          <div className="text-[9px] uppercase tracking-[0.06em] text-muted">
                            {best.buy_exchange === "delta" ? "buy D → sell CS" : "buy CS → sell D"}
                          </div>
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      <div className="flex flex-col items-center gap-1">
                        <SourceBadge source={delta?.source} />
                        <SourceBadge source={cs?.source} />
                      </div>
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {!row.equivalence.equivalent ? (
                          <Badge tone="warn" className="mr-1">
                            spec
                          </Badge>
                        ) : null}
                        <span
                          className={classNames(
                            "rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em]",
                            selected
                              ? "border-delta/60 text-delta"
                              : "border-line-strong text-muted",
                          )}
                        >
                          {selected ? "loaded" : "load"}
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 text-[9px] text-muted">
        <span>Contracts: {snapshot?.stats.contracts.delta ?? 0} Delta / {snapshot?.stats.contracts.coinswitch ?? 0} CoinSwitch</span>
        <span>·</span>
        <span>Premiums per 1 {snapshot?.underlying ?? "BTC"} · cash per contract uses the venue multiplier</span>
        {snapshot?.sources.coinswitch === "simulated" ? (
          <>
            <span>·</span>
            <span className="text-warn">CoinSwitch quotes are simulated until options API access is granted</span>
          </>
        ) : null}
      </div>
    </Panel>
  );
}
