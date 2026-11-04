import type { components } from "./schema";

/* Types come from the backend's OpenAPI schema (npm run gen:api, with the API running) — never hand-written,
   except QueueStats below, which the API returns as plain dictionaries. */
type S = components["schemas"];
export type Ambulance = S["AmbulanceOut"];
export type AmbulanceStatus = S["AmbulanceStatus"];
export type Dispatch = S["DispatchOut"];
export type Incident = S["IncidentOut"];
export type IncidentDetail = S["AllocationResult"];
export type Hospital = S["HospitalOut"];
export type Health = S["HealthOut"];
export type Severity = S["Severity"];
export type IncidentType = S["IncidentType"];
export type IncidentStatus = S["IncidentStatus"];
export type IncidentCreate = S["IncidentCreate"];
export type IncidentAccepted = S["IncidentAccepted"];

/** GET /queue: dispatch_queue.stats(), worker_pool.stats() and simulation_clock.stats() on the server. */
export type QueueStats = {
  queue: { backend: string; scheduler: string; depth: number; peak_depth: number; enqueued_total: number; dequeued_total: number };
  workers: { size: number; running: boolean; handled: number; failed: number };
  // leader: this server runs the clock (with Redis, one of several servers does)
  clock: { running: boolean; leader: boolean; ticks: number; transitions: number; requeued: number; time_scale: number };
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

export function qs(q?: Query) {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v != null && v !== "") p.append(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** FastAPI errors are {"detail": "text"} or {"detail": [{"msg": …}, …]} for validation. */
function detailOf(data: unknown, fallback: string) {
  const d = (data as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg ?? "").filter(Boolean).join("; ") || fallback;
  return fallback;
}

/** All calls go to /api/…; the dev server forwards them to the backend (vite.config.ts). */
export async function api<T>(path: string, opts: { method?: string; body?: unknown; query?: Query; signal?: AbortSignal } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}${qs(opts.query)}`, { method: opts.method ?? "GET", headers, body, signal: opts.signal });
  } catch {
    throw new ApiError(0, "The dispatch server is not reachable.");
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  // The dev proxy answers 5xx with an empty body when the backend is down.
  if (!res.ok) throw new ApiError(res.status, res.status >= 500 && !data ? "The dispatch server is not reachable." : detailOf(data, res.statusText));
  return data as T;
}
