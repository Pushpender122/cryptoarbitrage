import type {
  Exchange,
  OptionType,
  PortfolioSummaryDto,
  PositionDto,
  VenuePositionDto,
  VenueSummaryDto,
} from "../types";
import {
  EXCHANGE_LABEL,
  classNames,
  fmtDateTime,
  fmtMoney,
  fmtNumber,
  fmtPct,
  fmtPrice,
  fmtQty,
  optionLabel,
} from "../lib/format";
import { Badge, Button, EmptyState, Panel, SourceBadge, Stat } from "./ui";

interface Props {
  className?: string;
  mode: "paper" | "live";
  positions: PositionDto[];
  summary: PortfolioSummaryDto;
  venuePositions: VenuePositionDto[];
  venueSummary: VenueSummaryDto;
  syncedAt: string | null;
  venueNote: string;
  onClosePaper: (id: number) => void;
  onCloseVenue: (id: number) => void;
  onSync: () => void;
}

interface DisplayRow {
  id: number;
  source: "paper" | "venue";
  exchange: Exchange;
  underlying: string;
  option_type: OptionType;
  strike: number;
  qty: number;
  entry: number;
  mark: number;
  unrealized: number;
  pnlPct: number | null;
  stop: number | null;
  target: number | null;
  liquidation: number | null;
  updatedAt: string | null;
  dataSource: "live" | "simulated" | null;
}

