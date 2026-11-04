import type { Ambulance, AmbulanceStatus, Dispatch } from "@/api/client";
import { wallOf } from "./format";

/* The server moves an ambulance only twice per call: to the patient when it reaches the scene, and to the
   hospital when it arrives there. The map draws it driving in between, at a constant speed, from the
   phase's start time, its length in simulated minutes and the simulation speed (GET /queue → time_scale:
   one real second is time_scale / 60 simulated minutes). Timestamps carry their time zone, so only the
   browser's clock has to be right, not its zone. */

export type LatLon = [number, number];
export type Leg = { from: LatLon; to: LatLon; startedAt: number; minutes: number };

export function haversineKm([lat1, lon1]: LatLon, [lat2, lon2]: LatLon): number {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(a));
}

/** The leg an ambulance is driving now, or null while it stands still (assigned, at the scene, at the hospital). */
export function legOf(status: AmbulanceStatus, here: LatLon, d: Dispatch | undefined, incident?: LatLon, hospital?: LatLon): Leg | null {
  if (!d) return null;
  const startedAt = wallOf(d.phase_started_at);
  if (status === "EN_ROUTE" && incident) return { from: here, to: incident, startedAt, minutes: d.eta_minutes };
  if (status === "TRANSPORTING" && incident && hospital) {
    // Same average speed as the drive to the patient (distance_km / eta_minutes). If the ambulance started at
    // the patient's position there is no speed to read, and it is drawn at the hospital straight away.
    const minutes = d.distance_km > 0 ? (haversineKm(incident, hospital) * d.eta_minutes) / d.distance_km : 0;
    return { from: incident, to: hospital, startedAt, minutes };
  }
  return null;
}

/** The drive back to the station after a call, while the ambulance is free. It starts at returning_since from
 *  where the server still places it (the hospital) and takes distance / average speed (GET /health), as the
 *  server works it out; the server moves it to its station when the time is up. */
export function returnLeg(a: Ambulance, avgSpeedKmph?: number | null): Leg | null {
  if (a.status !== "AVAILABLE" || !a.returning_since || !avgSpeedKmph) return null;
  const from: LatLon = [a.current_lat, a.current_lon], to: LatLon = [a.base_lat, a.base_lon];
  return { from, to, startedAt: wallOf(a.returning_since), minutes: (haversineKm(from, to) / avgSpeedKmph) * 60 };
}

const elapsedMinutes = (leg: Leg, now: number, timeScale: number) => (Math.max(0, now - leg.startedAt) * timeScale) / 60;

/** How far along the leg the ambulance is at wall time `now` (seconds): 0 at the start, 1 on arrival. */
export const progress = (leg: Leg, now: number, timeScale: number) =>
  leg.minutes > 0 ? Math.min(1, elapsedMinutes(leg, now, timeScale) / leg.minutes) : 1;

/** Position along the leg at wall time `now`: along the road route when there is one, else in a straight line. */
export function along(leg: Leg, now: number, timeScale: number, path?: Path | null): LatLon {
  const f = progress(leg, now, timeScale);
  if (path) return pointAt(path, f).pos;
  return [leg.from[0] + (leg.to[0] - leg.from[0]) * f, leg.from[1] + (leg.to[1] - leg.from[1]) * f];
}

/** Simulated minutes until the end of the leg. */
export const minutesLeft = (leg: Leg, now: number, timeScale: number) => Math.max(0, leg.minutes - elapsedMinutes(leg, now, timeScale));

/* ---------------------------------------------------------------- routes along roads */

/** A route as points, with the distance (km) from the start to each point. */
export type Path = { pts: LatLon[]; cum: number[]; total: number };

export function makePath(pts: LatLon[]): Path {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1]! + haversineKm(pts[i - 1]!, pts[i]!));
  return { pts, cum, total: cum[cum.length - 1]! };
}

/** The point a fraction `f` of the way along the path, and the index of the segment it is on. */
export function pointAt(path: Path, f: number): { pos: LatLon; i: number } {
  const { pts, cum, total } = path;
  if (pts.length < 2 || total === 0) return { pos: pts[pts.length - 1] ?? pts[0]!, i: 0 };
  const d = Math.max(0, Math.min(1, f)) * total;
  let lo = 0, hi = cum.length - 1;              // last point with cum <= d
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (cum[mid]! <= d) lo = mid; else hi = mid - 1;
  }
  const i = Math.min(lo, pts.length - 2), seg = cum[i + 1]! - cum[i]!;
  const t = seg > 0 ? (d - cum[i]!) / seg : 0;
  const [a, b] = [pts[i]!, pts[i + 1]!];
  return { pos: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], i };
}

/** The part of the path still ahead at fraction `f`: the current position, then the remaining points. */
export function ahead(path: Path, f: number): LatLon[] {
  const { pos, i } = pointAt(path, f);
  return [pos, ...path.pts.slice(i + 1)];
}
