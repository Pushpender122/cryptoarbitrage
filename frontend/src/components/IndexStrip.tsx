import type { IndexDto } from "../types";
import { fmtNumber, fmtSigned } from "../lib/format";
import { Badge, SourceBadge } from "./ui";
import { classNames } from "../lib/format";

export function IndexStrip({
  indexes,
  active,
  onSelect,
}: {
  indexes: IndexDto[];
  active: string;
  onSelect: (underlying: string) => void;
}) {
  if (indexes.length === 0) {
    return (
      <div className="rounded-lg border border-line bg-panel p-3 text-[11px] text-muted">
        Waiting for the first market snapshot…
      </div>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {indexes.map((index) => {
        const deltaSource = index.sources.delta;
        const csSource = index.sources.coinswitch;
        return (
          <button
            key={index.underlying}
            type="button"
            onClick={() => onSelect(index.underlying)}
            className={classNames(
              "rounded-lg border p-3 text-left transition-colors",
              active === index.underlying
                ? "border-delta/50 bg-delta/5"
                : "border-line bg-panel hover:border-line-strong",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-[12px] font-bold tracking-[0.14em] text-strong">
                {index.underlying}
              </span>
              <div className="flex items-center gap-1">
                <SourceBadge source={deltaSource} />
                <SourceBadge source={csSource} />
              </div>
            </div>
            <div className="mt-1.5 flex items-end justify-between gap-2">
              <div>
                <p className="text-[10px] uppercase tracking-[0.12em] text-muted">Index</p>
                <p className="tnum font-mono text-[20px] font-semibold leading-tight text-strong">
                  {index.spot ? `$${fmtNumber(index.spot, 2)}` : "—"}
                </p>
              </div>
              <div className="text-right">
                <p className="text-[10px] uppercase tracking-[0.12em] text-muted">Best edge</p>
                <p
                  className={classNames(
                    "tnum font-mono text-[15px] font-semibold",
                    (index.max_edge_bps ?? 0) > 0 ? "text-up" : "text-muted",
                  )}
                >
                  {index.max_edge_bps === null ? "—" : fmtSigned(index.max_edge_bps, 1) + " bps"}
                </p>
              </div>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <Badge tone="neutral">{index.pairs} matched pairs</Badge>
              {csSource === "simulated" ? <Badge tone="warn">simulated</Badge> : null}
            </div>
          </button>
        );
      })}
    </div>
  );
}
