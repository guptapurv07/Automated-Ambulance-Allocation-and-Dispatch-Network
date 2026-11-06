import { useNavigate, useSearchParams } from "react-router-dom";
import type { Dispatch, Incident } from "@/api/client";
import { useAmbulances, useHistory, useHospitals } from "@/api/queries";
import { isSeeded, SEVERITIES, SEVERITY_LABEL, SEVERITY_TONE, SEVERITY_VAR, TARGET_MINUTES } from "@/lib/domain";
import { km, minutes, percent, timestamp, wallOf } from "@/lib/format";
import { useNow } from "@/lib/useNow";
import { Button } from "@/ui/Button";
import { Switch } from "@/ui/form";
import { Card, Figure, Figures, PageHeader, Strip } from "@/ui/layout";
import { Banner, Chip, EmptyState, Skeleton } from "@/ui/status";
import { DataTable, type Column } from "@/ui/Table";
import { Tooltip } from "@/ui/Tooltip";

/* Finished dispatches and how fast each patient was reached. The response time is stored by the server when the
   ambulance is assigned: the wait for a free ambulance plus its drive to the patient, in simulated minutes. It is
   compared with the target for the call's severity (CRITICAL 8, HIGH 15, MEDIUM 30, LOW 60 minutes). */

type Row = { d: Dispatch; i: Incident; late: boolean };

/** The calls with a stored response time: how many, how many late, the share on time and the average response. */
function summary(rows: Row[]) {
  const timed = rows.filter((r) => r.d.response_minutes != null);
  const late = timed.filter((r) => r.late).length;
  return {
    calls: timed.length,
    late,
    onTime: timed.length ? (timed.length - late) / timed.length : null,
    average: timed.length ? timed.reduce((s, r) => s + r.d.response_minutes!, 0) / timed.length : null,
  };
}

/** One bar per severity: the share of its calls reached within the target, in the severity's own colour. The name
 *  and the share are written beside each bar, so the colour is never the only way to read it. */
function OnTimeChart({ rows }: { rows: Row[] }) {
  return (
    <Card>
      <Strip title="On time, by severity" />
      <ul aria-label="Share of calls reached within the target, by severity" className="grid gap-1 p-2">
        {SEVERITIES.map((s) => {
          const t = summary(rows.filter((r) => r.i.severity === s));
          const detail = t.calls ? `${t.late} of ${t.calls} late · average ${minutes(t.average)} · target ${TARGET_MINUTES[s]} min` : "No calls";
          return (
            <Tooltip key={s} content={t.calls
              ? <span><strong className="font-semibold">{percent(t.onTime)} on time</strong> · {SEVERITY_LABEL[s]}: {t.calls - t.late} of {t.calls} within {TARGET_MINUTES[s]} min</span>
              : `No ${SEVERITY_LABEL[s]} calls`}>
              <li tabIndex={0} aria-label={`${SEVERITY_LABEL[s]}: ${percent(t.onTime)} on time. ${detail}`}
                className="grid grid-cols-[72px_minmax(0,1fr)_64px] items-center gap-x-3 gap-y-1 px-2 py-2 outline-none hover:bg-panel-muted focus-visible:bg-panel-muted">
                <span className="text-dense text-ink">{SEVERITY_LABEL[s]}</span>
                <span aria-hidden className="h-3 bg-surface-active">
                  <span className="block h-full" style={{ width: `${(t.onTime ?? 0) * 100}%`, background: `var(${SEVERITY_VAR[s]})` }} />
                </span>
                <span className="num text-right text-dense font-medium text-ink">{percent(t.onTime)}</span>
                <span className="col-span-2 col-start-2 text-meta text-muted">{detail}</span>
              </li>
            </Tooltip>
          );
        })}
      </ul>
    </Card>
  );
}

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

  const all = summary(rows);
  // The API pages finished dispatches 1,000 at a time, and this page reads the latest page only.
  const capped = (history.data?.dispatches.length ?? 0) >= 1000;
  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "History" }]} title="History" />

      <div className="flex flex-wrap items-center gap-3">
        <Switch label="Hide seeded calls" checked={sessionOnly}
          onChange={(v) => setParams(v ? { session: "1" } : {}, { replace: true })} />
      </div>

      {history.isPending && <Skeleton className="h-96" />}
      {history.isError && <Banner kind="error" title="Cannot load the history." actions={<Button size="sm" variant="secondary" onClick={() => history.refetch()}>Retry</Button>}>{(history.error as Error).message}</Banner>}

      {history.data && (rows.length ? (
        <>
          <div className="grid items-stretch gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <Figures className="grid-cols-2">
              <Figure label="Finished" value={rows.length.toLocaleString("en-IN")}
                detail={sessionOnly ? "seeded calls hidden" : capped ? "the latest 1,000" : undefined} />
              <Figure label="Average response" value={minutes(all.average)} detail="wait and drive to the patient" />
              <Figure label="On time" value={percent(all.onTime)} detail="within the target for the severity" />
              <Figure label="Late" value={all.late.toLocaleString("en-IN")} detail={`of ${all.calls.toLocaleString("en-IN")} calls`} />
            </Figures>
            <OnTimeChart rows={rows} />
          </div>
          <section className="grid gap-3">
            <div className="overflow-x-auto"><div className="min-w-[1000px]">
              <DataTable label="Finished dispatches" rows={rows} columns={columns} rowKey={(r) => r.d.id} fill
                onRowClick={(r) => navigate(`/incidents/${r.i.id}`)} />
            </div></div>
          </section>
        </>
      ) : (
        <Card className="px-5">
          <EmptyState title="No closed calls yet.">{sessionOnly ? "Turn off \"Hide seeded calls\" to see the seeded history." : null}</EmptyState>
        </Card>
      ))}
    </div>
  );
}
