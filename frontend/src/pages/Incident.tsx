import { clsx } from "clsx";
import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { Ambulance, Dispatch, IncidentDetail } from "@/api/client";
import { useHealth, useHospitals, useIncident, useQueueStats } from "@/api/queries";
import { INCIDENT_TYPE_LABEL, SEVERITY_LABEL, SEVERITY_TONE, statusLabel, TARGET_MINUTES } from "@/lib/domain";
import { km, timestamp, wallOf } from "@/lib/format";
import { legOf, minutesLeft, type LatLon } from "@/lib/motion";
import { useNow } from "@/lib/useNow";
import { Button } from "@/ui/Button";
import { CityMap } from "@/ui/CityMap";
import { Card, PageHeader, Strip } from "@/ui/layout";
import { Banner, Chip, Skeleton } from "@/ui/status";
import { NotFound } from "./NotFound";

/* One incident, followed live: the ambulance chosen for it, its drive to the patient along the roads, the
   hospital and the end of the call. Opened from the Incidents list, the map, a dispatch, or right after a call
   is reported. */

const STEPS = ["Received", "Dispatched", "Responding", "On scene", "Transporting", "At hospital", "Closed"];
const STEP_OF: Partial<Record<Ambulance["status"], number>> = { ASSIGNED: 1, EN_ROUTE: 2, AT_SCENE: 3, TRANSPORTING: 4, AT_HOSPITAL: 5 };

type Unit = { incident_id: number; dispatch?: Dispatch | null; ambulance?: Ambulance | null; hospital?: IncidentDetail["hospital"] };

function stepOf(u: Unit, resolved: boolean) {
  if (resolved || u.dispatch?.status === "COMPLETED") return 6;
  if (!u.dispatch || !u.ambulance) return 0;
  return STEP_OF[u.ambulance.status] ?? 1;
}

function Figure({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="grid content-start gap-1 bg-panel p-4">
      <span className="text-dense text-muted">{label}</span>
      <span className="num truncate text-[22px] font-light leading-7 text-ink">{value}</span>
      {detail && <span className="text-meta text-muted">{detail}</span>}
    </div>
  );
}

export default function Incident() {
  const id = Number(useParams().id);
  if (!Number.isInteger(id) || id <= 0) return <NotFound />;
  return <Tracker id={id} />;
}

