import { useNavigate, useSearchParams } from "react-router-dom";
import type { Dispatch, Incident } from "@/api/client";
import { useAmbulances, useHistory, useHospitals } from "@/api/queries";
import { isSeeded, SEVERITIES, SEVERITY_LABEL, SEVERITY_TONE, TARGET_MINUTES } from "@/lib/domain";
import { km, timestamp, wallOf } from "@/lib/format";
import { useNow } from "@/lib/useNow";
import { Button } from "@/ui/Button";
import { Switch } from "@/ui/form";
import { Card, PageHeader } from "@/ui/layout";
import { Banner, Chip, EmptyState, Skeleton } from "@/ui/status";
import { DataTable, type Column } from "@/ui/Table";

/* Finished dispatches and how fast each patient was reached. The response time is stored by the server when the
   ambulance is assigned: the wait for a free ambulance plus its drive to the patient, in simulated minutes. It is
   compared with the target for the call's severity (CRITICAL 8, HIGH 15, MEDIUM 30, LOW 60 minutes). */

type Row = { d: Dispatch; i: Incident; late: boolean };

export default function History() {
  const navigate = useNavigate();
  const now = useNow(30000);
  const [params, setParams] = useSearchParams();
  const sessionOnly = params.get("session") === "1";
  const history = useHistory();
  const ambulances = useAmbulances();
  const hospitals = useHospitals();
  const callSign = new Map((ambulances.data ?? []).map((a) => [a.id, a.call_sign]));
  const hospitalName = new Map((hospitals.data ?? []).map((h) => [h.id, h.name]));

  const rows: Row[] = [];
  for (const d of history.data?.dispatches ?? []) {
    const i = history.data!.incidents.get(d.incident_id);
    if (!i || (sessionOnly && isSeeded(i))) continue;
    rows.push({ d, i, late: d.response_minutes != null && d.response_minutes > TARGET_MINUTES[i.severity] });
  }

  const columns: Column<Row>[] = [
    { key: "inc", header: "Incident", width: "100px", sortValue: (r) => r.i.id, cell: (r) => <span className="mono font-medium">#{r.i.id}</span> },
    { key: "sev", header: "Severity", width: "110px", sortValue: (r) => SEVERITIES.indexOf(r.i.severity), cell: (r) => <Chip size="sm" tone={SEVERITY_TONE[r.i.severity]}>{SEVERITY_LABEL[r.i.severity]}</Chip> },
    { key: "amb", header: "Ambulance", width: "110px", sortValue: (r) => callSign.get(r.d.ambulance_id) ?? "", cell: (r) => <span className="mono">{callSign.get(r.d.ambulance_id) ?? `#${r.d.ambulance_id}`}</span> },
    { key: "hosp", header: "Hospital", width: "minmax(180px,1fr)", sortValue: (r) => hospitalName.get(r.d.hospital_id ?? -1) ?? "", cell: (r) => <span title={hospitalName.get(r.d.hospital_id ?? -1)}>{hospitalName.get(r.d.hospital_id ?? -1) ?? "—"}</span> },
    { key: "dist", header: "Distance", width: "100px", align: "right", numeric: true, sortValue: (r) => r.d.distance_km, cell: (r) => km(r.d.distance_km) },
    { key: "resp", header: "Response", width: "110px", align: "right", numeric: true, sortValue: (r) => r.d.response_minutes ?? -1,
      cell: (r) => (r.d.response_minutes == null ? "—" : <span className={r.late ? "text-critical-text" : ""}>{r.d.response_minutes.toFixed(1)} min</span>) },
    { key: "target", header: "Target", width: "90px", align: "right", numeric: true, cell: (r) => `${TARGET_MINUTES[r.i.severity]} min` },
    { key: "done", header: "Closed", width: "150px", sortValue: (r) => r.d.completed_at ?? "", cell: (r) => (r.d.completed_at ? timestamp(wallOf(r.d.completed_at), now) : "—") },
  ];

  const lateTotal = rows.filter((r) => r.late).length;
  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "History" }]} title="History"
        aside={history.data ? <span className="num text-meta text-muted">{rows.length} finished · {lateTotal} late</span> : undefined} />

      <div className="flex flex-wrap items-center gap-3">
        <Switch label="Hide seeded calls" checked={sessionOnly}
          onChange={(v) => setParams(v ? { session: "1" } : {}, { replace: true })} />
      </div>

      {history.isPending && <Skeleton className="h-96" />}
      {history.isError && <Banner kind="error" title="Cannot load the history." actions={<Button size="sm" variant="secondary" onClick={() => history.refetch()}>Retry</Button>}>{(history.error as Error).message}</Banner>}

      {history.data && (rows.length ? (
        <section className="grid gap-3">
          <div className="overflow-x-auto"><div className="min-w-[1000px]">
            <DataTable label="Finished dispatches" rows={rows} columns={columns} rowKey={(r) => r.d.id} height={Math.min(rows.length, 15) * 40}
              onRowClick={(r) => navigate(`/incidents/${r.i.id}`)} />
          </div></div>
        </section>
      ) : (
        <Card className="px-5">
          <EmptyState title="No closed calls yet.">{sessionOnly ? "Turn off \"Hide seeded calls\" to see the seeded history." : null}</EmptyState>
        </Card>
      ))}
    </div>
  );
}
