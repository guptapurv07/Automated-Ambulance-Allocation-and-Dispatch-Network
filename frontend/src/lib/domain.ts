import type { Ambulance, AmbulanceStatus, Health as ServerHealth, IncidentStatus, IncidentType, Severity } from "@/api/client";
import type { Health, Tone } from "@/ui/status";

/* The dispatch vocabulary and its colours. Colour only means urgency: CRITICAL red, HIGH amber, the rest grey;
   a free ambulance is green (ready), a busy one ink, one out of service faded grey. */

export const SEVERITIES: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
export const SEVERITY_LABEL: Record<Severity, string> = { CRITICAL: "Critical", HIGH: "High", MEDIUM: "Medium", LOW: "Low" };
export const SEVERITY_TONE: Record<Severity, Tone> = { CRITICAL: "danger", HIGH: "warning", MEDIUM: "neutral", LOW: "muted" };
/** Map markers. */
export const SEVERITY_VAR: Record<Severity, string> = { CRITICAL: "--critical", HIGH: "--warning", MEDIUM: "--ink", LOW: "--text-muted" };
/** Response targets in minutes (RESPONSE_TARGET_MINUTES in app/core/scheduling.py). */
export const TARGET_MINUTES: Record<Severity, number> = { CRITICAL: 8, HIGH: 15, MEDIUM: 30, LOW: 60 };

export const STATUS_LABEL: Record<AmbulanceStatus, string> = {
  AVAILABLE: "Free",
  ASSIGNED: "Dispatched",
  EN_ROUTE: "Responding",
  AT_SCENE: "On scene",
  TRANSPORTING: "Transporting",
  AT_HOSPITAL: "At hospital",
  OUT_OF_SERVICE: "Out of service",
};

export type Availability = "free" | "busy" | "out";
export const availability = (s: AmbulanceStatus): Availability => (s === "AVAILABLE" ? "free" : s === "OUT_OF_SERVICE" ? "out" : "busy");
export const AVAILABILITY_HEALTH: Record<Availability, Health> = { free: "ok", busy: "busy", out: "off" };
export const AVAILABILITY_LABEL: Record<Availability, string> = { free: "Free", busy: "Busy", out: "Out of service" };

export const POLICY_LABEL: Record<string, string> = {
  greedy: "Nearest unit",
  lookahead: "Lookahead",
  learned: "RL policy",
};
export const LOCKING_LABEL: Record<string, string> = {
  for_update: "FOR UPDATE",
  skip_locked: "SKIP LOCKED",
  none: "None (unsafe)",
};
export const SCHEDULER_LABEL: Record<string, string> = { fifo: "FIFO", priority: "Severity first", llf: "Least laxity first" };

/** The simulation speed against real time: "20×" (one real second is 20 simulated seconds). */
export const speedText = (timeScale: number) => `${+timeScale.toFixed(1)}×`;

export const INCIDENT_TYPE_LABEL: Record<IncidentType, string> = {
  ROAD_ACCIDENT: "Road accident",
  CARDIAC: "Heart attack",
  BREATHING: "Breathing problem",
  INJURY: "Injury",
  FIRE: "Fire",
  PREGNANCY: "Pregnancy",
  OTHER: "Other",
};

/* The phases, grouped the way they look on the map. After the hospital an ambulance drives back to its station:
   it is free on the way (AVAILABLE, with returning_since set) and can be sent to a new call. */
export type Phase = "free" | "returning" | "to_patient" | "at_scene" | "with_patient" | "at_hospital" | "out";
export const phaseOf = ({ status: s, returning_since }: Pick<Ambulance, "status" | "returning_since">): Phase =>
  s === "AVAILABLE" ? (returning_since ? "returning" : "free") : s === "OUT_OF_SERVICE" ? "out" : s === "AT_SCENE" ? "at_scene"
    : s === "TRANSPORTING" ? "with_patient" : s === "AT_HOSPITAL" ? "at_hospital" : "to_patient";
export const PHASE_LABEL: Record<Phase, string> = {
  free: "Free",
  returning: "Returning",
  to_patient: "Responding",
  at_scene: "On scene",
  with_patient: "Transporting",
  at_hospital: "At hospital",
  out: "Out of service",
};
export const PHASES: Phase[] = ["free", "returning", "to_patient", "at_scene", "with_patient", "at_hospital", "out"];

/** An ambulance's status in words, telling a free one at its station from one driving back to it. */
export const statusLabel = (a: Pick<Ambulance, "status" | "returning_since">) =>
  phaseOf(a) === "returning" ? PHASE_LABEL.returning : STATUS_LABEL[a.status];

/** The box the backend accepts calls from (GET /health → service_area). The server checks it too and refuses
 *  calls from outside with 422; checking here first lets the form say so up front. */
export type ServiceArea = NonNullable<ServerHealth["service_area"]>;
export const inServiceArea = (a: ServiceArea, lat: number, lon: number) =>
  lat >= a.lat_min && lat <= a.lat_max && lon >= a.lon_min && lon <= a.lon_max;
export const serviceAreaText = (a: ServiceArea) =>
  `lat ${a.lat_min.toFixed(2)}–${a.lat_max.toFixed(2)}, lon ${a.lon_min.toFixed(2)}–${a.lon_max.toFixed(2)}`;

/** The backend's default speed (SIM_TIME_SCALE in .env.example); a server can be started faster or slower. */
export const DEFAULT_TIME_SCALE = 60;

export const REDIS_LABEL: Record<string, string> = { off: "Off", connected: "Connected", unreachable: "Unreachable" };
export const QUEUE_BACKEND_LABEL: Record<string, string> = { memory: "In memory", redis: "Redis" };

export const INCIDENT_STATUS_LABEL: Record<IncidentStatus, string> = {
  REPORTED: "Queued",
  UNASSIGNED: "No ambulance free",
  ASSIGNED: "Assigned",
  RESOLVED: "Closed",
};
export const INCIDENT_STATUS_HEALTH: Record<IncidentStatus, Health> = { REPORTED: "warn", UNASSIGNED: "error", ASSIGNED: "busy", RESOLVED: "off" };

/** The past calls sim/seed.py loads come with caller phones SIM-000001, SIM-000002, … */
export const isSeeded = (i: { caller_phone?: string | null }) => !!i.caller_phone?.startsWith("SIM-");
