import { ArrowRight, Maximize, Minimize } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { Ambulance, Dispatch, IncidentDetail, QueueStats } from "@/api/client";
import { useAmbulances, useDispatches, useHealth, useIncidentDetails, useIncidents, useQueueStats } from "@/api/queries";
import { Clock } from "@/app/Shell";
import { LOCKING_LABEL, PHASES, PHASE_LABEL, phaseOf, POLICY_LABEL, SCHEDULER_LABEL, SEVERITIES, speedText, SEVERITY_LABEL, SEVERITY_TONE, STATUS_LABEL, TARGET_MINUTES, type Phase } from "@/lib/domain";
import { minutes, percent } from "@/lib/format";
import { legOf, minutesLeft } from "@/lib/motion";
import { useNow } from "@/lib/useNow";
import { Button, IconButton } from "@/ui/Button";
import { CityMap, MapLegend } from "@/ui/CityMap";
import { SimulatorPanel } from "@/ui/SimulatorPanel";
import { Card, Figure, Figures, PageHeader, Strip } from "@/ui/layout";
import { Banner, Chip, Skeleton } from "@/ui/status";

/* ---------------------------------------------------------------- right rail */

function DispatchRow({ d, amb, det, now, timeScale }: { d: Dispatch; amb?: Ambulance; det?: IncidentDetail; now: number; timeScale: number }) {
  const sev = det?.incident.severity;
  const inc = det ? ([det.incident.lat, det.incident.lon] as [number, number]) : undefined;
  const hosp = det?.hospital ? ([det.hospital.lat, det.hospital.lon] as [number, number]) : undefined;
  const leg = amb && legOf(amb.status, [amb.current_lat, amb.current_lon], d, inc, hosp);
  const left = leg ? minutesLeft(leg, now, timeScale) : null;
  let detail = amb ? STATUS_LABEL[amb.status] : "—";
  if (amb?.status === "ASSIGNED") detail = `Dispatched · ${d.eta_minutes.toFixed(1)} min out`;
  else if (amb?.status === "EN_ROUTE" && left != null) detail = `Responding · ${left.toFixed(1)} min out`;
  else if (amb?.status === "TRANSPORTING") detail = `To ${det?.hospital?.name ?? "hospital"}${left != null ? ` · ${left.toFixed(1)} min left` : ""}`;
  else if (amb?.status === "AT_HOSPITAL" && det?.hospital) detail = `At ${det.hospital.name}`;

  return (
    <li className="relative border-b border-line px-4 py-3 last:border-b-0 hover:bg-panel-muted">
      <Link to={`/incidents/${d.incident_id}`} aria-label={`Open incident ${d.incident_id}`} className="absolute inset-0" />
      <div className="flex items-center gap-2">
        <span className="mono text-[14px] font-medium text-ink">{amb?.call_sign ?? `#${d.ambulance_id}`}</span>
        <ArrowRight size={14} strokeWidth={1.5} aria-hidden className="text-faint" />
        <span className="mono text-dense text-ink">#{d.incident_id}</span>
        {sev && <Chip size="sm" tone={SEVERITY_TONE[sev]}>{SEVERITY_LABEL[sev]}</Chip>}
      </div>
      <p className="num mt-1 truncate text-meta text-muted">{detail}</p>
      {sev && d.response_minutes != null && (
        <p className="num truncate text-meta text-muted">
          Response <span className={d.response_minutes > TARGET_MINUTES[sev] ? "text-critical-text" : "text-ink"}>{d.response_minutes.toFixed(1)} min</span>
          {" "}/ {TARGET_MINUTES[sev]} target
        </p>
      )}
    </li>
  );
}

function DispatchesPanel({ dispatches, ambulances, details, timeScale }: {
  dispatches: Dispatch[]; ambulances: Ambulance[]; details: Map<number, IncidentDetail>; timeScale: number;
}) {
  const now = useNow(1000);
  const byId = new Map(ambulances.map((a) => [a.id, a]));
  const rank = (d: Dispatch) => {
    const s = details.get(d.incident_id)?.incident.severity;
    return s ? SEVERITIES.indexOf(s) : SEVERITIES.length;
  };
  const rows = [...dispatches].sort((a, b) => rank(a) - rank(b) || a.assigned_at.localeCompare(b.assigned_at));
  return (
    <Card>
      <Strip title="Active dispatches" aside={<span className="num">{dispatches.length}</span>} />
      {rows.length ? (
        <ul className="max-h-[440px] overflow-auto">
          {rows.map((d) => <DispatchRow key={d.id} d={d} amb={byId.get(d.ambulance_id)} det={details.get(d.incident_id)} now={now} timeScale={timeScale} />)}
        </ul>
      ) : <p className="px-4 py-5 text-[14px] text-muted">No active calls.</p>}
    </Card>
  );
}

