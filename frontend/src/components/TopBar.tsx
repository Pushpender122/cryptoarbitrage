import { useState } from "react";
import type { StatusDto } from "../types";
import { fmtDateTime } from "../lib/format";
import { Badge, Button, Panel, SourceBadge } from "./ui";

function statusTone(value: string) {
  if (value === "live" || value === "enabled" || value === "allowlisted") return "up" as const;
  if (value === "simulated") return "warn" as const;
  if (value === "keys_required" || value === "allowlist_required") return "warn" as const;
  return "muted" as const;
}

export function TopBar({
  status,
  connected,
  halted,
  onToggleKill,
  onRefresh,
}: {
  status: StatusDto | null;
  connected: boolean;
  halted: boolean;
  onToggleKill: () => void;
  onRefresh: () => void;
}) {
  const [drawer, setDrawer] = useState(false);
  const mode = status?.mode ?? "paper";
  const simulated = status?.exchanges.some((exchange) => exchange.market_data === "simulated");

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-line bg-ink/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
          <div className="flex items-center gap-2.5">
            <div className="grid h-7 w-7 place-items-center rounded border border-delta/40 bg-delta/10 font-mono text-[13px] font-bold text-delta">
              A
            </div>
            <div className="leading-tight">
              <h1 className="font-mono text-[13px] font-bold tracking-[0.18em] text-strong">ARBITER</h1>
              <p className="text-[10px] uppercase tracking-[0.14em] text-muted">Options terminal</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={mode === "paper" ? "warn" : "down"} dot>
              {mode === "paper" ? "Paper mode" : "Live mode"}
            </Badge>
            {simulated ? <Badge tone="warn">Simulated leg</Badge> : null}
            {status?.exchanges.map((exchange) => (
              <span
                key={exchange.exchange}
                className="flex items-center gap-1.5 rounded border border-line bg-panel px-2 py-1"
              >
                <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
                  {exchange.label.split(" ")[0]}
                </span>
                <SourceBadge source={exchange.market_data === "unavailable" ? null : exchange.market_data} />
              </span>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <span className="hidden items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted sm:flex">
              <span
                className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-up shadow-[0_0_6px_var(--color-up)]" : "bg-down"}`}
              />
              {connected ? "Stream live" : "Reconnecting"}
            </span>
            <span className="hidden text-[10px] text-muted md:inline">
              refreshed {fmtDateTime(status?.market.updated_at)}
            </span>
            <Button size="sm" variant="ghost" onClick={onRefresh} title="Refresh status and portfolio">
              Refresh
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDrawer(true)}>
              Status
            </Button>
            <Button size="sm" variant={halted ? "danger" : "outline"} onClick={onToggleKill}>
              {halted ? "Halted" : "Kill switch"}
            </Button>
          </div>
        </div>
        {halted ? (
          <div className="border-t border-down/30 bg-down/10 px-3 py-1 text-center text-[10px] font-semibold uppercase tracking-[0.16em] text-down">
            Kill switch engaged — every new order is rejected until it is released
          </div>
        ) : null}
      </header>

      {drawer ? (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-ink/80 p-4 backdrop-blur-sm">
          <div className="mt-10 w-full max-w-3xl">
            <Panel
              title="Connection & assumptions"
              subtitle="What this terminal is actually connected to right now"
              actions={
                <Button size="sm" variant="ghost" onClick={() => setDrawer(false)}>
                  Close
                </Button>
              }
              bodyClassName="space-y-4"
            >
              <div className="grid gap-2 md:grid-cols-2">
                {status?.exchanges.map((exchange) => (
                  <div key={exchange.exchange} className="rounded border border-line bg-panel-2/60 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-[12px] font-semibold text-strong">{exchange.label}</p>
                      <Badge tone={exchange.market_data === "live" ? "up" : "warn"}>{exchange.market_data}</Badge>
                    </div>
                    <dl className="mt-2 space-y-1 text-[11px] text-muted">
                      <div className="flex justify-between gap-2">
                        <dt>Trading</dt>
                        <dd>
                          <Badge tone={statusTone(exchange.trading)}>{exchange.trading.replaceAll("_", " ")}</Badge>
                        </dd>
                      </div>
                      {exchange.options_access ? (
                        <div className="flex justify-between gap-2">
                          <dt>Options access</dt>
                          <dd>{exchange.options_access.replaceAll("_", " ")}</dd>
                        </div>
                      ) : null}
                      <div className="flex justify-between gap-2">
                        <dt>Latency</dt>
                        <dd className="tnum font-mono">{exchange.latency_ms ? `${exchange.latency_ms} ms` : "—"}</dd>
                      </div>
                      <div className="flex justify-between gap-2">
                        <dt>Last ok</dt>
                        <dd>{fmtDateTime(exchange.last_ok_at)}</dd>
                      </div>
                    </dl>
                    {exchange.note ? <p className="mt-2 text-[10px] leading-relaxed text-muted">{exchange.note}</p> : null}
                    {exchange.last_error ? (
                      <p className="mt-1 text-[10px] leading-relaxed text-down">{exchange.last_error}</p>
                    ) : null}
                    {exchange.access_url ? (
                      <a
                        className="mt-2 inline-block text-[10px] text-delta underline decoration-dotted"
                        href={exchange.access_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Options API access
                      </a>
                    ) : null}
                  </div>
                ))}
              </div>

              {status?.live_sync ? (
                <div className="rounded border border-line bg-panel-2/60 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[11px] font-semibold text-strong">Live reconciliation</p>
                    <Badge tone={status.live_sync.enabled ? "up" : "muted"}>
                      {status.live_sync.enabled ? "polling" : "paper mode"}
                    </Badge>
                  </div>
                  <dl className="mt-2 grid gap-1 text-[11px] text-muted sm:grid-cols-2">
                    <div className="flex justify-between gap-2">
                      <dt>Order status</dt>
                      <dd className="tnum font-mono">every {status.live_sync.order_interval_ms} ms</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Positions</dt>
                      <dd className="tnum font-mono">every {status.live_sync.positions_interval_ms} ms</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Last run</dt>
                      <dd>{fmtDateTime(status.live_sync.last_run)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Last result</dt>
                      <dd className="tnum font-mono">
                        {status.live_sync.last_result.orders}o / {status.live_sync.last_result.fills}f /{" "}
                        {status.live_sync.last_result.positions}p
                      </dd>
                    </div>
                  </dl>
                  {status.live_sync.last_result.errors.length ? (
                    <p className="mt-1.5 text-[10px] leading-relaxed text-down">
                      {status.live_sync.last_result.errors[0]}
                    </p>
                  ) : null}
                  <p className="mt-1.5 text-[10px] leading-relaxed text-muted">
                    Fills are stored once per venue execution id, so a retried poll cannot double-count a partial
                    fill.
                  </p>
                </div>
              ) : null}

              <div>
                <h3 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Assumptions</h3>
                <ul className="mt-1.5 space-y-1.5">
                  {status?.assumptions.map((assumption) => (
                    <li key={assumption} className="flex gap-2 text-[11px] leading-relaxed text-strong/75">
                      <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-warn" />
                      {assumption}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="rounded border border-warn/25 bg-warn/5 p-3 text-[11px] leading-relaxed text-warn/90">
                Options arbitrage is not risk-free. Contract specs, settlement currency, fees, margin and liquidity
                all differ between venues, and a matched strike can still be a different contract. Verify every leg on
                the exchange before routing real capital.
              </div>
            </Panel>
          </div>
        </div>
      ) : null}
    </>
  );
}
