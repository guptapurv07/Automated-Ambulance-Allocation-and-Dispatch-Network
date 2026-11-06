import { clsx } from "clsx";
import { Activity, Ambulance, FlaskConical, History, Hospital, ListChecks, Map as MapIcon, Menu as MenuIcon, PanelLeftClose, PanelLeftOpen, Siren } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useMatches, useNavigate } from "react-router-dom";
import { useAmbulances, useHealth } from "@/api/queries";
import { AVAILABILITY_HEALTH, AVAILABILITY_LABEL, availability, type Availability } from "@/lib/domain";
import { clock } from "@/lib/format";
import { usePrefs } from "@/lib/prefs";
import { IconButton, Kbd } from "@/ui/Button";
import { Dialog, Drawer } from "@/ui/overlay";
import { StatusDot, type Health } from "@/ui/status";
import { Mark } from "./Mark";

export type RouteHandle = { title: string };

const APP_NAME = "Ambulance Dispatch";

const NAV = [
  { to: "/", label: "Live", icon: MapIcon, end: true },
  { to: "/report", label: "Report emergency", icon: Siren },
  { to: "/incidents", label: "Incidents", icon: ListChecks },
  { to: "/history", label: "History", icon: History },
  { to: "/scenarios", label: "Scenarios", icon: FlaskConical },
  { to: "/fleet", label: "Fleet", icon: Ambulance },
  { to: "/hospitals", label: "Hospitals", icon: Hospital },
  { to: "/system", label: "System", icon: Activity },
];

/** Wall clock, ticking every second (Live header). */
export function Clock({ className }: { className?: string }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  return <span className={clsx("num", className)} aria-label="Local time">{clock(now)}</span>;
}

/* ---------------------------------------------------------------- sidebar */