function FleetPanel({ ambulances }: { ambulances: Ambulance[] }) {
  const navigate = useNavigate();
  const counts = Object.fromEntries(PHASES.map((p) => [p, 0])) as Record<Phase, number>;
  for (const a of ambulances) counts[phaseOf(a)]++;
  return (
    <Card>
      <Strip title="Fleet" aside={<span className="num">{ambulances.length}</span>} />
      <ul className="grid gap-px bg-line">
        {PHASES.map((p) => (
          <li key={p} className="flex items-center justify-between gap-3 bg-panel px-4 py-2.5">
            <span className="inline-flex items-center gap-2.5 text-dense"><span aria-hidden className={`m-amb ${p}`} />{PHASE_LABEL[p]}</span>
            <span className="num text-dense font-medium text-ink">{counts[p]}</span>
          </li>
        ))}
      </ul>
      <div className="border-t border-line px-4 py-3">
        <Button variant="text" arrow onClick={() => navigate("/fleet")}>All ambulances</Button>
      </div>
    </Card>
  );
}

function EnginePanel({ queue }: { queue?: QueueStats }) {
  const { data: h } = useHealth();
  const rows: [string, string][] = [
    ["Policy", h ? POLICY_LABEL[h.dispatch_policy] ?? h.dispatch_policy : "—"],
    ["Locking", h ? LOCKING_LABEL[h.locking_mode] ?? h.locking_mode : "—"],
    ["Queue", h ? SCHEDULER_LABEL[h.queue_scheduler] ?? h.queue_scheduler : "—"],
    ["In queue", queue ? String(queue.queue.depth) : "—"],
    ["Sim speed", queue ? speedText(queue.clock.time_scale) : "—"],
  ];
  return (
    <Card>
      <Strip title="Engine" />
      <dl className="grid grid-cols-[minmax(0,1fr)] gap-px bg-line">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-4 bg-panel px-4 py-2.5">
            <dt className="shrink-0 text-dense text-muted">{k}</dt>
            <dd className="num m-0 min-w-0 truncate text-right text-dense text-ink" title={v}>{v}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

/* ---------------------------------------------------------------- headline */

/** The numbers to read first. Response is the wait plus the drive to the patient, stored when the ambulance is
 *  assigned; on time means within the target for the call's severity. Both are for the calls in progress. */
function Headline({ ambulances, dispatches, details, waiting }: {
  ambulances: Ambulance[]; dispatches: Dispatch[]; details: Map<number, IncidentDetail>; waiting?: number;
}) {
  const free = ambulances.filter((a) => a.status === "AVAILABLE");
  const back = free.filter((a) => phaseOf(a) === "returning").length;
  const timed = dispatches.flatMap((d) => {
    const sev = details.get(d.incident_id)?.incident.severity;
    return d.response_minutes != null && sev ? [{ minutes: d.response_minutes, onTime: d.response_minutes <= TARGET_MINUTES[sev] }] : [];
  });
  const onTime = timed.filter((t) => t.onTime).length;
  return (
    <Figures className="grid-cols-2 sm:grid-cols-5">
      <Figure label="Active calls" value={String(dispatches.length)} detail="ambulances on a call" />
      <Figure label="Free" value={String(free.length)}
        detail={back ? `${back} driving back` : free.length ? "at their stations" : undefined} />
      <Figure label="Waiting" value={waiting == null ? "—" : String(waiting)} detail="for a free ambulance" />
      <Figure label="Average response" value={minutes(timed.length ? timed.reduce((s, t) => s + t.minutes, 0) / timed.length : null)}
        detail="calls in progress" />
      <Figure label="On time" value={percent(timed.length ? onTime / timed.length : null)}
        detail={timed.length ? `${onTime} of ${timed.length} calls in progress` : "calls in progress"} className="col-span-2 sm:col-span-1" />
    </Figures>
  );
}

/* ---------------------------------------------------------------- page */

export default function Live() {
  const navigate = useNavigate();
  const ambulances = useAmbulances();
  const active = useDispatches({ status: "ACTIVE", limit: 1000 });
  const queue = useQueueStats();
  const dispatches = active.data ?? [];
  const details = useIncidentDetails(dispatches);
  const timeScale = queue.data?.clock.time_scale ?? 60;
  const ambs = ambulances.data ?? [];
  // Accepted calls without an ambulance yet: on the queue, or back in the database until one is free.
  const reported = useIncidents({ status: "REPORTED", limit: 1000 });
  const unassigned = useIncidents({ status: "UNASSIGNED", limit: 1000 });
  const waiting = reported.data && unassigned.data ? reported.data.length + unassigned.data.length : undefined;
  const avgSpeedKmph = useHealth().data?.avg_speed_kmph;

  // The map card can take the whole screen (the browser's full-screen mode). Esc leaves it too, so the button
  // follows the browser's state rather than its own.
  const mapBox = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  useEffect(() => {
    const sync = () => setFull(document.fullscreenElement === mapBox.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  const toggleFull = () => {
    const done = full ? document.exitFullscreen() : mapBox.current?.requestFullscreen();
    done?.catch(() => {});     // refused by the browser: the map stays as it is
  };

  // On a wide screen the page fills the window exactly: the map takes all the height left under the header
  // and stays put, and only the panels on the right scroll. On a narrow screen everything stacks and scrolls.
  return (
    <div className="flex min-h-full flex-col min-[1100px]:h-full">
      <div className="px-4 pt-5 md:px-8 md:pt-7">
        <PageHeader crumbs={[{ label: "Live" }]} title="Live"
          aside={<Clock className="text-[14px] font-medium text-ink" />}
          actions={<Button variant="primary" onClick={() => navigate("/report")}>Report emergency</Button>} />
      </div>
      {ambulances.data && (
        <div className="px-4 pt-4 md:px-6 md:pt-6">
          <Headline ambulances={ambs} dispatches={dispatches} details={details} waiting={waiting} />
        </div>
      )}

      <div className="grid flex-1 min-[1100px]:min-h-0 min-[1100px]:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-4 p-4 md:p-6 min-[1100px]:min-h-0">
          {ambulances.isError && (
            <Banner kind="error" title="Cannot reach the dispatch server." actions={<Button size="sm" variant="secondary" onClick={() => ambulances.refetch()}>Retry</Button>}>
              Start it with <span className="mono">uvicorn app.main:app</span> on port 8000. This page reconnects on its own.
            </Banner>
          )}
          <div ref={mapBox} className="flex min-h-[420px] flex-1 flex-col">
            <Card className="flex flex-1 flex-col">
              <Strip title="Dehradun" aside={
                <IconButton variant="overlay" size="xs" className="-my-1" label={full ? "Exit full screen" : "Full screen"}
                  icon={full ? <Minimize size={16} strokeWidth={1.75} /> : <Maximize size={16} strokeWidth={1.75} />} onClick={toggleFull} />} />
              {/* own row, so the legend can wrap on a narrow screen instead of pushing the title out */}
              <div className="border-b border-line px-4 py-2"><MapLegend /></div>
              {ambulances.isPending ? <Skeleton className="flex-1 outline-0" />
                : <CityMap data={{ ambulances: ambs, dispatches, details, timeScale, avgSpeedKmph }} className="min-h-[360px] flex-1"
                    onIncident={(id) => navigate(`/incidents/${id}`)} />}
            </Card>
          </div>
        </div>
        <aside aria-label="Overview" className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4 border-t border-line p-4 md:p-6 min-[1100px]:min-h-0 min-[1100px]:overflow-y-auto min-[1100px]:border-l min-[1100px]:border-t-0">
          <SimulatorPanel />
          <DispatchesPanel dispatches={dispatches} ambulances={ambs} details={details} timeScale={timeScale} />
          <FleetPanel ambulances={ambs} />
          <EnginePanel queue={queue.data} />
        </aside>
      </div>
    </div>
  );
}
