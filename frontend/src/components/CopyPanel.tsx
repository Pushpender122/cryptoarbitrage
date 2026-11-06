import { useState } from "react";
import type { Exchange, StrategyDto } from "../types";
import { EXCHANGE_LABEL, fmtDateTime, fmtNumber } from "../lib/format";
import { Badge, Button, EmptyState, Field, Panel, Segmented, Toggle, inputClass } from "./ui";

interface Props {
  className?: string;
  strategies: StrategyDto[];
  onCreate: (payload: {
    name: string;
    source_exchange: Exchange;
    target_exchange: Exchange;
    size_ratio: number;
    max_qty: number;
    underlyings: string[];
    mirror_exits: boolean;
    enabled: boolean;
  }) => Promise<boolean>;
  onToggle: (id: number, enabled: boolean) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}

export function CopyPanel({ className, strategies, onCreate, onToggle, onDelete }: Props) {
  const [open, setOpen] = useState(strategies.length === 0);
  const [name, setName] = useState("Mirror Delta → CoinSwitch");
  const [source, setSource] = useState<Exchange>("delta");
  const [target, setTarget] = useState<Exchange>("coinswitch");
  const [ratio, setRatio] = useState("1");
  const [maxQty, setMaxQty] = useState("5");
  const [mirrorExits, setMirrorExits] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [underlying, setUnderlying] = useState<"BTC" | "ETH" | "BOTH">("BOTH");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!name.trim() || source === target) return;
    setBusy(true);
    const ok = await onCreate({
      name: name.trim(),
      source_exchange: source,
      target_exchange: target,
      size_ratio: Math.max(0.01, Number(ratio) || 1),
      max_qty: Math.max(1, Number(maxQty) || 1),
      underlyings: underlying === "BOTH" ? ["BTC", "ETH"] : [underlying],
      mirror_exits: mirrorExits,
      enabled,
    });
    setBusy(false);
    if (ok) setOpen(false);
  };

  return (
    <Panel
      className={className}
      title="Copy trading"
      subtitle="Mirror fills from one venue onto the other, with its own size cap and risk gate"
      actions={
        <Button size="sm" variant="outline" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide form" : "New rule"}
        </Button>
      }
      bodyClassName="space-y-2"
    >
      {strategies.length === 0 ? (
        <EmptyState>
          No copy rules yet. A rule mirrors filled orders — including exits — from the source venue to the target.
        </EmptyState>
      ) : (
        <div className="space-y-1.5">
          {strategies.map((strategy) => (
            <div key={strategy.id} className="rounded border border-line bg-panel-2/50 p-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-[11px] font-semibold text-strong">{strategy.name}</p>
                  <p className="mt-0.5 text-[9px] uppercase tracking-[0.06em] text-muted">
                    {EXCHANGE_LABEL[strategy.source_exchange]} → {EXCHANGE_LABEL[strategy.target_exchange]} ·{" "}
                    {fmtNumber(strategy.size_ratio, 2)}× · max {fmtNumber(strategy.max_qty, 0)} ·{" "}
                    {strategy.underlyings.join("/")}
                    {strategy.mirror_exits ? " · exits mirrored" : ""}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Badge tone={strategy.enabled ? "up" : "muted"} dot>
                    {strategy.enabled ? "on" : "off"}
                  </Badge>
                  <Button size="sm" variant="ghost" onClick={() => onToggle(strategy.id, !strategy.enabled)}>
                    {strategy.enabled ? "Pause" : "Enable"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onDelete(strategy.id)}>
                    ×
                  </Button>
                </div>
              </div>
              <p className="mt-1 font-mono text-[9px] text-muted">
                mirrored {strategy.copied_count} · last {fmtDateTime(strategy.last_event_at)}
              </p>
              {strategy.last_error ? (
                <p className="mt-1 text-[9px] leading-relaxed text-down">{strategy.last_error}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}

      {open ? (
        <div className="space-y-2 rounded border border-line-strong/60 bg-panel-2/40 p-2">
          <Field label="Rule name">
            <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-[0.1em] text-muted">Source</p>
              <Segmented
                className="w-full"
                value={source}
                onChange={setSource}
                options={[
                  { value: "delta", label: "Delta" },
                  { value: "coinswitch", label: "CS" },
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-[0.1em] text-muted">Target</p>
              <Segmented
                className="w-full"
                value={target}
                onChange={setTarget}
                options={[
                  { value: "coinswitch", label: "CS" },
                  { value: "delta", label: "Delta" },
                ]}
              />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Field label="Ratio">
              <input className={inputClass} type="number" step="0.1" value={ratio} onChange={(event) => setRatio(event.target.value)} />
            </Field>
            <Field label="Max qty">
              <input className={inputClass} type="number" step="1" value={maxQty} onChange={(event) => setMaxQty(event.target.value)} />
            </Field>
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-[0.1em] text-muted">Universe</p>
              <Segmented
                className="w-full"
                value={underlying}
                onChange={setUnderlying}
                options={[
                  { value: "BTC", label: "BTC" },
                  { value: "ETH", label: "ETH" },
                  { value: "BOTH", label: "Both" },
                ]}
              />
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Toggle label="Mirror exits" checked={mirrorExits} onChange={setMirrorExits} />
            <Toggle label="Start enabled" checked={enabled} onChange={setEnabled} />
          </div>
          {source === target ? (
            <p className="text-[10px] text-down">Source and target must be different venues.</p>
          ) : null}
          <Button className="w-full" variant="primary" disabled={busy || source === target} onClick={submit}>
            {busy ? "Creating…" : "Create copy rule"}
          </Button>
          <p className="text-[9px] leading-relaxed text-muted">
            Copy orders pass through the same pre-trade risk gates as manual orders. A new rule starts paused unless
            you enable it here.
          </p>
        </div>
      ) : null}
    </Panel>
  );
}
