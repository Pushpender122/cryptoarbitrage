import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../lib/api";
import type { RowDto } from "../types";
import { fmtBps, fmtExpiry, fmtNumber, fmtPrice, optionLabel } from "../lib/format";
import { Badge, EmptyState, Panel } from "./ui";

export function SpreadChart({
  className,
  row,
  asOf,
}: {
  className?: string;
  row: RowDto | null;
  asOf: string | null;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const fittedRef = useRef(false);
  const [count, setCount] = useState(0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      width: container.clientWidth || 480,
      height: 170,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#7f8b9e",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "rgba(27,33,48,0.5)" },
        horzLines: { color: "rgba(27,33,48,0.5)" },
      },
      rightPriceScale: { borderColor: "#1b2130", scaleMargins: { top: 0.15, bottom: 0.15 } },
      timeScale: { borderColor: "#1b2130", timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: "#2a3244", labelBackgroundColor: "#1b2130" },
        horzLine: { color: "#2a3244", labelBackgroundColor: "#1b2130" },
      },
      handleScroll: false,
      handleScale: false,
    });
    const series = chart.addLineSeries({
      color: "#2fe0a0",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    const observer = new ResizeObserver(() => {
      if (container.clientWidth) chart.applyOptions({ width: container.clientWidth });
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      fittedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!row) {
      seriesRef.current?.setData([]);
      setCount(0);
      return;
    }
    api
      .pair(row.key)
      .then((response) => {
        if (cancelled) return;
        const seen = new Set<number>();
        const data = response.points
          .map((point) => ({
            time: Math.floor(new Date(point.ts).getTime() / 1000) as UTCTimestamp,
            value: point.edge_bps,
          }))
          .filter((point) => {
            if (!Number.isFinite(point.value) || seen.has(point.time as number)) return false;
            seen.add(point.time as number);
            return true;
          })
          .sort((a, b) => (a.time as number) - (b.time as number));
        seriesRef.current?.setData(data);
        setCount(data.length);
        if (!fittedRef.current && data.length > 1) {
          chartRef.current?.timeScale().fitContent();
          fittedRef.current = true;
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [row?.key, asOf]);

  const edge = row?.best?.net_edge_bps ?? null;

  return (
    <Panel
      className={className}
      title="Spread history"
      subtitle={
        row
          ? `${row.underlying} ${fmtNumber(row.strike, 0)} ${optionLabel(row.option_type)} · ${fmtExpiry(row.expiry)} UTC`
          : "select a contract"
      }
      actions={
        <div className="flex items-center gap-1.5">
          <Badge tone={(edge ?? 0) > 0 ? "up" : "muted"}>{fmtBps(edge)}</Badge>
          <Badge tone="neutral">{count} pts</Badge>
        </div>
      }
      bodyClassName="space-y-2"
    >
      <div className="rounded border border-line bg-ink/50">
        <div ref={containerRef} className="h-[170px] w-full" />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-muted">
        <span>Net edge of the better direction, after fees and slippage buffer, sampled on every poll.</span>
        <span className="ml-auto font-mono">
          {row?.best
            ? `buy ${fmtPrice(row.best.buy_price)} / sell ${fmtPrice(row.best.sell_price)}`
            : ""}
        </span>
      </div>
      {count < 2 ? (
        <EmptyState>History builds as the terminal polls — leave it running to see the spread curve.</EmptyState>
      ) : null}
    </Panel>
  );
}
