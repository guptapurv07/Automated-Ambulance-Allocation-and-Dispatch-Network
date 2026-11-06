// Builds public/roads.json: the road network of the service area, small enough to ship with the dashboard,
// so the map can draw ambulances driving along real roads. Road data © OpenStreetMap contributors (ODbL).
//
//   node scripts/build-roads.mjs                  downloads the roads from the Overpass API, then builds
//   node scripts/build-roads.mjs roads_raw.json   builds from a download made earlier
//
// The area is the service area of the backend (SERVICE_AREA_* in app/config.py). Every road a car can use is
// kept; footpaths, tracks and private service roads are not. The graph keeps junctions as nodes and the bends
// of each road as shape points, keeps only the largest connected piece, and records each road's class so routes
// prefer main roads the way a driver would.

import { readFileSync, writeFileSync } from "node:fs";

const BBOX = [30.1, 77.75, 30.5, 78.25];   // lat_min, lon_min, lat_max, lon_max (app/config.py service area)
const CLASSES = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street"];
const SPEED_KMPH = [80, 65, 50, 40, 35, 28, 22, 12];   // for choosing a route only; travel time comes from the server
const SCALE = 1e5;                                     // coordinates are stored as integers (about 1 m)
const SIMPLIFY_M = 4;                                  // drop shape points closer than this to the straight line

const QUERY = `[out:json][timeout:240];
way["highway"~"^(${CLASSES.join("|")}|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"](${BBOX.join(",")});
out body qt;
>;
out skel qt;`;

async function download() {
  const res = await fetch("https://overpass-api.de/api/interpreter", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "ambulance-dispatch-dashboard (build-roads.mjs)" },
    body: "data=" + encodeURIComponent(QUERY),
  });
  if (!res.ok) throw new Error(`Overpass answered ${res.status}`);
  return res.json();
}

const raw = process.argv[2] ? JSON.parse(readFileSync(process.argv[2], "utf8")) : await download();

// ---------------------------------------------------------------- read nodes and ways
const pos = new Map();
const ways = [];
for (const el of raw.elements) {
  if (el.type === "node") pos.set(el.id, [el.lat, el.lon]);
  else if (el.type === "way") {
    const cls = CLASSES.indexOf(String(el.tags?.highway ?? "").replace(/_link$/, ""));
    if (cls >= 0 && el.nodes.length > 1) ways.push({ nodes: el.nodes, cls });
  }
}

// A node is a junction if it ends a way or is shared by two or more ways.
const uses = new Map();
for (const w of ways) for (const n of w.nodes) uses.set(n, (uses.get(n) ?? 0) + 1);
const isJunction = (w, i) => i === 0 || i === w.nodes.length - 1 || uses.get(w.nodes[i]) > 1;

// ---------------------------------------------------------------- split ways into edges between junctions
const edges = [];   // { a, b, cls, shape: [nodeId...] }
for (const w of ways) {
  let start = 0;
  for (let i = 1; i < w.nodes.length; i++) {
    if (!isJunction(w, i)) continue;
    const a = w.nodes[start], b = w.nodes[i];
    if (a !== b && pos.has(a) && pos.has(b)) edges.push({ a, b, cls: w.cls, shape: w.nodes.slice(start + 1, i).filter((n) => pos.has(n)) });
    start = i;
  }
}

// ---------------------------------------------------------------- keep the largest connected piece
const parent = new Map();
const find = (x) => {
  while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); }
  return x;
};
for (const e of edges) for (const n of [e.a, e.b]) if (!parent.has(n)) parent.set(n, n);
for (const e of edges) parent.set(find(e.a), find(e.b));
const size = new Map();
for (const n of parent.keys()) size.set(find(n), (size.get(find(n)) ?? 0) + 1);
const biggest = [...size.entries()].sort((x, y) => y[1] - x[1])[0][0];
const kept = edges.filter((e) => find(e.a) === biggest);

// ---------------------------------------------------------------- simplify road shapes (Douglas-Peucker, metres)
const M_PER_DEG_LAT = 111320, M_PER_DEG_LON = 111320 * Math.cos((((BBOX[0] + BBOX[2]) / 2) * Math.PI) / 180);
function offLine([py, px], [ay, ax], [by, bx]) {
  const [x, y, x1, y1, x2, y2] = [px * M_PER_DEG_LON, py * M_PER_DEG_LAT, ax * M_PER_DEG_LON, ay * M_PER_DEG_LAT, bx * M_PER_DEG_LON, by * M_PER_DEG_LAT];
  const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / l2)) : 0;
  return Math.hypot(x - (x1 + t * dx), y - (y1 + t * dy));
}
function simplify(pts) {
  if (pts.length < 3) return pts;
  let worst = 0, at = 0;
  for (let i = 1; i < pts.length - 1; i++) {
    const d = offLine(pts[i], pts[0], pts[pts.length - 1]);
    if (d > worst) { worst = d; at = i; }
  }
  if (worst < SIMPLIFY_M) return [pts[0], pts[pts.length - 1]];
  return [...simplify(pts.slice(0, at + 1)).slice(0, -1), ...simplify(pts.slice(at))];
}

// ---------------------------------------------------------------- write: integer coordinates relative to the origin
const index = new Map();
const v = [];
const q = ([lat, lon]) => [Math.round((lat - BBOX[0]) * SCALE), Math.round((lon - BBOX[1]) * SCALE)];
const vertex = (id) => {
  if (!index.has(id)) { index.set(id, index.size); v.push(...q(pos.get(id))); }
  return index.get(id);
};
const e = [];   // per edge: a, b, class, number of shape points, then their coordinates
let shapePoints = 0;
for (const ed of kept) {
  const pts = simplify([ed.a, ...ed.shape, ed.b].map((n) => pos.get(n))).slice(1, -1);
  e.push(vertex(ed.a), vertex(ed.b), ed.cls, pts.length, ...pts.flatMap(q));
  shapePoints += pts.length;
}

const out = {
  attribution: "© OpenStreetMap contributors, ODbL. Built by scripts/build-roads.mjs.",
  origin: [BBOX[0], BBOX[1]],
  scale: SCALE,
  speeds: SPEED_KMPH,
  v,
  e,
};
const json = JSON.stringify(out);
writeFileSync(new URL("../public/roads.json", import.meta.url), json);
console.log(`ways ${ways.length}, edges ${kept.length} of ${edges.length}, junctions ${index.size}, shape points ${shapePoints}, ${(json.length / 1e6).toFixed(2)} MB`);
