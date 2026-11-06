import type { ButtonHTMLAttributes, ReactNode } from "react";
import { classNames } from "../lib/format";

export type Tone = "neutral" | "up" | "down" | "warn" | "delta" | "cs" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "border-line text-strong/80 bg-panel-2",
  up: "border-up/30 text-up bg-up/10",
  down: "border-down/30 text-down bg-down/10",
  warn: "border-warn/30 text-warn bg-warn/10",
  delta: "border-delta/30 text-delta bg-delta/10",
  cs: "border-cs/30 text-cs bg-cs/10",
  muted: "border-line text-muted bg-panel-2",
};

export function Badge({
  children,
  tone = "neutral",
  dot = false,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={classNames(
        "inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]",
        TONE_CLASS[tone],
        className,
      )}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section
      className={classNames(
        "flex min-w-0 flex-col overflow-hidden rounded-lg border border-line bg-panel/90 shadow-[0_1px_0_rgba(255,255,255,0.02)_inset]",
        className,
      )}
    >
      <header className="flex min-h-11 items-center justify-between gap-3 border-b border-line bg-panel-2/50 px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-[11px] font-semibold uppercase tracking-[0.16em] text-strong/90">{title}</h2>
          {subtitle ? <p className="mt-0.5 truncate text-[11px] text-muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </header>
      <div className={classNames("min-h-0 flex-1 p-3", bodyClassName)}>{children}</div>
    </section>
  );
}

type ButtonVariant = "primary" | "outline" | "ghost" | "buy" | "sell" | "danger";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-delta/90 text-ink hover:bg-delta border-transparent",
  outline: "border-line-strong text-strong/80 hover:border-muted hover:text-strong",
  ghost: "border-transparent text-muted hover:text-strong",
  buy: "bg-up/15 text-up border-up/40 hover:bg-up/25",
  sell: "bg-down/15 text-down border-down/40 hover:bg-down/25",
  danger: "bg-down/90 text-ink border-transparent hover:bg-down",
};

export function Button({
  variant = "outline",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md" }) {
  return (
    <button
      {...props}
      className={classNames(
        "inline-flex items-center justify-center gap-1.5 rounded border font-semibold uppercase tracking-[0.06em] transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        size === "sm" ? "px-2 py-1 text-[10px]" : "px-3 py-2 text-[11px]",
        BUTTON_VARIANT[variant],
        className,
      )}
    />
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  className,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div className={classNames("inline-flex rounded border border-line bg-panel-2 p-0.5", className)}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={classNames(
            "rounded-sm px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] transition-colors",
            value === option.value ? "bg-line-strong text-strong" : "text-muted hover:text-strong",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  const toneClass =
    tone === "up" ? "text-up" : tone === "down" ? "text-down" : tone === "warn" ? "text-warn" : "text-strong";
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted">{label}</p>
      <p className={classNames("tnum mt-0.5 truncate font-mono text-[15px] font-semibold", toneClass)}>{value}</p>
      {hint ? <p className="mt-0.5 truncate text-[10px] text-muted">{hint}</p> : null}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={classNames("block min-w-0", className)}>
      <span className="mb-1 flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-[0.1em] text-muted">
        {label}
        {hint ? <span className="font-normal normal-case tracking-normal text-muted/70">{hint}</span> : null}
      </span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full rounded border border-line bg-ink/60 px-2 py-1.5 font-mono text-[12px] text-strong outline-none transition-colors placeholder:text-muted/50 focus:border-delta/60";

export function Toggle({
  checked,
  onChange,
  label,
  tone = "delta",
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  tone?: "delta" | "down";
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center justify-between gap-2 rounded border border-line bg-panel-2 px-2 py-1.5 text-left"
    >
      <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted">{label}</span>
      <span
        className={classNames(
          "relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors",
          checked ? (tone === "down" ? "bg-down/80" : "bg-up/70") : "bg-line-strong",
        )}
      >
        <span
          className={classNames(
            "absolute h-3 w-3 rounded-full bg-ink transition-transform",
            checked ? "translate-x-3.5" : "translate-x-0.5",
          )}
        />
      </span>
    </button>
  );
}

export function SourceBadge({ source }: { source: string | null | undefined }) {
  if (source === "live") return <Badge tone="up" dot>Live</Badge>;
  if (source === "simulated") return <Badge tone="warn" dot>Sim</Badge>;
  return <Badge tone="muted">No data</Badge>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center rounded border border-dashed border-line px-4 py-6 text-center text-[11px] text-muted">
      {children}
    </div>
  );
}
