import { haversineKm, makePath, type LatLon, type Path } from "./motion";

/* Routes along real roads, worked out in the browser. public/roads.json is the service area's road network
   from OpenStreetMap (built by scripts/build-roads.mjs): junctions, and for each road between two junctions
   its class and its bends. A route is the quickest path by road class (main roads preferred), found with A*.
   Used for drawing only: when an ambulance arrives is still decided by the server. */

type Raw = { origin: [number, number]; scale: number; speeds: number[]; v: number[]; e: number[] };

export type RoadGraph = {
  lat: Float64Array; lon: Float64Array;                       // junctions
  adjStart: Int32Array; adjTo: Int32Array; adjEdge: Int32Array; // roads leaving each junction
  edgeA: Int32Array; edgeB: Int32Array; edgeSeconds: Float64Array;   // a road's two junctions, and its driving time
  shapeStart: Int32Array; shapeCount: Int32Array; shape: Float64Array;   // bends, from edgeA to the other end
  grid: Map<number, number[]>;                                // junctions per 0.01° cell, for finding the nearest
  maxSpeed: number;                                           // m/s, for the A* estimate
};

const CELL = 100;   // grid cells per degree
const cellKey = (lat: number, lon: number) => Math.floor(lat * CELL) * 100000 + Math.floor(lon * CELL);

export function parseRoads(raw: Raw): RoadGraph {
  const n = raw.v.length / 2;
  const at = (i: number, arr: number[]): LatLon => [raw.origin[0] + arr[i]! / raw.scale, raw.origin[1] + arr[i + 1]! / raw.scale];
  const lat = new Float64Array(n), lon = new Float64Array(n);
  for (let i = 0; i < n; i++) [lat[i], lon[i]] = at(2 * i, raw.v);

  // first pass: count edges and shape points
  let edges = 0, points = 0;
  for (let i = 0; i < raw.e.length; i += 4 + 2 * raw.e[i + 3]!) { edges++; points += raw.e[i + 3]!; }
  const edgeA = new Int32Array(edges), edgeB = new Int32Array(edges), edgeSeconds = new Float64Array(edges);
  const shapeStart = new Int32Array(edges), shapeCount = new Int32Array(edges), shape = new Float64Array(points * 2);
  const degree = new Int32Array(n);
  let k = 0, s = 0;
  for (let i = 0; i < raw.e.length; k++) {
    const a = raw.e[i]!, b = raw.e[i + 1]!, cls = raw.e[i + 2]!, c = raw.e[i + 3]!;
    edgeA[k] = a; edgeB[k] = b; shapeStart[k] = s; shapeCount[k] = c;
    let prev: LatLon = [lat[a]!, lon[a]!], metres = 0;
    for (let j = 0; j < c; j++) {
      const p = at(i + 4 + 2 * j, raw.e);
      shape[2 * (s + j)] = p[0]; shape[2 * (s + j) + 1] = p[1];
      metres += haversineKm(prev, p) * 1000;
      prev = p;
    }
    metres += haversineKm(prev, [lat[b]!, lon[b]!]) * 1000;
    edgeSeconds[k] = metres / ((raw.speeds[cls] ?? 20) / 3.6);
    degree[a]!++; degree[b]!++;
    s += c;
    i += 4 + 2 * c;
  }
  const adjStart = new Int32Array(n + 1);
  for (let i = 0; i < n; i++) adjStart[i + 1] = adjStart[i]! + degree[i]!;
  const fill = adjStart.slice(0, n), adjTo = new Int32Array(adjStart[n]!), adjEdge = new Int32Array(adjStart[n]!);
  for (let e = 0; e < edges; e++) {
    const a = edgeA[e]!, b = edgeB[e]!;
    adjTo[fill[a]!] = b; adjEdge[fill[a]!++] = e;
    adjTo[fill[b]!] = a; adjEdge[fill[b]!++] = e;
  }
  const grid = new Map<number, number[]>();
  for (let i = 0; i < n; i++) {
    const key = cellKey(lat[i]!, lon[i]!);
    const list = grid.get(key);
    if (list) list.push(i); else grid.set(key, [i]);
  }
  return { lat, lon, adjStart, adjTo, adjEdge, edgeA, edgeB, edgeSeconds, shapeStart, shapeCount, shape, grid, maxSpeed: Math.max(...raw.speeds) / 3.6 };
}

