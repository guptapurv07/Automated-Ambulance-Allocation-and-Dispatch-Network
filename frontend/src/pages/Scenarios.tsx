import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, type IncidentDetail, type Severity } from "@/api/client";
import { SEVERITY_LABEL, SEVERITY_TONE, STATUS_LABEL } from "@/lib/domain";
import {
  runScene1,
  runScene2,
  runScene3,
  runScene4,
  runScene5,
  runScene6,
  type ScenarioResult,
} from "@/lib/scenarios";
import { Button } from "@/ui/Button";
import { Card, PageHeader, Strip } from "@/ui/layout";
import { Banner, Chip } from "@/ui/status";
import { toast } from "@/ui/Toast";

/* The six cases of the terminal demonstration (python -m demo), run from the dashboard. Each one sends real
   calls to the server through the normal API, so what it shows is the system at work: the duplicate check,
   several patients, row locking under simultaneous calls, the waiting queue and the service-area check. */

const SCENARIOS = [
  {
    id: 1,
    title: "One emergency",
    description: "Submit one critical cardiac emergency near Railway Station and follow its incident record.",
    run: runScene1,
  },
  {
    id: 2,
    title: "Five callers, one accident",
    description: "Send five simultaneous HIGH road-accident reports near Haridwar Bypass and verify deduplication.",
    run: runScene2,
  },
  {
    id: 3,
    title: "Multiple patients",
    description: "Send three sequential reports near Forest Research Institute and verify the final patient count and units.",
    run: runScene3,
  },
  {
    id: 4,
    title: "Concurrent emergencies",
    description: "Send simultaneous calls across the fleet, inspect ambulance assignments, then test the waiting queue.",
    run: runScene4,
  },
  {
    id: 5,
    title: "Outside the service area",
    description: "Submit a critical emergency from Delhi and verify that the server refuses it without changing fleet availability.",
    run: runScene5,
  },
  {
    id: 6,
    title: "Fleet and queue snapshot",
    description: "Fetch the current ambulance fleet and dispatch queue directly from the API.",
    run: runScene6,
  },
] as const;

