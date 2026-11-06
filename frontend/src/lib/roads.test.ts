import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ahead, haversineKm, makePath, pointAt, type LatLon } from "./motion";
import { parseRoads, route } from "./roads";

const graph = parseRoads(JSON.parse(readFileSync(new URL("../../public/roads.json", import.meta.url), "utf8")));
const clockTower: LatLon = [30.3255, 78.0413];
const isbt: LatLon = [30.2892, 77.9959];

describe("routes along roads", () => {
  it("drives from Clock Tower to ISBT on the roads, a little longer than the straight line", () => {
    const t0 = performance.now();
    const pts = route(graph, clockTower, isbt)!;
    const ms = performance.now() - t0;
    const road = makePath(pts).total, straight = haversineKm(clockTower, isbt);
    expect(pts[0]).toEqual(clockTower);
    expect(pts[pts.length - 1]).toEqual(isbt);
    expect(pts.length).toBeGreaterThan(10);               // it turns along streets, not one straight segment
    expect(road).toBeGreaterThan(straight);
    expect(road).toBeLessThan(straight * 2);
    expect(ms).toBeLessThan(500);
  });

  it("reaches the outer towns too", () => {
    for (const to of [[30.179, 78.123], [30.457, 78.077], [30.372, 77.865]] as LatLon[]) expect(route(graph, clockTower, to)).not.toBeNull();
  });
});

describe("moving along a path", () => {
  const path = makePath([[30, 78], [30, 78.01], [30.01, 78.01]]);   // east, then north
  it("finds the point a fraction of the way along, segment by segment", () => {
    expect(pointAt(path, 0).pos).toEqual([30, 78]);
    expect(pointAt(path, 1).pos).toEqual([30.01, 78.01]);
    const firstLeg = path.cum[1]! / path.total;
    expect(pointAt(path, firstLeg / 2).pos[1]).toBeCloseTo(78.005, 6);
    expect(pointAt(path, firstLeg / 2).i).toBe(0);
    expect(pointAt(path, 0.9).i).toBe(1);
  });
  it("gives what is still ahead", () => {
    expect(ahead(path, 0)).toEqual([[30, 78], [30, 78.01], [30.01, 78.01]]);
    expect(ahead(path, 0.9)).toHaveLength(2);
  });
});
