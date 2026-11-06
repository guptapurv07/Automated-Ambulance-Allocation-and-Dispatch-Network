import { useNavigate, useSearchParams } from "react-router-dom";
import type { Incident, IncidentStatus, Severity } from "@/api/client";
import { useIncidents } from "@/api/queries";
import { INCIDENT_STATUS_HEALTH, INCIDENT_STATUS_LABEL, INCIDENT_TYPE_LABEL, isSeeded, SEVERITIES, SEVERITY_LABEL, SEVERITY_TONE } from "@/lib/domain";
import { coord, relative, timestamp, wallOf } from "@/lib/format";
import { useNow } from "@/lib/useNow";
import { Button } from "@/ui/Button";
import { Select, Switch } from "@/ui/form";
import { Card, PageHeader } from "@/ui/layout";
import { Banner, Chip, EmptyState, Skeleton, StatusDot } from "@/ui/status";
import { DataTable, type Column } from "@/ui/Table";
import { Tabs } from "@/ui/Tabs";

/* Every call, newest first, one row per accident with its number of patients (top_level). The calls loaded with
   the demo data (phone SIM-…) are hidden unless asked for. Filters live in the address, so a filtered list can be reloaded or shared. */

type Show = "all" | "waiting" | "assigned" | "resolved";
const SHOWS: { value: Show; label: string }[] = [
  { value: "all", label: "All" }, { value: "waiting", label: "Waiting" }, { value: "assigned", label: "Assigned" }, { value: "resolved", label: "Closed" },
];
const IN_SHOW: Record<Show, (s: IncidentStatus) => boolean> = {
  all: () => true,
  waiting: (s) => s === "REPORTED" || s === "UNASSIGNED",
  assigned: (s) => s === "ASSIGNED",
  resolved: (s) => s === "RESOLVED",
};

export default function Incidents() {
  const navigate = useNavigate();
  const now = useNow(10000);
  const [params, setParams] = useSearchParams();
  const show = (SHOWS.some((s) => s.value === params.get("show")) ? params.get("show") : "all") as Show;
  const severity = (SEVERITIES as string[]).includes(params.get("severity") ?? "") ? (params.get("severity") as Severity) : undefined;
  const past = params.get("past") === "1";
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };

  const { data, isPending, isError, error, refetch } = useIncidents({ severity, top_level: true, limit: 1000 });
  const accidents = (data ?? []).filter((i) => past || !isSeeded(i));
  const rows = accidents.filter((i) => IN_SHOW[show](i.status));
  const seededHidden = (data ?? []).filter(isSeeded).length;

  const columns: Column<Incident>[] = [
    { key: "id", header: "Incident", width: "100px", sortValue: (i) => i.id, cell: (i) => <span className="mono font-medium">#{i.id}</span> },
    { key: "at", header: "Received", width: "150px", sortValue: (i) => i.id, cell: (i) => <span title={relative(wallOf(i.reported_at), now)}>{timestamp(wallOf(i.reported_at), now)}</span> },
    { key: "sev", header: "Severity", width: "110px", sortValue: (i) => SEVERITIES.indexOf(i.severity), cell: (i) => <Chip size="sm" tone={SEVERITY_TONE[i.severity]}>{SEVERITY_LABEL[i.severity]}</Chip> },
    { key: "type", header: "Type", width: "minmax(140px,1fr)", sortValue: (i) => INCIDENT_TYPE_LABEL[i.incident_type], cell: (i) => INCIDENT_TYPE_LABEL[i.incident_type] },
    { key: "patients", header: "Patients", width: "90px", align: "right", numeric: true, sortValue: (i) => i.patients, cell: (i) => i.patients },
    { key: "callers", header: "Callers", width: "80px", align: "right", numeric: true, sortValue: (i) => i.report_count, cell: (i) => i.report_count },
    { key: "status", header: "Status", width: "190px", sortValue: (i) => INCIDENT_STATUS_LABEL[i.status], cell: (i) => <StatusDot status={INCIDENT_STATUS_HEALTH[i.status]} label={INCIDENT_STATUS_LABEL[i.status]} /> },
    { key: "where", header: "Where", width: "minmax(160px,1.2fr)", cell: (i) => <span className="text-muted" title={i.description ?? undefined}>{i.description || coord(i.lat, i.lon)}</span> },
  ];

  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "Incidents" }]} title="Incidents"
        actions={<Button variant="primary" onClick={() => navigate("/report")}>Report emergency</Button>} />

      <div className="flex flex-wrap items-center gap-3">
        <Tabs label="Show" value={show} onChange={(v) => set("show", v === "all" ? null : v)}
          tabs={SHOWS.map((s) => ({ ...s, count: data ? accidents.filter((i) => IN_SHOW[s.value](i.status)).length : undefined }))} />
        <Select label="Severity" value={severity ?? "ALL"} onChange={(v) => set("severity", v === "ALL" ? null : v)}
          options={[{ value: "ALL", label: "All severities" }, ...SEVERITIES.map((s) => ({ value: s, label: SEVERITY_LABEL[s] }))]} />
        <Switch label="Show seeded calls" checked={past} onChange={(v) => set("past", v ? "1" : null)} />
      </div>

      {isPending && <Skeleton className="h-96" />}
      {isError && <Banner kind="error" title="Cannot load incidents." actions={<Button size="sm" variant="secondary" onClick={() => refetch()}>Retry</Button>}>{(error as Error).message}</Banner>}
      {data && (rows.length ? (
        <div className="overflow-x-auto"><div className="min-w-[1000px]">
          <DataTable label="Incidents" rows={rows} columns={columns} rowKey={(i) => i.id} height={Math.min(rows.length, 15) * 40}
            onRowClick={(i) => navigate(`/incidents/${i.id}`)} />
        </div></div>
      ) : (
        <Card className="px-5">
          <EmptyState title={show === "all" ? "No calls yet." : "No calls match this filter."}
            actions={<Button variant="secondary" onClick={() => navigate("/report")}>Report emergency</Button>}>
            {!past && seededHidden > 0 ? `${seededHidden} seeded calls hidden.` : null}
          </EmptyState>
        </Card>
      ))}
    </div>
  );
}
