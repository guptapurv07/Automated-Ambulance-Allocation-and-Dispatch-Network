import { clsx } from "clsx";
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

/* Page structure: white panels with a 1 px hairline on the graphite background, a 28 px semibold page title
   above a full-width rule, 18 px section titles, small upper-case panel labels. No radius, no shadows. */

export function Card({ className, children, as: As = "section", ...rest }: {
  className?: string;
  children: ReactNode;
  as?: "section" | "div" | "article" | "aside";
} & React.HTMLAttributes<HTMLElement>) {
  return <As className={clsx("border border-line bg-panel", className)} {...rest}>{children}</As>;
}

/** Card title (small upper-case label) with an optional subtitle and actions on the right. */
export function CardHeader({ title, subtitle, actions, className }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-start justify-between gap-3", className)}>
      <div className="grid min-w-0 gap-0.5">
        <h2 className="text-micro font-semibold uppercase tracking-[0.08em] text-ink">{title}</h2>
        {subtitle && <p className="text-dense text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export type Crumb = { label: ReactNode; to?: string };

/** A 28 px semibold page title with its status and actions on the same row. A breadcrumb is shown only for a nested page
    (Incidents / Incident #12): on a top-level page it would just repeat the title. */
export function PageHeader({ crumbs, title, actions, aside, className }: {
  crumbs: Crumb[];
  title: ReactNode;
  actions?: ReactNode;
  aside?: ReactNode;            // before the actions (clock, status chip)
  className?: string;
}) {
  return (
    // Full-bleed: cancels the page padding so the rule under the header runs edge to edge.
    <header className={clsx("-mx-4 -mt-5 grid gap-1 border-b border-line bg-panel px-4 py-4 md:-mx-8 md:-mt-7 md:px-8", className)}>
      {crumbs.length > 1 && (
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2 text-meta">
          {crumbs.map((c, i) => {
            const last = i === crumbs.length - 1;
            return (
              <Fragment key={i}>
                {i > 0 && <span aria-hidden className="text-faint">/</span>}
                {c.to && !last ? (
                  <Link to={c.to} className="truncate text-muted hover:text-text">{c.label}</Link>
                ) : (
                  <span className={clsx("truncate", last ? "text-text" : "text-muted")} aria-current={last ? "page" : undefined}>{c.label}</span>
                )}
              </Fragment>
            );
          })}
        </nav>
      )}
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <h1 className="min-w-0 text-page font-semibold text-ink">{title}</h1>
        {(aside || actions) && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {aside && <div className="flex flex-wrap items-center gap-3">{aside}</div>}
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
          </div>
        )}
      </div>
    </header>
  );
}

/** 18 px section title with square icon actions. */
export function SectionTitle({ title, actions, className }: { title: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-center justify-between gap-4", className)}>
      <h2 className="text-h2 font-semibold text-ink">{title}</h2>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A square tile holding an icon. */
export function IconTile({ children, tone = "muted", size = 40 }: { children: ReactNode; tone?: "muted" | "danger" | "warning" | "card"; size?: 40 | 44 }) {
  const tones = { muted: "border-line bg-panel-muted text-muted", danger: "border-critical-line bg-critical-tint text-critical-text", warning: "border-warning-line bg-warning-tint text-warning", card: "border-line bg-panel text-ink" };
  return (
    <span aria-hidden className={clsx("inline-flex shrink-0 items-center justify-center border", tones[tone], size === 40 ? "size-10" : "size-11")}>
      {children}
    </span>
  );
}

/** Panel header strip: a title on the left, muted detail or actions on the right, a rule underneath. */
export function Strip({ title, aside, className }: { title: ReactNode; aside?: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex min-h-12 items-center justify-between gap-3 border-b border-line px-4 py-2.5", className)}>
      <h2 className="min-w-0 truncate text-micro font-semibold uppercase tracking-[0.08em] text-ink">{title}</h2>
      {aside && <div className="flex shrink-0 items-center gap-3 text-meta text-muted">{aside}</div>}
    </div>
  );
}
