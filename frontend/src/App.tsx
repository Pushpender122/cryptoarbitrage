import { useMemo, useState } from "react";
import { ComparisonPanel } from "./components/ComparisonPanel";
import { ArbitragePanel } from "./components/ArbitragePanel";
import { CopyPanel } from "./components/CopyPanel";
import { HistoryPanel } from "./components/HistoryPanel";
import { IndexStrip } from "./components/IndexStrip";
import { OrderTicket } from "./components/OrderTicket";
import { PositionsPanel } from "./components/PositionsPanel";
import { RiskPanel } from "./components/RiskPanel";
import { SpreadChart } from "./components/SpreadChart";
import { TopBar } from "./components/TopBar";
import { Badge } from "./components/ui";
import { useTerminal, type TerminalQuery } from "./hooks/useTerminal";
import { classNames } from "./lib/format";

export default function App() {
  const terminal = useTerminal();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const rows = terminal.snapshot?.rows ?? [];
  const selectedRow = useMemo(() => {
    if (selectedKey) {
      const match = rows.find((row) => row.key === selectedKey);
      if (match) return match;
    }
    return rows[0] ?? null;
  }, [rows, selectedKey]);

  const handleQuery = (patch: Partial<TerminalQuery>) => {
    if (patch.underlying) setSelectedKey(null);
    terminal.updateQuery(patch);
  };

  const mode = terminal.status?.mode ?? terminal.snapshot?.mode ?? "paper";
  const halted = terminal.risk?.kill_switch ?? false;

  return (
    <div className="min-h-screen pb-6">
      <TopBar
        status={terminal.status}
        connected={terminal.connected}
        halted={halted}
        onToggleKill={() => void terminal.toggleKillSwitch()}
        onRefresh={() => void terminal.refreshAll()}
      />

      <main className="mx-auto max-w-[1800px] space-y-3 px-3 py-3">
        <IndexStrip
          indexes={terminal.snapshot?.indexes ?? []}
          active={terminal.query.underlying}
          onSelect={(underlying) => handleQuery({ underlying })}
        />

        <div className="grid gap-3 xl:grid-cols-12">
          <ComparisonPanel
            className="xl:col-span-8"
            snapshot={terminal.snapshot}
            query={terminal.query}
            onQuery={handleQuery}
            selectedKey={selectedRow?.key ?? null}
            onSelect={setSelectedKey}
          />

          <OrderTicket
            className="xl:col-span-4"
            row={selectedRow}
            risk={terminal.risk}
            mode={mode}
            onSubmit={terminal.placeOrder}
            onExecutePair={(key, qty) => terminal.executePair(key, qty)}
          />

          <ArbitragePanel
            className="xl:col-span-5"
            opportunities={terminal.snapshot?.arbitrage ?? []}
            risk={terminal.risk}
            excludedCount={terminal.snapshot?.arbitrage_stats?.sanity_excluded ?? 0}
            onExecute={(key, qty) => terminal.executePair(key, qty)}
          />

          <PositionsPanel
            className="xl:col-span-7"
            mode={terminal.positionMode}
            positions={terminal.positions}
            summary={terminal.summary}
            venuePositions={terminal.venuePositions}
            venueSummary={terminal.venueSummary}
            syncedAt={terminal.positionsSyncedAt}
            venueNote={terminal.venueNote}
            onClosePaper={(id) => void terminal.closePosition(id)}
            onCloseVenue={(id) => void terminal.closeVenuePosition(id)}
            onSync={() => void terminal.syncPositions()}
          />

          <SpreadChart
            className="xl:col-span-7"
            row={selectedRow}
            asOf={terminal.snapshot?.as_of ?? null}
          />

          <RiskPanel className="xl:col-span-5" risk={terminal.risk} onSave={terminal.saveRisk} />

          <CopyPanel
            className="xl:col-span-5"
            strategies={terminal.strategies}
            onCreate={terminal.createStrategy}
            onToggle={terminal.toggleStrategy}
            onDelete={terminal.deleteStrategy}
          />

          <HistoryPanel
            className="xl:col-span-7"
            orders={terminal.orders}
            summary={terminal.historySummary}
            onCancel={(id) => void terminal.cancelOrder(id)}
          />
        </div>

        <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3 text-[9px] leading-relaxed text-muted">
          <Badge tone={mode === "paper" ? "warn" : "down"}>{mode === "paper" ? "paper" : "live"}</Badge>
          <span>
            Delta market data is public and live; CoinSwitch options data is simulated until its private-beta API
            access is allowlisted.
          </span>
          <span className="text-muted/60">·</span>
          <span>Admin session protected · exchange keys remain server-side.</span>
          <span className="text-muted/60">·</span>
          <span>Matched strikes are not identical contracts. Not investment advice.</span>
        </footer>
      </main>

      {terminal.loading && !terminal.snapshot ? (
        <div className="pointer-events-none fixed inset-0 grid place-items-center bg-ink/60">
          <div className="rounded border border-line bg-panel px-4 py-3 text-center">
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">Connecting venues</p>
            <p className="mt-1 text-[10px] text-muted/70">Delta public feed · CoinSwitch simulated mirror</p>
          </div>
        </div>
      ) : null}

      <div className="pointer-events-none fixed bottom-3 right-3 z-50 flex w-80 flex-col gap-1.5">
        {terminal.toasts.map((toast) => (
          <div
            key={toast.id}
            className={classNames(
              "pointer-events-auto rounded border px-3 py-2 text-[11px] leading-relaxed shadow-lg",
              toast.kind === "error"
                ? "border-down/40 bg-down/15 text-down"
                : toast.kind === "success"
                  ? "border-up/40 bg-up/15 text-up"
                  : "border-line-strong bg-panel text-strong/85",
            )}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </div>
  );
}