/** A small table in the dashboard's style: hairlines, upper-case headers, tabular numbers. */
function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto border border-line">
      <table className="w-full text-left text-dense">
        <thead>
          <tr className="border-b border-line bg-panel-muted text-micro font-semibold uppercase tracking-[0.06em] text-muted">
            {head.map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line last:border-b-0">
              {r.map((c, j) => <td key={j} className="num px-3 py-2 text-ink">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SubTitle = ({ children }: { children: ReactNode }) => (
  <p className="text-micro font-semibold uppercase tracking-[0.08em] text-ink">{children}</p>
);

function IncidentLinks({ ids }: { ids?: number[] }) {
  if (!ids?.length) return null;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-dense">
      {ids.map((id) => (
        <Link key={id} to={`/incidents/${id}`} className="mono font-medium text-ink underline underline-offset-4 hover:text-text">#{id}</Link>
      ))}
    </div>
  );
}

const SeverityChip = ({ severity }: { severity: string }) =>
  severity in SEVERITY_LABEL
    ? <Chip size="sm" tone={SEVERITY_TONE[severity as Severity]}>{SEVERITY_LABEL[severity as Severity]}</Chip>
    : <>{severity}</>;

function ResultDetails({ result }: { result: ScenarioResult }) {
  const navigate = useNavigate();
  const details = result.details as Record<string, unknown> | undefined;
  if (!details) return null;

  if (Array.isArray(details.replies)) {
    const replies = details.replies as Array<{ incident_id: number; duplicate: boolean; report_count: number }>;
    return (
      <Table head={["Caller", "Incident", "Repeat of an earlier call", "Reports so far"]}
        rows={replies.map((r, i) => [i + 1, <span className="mono">#{r.incident_id}</span>, r.duplicate ? "Yes" : "No, new incident", r.report_count])} />
    );
  }

  if (Array.isArray(details.units)) {
    const units = (details as IncidentDetail).units;
    return (
      <div className="grid gap-2">
        <SubTitle>One ambulance per patient</SubTitle>
        <Table head={["Patient", "Incident", "Ambulance"]}
          rows={units.map((u, i) => [i + 1, <span className="mono">#{u.incident_id}</span>, <span className="mono">{u.ambulance?.call_sign ?? "waiting"}</span>])} />
      </div>
    );
  }

  if (Array.isArray(details.assignments)) {
    const assignments = details.assignments as Array<{ incidentId: number; ambulance: string; place: string; distanceKm: number }>;
    const waiting = (details.waitingOrder ?? []) as Array<{ incidentId: number; severity: string; ambulance: string }>;
    return (
      <div className="grid gap-4">
        <div className="grid gap-2">
          <SubTitle>Part 1 · Ambulance assignments</SubTitle>
          <Table head={["Place", "Incident", "Ambulance", "Distance"]}
            rows={assignments.map((r) => [r.place, <span className="mono">#{r.incidentId}</span>, <span className="mono">{r.ambulance}</span>, `${r.distanceKm.toFixed(1)} km`])} />
        </div>
        <div className="grid gap-2">
          <SubTitle>Part 2 · Waiting calls, in the order they were served</SubTitle>
          {waiting.length
            ? <Table head={["Order", "Severity", "Incident", "Ambulance"]}
                rows={waiting.map((r, i) => [i + 1, <SeverityChip severity={r.severity} />, <span className="mono">#{r.incidentId}</span>, <span className="mono">{r.ambulance}</span>])} />
            : <p className="text-dense text-muted">No waiting calls were assigned during this run.</p>}
        </div>
      </div>
    );
  }

  if (Array.isArray(details.ambulances)) {
    const ambulances = details.ambulances as Array<{ id: number; call_sign: string; station_name: string; status: keyof typeof STATUS_LABEL }>;
    const queue = details.queue as { queue?: { depth?: number; scheduler?: string; backend?: string }; workers?: { size?: number } };
    return (
      <div className="grid gap-2">
        <SubTitle>Fleet ({ambulances.length} ambulances)</SubTitle>
        <Table head={["Call sign", "Station", "Status"]}
          rows={ambulances.map((a) => [<span className="mono">{a.call_sign}</span>, a.station_name, STATUS_LABEL[a.status] ?? a.status])} />
        <p className="num text-dense text-muted">
          Queue depth {queue?.queue?.depth ?? "—"} · scheduler {queue?.queue?.scheduler ?? "—"} · queue {queue?.queue?.backend ?? "—"} · {queue?.workers?.size ?? "—"} workers
        </p>
        <div className="flex gap-4">
          <Button variant="text" arrow onClick={() => navigate("/fleet")}>Fleet</Button>
          <Button variant="text" arrow onClick={() => navigate("/system")}>System</Button>
        </div>
      </div>
    );
  }

  return null;
}

export default function Scenarios() {
  const [running, setRunning] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, ScenarioResult>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});

  async function execute(id: number, run: () => Promise<ScenarioResult>) {
    setRunning(id);
    setErrors((old) => ({ ...old, [id]: "" }));
    try {
      const result = await run();
      setResults((old) => ({ ...old, [id]: result }));
      toast.success("Scenario completed", result.message);
    } catch (error) {
      const message = error instanceof ApiError
        ? `HTTP ${error.status || "network"}: ${error.message}`
        : error instanceof Error ? error.message : "The scenario failed.";
      setErrors((old) => ({ ...old, [id]: message }));
      toast.error("Scenario failed", message);
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "Scenarios" }]} title="Scenarios"
        aside={<span className="text-meta text-muted">Six live API demonstrations</span>} />

      <Banner kind="info" title="These are real API calls">
        Each scenario sends requests to the running dispatch server and may create incidents or dispatches.
        To start with a clean fleet, stop the server and run <span className="mono">python -m sim.seed --reset</span>, then start it again.
      </Banner>

      <div className="grid gap-4">
        {SCENARIOS.map((scenario) => {
          const result = results[scenario.id];
          const error = errors[scenario.id];
          return (
            <Card key={scenario.id}>
              <Strip title={`Scene ${scenario.id} · ${scenario.title}`}
                aside={<Button variant="primary" size="sm" disabled={running !== null} onClick={() => void execute(scenario.id, scenario.run)}>
                  {running === scenario.id ? "Running…" : "Run"}
                </Button>} />
              <div className="grid gap-4 p-4">
                <p className="text-dense text-muted">{scenario.description}</p>

                {result && (
                  <div className="grid gap-3 border-t border-line pt-4">
                    <p className="text-dense font-semibold text-ink">{result.title}</p>
                    <p className="text-dense text-ink">{result.message}</p>
                    <IncidentLinks ids={result.incidentIds} />
                    <ResultDetails result={result} />
                  </div>
                )}

                {error && <Banner kind="error" title="Scenario could not complete">{error}</Banner>}

                {scenario.id === 4 && (
                  <div className="grid gap-1 border border-line bg-panel-muted p-3 text-meta text-muted">
                    <p className="font-semibold text-ink">Race-condition comparison</p>
                    <p>To show the failure mode, stop the server and restart it with locking switched off:</p>
                    <code className="mono block break-all text-ink">LOCKING_MODE=none RACE_WINDOW_MS=20 uvicorn app.main:app</code>
                    <p>Afterwards, restart it normally and reset the demo data:</p>
                    <code className="mono block text-ink">python -m sim.seed --reset</code>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
