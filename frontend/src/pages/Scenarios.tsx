import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
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
import { Banner } from "@/ui/status";
import { toast } from "@/ui/Toast";

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

function IncidentLinks({ ids }: { ids?: number[] }) {
  if (!ids?.length) return null;

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {ids.map((id) => (
        <Link
          key={id}
          to={`/incidents/${id}`}
          className="text-sm font-medium text-[var(--accent)] underline underline-offset-4"
        >
          Open incident #{id}
        </Link>
      ))}
    </div>
  );
}

function ResultDetails({ result }: { result: ScenarioResult }) {
  const details = result.details as Record<string, unknown> | undefined;
  if (!details) return null;

  if (Array.isArray(details.replies)) {
    const replies = details.replies as Array<{
      incident_id: number;
      duplicate: boolean;
      report_count: number;
    }>;
    return (
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--line)] text-[var(--muted)]">
              <th className="py-2 pr-3">Caller</th>
              <th className="py-2 pr-3">Incident</th>
              <th className="py-2 pr-3">Duplicate</th>
              <th className="py-2">Reports</th>
            </tr>
          </thead>
          <tbody>
            {replies.map((reply, index) => (
              <tr key={index} className="border-b border-[var(--line)]">
                <td className="py-2 pr-3">{index + 1}</td>
                <td className="py-2 pr-3">#{reply.incident_id}</td>
                <td className="py-2 pr-3">{reply.duplicate ? "Yes" : "No"}</td>
                <td className="py-2">{reply.report_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (Array.isArray(details.assignments)) {
    const assignments = details.assignments as Array<{
      incidentId: number;
      ambulance: string;
      place: string;
      distanceKm: number;
    }>;
    const waiting = (details.waitingOrder ?? []) as Array<{
      incidentId: number;
      severity: string;
      ambulance: string;
    }>;
    return (
      <div className="mt-4 space-y-4">
        <div className="overflow-x-auto">
          <p className="mb-2 text-sm font-semibold">Part 1 — Ambulance assignments</p>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--line)] text-[var(--muted)]">
                <th className="py-2 pr-3">Place</th>
                <th className="py-2 pr-3">Incident</th>
                <th className="py-2 pr-3">Ambulance</th>
                <th className="py-2">Distance</th>
              </tr>
            </thead>
            <tbody>
              {assignments.map((row) => (
                <tr key={row.incidentId} className="border-b border-[var(--line)]">
                  <td className="py-2 pr-3">{row.place}</td>
                  <td className="py-2 pr-3">#{row.incidentId}</td>
                  <td className="py-2 pr-3">{row.ambulance}</td>
                  <td className="py-2">{row.distanceKm.toFixed(1)} km</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="overflow-x-auto">
          <p className="mb-2 text-sm font-semibold">Part 2 — Waiting calls served</p>
          {waiting.length ? (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--line)] text-[var(--muted)]">
                  <th className="py-2 pr-3">Order</th>
                  <th className="py-2 pr-3">Severity</th>
                  <th className="py-2">Ambulance</th>
                </tr>
              </thead>
              <tbody>
                {waiting.map((row, index) => (
                  <tr key={row.incidentId} className="border-b border-[var(--line)]">
                    <td className="py-2 pr-3">{index + 1}</td>
                    <td className="py-2 pr-3">{row.severity}</td>
                    <td className="py-2">{row.ambulance}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-[var(--muted)]">No waiting calls were assigned during this run.</p>
          )}
        </div>
      </div>
    );
  }

  if (Array.isArray(details.ambulances)) {
    const ambulances = details.ambulances as Array<{
      id: number;
      call_sign: string;
      station_name: string;
      status: string;
    }>;
    const queue = details.queue as {
      queue?: { depth?: number; scheduler?: string; backend?: string };
      workers?: { size?: number; running?: boolean };
    };
    return (
      <div className="mt-4 space-y-3">
        <p className="text-sm font-semibold">Fleet snapshot ({ambulances.length} ambulances)</p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--line)] text-[var(--muted)]">
                <th className="py-2 pr-3">Call sign</th>
                <th className="py-2 pr-3">Station</th>
                <th className="py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {ambulances.map((a) => (
                <tr key={a.id} className="border-b border-[var(--line)]">
                  <td className="py-2 pr-3">{a.call_sign}</td>
                  <td className="py-2 pr-3">{a.station_name}</td>
                  <td className="py-2">{a.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-sm text-[var(--muted)]">
          Queue depth: {queue?.queue?.depth ?? "—"} · Scheduler: {queue?.queue?.scheduler ?? "—"} ·
          Backend: {queue?.queue?.backend ?? "—"} · Workers: {queue?.workers?.size ?? "—"}
        </p>
        <div className="flex gap-3 text-sm">
          <Link to="/fleet" className="text-[var(--accent)] underline">Open Fleet</Link>
          <Link to="/system" className="text-[var(--accent)] underline">Open System</Link>
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
    <div className="space-y-6">
      <PageHeader
        crumbs={["Dashboard"]}
        title="Scenarios"
        aside="Six live API demonstrations"
      />

      <Banner kind="info" title="These are real API calls">
        Each scenario sends requests to the running dispatch server and may create incidents or dispatches.
        Use a reset demo database when you want to start with a clean fleet.
      </Banner>

      <div className="grid gap-4">
        {SCENARIOS.map((scenario) => {
          const result = results[scenario.id];
          const error = errors[scenario.id];
          return (
            <Card key={scenario.id}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-[var(--muted)]">
                      SCENE {scenario.id}
                    </span>
                    <h2 className="font-semibold">{scenario.title}</h2>
                  </div>
                  <p className="mt-1 text-sm text-[var(--muted)]">{scenario.description}</p>
                </div>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={running !== null}
                  onClick={() => void execute(scenario.id, scenario.run)}
                >
                  {running === scenario.id ? "Running…" : "Run"}
                </Button>
              </div>

              {result && (
                <div className="mt-4 border-t border-[var(--line)] pt-4">
                  <p className="text-sm font-semibold">{result.title}</p>
                  <p className="mt-1 text-sm">{result.message}</p>
                  <IncidentLinks ids={result.incidentIds} />
                  <ResultDetails result={result} />
                </div>
              )}

              {error && (
                <div className="mt-4">
                  <Banner kind="error" title="Scenario could not complete">
                    {error}
                  </Banner>
                </div>
              )}

              {scenario.id === 4 && (
                <div className="mt-4 rounded-lg border border-[var(--line)] p-3 text-xs text-[var(--muted)]">
                  <p className="font-semibold">Race-condition comparison</p>
                  <p className="mt-1">To demonstrate the failure mode, stop the server and restart with locking disabled:</p>
                  <code className="mt-2 block break-all">LOCKING_MODE=none RACE_WINDOW_MS=20 uvicorn app.main:app</code>
                  <p className="mt-2">After the comparison, restart normally and reset the demo data:</p>
                  <code className="mt-1 block">python -m sim.seed --reset</code>
                </div>
              )}
            </Card>
          );
        })}
      </div>

      <Strip title="Start fresh">
        <p className="text-sm text-[var(--muted)]">
          To reset the demo data, stop the server and run{" "}
          <code>python -m sim.seed --reset</code>, then start the server again.
        </p>
      </Strip>
    </div>
  );
}
