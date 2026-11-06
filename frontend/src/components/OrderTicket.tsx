import { useEffect, useState } from "react";
import type { Exchange, OrderPayload, RiskDto, RowDto } from "../types";
import type { ActionResult } from "../hooks/useTerminal";
import {
  EXCHANGE_LABEL,
  classNames,
  fmtBps,
  fmtExpiry,
  fmtMoney,
  fmtNumber,
  fmtPrice,
  optionLabel,
} from "../lib/format";
import { Badge, Button, EmptyState, Field, Panel, Segmented, SourceBadge, inputClass } from "./ui";

interface Props {
  className?: string;
  row: RowDto | null;
  risk: RiskDto | null;
  mode: "paper" | "live";
  onSubmit: (payload: OrderPayload) => Promise<ActionResult>;
  onExecutePair: (key: string, qty: number) => Promise<ActionResult>;
}

export function OrderTicket({ className, row, risk, mode, onSubmit, onExecutePair }: Props) {
  const [exchange, setExchange] = useState<Exchange>("delta");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [orderType, setOrderType] = useState<"limit" | "market">("limit");
  const [timeInForce, setTimeInForce] = useState<"gtc" | "ioc">("gtc");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!row) return;
    const preset = row.best?.buy_exchange ?? "delta";
    setExchange(preset);
    setSide("buy");
    setOrderType("limit");
    setError(null);
    setStopPrice("");
    setTargetPrice("");
  }, [row?.key]);

  useEffect(() => {
    if (!row) return;
    const quote = row.legs[exchange]?.quote;
    const touch = side === "buy" ? quote?.ask : quote?.bid;
    setPrice(touch != null ? String(touch) : "");
  }, [exchange, side, orderType, row?.key]);

  if (!row) {
    return (
      <Panel className={className} title="Quick order" subtitle="Pick a matched contract to trade">
        <EmptyState>Select a strike in the comparison table.</EmptyState>
      </Panel>
    );
  }

  const spec = row.legs[exchange]?.spec ?? null;
  const quote = row.legs[exchange]?.quote ?? null;
  const quantity = Number(qty);
  const limit = Number(price);
  const effectivePrice = orderType === "market" ? (side === "buy" ? quote?.ask : quote?.bid) ?? 0 : limit;
  const notional = spec && Number.isFinite(quantity) ? effectivePrice * quantity * spec.contract_value : 0;
  const fee = spec ? notional * spec.taker_fee_rate : 0;
  const blocked = risk?.kill_switch || risk?.trading_enabled === false;
  const overQty = risk ? quantity > risk.max_order_qty : false;
  const overNotional = risk ? notional > risk.max_order_notional : false;
  const invalid = !spec || !Number.isFinite(quantity) || quantity <= 0 || (orderType === "limit" && !(limit > 0));

  const submit = async () => {
    if (!spec || invalid) return;
    setBusy(true);
    setError(null);
    const result = await onSubmit({
      exchange,
      symbol: spec.symbol,
      side,
      qty: quantity,
      order_type: orderType,
      limit_price: orderType === "limit" ? limit : null,
      time_in_force: timeInForce,
      stop_price: stopPrice ? Number(stopPrice) : null,
      target_price: targetPrice ? Number(targetPrice) : null,
    });
    setBusy(false);
    if (!result.ok) setError(result.error ?? "Order rejected");
  };

  const pair = async () => {
    setBusy(true);
    setError(null);
    const result = await onExecutePair(row.key, quantity);
    setBusy(false);
    if (!result.ok) setError(result.error ?? "Pair rejected");
    else if (result.warning) setError(result.warning);
  };

  return (
    <Panel
      className={className}
      title="Quick order"
      subtitle={`${row.underlying} ${fmtNumber(row.strike, 0)} ${optionLabel(row.option_type)} · ${fmtExpiry(row.expiry)} UTC`}
      actions={
        <Badge tone={mode === "paper" ? "warn" : "down"}>{mode === "paper" ? "paper" : "live"}</Badge>
      }
      bodyClassName="space-y-3"
    >
      <div className="grid grid-cols-2 gap-2">
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-[0.1em] text-muted">Exchange</p>
          <Segmented
            className="w-full"
            value={exchange}
            onChange={(value) => setExchange(value)}
            options={[
              { value: "delta", label: "Delta" },
              { value: "coinswitch", label: "CS" },
            ]}
          />
        </div>
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-[0.1em] text-muted">Side</p>
          <Segmented
            className="w-full"
            value={side}
            onChange={(value) => setSide(value)}
            options={[
              { value: "buy", label: "Buy" },
              { value: "sell", label: "Sell" },
            ]}
          />
        </div>
      </div>

      <div className="rounded border border-line bg-panel-2/60 p-2">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-mono text-[11px] text-strong/90">{spec?.symbol ?? "no contract"}</span>
          <SourceBadge source={row.legs[exchange]?.source} />
        </div>
        <div className="mt-1 flex items-center justify-between text-[10px] text-muted">
          <span>
            {EXCHANGE_LABEL[exchange]} · {spec ? `${spec.contract_value} ${spec.contract_unit}/contract` : "—"}
          </span>
          <span className="tnum font-mono">
            {quote?.bid != null ? `${fmtPrice(quote.bid)} / ${fmtPrice(quote.ask)}` : "no quote"}
          </span>
        </div>
        {spec ? (
          <div className="mt-1 flex items-center gap-2 text-[9px] text-muted/80">
            <span>settle {spec.settlement_currency}</span>
            <span>· taker {(spec.taker_fee_rate * 10000).toFixed(1)} bps</span>
            {spec.expiry_assumed ? <span className="text-warn">· expiry assumed</span> : null}
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label="Quantity" hint="contracts">
          <input
            className={inputClass}
            type="number"
            min={1}
            step={1}
            value={qty}
            onChange={(event) => setQty(event.target.value)}
          />
        </Field>
        <Field label="Order type">
          <Segmented
            className="w-full"
            value={orderType}
            onChange={(value) => setOrderType(value)}
            options={[
              { value: "limit", label: "Limit" },
              { value: "market", label: "Market" },
            ]}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label={side === "buy" ? "Limit price (ask)" : "Limit price (bid)"}>
          <input
            className={inputClass}
            type="number"
            step="any"
            value={price}
            disabled={orderType === "market"}
            onChange={(event) => setPrice(event.target.value)}
          />
        </Field>
        <Field label="Time in force">
          <Segmented
            className="w-full"
            value={timeInForce}
            onChange={(value) => setTimeInForce(value)}
            options={[
              { value: "gtc", label: "GTC" },
              { value: "ioc", label: "IOC" },
            ]}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label="Stop loss" hint="premium">
          <input
            className={inputClass}
            type="number"
            step="any"
            placeholder="optional"
            value={stopPrice}
            onChange={(event) => setStopPrice(event.target.value)}
          />
        </Field>
        <Field label="Target" hint="premium">
          <input
            className={inputClass}
            type="number"
            step="any"
            placeholder="optional"
            value={targetPrice}
            onChange={(event) => setTargetPrice(event.target.value)}
          />
        </Field>
      </div>

      <div className="space-y-1 rounded border border-line bg-ink/40 p-2 font-mono text-[10px] text-muted">
        <div className="flex justify-between">
          <span>Notional</span>
          <span className="text-strong">{fmtMoney(notional)}</span>
        </div>
        <div className="flex justify-between">
          <span>Est. taker fee</span>
          <span className="text-strong">{fmtMoney(fee)}</span>
        </div>
        {row.best ? (
          <div className="flex justify-between">
            <span>Best pair edge</span>
            <span className={row.best.net_edge_bps > 0 ? "text-up" : "text-muted"}>
              {fmtBps(row.best.net_edge_bps)}
            </span>
          </div>
        ) : null}
        {quantity > 0 ? (
          <div className="flex justify-between">
            <span>Qty step</span>
            <span className="text-strong">{spec ? `${fmtNumber(spec.qty_step, 0)} / min ${fmtNumber(spec.min_qty, 0)}` : "—"}</span>
          </div>
        ) : null}
      </div>

      {error ? (
        <p className="rounded border border-down/30 bg-down/10 px-2 py-1.5 text-[10px] leading-relaxed text-down">
          {error}
        </p>
      ) : null}
      {blocked ? (
        <p className="rounded border border-warn/30 bg-warn/10 px-2 py-1.5 text-[10px] text-warn">
          Risk gate is engaged — orders will be rejected until it is released.
        </p>
      ) : null}
      {overQty || overNotional ? (
        <p className="rounded border border-warn/30 bg-warn/10 px-2 py-1.5 text-[10px] text-warn">
          {overQty ? `Above the ${fmtNumber(risk?.max_order_qty ?? 0, 0)} contract order cap. ` : ""}
          {overNotional ? `Notional above ${fmtMoney(risk?.max_order_notional ?? 0)}.` : ""}
        </p>
      ) : null}

      <Button
        className={classNames("w-full", side === "buy" ? "bg-up/20" : "bg-down/20")}
        variant={side === "buy" ? "buy" : "sell"}
        disabled={busy || invalid || blocked}
        onClick={submit}
      >
        {busy ? "Routing…" : `${side} ${qty || 0} × ${EXCHANGE_LABEL[exchange]} ${optionLabel(row.option_type)}`}
      </Button>

      {row.best && row.equivalence.equivalent ? (
        <Button
          className="w-full"
          variant="outline"
          disabled={busy || blocked || row.best.executable_qty <= 0}
          onClick={pair}
          title={`${row.best.buy_exchange} → ${row.best.sell_exchange} at ${row.best.executable_qty} contracts top of book`}
        >
          Execute pair · {fmtBps(row.best.net_edge_bps)}
        </Button>
      ) : null}

      {row.equivalence.notes.length > 0 ? (
        <div className="space-y-1 rounded border border-warn/20 bg-warn/5 p-2">
          {row.equivalence.notes.slice(0, 3).map((note) => (
            <p key={note} className="text-[9px] leading-relaxed text-warn/85">
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}