export function PositionsPanel({
  className,
  mode,
  positions,
  summary,
  venuePositions,
  venueSummary,
  syncedAt,
  venueNote,
  onClosePaper,
  onCloseVenue,
  onSync,
}: Props) {
  const live = mode === "live";

  const rows: DisplayRow[] = live
    ? venuePositions.map((position) => ({
        id: position.id,
        source: "venue",
        exchange: position.exchange,
        underlying: position.underlying ?? position.symbol.split("-")[1] ?? "—",
        option_type: position.option_type ?? "call",
        strike: position.strike ?? 0,
        qty: position.size,
        entry: position.entry_price,
        mark: position.mark,
        unrealized: position.unrealized_pnl,
        pnlPct: position.pnl_pct,
        stop: null,
        target: null,
        liquidation: position.liquidation_price,
        updatedAt: position.venue_updated_at ?? position.synced_at,
        dataSource: "live",
      }))
    : positions.map((position) => ({
        id: position.id,
        source: "paper",
        exchange: position.exchange,
        underlying: position.underlying,
        option_type: position.option_type,
        strike: position.strike,
        qty: position.qty,
        entry: position.avg_price,
        mark: position.mark,
        unrealized: position.unrealized_pnl,
        pnlPct: position.pnl_pct,
        stop: position.stop_price,
        target: position.target_price,
        liquidation: null,
        updatedAt: position.opened_at,
        dataSource: position.source,
      }));

  const open = live ? venueSummary.open_positions : summary.open_positions;
  const unrealized = live ? venueSummary.unrealized_pnl : summary.unrealized_pnl;
  const gross = live ? venueSummary.gross_notional : summary.gross_notional;

  return (
    <Panel
      className={className}
      title="Positions"
      subtitle={`${open} open · gross ${fmtMoney(gross)}${
        live && syncedAt ? ` · synced ${fmtDateTime(syncedAt)}` : ""
      }`}
      actions={
        <>
          <Badge tone={live ? "up" : "neutral"}>{live ? "venue-sourced" : "paper ledger"}</Badge>
          {live ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onSync}
              title="Poll the exchanges now for order status, fills and positions"
            >
              Sync
            </Button>
          ) : null}
        </>
      }
      bodyClassName="flex min-h-0 flex-col gap-3 p-0"
    >
      <div className="grid grid-cols-2 gap-3 border-b border-line px-3 py-2 sm:grid-cols-4">
        <Stat label="Open" value={open} />
        <Stat
          label="Unrealized"
          value={fmtMoney(unrealized)}
          tone={unrealized >= 0 ? "up" : "down"}
        />
        {live ? (
          <Stat
            label="Realized"
            value={fmtMoney(venueSummary.realized_pnl)}
            tone={venueSummary.realized_pnl >= 0 ? "up" : "down"}
            hint="venue-reported"
          />
        ) : (
          <Stat
            label="Daily P&L"
            value={fmtMoney(summary.daily_pnl)}
            tone={summary.daily_pnl >= 0 ? "up" : "down"}
            hint={`limit ${fmtMoney(-(summary.max_daily_loss ?? 0))}`}
          />
        )}
        <Stat label="Gross notional" value={fmtMoney(gross)} />
      </div>

      <div className="min-h-0 flex-1 overflow-auto" style={{ maxHeight: "min(34vh, 360px)" }}>
        {rows.length === 0 ? (
          <div className="px-3">
            <EmptyState>
              {live
                ? "No positions reported by the venue. Live orders appear here after the next reconciliation."
                : "Flat. Paper fills from the order ticket will appear here with live marks."}
            </EmptyState>
          </div>
        ) : (
          <table className="w-full border-collapse text-[11px]">
            <thead className="sticky top-0 z-10 bg-panel-2 text-[9px] uppercase tracking-[0.1em] text-muted">
              <tr>
                <th className="px-3 py-2 text-left font-semibold">Contract</th>
                <th className="px-2 py-2 text-right font-semibold">Qty</th>
                <th className="px-2 py-2 text-right font-semibold">Entry</th>
                <th className="px-2 py-2 text-right font-semibold">Mark</th>
                <th className="px-2 py-2 text-right font-semibold">uPnL</th>
                <th className="px-2 py-2 text-right font-semibold">{live ? "Liq." : "SL / TP"}</th>
                <th className="px-2 py-2 text-center font-semibold">Data</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const positive = row.unrealized >= 0;
                return (
                  <tr key={`${row.source}-${row.id}`} className="border-t border-line/60 hover:bg-panel-2/60">
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[11px] font-semibold text-strong">
                          {row.underlying} {fmtNumber(row.strike, 0)}
                        </span>
                        <Badge tone={row.option_type === "call" ? "up" : "down"}>
                          {optionLabel(row.option_type)}
                        </Badge>
                        <span className="text-[9px] text-muted">{EXCHANGE_LABEL[row.exchange]}</span>
                      </div>
                      <span className="text-[9px] text-muted">{fmtDateTime(row.updatedAt)}</span>
                    </td>
                    <td
                      className={classNames(
                        "tnum px-2 py-1.5 text-right font-mono",
                        row.qty > 0 ? "text-up" : "text-down",
                      )}
                    >
                      {row.qty > 0 ? "+" : ""}
                      {fmtQty(row.qty)}
                    </td>
                    <td className="tnum px-2 py-1.5 text-right font-mono text-strong/85">
                      {fmtPrice(row.entry)}
                    </td>
                    <td className="tnum px-2 py-1.5 text-right font-mono text-strong">{fmtPrice(row.mark)}</td>
                    <td
                      className={classNames(
                        "tnum px-2 py-1.5 text-right font-mono font-semibold",
                        positive ? "text-up" : "text-down",
                      )}
                    >
                      {fmtMoney(row.unrealized)}
                      <div className="text-[9px] font-normal text-muted">{fmtPct(row.pnlPct)}</div>
                    </td>
                    <td className="tnum px-2 py-1.5 text-right font-mono text-[10px] text-muted">
                      {row.source === "venue"
                        ? row.liquidation
                          ? fmtPrice(row.liquidation)
                          : "—"
                        : `${row.stop ? fmtPrice(row.stop) : "—"} / ${row.target ? fmtPrice(row.target) : "—"}`}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <SourceBadge source={row.dataSource} />
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        title={
                          row.source === "venue"
                            ? "Send a reduce-only market order to flatten this venue position"
                            : "Market close this paper position"
                        }
                        onClick={() =>
                          row.source === "venue" ? onCloseVenue(row.id) : onClosePaper(row.id)
                        }
                      >
                        Close
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <p className="border-t border-line px-3 py-1.5 text-[9px] leading-relaxed text-muted">{venueNote}</p>
    </Panel>
  );
}
