import { clsx } from "clsx";
import { AlertTriangle, Info, OctagonAlert, X } from "lucide-react";
import type { ReactNode } from "react";

/* Status vocabulary. Colour answers "how urgent is this?" and nothing else:
   critical = CRITICAL calls, warning = HIGH calls, ok = free / healthy, offline = out of service or stopped. */

/* ---------------------------------------------------------------- tag: square, 12 px, hairline border */

export type Tone = "neutral" | "danger" | "warning" | "success" | "muted";
const TAG: Record<Tone, string> = {
  neutral: "border-line bg-panel text-ink",
  muted: "border-line bg-offline-tint text-muted",
  danger: "border-critical-line bg-critical-tint text-critical-text",
  warning: "border-warning-line bg-warning-tint text-warning",
  success: "border-ok-line bg-ok-tint text-ok",
};
const DOT_TONE: Record<Tone, string> = {
  neutral: "bg-muted", muted: "bg-offline", danger: "bg-critical", warning: "bg-warning", success: "bg-ok",
};

export function Chip({ tone = "neutral", dot, icon, size = "md", className, children, title }: {
  tone?: Tone;
  dot?: boolean;
  icon?: ReactNode;
  size?: "sm" | "md";
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span title={title} className={clsx("inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border text-micro font-medium tracking-[0.01em]",
      size === "sm" ? "h-5 px-1.5" : "h-6 px-2", TAG[tone], className)}>
      {dot && <span aria-hidden className={clsx("dot size-1.5", DOT_TONE[tone])} />}
      {icon && <span aria-hidden className="flex">{icon}</span>}
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------- status dot (the only round shape) */

export type Health = "ok" | "warn" | "error" | "off" | "busy";
const DOT: Record<Health, string> = { ok: "bg-ok", warn: "bg-warning", error: "bg-critical", off: "bg-offline", busy: "bg-ink" };

export function StatusDot({ status, label, className }: { status: Health; label?: ReactNode; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-2 text-dense", className)}>
      <span aria-hidden className={clsx("dot size-2 shrink-0", DOT[status])} />
      {label && <span>{label}</span>}
    </span>
  );
}

/* ---------------------------------------------------------------- progress bar, always with its value */

export function Progress({ value, label, detail }: { value: number; label: string; detail?: ReactNode }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="grid gap-2">
      <div className="flex items-baseline justify-between gap-3 text-dense">
        <span className="text-muted">{label}</span>
        <span className="num font-medium text-ink">{(v * 100).toFixed(1)} %</span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)}
        className="h-1 overflow-hidden bg-panel-muted outline outline-1 outline-line">
        <div className="h-full bg-ink transition-[width] duration-150" style={{ width: `${v * 100}%` }} />
      </div>
      {detail && <div className="num text-meta text-muted">{detail}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- inline banner: tint + hairline */

type BannerKind = "info" | "warning" | "error";
const BANNER: Record<BannerKind, { cls: string; icon: ReactNode }> = {
  info: { cls: "border-line bg-panel-muted text-ink", icon: <Info size={16} strokeWidth={1.5} className="text-muted" /> },
  warning: { cls: "border-warning-line bg-warning-tint text-ink", icon: <AlertTriangle size={16} strokeWidth={1.5} className="text-warning" /> },
  error: { cls: "border-critical-line bg-critical-tint text-ink", icon: <OctagonAlert size={16} strokeWidth={1.5} className="text-critical-text" /> },
};

export function Banner({
  kind = "info",
  title,
  children,
  actions,
  onDismiss,
}: {
  kind?: BannerKind;
  title?: string;
  children?: ReactNode;
  actions?: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div role={kind === "error" ? "alert" : "status"} className={clsx("flex items-start gap-3 border px-4 py-3 text-dense", BANNER[kind].cls)}>
      <span className="mt-0.5">{BANNER[kind].icon}</span>
      <div className="grid flex-1 gap-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="text-muted">{children}</div>}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
      {onDismiss && (
        <button type="button" aria-label="Dismiss" onClick={onDismiss} className="p-1 text-muted hover:bg-panel hover:text-ink">
          <X size={16} strokeWidth={1.5} />
        </button>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- static skeleton, text-only empty state */

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx("bg-panel-muted outline outline-1 outline-line", className)} />;
}

export function EmptyState({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="grid justify-items-start gap-2 py-4">
      <p className="text-title font-semibold text-ink">{title}</p>
      {children && <p className="max-w-prose text-dense text-muted">{children}</p>}
      {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
