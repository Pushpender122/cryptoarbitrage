import { useEffect, useState } from "react";
import type { RiskDto } from "../types";
import { fmtNumber } from "../lib/format";
import { Badge, Button, Field, Panel, Toggle } from "./ui";

interface Props {
  className?: string;
  risk: RiskDto | null;
  onSave: (patch: Partial<RiskDto>) => Promise<void>;
}

const NUMBER_FIELDS: {
  key: keyof RiskDto;
  label: string;
  hint?: string;
  step: number;
}[] = [
  { key: "max_order_qty", label: "Max order qty", hint: "contracts", step: 1 },
  { key: "max_order_notional", label: "Max order notional", hint: "$ premium", step: 100 },
  { key: "max_open_positions", label: "Max open positions", hint: "count", step: 1 },
  { key: "max_gross_notional", label: "Max gross exposure", hint: "$", step: 500 },
  { key: "max_daily_loss", label: "Daily loss limit", hint: "$", step: 100 },
  { key: "max_price_deviation_pct", label: "Max price vs mid", hint: "%", step: 0.5 },
  { key: "stop_loss_pct", label: "Default stop loss", hint: "% of premium", step: 1 },
  { key: "target_pct", label: "Default target", hint: "% of premium", step: 1 },
  { key: "slippage_bps", label: "Slippage buffer", hint: "bps per leg", step: 1 },
  { key: "min_edge_bps", label: "Min arb edge", hint: "bps net", step: 1 },
];

export function RiskPanel({ className, risk, onSave }: Props) {
  const [draft, setDraft] = useState<Partial<RiskDto>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft({});
  }, [risk?.updated_at]);

  if (!risk) {
    return (
      <Panel className={className} title="Risk controls" subtitle="loading…">
        <div className="text-[11px] text-muted">…</div>
      </Panel>
    );
  }

  const valueOf = (key: keyof RiskDto): number => {
    const value = draft[key] ?? risk[key];
    return typeof value === "number" ? value : 0;
  };
  const boolOf = (key: keyof RiskDto): boolean => {
    const value = draft[key] ?? risk[key];
    return Boolean(value);
  };
  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setBusy(true);
    await onSave(draft);
    setBusy(false);
    setDraft({});
  };

  return (
    <Panel
      className={className}
      title="Risk controls"
      subtitle="Pre-trade gates apply to every order, including copy and arbitrage legs"
      actions={
        <div className="flex items-center gap-1.5">
          {risk.kill_switch ? <Badge tone="down">halted</Badge> : null}
          <Badge tone={risk.auto_risk_exits ? "up" : "muted"}>auto exits {risk.auto_risk_exits ? "on" : "off"}</Badge>
        </div>
      }
      bodyClassName="space-y-3"
    >
      <div className="grid grid-cols-2 gap-2">
        {NUMBER_FIELDS.map((field) => (
          <Field key={field.key} label={field.label} hint={field.hint}>
            <input
              className="w-full rounded border border-line bg-ink/60 px-2 py-1.5 font-mono text-[12px] text-strong outline-none focus:border-delta/60"
              type="number"
              step={field.step}
              min={0}
              value={valueOf(field.key)}
              onChange={(event) =>
                setDraft((current) => ({ ...current, [field.key]: Number(event.target.value) }))
              }
            />
          </Field>
        ))}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <Toggle
          label="Trading enabled"
          checked={boolOf("trading_enabled")}
          onChange={(next) => setDraft((current) => ({ ...current, trading_enabled: next }))}
        />
        <Toggle
          label="Automatic stop / target exits"
          checked={boolOf("auto_risk_exits")}
          onChange={(next) => setDraft((current) => ({ ...current, auto_risk_exits: next }))}
        />
      </div>

      <div className="flex items-center justify-between gap-2">
        <p className="text-[9px] leading-relaxed text-muted">
          Stops and targets are evaluated on premium P&L per position and only auto-execute in paper mode.
          Live positions stay under the exchange's risk engine.
        </p>
        <Button variant={dirty ? "primary" : "outline"} disabled={!dirty || busy} onClick={save}>
          {busy ? "Saving…" : dirty ? "Save changes" : "Saved"}
        </Button>
      </div>

      <div className="rounded border border-line bg-panel-2/50 p-2 font-mono text-[9px] text-muted">
        <div className="flex justify-between">
          <span>Order cap</span>
          <span className="text-strong">
            {fmtNumber(risk.max_order_qty, 0)} contracts / ${fmtNumber(risk.max_order_notional, 0)}
          </span>
        </div>
        <div className="flex justify-between">
          <span>Exposure cap</span>
          <span className="text-strong">${fmtNumber(risk.max_gross_notional, 0)}</span>
        </div>
        <div className="flex justify-between">
          <span>Scanner threshold</span>
          <span className="text-strong">{fmtNumber(risk.min_edge_bps, 0)} bps net</span>
        </div>
      </div>
    </Panel>
  );
}
