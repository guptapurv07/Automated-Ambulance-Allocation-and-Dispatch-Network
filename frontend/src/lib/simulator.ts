import { create } from "zustand";
import type { IncidentType, Severity } from "@/api/client";
import { reportIncident } from "@/api/queries";
import { PLACES } from "./places";

/* A built-in caller for demonstrations: it phones in emergencies from places around the city, either one every
   few seconds or many at once. It runs in the browser and uses the normal API (POST /incidents), so the server
   treats every call like a real one. The timer lives outside React, so it keeps going while you change page. */

const CITY = PLACES.slice(0, 23);                       // the city places; the outer towns are rarely called from
const SEVERITY_MIX: [Severity, number][] = [["CRITICAL", 1], ["HIGH", 2.5], ["MEDIUM", 4], ["LOW", 2.5]];
const TYPES: IncidentType[] = ["ROAD_ACCIDENT", "CARDIAC", "BREATHING", "INJURY", "FIRE", "PREGNANCY", "OTHER"];
const SPREAD_DEG = 0.008;                               // about 900 m: calls from one place are not merged as duplicates

function pick<T>(items: [T, number][]): T {
  let r = Math.random() * items.reduce((a, [, w]) => a + w, 0);
  for (const [v, w] of items) if ((r -= w) <= 0) return v;
  return items[items.length - 1]![0];
}
const jitter = () => (Math.random() + Math.random() + Math.random() - 1.5) * SPREAD_DEG;   // roughly bell-shaped

function randomCall() {
  const p = CITY[Math.floor(Math.random() * CITY.length)]!;
  return {
    lat: +(p.lat + jitter()).toFixed(5),
    lon: +(p.lon + jitter()).toFixed(5),
    severity: pick(SEVERITY_MIX),
    incident_type: TYPES[Math.floor(Math.random() * TYPES.length)]!,
    patients: Math.random() < 0.1 ? 2 : 1,
    description: `Simulated call near ${p.name}`,
  };
}

type Sim = {
  running: boolean;
  everySeconds: number;
  created: number;                                      // new incidents
  merged: number;                                       // calls the server matched to an incident already on record
  failed: number;
  lastError: string | null;
  start: (everySeconds: number) => void;
  stop: () => void;
  burst: (n: number) => Promise<void>;
};

let timer: ReturnType<typeof setInterval> | undefined;

export const useSimulator = create<Sim>()((set) => {
  const sendOne = async () => {
    try {
      const r = await reportIncident(randomCall());
      set((s) => (r.duplicate ? { merged: s.merged + 1, lastError: null } : { created: s.created + 1, lastError: null }));
    } catch (e) {
      set((s) => ({ failed: s.failed + 1, lastError: (e as Error).message }));
    }
  };
  return {
    running: false,
    everySeconds: 2,
    created: 0,
    merged: 0,
    failed: 0,
    lastError: null,
    start: (everySeconds) => {
      clearInterval(timer);
      timer = setInterval(sendOne, everySeconds * 1000);
      set({ running: true, everySeconds });
      void sendOne();
    },
    stop: () => {
      clearInterval(timer);
      timer = undefined;
      set({ running: false });
    },
    burst: async (n) => {
      await Promise.all(Array.from({ length: n }, sendOne));
    },
  };
});