/** The junction nearest to a position (searches outwards cell by cell). */
export function nearestJunction(g: RoadGraph, [la, lo]: LatLon): number {
  const ci = Math.floor(la * CELL), cj = Math.floor(lo * CELL), kx = Math.cos((la * Math.PI) / 180);
  let best = -1, bestD = Infinity;
  for (let r = 0; r < 30; r++) {
    for (let di = -r; di <= r; di++) for (let dj = -r; dj <= r; dj++) {
      if (Math.max(Math.abs(di), Math.abs(dj)) !== r) continue;          // only the ring at distance r
      for (const v of g.grid.get((ci + di) * 100000 + (cj + dj)) ?? []) {
        const d = (g.lat[v]! - la) ** 2 + ((g.lon[v]! - lo) * kx) ** 2;
        if (d < bestD) { bestD = d; best = v; }
      }
    }
    if (best >= 0 && r >= 1) return best;   // one ring past the first hit covers cell-edge cases
  }
  return best;
}

/** Quickest route between two positions, as points from `from` to `to`; null if they are not connected. */
export function route(g: RoadGraph, from: LatLon, to: LatLon): LatLon[] | null {
  const s = nearestJunction(g, from), t = nearestJunction(g, to);
  if (s < 0 || t < 0) return null;
  if (s === t) return [from, [g.lat[s]!, g.lon[s]!], to];
  const n = g.lat.length;
  const cost = new Float64Array(n).fill(Infinity), via = new Int32Array(n).fill(-1), done = new Uint8Array(n);
  const tLat = g.lat[t]!, tLon = g.lon[t]!;
  const estimate = (v: number) => (haversineKm([g.lat[v]!, g.lon[v]!], [tLat, tLon]) * 1000) / g.maxSpeed;
  // binary heap of [priority, junction]
  const heap: [number, number][] = [];
  const push = (p: number, v: number) => {
    heap.push([p, v]);
    for (let i = heap.length - 1; i > 0;) {
      const up = (i - 1) >> 1;
      if (heap[up]![0] <= heap[i]![0]) break;
      [heap[up], heap[i]] = [heap[i]!, heap[up]!];
      i = up;
    }
  };
  const pop = () => {
    const top = heap[0]!, last = heap.pop()!;
    if (heap.length) {
      heap[0] = last;
      for (let i = 0; ;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < heap.length && heap[l]![0] < heap[m]![0]) m = l;
        if (r < heap.length && heap[r]![0] < heap[m]![0]) m = r;
        if (m === i) break;
        [heap[m], heap[i]] = [heap[i]!, heap[m]!];
        i = m;
      }
    }
    return top;
  };
  cost[s] = 0;
  push(estimate(s), s);
  while (heap.length) {
    const [, v] = pop();
    if (done[v]) continue;
    if (v === t) break;
    done[v] = 1;
    for (let k = g.adjStart[v]!; k < g.adjStart[v + 1]!; k++) {
      const w = g.adjTo[k]!, e = g.adjEdge[k]!, c = cost[v]! + g.edgeSeconds[e]!;
      if (c < cost[w]!) { cost[w] = c; via[w] = e; push(c + estimate(w), w); }
    }
  }
  if (via[t] === -1) return null;

  // Walk back from t, adding each road's bends in the direction of travel.
  const back: LatLon[] = [[tLat, tLon]];
  for (let v = t; v !== s;) {
    const e = via[v]!, a = g.edgeA[e]!, start = g.shapeStart[e]!, count = g.shapeCount[e]!;
    const forward = a !== v;                       // drove from a to v
    for (let j = 0; j < count; j++) {
      const idx = forward ? start + count - 1 - j : start + j;
      back.push([g.shape[2 * idx]!, g.shape[2 * idx + 1]!]);
    }
    v = forward ? a : g.edgeB[e]!;
    back.push([g.lat[v]!, g.lon[v]!]);
  }
  return [from, ...back.reverse(), to];
}

/* ---------------------------------------------------------------- loading and caching */

let loading: Promise<RoadGraph> | null = null;
/** Loads public/roads.json once; every map shares it. */
export function loadRoads(): Promise<RoadGraph> {
  loading ??= fetch("/roads.json").then((r) => {
    if (!r.ok) throw new Error(`roads.json: ${r.status}`);
    return r.json();
  }).then(parseRoads);
  return loading;
}

const cache = new Map<string, Path | null>();
const keyOf = (p: LatLon) => `${p[0].toFixed(4)},${p[1].toFixed(4)}`;

/** A cached route as a Path; `budget.left` limits how many new routes are worked out per frame (undefined = not yet). */
export function cachedRoute(g: RoadGraph, from: LatLon, to: LatLon, budget: { left: number }): Path | null | undefined {
  const key = `${keyOf(from)}>${keyOf(to)}`;
  if (cache.has(key)) return cache.get(key);
  if (budget.left <= 0) return undefined;
  budget.left--;
  const pts = route(g, from, to);
  const path = pts ? makePath(pts) : null;
  cache.set(key, path);
  if (cache.size > 2000) cache.delete(cache.keys().next().value!);
  return path;
}
