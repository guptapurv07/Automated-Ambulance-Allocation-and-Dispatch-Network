import { useQueries, useQuery } from "@tanstack/react-query";
import { api, type Ambulance, type Dispatch, type Health, type Hospital, type Incident, type IncidentStatus, type IncidentDetail, type QueueStats, type Severity } from "./client";

/* The API does not push updates, so the dashboard polls. At the default simulation speed one real second
   is one simulated minute, so ambulances and dispatches are polled every second. */

export const useAmbulances = () =>
  useQuery({ queryKey: ["ambulances"], queryFn: () => api<Ambulance[]>("/ambulances", { query: { limit: 5000 } }), refetchInterval: 1000 });

export const useDispatches = (f: { status?: "ACTIVE" | "COMPLETED" | "CANCELLED"; limit?: number } = {}) =>
  useQuery({ queryKey: ["dispatches", f], queryFn: () => api<Dispatch[]>("/dispatches", { query: f }), refetchInterval: 1000, placeholderData: (p) => p });

export const useQueueStats = () => useQuery({ queryKey: ["queue"], queryFn: () => api<QueueStats>("/queue"), refetchInterval: 2000 });

export const useHealth = () => useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/health"), refetchInterval: 5000 });

/** Incident details (position, and the hospital once the ambulance leaves the scene) for a set of dispatches.
 *  An incident does not move, so each detail is fetched once, and again when its dispatch gets a hospital. */
export function useIncidentDetails(dispatches: Dispatch[]) {
  const results = useQueries({
    queries: dispatches.map((d) => ({
      queryKey: ["incident", d.incident_id, d.hospital_id ?? null],
      queryFn: () => api<IncidentDetail>(`/incidents/${d.incident_id}`),
      staleTime: Infinity,
    })),
  });
  const byIncident = new Map<number, IncidentDetail>();
  results.forEach((r, i) => r.data && byIncident.set(dispatches[i]!.incident_id, r.data));
  return byIncident;
}

/** One incident, followed every second until its ambulance has finished (or its call was merged away). */
export const useIncident = (id: number | null) =>
  useQuery({
    queryKey: ["incident", id, "live"],
    queryFn: () => api<IncidentDetail>(`/incidents/${id}`),
    enabled: id != null,
    refetchInterval: (q) => (q.state.data?.incident.status === "RESOLVED" ? false : 1000),
  });

/** The hospitals do not change while the server runs. */
export const useHospitals = () => useQuery({ queryKey: ["hospitals"], queryFn: () => api<Hospital[]>("/hospitals"), staleTime: Infinity });

/** Incidents, newest first (the API pages them; 1000 is its largest page). */
export const useIncidents = (f: { status?: IncidentStatus; severity?: Severity; top_level?: boolean; limit?: number; offset?: number } = {}) =>
  useQuery({ queryKey: ["incidents", "list", f], queryFn: () => api<Incident[]>("/incidents", { query: f }), refetchInterval: 2000, placeholderData: (p) => p });
