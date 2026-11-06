import { describe, expect, it } from "vitest";
import type { Dispatch } from "@/api/client";
import { along, haversineKm, legOf, minutesLeft, type LatLon } from "./motion";

const station: LatLon = [30.3, 78.0];
const patient: LatLon = [30.4, 78.0];
const hospital: LatLon = [30.4, 78.1];
const t0 = Date.parse("2026-10-26T12:00:00") / 1000;
const dispatch = (over: Partial<Dispatch> = {}): Dispatch => ({
  id: 1, incident_id: 7, ambulance_id: 3, hospital_id: null, assigned_at: "2026-10-26T11:59:30",
  phase_started_at: "2026-10-26T12:00:00", completed_at: null, distance_km: 10, eta_minutes: 20, status: "ACTIVE", ...over,
});

describe("ambulance motion", () => {
  it("drives to the patient over eta_minutes of simulated time", () => {
    const leg = legOf("EN_ROUTE", station, dispatch(), patient)!;
    // time_scale 60: one real second is one simulated minute
    expect(along(leg, t0, 60)).toEqual(station);
    const half = along(leg, t0 + 10, 60);
    expect(half[0]).toBeCloseTo(30.35, 6);
    expect(along(leg, t0 + 999, 60)).toEqual(patient);   // never overshoots
    expect(minutesLeft(leg, t0 + 5, 60)).toBe(15);
  });

  it("drives to the hospital at the same speed as the drive to the patient", () => {
    const leg = legOf("TRANSPORTING", patient, dispatch(), patient, hospital)!;
    expect(leg.from).toEqual(patient);
    expect(leg.minutes).toBeCloseTo((haversineKm(patient, hospital) * 20) / 10, 9);
  });

  it("stands still while assigned, at the scene or without a dispatch", () => {
    expect(legOf("ASSIGNED", station, dispatch(), patient)).toBeNull();
    expect(legOf("AT_SCENE", patient, dispatch(), patient, hospital)).toBeNull();
    expect(legOf("EN_ROUTE", station, undefined, patient)).toBeNull();
    expect(legOf("TRANSPORTING", patient, dispatch(), patient)).toBeNull();   // hospital not known yet
  });
});
