import { useSearchParams } from "react-router-dom";
import type { Ambulance } from "@/api/client";
import { useAmbulances, useDispatches, useHealth, useQueueStats } from "@/api/queries";
import { availability, phaseOf, statusLabel, type Availability } from "@/lib/domain";
import { coord, wallOf } from "@/lib/format";
import { minutesLeft, returnLeg } from "@/lib/motion";
import { useNow } from "@/lib/useNow";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/layout";
import { Banner, EmptyState, Skeleton } from "@/ui/status";
import { DataTable, type Column } from "@/ui/Table";
import { Tabs } from "@/ui/Tabs";

type Tab = "all" | Availability;
const TABS: { value: Tab; label: string }[] = [
  { value: "all", label: "All" }, { value: "free", label: "Free" }, { value: "busy", label: "Busy" }, { value: "out", label: "Out of service" },
];

export default function Fleet() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.value === params.get("status")) ? params.get("status") : "all") as Tab;
  const ambulances = useAmbulances();
  const active = useDispatches({ status: "ACTIVE", limit: 1000 });
  const queue = useQueueStats();
  const avgSpeed = useHealth().data?.avg_speed_kmph;
  const now = useNow(1000);
  const timeScale = queue.data?.clock.time_scale ?? 60;
  const job = new Map((active.data ?? []).map((d) => [d.ambulance_id, d]));

  const all = ambulances.data ?? [];
  const count = (t: Tab) => (t === "all" ? all.length : all.filter((a) => availability(a.status) === t).length);
  const rows = tab === "all" ? all : all.filter((a) => availability(a.status) === tab);
  // Simulated minutes the ambulance has spent in its current phase.
  const inPhase = (a: Ambulance) => {
    const d = job.get(a.id);
    return d ? (Math.max(0, now - wallOf(d.phase_started_at)) * timeScale) / 60 : null;
  };
  // Simulated minutes until a returning ambulance is back at its station. Until then the server keeps it at the
  // hospital, so its position is the hospital's.
  const toStation = (a: Ambulance) => {
    const leg = returnLeg(a, avgSpeed);
    return leg ? minutesLeft(leg, now, timeScale) : null;
  };
  const timeText = (a: Ambulance) => {
    const m = inPhase(a);
    if (m != null) return `${m.toFixed(1)} min`;
    const left = toStation(a);
    return left == null ? "—" : `${left.toFixed(1)} min to station`;
  };

  const columns: Column<Ambulance>[] = [
    { key: "call", header: "Call sign", width: "120px", sortValue: (a) => a.call_sign, cell: (a) => <span className="mono font-medium">{a.call_sign}</span> },
    { key: "station", header: "Station", width: "minmax(160px,1fr)", sortValue: (a) => a.station_name, cell: (a) => <span title={a.station_name}>{a.station_name}</span> },
    { key: "status", header: "Status", width: "170px", sortValue: (a) => statusLabel(a), cell: (a) => <span className="inline-flex items-center gap-2.5"><span aria-hidden className={`m-amb ${phaseOf(a)}`} />{statusLabel(a)}</span> },
    { key: "job", header: "Incident", width: "110px", sortValue: (a) => job.get(a.id)?.incident_id ?? 0, cell: (a) => { const d = job.get(a.id); return d ? <span className="mono">#{d.incident_id}</span> : <span className="text-muted">—</span>; } },
    { key: "phase", header: "In status", width: "180px", align: "right", numeric: true, sortValue: (a) => inPhase(a) ?? toStation(a) ?? -1, cell: timeText },
    { key: "pos", header: "Position", width: "170px", align: "right", numeric: true, cell: (a) => coord(a.current_lat, a.current_lon) },
  ];

  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "Fleet" }]} title="Fleet"
        actions={<Tabs label="Show" value={tab} onChange={(v) => setParams(v === "all" ? {} : { status: v })} tabs={TABS.map((t) => ({ ...t, count: ambulances.data ? count(t.value) : undefined }))} />} />
      {ambulances.isPending && <Skeleton className="h-80" />}
      {ambulances.isError && (
        <Banner kind="error" title="Cannot load the fleet." actions={<Button size="sm" variant="secondary" onClick={() => ambulances.refetch()}>Retry</Button>}>
          {(ambulances.error as Error).message}
        </Banner>
      )}
      {ambulances.data && (
        <div className="overflow-x-auto"><div className="min-w-[870px]">
          <DataTable label="Ambulances" rows={rows} columns={columns} rowKey={(a) => a.id} fill
            empty={<EmptyState title="No ambulance matches this filter." />} />
        </div></div>
      )}
    </div>
  );
}
