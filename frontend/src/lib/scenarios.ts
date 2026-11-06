import { api, type IncidentAccepted, type IncidentDetail, type IncidentCreate } from "@/api/client";

type CallInput = IncidentCreate;
type AcceptedCall = IncidentAccepted;

export type ScenarioResult = {
  title: string;
  message: string;
  incidentIds?: number[];
  details?: unknown;
};

const PLACES = {
  railway: { name: "Railway Station", lat: 30.3140, lon: 78.0330 },
  bypass: { name: "Haridwar Bypass", lat: 30.2740, lon: 78.0480 },
  forest: { name: "Forest Research Institute", lat: 30.3420, lon: 77.9990 },
};

const BURST_PLACES = [
  ["Clock Tower", 30.3255, 78.0413],
  ["ISBT", 30.2892, 77.9959],
  ["Rajpur Road", 30.3563, 78.0664],
  ["Prem Nagar", 30.3320, 77.9412],
  ["Raipur", 30.3241, 78.1086],
  ["Clement Town", 30.2666, 78.0064],
  ["Selaqui", 30.3696, 77.8558],
  ["Doiwala", 30.1786, 78.1203],
  ["Mussoorie", 30.4598, 78.0644],
  ["Vikasnagar", 30.4686, 77.7736],
] as const;

const BURST_MIX = [
  ["CRITICAL", "CARDIAC"], ["MEDIUM", "INJURY"], ["LOW", "OTHER"],
  ["HIGH", "ROAD_ACCIDENT"], ["LOW", "INJURY"], ["CRITICAL", "BREATHING"],
  ["MEDIUM", "PREGNANCY"], ["HIGH", "FIRE"], ["LOW", "OTHER"],
  ["CRITICAL", "ROAD_ACCIDENT"], ["HIGH", "CARDIAC"], ["MEDIUM", "BREATHING"],
] as const;

const BURST_OFFSETS = [
  [0.0007, 0.0007],
  [-0.0007, -0.0005],
  [0.0005, -0.0008],
] as const;

const BURST_CALLS = BURST_PLACES.flatMap(([name, lat, lon], k) =>
  BURST_OFFSETS.map(([dLat, dLon], j) => {
    const [severity, incident_type] = BURST_MIX[(3 * k + j) % BURST_MIX.length]!;
    return {
      place: name,
      lat: lat + dLat,
      lon: lon + dLon,
      severity,
      incident_type,
      patients: 1,
    } as CallInput & { place: string };
  }),
);

const LATE_CALLS: CallInput[] = [
  { severity: "LOW", incident_type: "INJURY", lat: 30.3180, lon: 78.0300, patients: 1 },
  { severity: "LOW", incident_type: "OTHER", lat: 30.3195, lon: 78.0420, patients: 1 },
  { severity: "MEDIUM", incident_type: "OTHER", lat: 30.3215, lon: 78.0345, patients: 1 },
  { severity: "HIGH", incident_type: "BREATHING", lat: 30.3150, lon: 78.0380, patients: 1 },
  { severity: "CRITICAL", incident_type: "CARDIAC", lat: 30.3240, lon: 78.0290, patients: 1 },
];

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function report(input: CallInput): Promise<AcceptedCall> {
  return api<AcceptedCall>("/incidents", { method: "POST", body: input });
}

async function incident(id: number): Promise<IncidentDetail> {
  return api<IncidentDetail>(`/incidents/${id}`);
}

async function waitFor(predicate: () => Promise<boolean>, timeoutMs = 15000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await predicate()) return true;
    await sleep(300);
  }
  return false;
}

export async function runScene1(): Promise<ScenarioResult> {
  const place = PLACES.railway;
  const reply = await report({
    lat: place.lat,
    lon: place.lon,
    severity: "CRITICAL",
    incident_type: "CARDIAC",
    patients: 1,
    description: `Heart attack at ${place.name}`,
  });
  return {
    title: "One emergency reported",
    message: `Incident ${reply.incident_id} was accepted. Duplicate: ${reply.duplicate}.`,
    incidentIds: [reply.incident_id],
    details: reply,
  };
}

export async function runScene2(): Promise<ScenarioResult> {
  const place = PLACES.bypass;
  const calls = Array.from({ length: 5 }, (_, k) =>
    report({
      lat: place.lat + k * 0.00004,
      lon: place.lon + k * 0.00003,
      severity: "HIGH",
      incident_type: "ROAD_ACCIDENT",
      patients: 1,
      caller_phone: `98970000${String(k).padStart(2, "0")}`,
      description: `Accident at ${place.name}`,
    }),
  );

  const replies = await Promise.all(calls);
  const id = replies[0]!.incident_id;
  const finalDetail = await incident(id);
  const duplicates = replies.filter((r) => r.duplicate).length;

  return {
    title: "Five callers, one accident",
    message: `${replies.length} calls were sent simultaneously. ${replies.length - duplicates} new incident and ${duplicates} duplicate reports. Final report count: ${finalDetail.incident.report_count}.`,
    incidentIds: [id],
    details: { replies, incident: finalDetail.incident },
  };
}