function Nav({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="grid">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} onClick={onNavigate} title={collapsed ? label : undefined}
          className={({ isActive }) => clsx(
            "group relative flex h-10 items-center gap-3 text-[14px] font-medium transition-[background-color,color] transition-ui",
            collapsed ? "justify-center" : "px-4",
            isActive ? "bg-panel-muted text-ink" : "text-muted hover:bg-panel-muted hover:text-ink",
          )}>
          <Icon size={18} strokeWidth={1.5} className="shrink-0" />
          {!collapsed && <span className="flex-1">{label}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

/** Free / busy / out-of-service counts, each opening the Fleet page filtered to it. */
function FleetStatus({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { data } = useAmbulances();
  const counts: Record<Availability, number> = { free: 0, busy: 0, out: 0 };
  for (const a of data ?? []) counts[availability(a.status)]++;
  return (
    <div className="grid">
      {!collapsed && <div className="flex h-9 items-center pl-4 text-micro font-semibold uppercase tracking-[0.08em] text-muted">Ambulances</div>}
      {(Object.keys(counts) as Availability[]).map((k) => (
        <Link key={k} to={`/fleet?status=${k}`} onClick={onNavigate} title={collapsed ? `${AVAILABILITY_LABEL[k]}: ${counts[k]}` : undefined}
          className={clsx("flex h-9 items-center gap-3 text-[14px] text-ink transition-colors transition-ui hover:bg-panel-muted", collapsed ? "justify-center" : "px-4")}>
          <StatusDot status={AVAILABILITY_HEALTH[k]} />
          {!collapsed && <span className="min-w-0 flex-1 truncate">{AVAILABILITY_LABEL[k]}</span>}
          {!collapsed && <span className="num text-meta text-muted">{data ? counts[k] : "—"}</span>}
        </Link>
      ))}
    </div>
  );
}

function useSystemState(): { status: Health; label: string } {
  const { data, isError, isPending } = useHealth();
  if (isError) return { status: "error", label: "Server offline" };
  if (isPending || !data) return { status: "off", label: "Connecting" };
  if (data.status === "ok") return { status: "ok", label: "Running" };
  const why = data.database !== "connected" ? "database unreachable" : data.redis === "unreachable" ? "Redis unreachable" : "see System";
  return { status: "warn", label: `Degraded · ${why}` };
}

/** Flat status block at the bottom of the sidebar: the server state. */
function SystemBlock({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const state = useSystemState();
  if (collapsed)
    return (
      <Link to="/system" onClick={onNavigate} title={state.label} className="flex h-12 items-center justify-center border-t border-line hover:bg-panel-muted">
        <StatusDot status={state.status} />
      </Link>
    );
  return (
    <div role="status" className="grid gap-1.5 border-t border-line px-4 py-4">
      <StatusDot status={state.status} label={state.label} className="text-[14px] font-medium text-ink" />
    </div>
  );
}

function SidebarBody({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  return (
    <>
      <div className="grid content-start gap-3">
        <Nav collapsed={collapsed} onNavigate={onNavigate} />
        <div className="h-px bg-line" />
        <FleetStatus collapsed={collapsed} onNavigate={onNavigate} />
      </div>
      <div className="mt-auto grid pt-5">
        <SystemBlock collapsed={collapsed} onNavigate={onNavigate} />
      </div>
    </>
  );
}

function Sidebar() {
  const collapsed = usePrefs((s) => s.sidebarCollapsed);
  const set = usePrefs((s) => s.set);
  return (
    <aside className={clsx("hidden shrink-0 flex-col overflow-y-auto border-r border-line bg-panel md:flex", collapsed ? "w-[64px]" : "w-[248px]")}>
      <div className={clsx("flex h-[60px] shrink-0 items-center gap-2.5 border-b border-line", collapsed ? "justify-center" : "px-4")}>
        {!collapsed && <Mark size={18} />}
        {!collapsed && <span className="flex-1 whitespace-nowrap text-[15px] font-semibold text-ink">{APP_NAME}</span>}
        <IconButton variant="ghost" size="xs" label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          icon={collapsed ? <PanelLeftOpen size={18} strokeWidth={1.5} /> : <PanelLeftClose size={18} strokeWidth={1.5} />}
          onClick={() => set({ sidebarCollapsed: !collapsed })} />
      </div>
      <div className="flex flex-1 flex-col pt-3"><SidebarBody collapsed={collapsed} /></div>
    </aside>
  );
}

/* ---------------------------------------------------------------- keyboard */

const GLOBAL_KEYS: [string, string][] = [["g l", "Live"], ["g r", "Report emergency"], ["g i", "Incidents"], ["g h", "History"], ["g f", "Fleet"], ["g s", "System"], ["?", "Keyboard shortcuts"]];
const SHEET: { title: string; keys: [string, string][] }[] = [{ title: "Anywhere", keys: GLOBAL_KEYS }];

export function isTyping(e: KeyboardEvent) {
  const t = e.target;
  return t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.getAttribute("role") === "slider");
}

function useGlobalKeys(openSheet: () => void) {
  const navigate = useNavigate();
  useEffect(() => {
    let g = 0;
    const go: Record<string, string> = { l: "/", r: "/report", i: "/incidents", h: "/history", f: "/fleet", s: "/system" };
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
        e.preventDefault();
        openSheet();
      } else if (e.key === "g") g = Date.now();
      else if (Date.now() - g < 1000 && go[e.key]) {
        e.preventDefault();
        g = 0;
        navigate(go[e.key]!);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, openSheet]);
}

function ShortcutSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Keyboard shortcuts" width="max-w-lg">
      <div className="grid gap-5 pb-3">
        {SHEET.map((g) => (
          <section key={g.title}>
            <h3 className="mb-2 text-dense text-muted">{g.title}</h3>
            <dl className="grid grid-cols-[132px_1fr] gap-y-2 text-dense">
              {g.keys.map(([k, d]) => (
                <div key={k} className="contents">
                  <dt className="flex gap-1">{k.split(" ").map((p, i) => <Kbd key={i}>{p}</Kbd>)}</dt>
                  <dd className="m-0 self-center text-text">{d}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- shell */

/** Full-bleed: sidebar | main, separated by a hairline. Pages render their own header. */
export function Shell() {
  const matches = useMatches();
  const title = [...matches].reverse().map((m) => (m.handle as RouteHandle | undefined)?.title).find(Boolean) ?? APP_NAME;
  const [sheet, setSheet] = useState(false);
  const [menu, setMenu] = useState(false);
  useGlobalKeys(() => setSheet(true));
  useEffect(() => {
    document.title = `${title} · ${APP_NAME}`;
  }, [title]);

  return (
    <div className="h-dvh bg-app text-ink">
      <div className="flex h-full overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-4 md:hidden">
            <IconButton variant="raised" label="Menu" icon={<MenuIcon size={18} strokeWidth={1.5} />} onClick={() => setMenu(true)} />
            <Mark size={18} />
            <span className="text-[16px] font-semibold">{APP_NAME}</span>
          </div>
          {/* tabIndex: the scroll area must be reachable by keyboard */}
          <main tabIndex={0} className="flex min-h-0 flex-1 flex-col overflow-auto outline-offset-[-2px]">
            <Outlet />
          </main>
        </div>
      </div>
      <ShortcutSheet open={sheet} onOpenChange={setSheet} />
      <Drawer open={menu} onOpenChange={setMenu} title={APP_NAME} width="w-[300px]" side="left">
        <div className="-mx-5 -my-4 flex min-h-full flex-col">
          <SidebarBody collapsed={false} onNavigate={() => setMenu(false)} />
        </div>
      </Drawer>
    </div>
  );
}