function Tracker({ id }: { id: number }) {
  const navigate = useNavigate();
  const { data, isPending, isError, error } = useIncident(id);
  const queue = useQueueStats();
  const hospitals = useHospitals();
  const now = useNow(1000);
  const timeScale = queue.data?.clock.time_scale ?? 60;
  const avgSpeedKmph = useHealth().data?.avg_speed_kmph;

  const header = (aside?: ReactNode) => (
    <PageHeader crumbs={[{ label: "Incidents", to: "/incidents" }, { label: `Incident #${id}` }]} title={`Incident #${id}`} aside={aside}
      actions={<>
        <Button variant="secondary" onClick={() => navigate("/")}>Live map</Button>
      </>} />
  );
  if (isPending) return <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">{header()}<Skeleton className="h-96" /></div>;
  if (isError || !data)
    return <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">{header()}<Banner kind="error" title="Couldn't load this incident.">{(error as Error | null)?.message}</Banner></div>;

  const inc = data.incident;
  const resolved = inc.status === "RESOLVED";
  const units: Unit[] = data.units.length ? data.units : [{ incident_id: inc.id, dispatch: data.dispatch, ambulance: data.ambulance, hospital: data.hospital }];
  const main = units[0]!;
  const step = stepOf(main, resolved);
  const waiting = !main.dispatch && !resolved;
  const noAmbulance = waiting && inc.status === "UNASSIGNED";      // queued is normal for a moment; this is not
  const here: LatLon = [inc.lat, inc.lon];

  // Map: this call's ambulances driving to it, and on to their hospitals.
  const ambulances = units.flatMap((u) => (u.ambulance ? [u.ambulance] : []));
  const dispatches = units.flatMap((u) => (u.dispatch?.status === "ACTIVE" ? [u.dispatch] : []));
  const details = new Map(units.map((u) => [u.incident_id, { ...data, incident: { ...inc, id: u.incident_id }, hospital: u.hospital ?? null }]));

  const amb = main.ambulance;
  const leg = amb && main.dispatch && legOf(amb.status, [amb.current_lat, amb.current_lon], main.dispatch, here,
    main.hospital ? [main.hospital.lat, main.hospital.lon] : undefined);
  const left = leg ? minutesLeft(leg, now, timeScale) : null;
  const response = main.dispatch?.response_minutes;
  const target = TARGET_MINUTES[inc.severity];

  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      {header(<>
        <Chip size="sm" tone={SEVERITY_TONE[inc.severity]}>{SEVERITY_LABEL[inc.severity]}</Chip>
        <Chip size="sm" tone={resolved ? "success" : noAmbulance ? "warning" : "neutral"} dot>{resolved ? "Closed" : noAmbulance ? "No ambulance free" : waiting ? "Queued" : STEPS[step]}</Chip>
      </>)}

      {noAmbulance && <Banner kind="warning" title="No ambulance free">Next free ambulance takes it, most urgent call first.</Banner>}
      {waiting && !noAmbulance && <Banner kind="info" title="Queued">Picking an ambulance.</Banner>}
      {resolved && <Banner kind="info" title="Closed">{main.hospital ? `Handed over at ${main.hospital.name}. The ambulance is returning to station.` : "The ambulance is returning to station."}</Banner>}

      <div className="grid gap-px border border-line bg-line sm:grid-cols-2 xl:grid-cols-4">
        <Figure label="Ambulance" value={amb ? <span className="mono">{amb.call_sign}</span> : "—"} detail={amb ? statusLabel(amb) : waiting ? "Queued" : undefined} />
        <Figure label="Distance" value={main.dispatch ? km(main.dispatch.distance_km) : "—"} detail={main.dispatch ? `${main.dispatch.eta_minutes.toFixed(1)} min drive` : undefined} />
        <Figure label="Response" value={response == null ? "—" : `${response.toFixed(1)} min`}
          detail={response == null ? `Target ${target} min` : <span className={response > target ? "text-critical-text" : "text-ok"}>{response > target ? "Late" : "On time"} · target {target} min</span>} />
        <Figure label="Hospital" value={main.hospital?.name ?? "—"} detail={main.hospital ? (left != null && step === 4 ? `${left.toFixed(1)} min away` : undefined) : "Picked on leaving the scene"} />
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card>
          <Strip title="Map" aside={<span className="num">{inc.lat.toFixed(4)}, {inc.lon.toFixed(4)}</span>} />
          <CityMap data={{ ambulances, dispatches, details, timeScale, avgSpeedKmph, incidents: [inc], hospitals: hospitals.data, fit: [here, ...ambulances.map((a) => [a.current_lat, a.current_lon] as LatLon)] }}
            className="h-[min(560px,calc(100dvh-340px))] min-h-[380px]" />
        </Card>

        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
          <Card>
            <Strip title="Progress" />
            <ol>
              {STEPS.map((s, i) => {
                const done = i < step || (i === 6 && step === 6);
                const current = i === step && step !== 6;
                let detail: string | null = null;
                if (current && i === 0 && waiting) detail = noAmbulance ? "No ambulance free" : "Queued";
                if (current && i === 2 && left != null) detail = `${left.toFixed(1)} min left`;
                if (current && i === 4) detail = `${main.hospital?.name ?? "Hospital"}${left != null ? ` · ${left.toFixed(1)} min left` : ""}`;
                return (
                  <li key={s} className={clsx("flex items-start gap-3 border-b border-line px-4 py-2.5 last:border-b-0", current && "bg-panel-muted")}>
                    <span aria-hidden className={clsx("mt-0.5 inline-flex size-4 shrink-0 items-center justify-center border",
                      done ? "border-ok bg-ok text-white" : current ? "border-ink" : "border-line-strong")}>
                      {done && <Check size={12} strokeWidth={2} />}
                    </span>
                    <span className="grid min-w-0">
                      <span className={clsx("text-dense", done || current ? "text-ink" : "text-muted", current && "font-medium")}>{s}</span>
                      {detail && <span className="num truncate text-meta text-muted">{detail}</span>}
                    </span>
                  </li>
                );
              })}
            </ol>
          </Card>

          <Card>
            <Strip title="Call" />
            <dl className="grid grid-cols-[minmax(0,1fr)] gap-px bg-line">
              {([
                ["Status", data.message],
                ["Type", INCIDENT_TYPE_LABEL[inc.incident_type]],
                ["Patients", String(inc.patients)],
                ["Callers", String(inc.report_count)],
                ["Received", timestamp(wallOf(inc.reported_at))],
                ["Caller phone", inc.caller_phone ?? "—"],
                ["Notes", inc.description ?? "—"],
              ] as const).map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-4 bg-panel px-4 py-2.5">
                  <dt className="shrink-0 text-dense text-muted">{k}</dt>
                  <dd className="num m-0 min-w-0 truncate text-right text-dense text-ink" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
          </Card>

          {units.length > 1 && (
            <Card>
              <Strip title="Ambulances" aside={<span className="num">{units.filter((u) => u.dispatch).length} of {units.length} sent</span>} />
              <ul>
                {units.map((u, n) => (
                  <li key={u.incident_id} className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5 text-dense last:border-b-0">
                    <span className="text-muted">Patient {n + 1}</span>
                    <span className="mono text-ink">{u.ambulance?.call_sign ?? "—"}</span>
                    <span className="truncate text-muted">{STEPS[stepOf(u, resolved)]}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