export async function runScene3(): Promise<ScenarioResult> {
  const place = PLACES.forest;
  const calls = [
    { dLat: 0, dLon: 0, patients: 4, description: "4 people badly hurt" },
    { dLat: 0.00004, dLon: 0.00002, patients: 5, description: "5 injured, one is trapped" },
    { dLat: -0.00003, dLon: 0.00003, patients: 5, description: "Bus accident, 5 injured" },
  ];

  let lastReply: AcceptedCall | undefined;
  for (let i = 0; i < calls.length; i++) {
    const call = calls[i]!;
    lastReply = await report({
      lat: place.lat + call.dLat,
      lon: place.lon + call.dLon,
      severity: "CRITICAL",
      incident_type: "ROAD_ACCIDENT",
      patients: call.patients,
      caller_phone: `98971111${String(i + 1).padStart(2, "0")}`,
      description: call.description,
    });
  }

  const id = lastReply!.incident_id;
  await waitFor(async () => (await incident(id)).units.every((u) => u.dispatch));
  const detail = await incident(id);
  return {
    title: "Three callers, five patients",
    message: `The incident now records ${detail.incident.patients} patients across ${detail.incident.report_count} reports.`,
    incidentIds: [id],
    details: detail,
  };
}

export async function runScene4(): Promise<ScenarioResult> {
  const fleet = await api<Array<{ id: number; call_sign: string; status: string }>>(
    "/ambulances",
    { query: { limit: 5000 } },
  );
  const freeCount = fleet.filter((a) => a.status === "AVAILABLE").length;
  if (freeCount === 0) {
    throw new Error("There are no available ambulances. Reset the demo fleet and try again.");
  }

  const burst = BURST_CALLS.slice(0, freeCount);
  const accepted = await Promise.all(burst.map((call) => report(call)));
  const ids = accepted.map((r) => r.incident_id);

  await waitFor(async () => {
    const rows = await Promise.all(ids.map(incident));
    return rows.every((row) => Boolean(row.dispatch));
  });

  const firstPart = await Promise.all(ids.map(incident));
  const assigned = firstPart
    .filter((row) => row.dispatch && row.ambulance)
    .map((row) => ({
      incidentId: row.incident.id,
      ambulance: row.ambulance!.call_sign,
      place: burst.find((c) => c.lat === row.incident.lat && c.lon === row.incident.lon)?.place ?? "—",
      distanceKm: row.dispatch!.distance_km,
      dispatchId: row.dispatch!.id,
    }));

  const used = assigned.map((row) => row.ambulance);
  const doubleBooked = used.filter((sign, index) => used.indexOf(sign) !== index);

  const lateAccepted = await Promise.all(LATE_CALLS.map(report));
  const lateIds = lateAccepted.map((r) => r.incident_id);
  const served: Array<{ incidentId: number; severity: string; ambulance: string; assignedAt: string }> = [];

  const runningDispatchIds = assigned.map((row) => row.dispatchId);
  for (const dispatchId of runningDispatchIds) {
    await api(`/dispatches/${dispatchId}/complete`, { method: "POST" });

    const before = served.length;
    await waitFor(async () => {
      const rows = await Promise.all(lateIds.map(incident));
      return rows.filter((row) => Boolean(row.dispatch)).length > before;
    }, 5000);

    const rows = await Promise.all(lateIds.map(incident));
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i]!;
      if (row.dispatch && row.ambulance && !served.some((s) => s.incidentId === row.incident.id)) {
        served.push({
          incidentId: row.incident.id,
          severity: row.incident.severity,
          ambulance: row.ambulance.call_sign,
          assignedAt: row.dispatch.assigned_at,
        });
      }
    }
    if (served.length >= lateIds.length) break;
  }
  served.sort((a, b) => Date.parse(a.assignedAt) - Date.parse(b.assignedAt));

  return {
    title: "Concurrent calls and waiting emergencies",
    message: `${assigned.length} of ${burst.length} simultaneous calls received ambulances. ${new Set(used).size} different ambulances were assigned.${doubleBooked.length ? ` Warning: repeated ambulance assignment detected (${[...new Set(doubleBooked)].join(", ")}).` : " No ambulance was assigned to two simultaneous calls."} ${served.length} of ${lateIds.length} later calls were assigned as ambulances were freed.`,
    incidentIds: [...ids, ...lateIds],
    details: { assignments: assigned, waitingOrder: served, freeAtStart: freeCount },
  };
}

export async function runScene5(): Promise<ScenarioResult> {
  const before = await api<Array<{ status: string }>>("/ambulances", { query: { limit: 5000 } });
  const freeBefore = before.filter((a) => a.status === "AVAILABLE").length;

  try {
    await report({
      lat: 28.6139,
      lon: 77.2090,
      severity: "CRITICAL",
      incident_type: "ROAD_ACCIDENT",
      patients: 1,
      description: "Test call outside the service area",
    });
    throw new Error("The server accepted the Delhi call; the expected 422 refusal did not occur.");
  } catch (error) {
    if (!(error instanceof Error) || !("status" in error) || error.status !== 422) throw error;
    const after = await api<Array<{ status: string }>>("/ambulances", { query: { limit: 5000 } });
    const freeAfter = after.filter((a) => a.status === "AVAILABLE").length;
    return {
      title: "Out-of-area call refused",
      message: `HTTP 422: ${error.message.replace(/\.$/, "")}. Available ambulances: ${freeBefore} before and ${freeAfter} after.`,
      details: { freeBefore, freeAfter, refusal: error.message },
    };
  }
}

export async function runScene6(): Promise<ScenarioResult> {
  const [ambulances, queue] = await Promise.all([
    api("/ambulances", { query: { limit: 5000 } }),
    api("/queue"),
  ]);
  return {
    title: "Live fleet and queue snapshot",
    message: "Fetched the current ambulance fleet and dispatch queue.",
    details: { ambulances, queue },
  };
}
