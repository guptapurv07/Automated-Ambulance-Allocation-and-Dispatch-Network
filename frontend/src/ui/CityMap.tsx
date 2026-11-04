import L from "leaflet";
import { clsx } from "clsx";
import { useEffect, useRef } from "react";
import type { Ambulance, Dispatch, Hospital, Incident, IncidentDetail } from "@/api/client";
import { PHASE_LABEL, phaseOf, SEVERITY_LABEL, SEVERITY_VAR, statusLabel, type Phase } from "@/lib/domain";
import { ahead, along, legOf, progress, returnLeg, type LatLon } from "@/lib/motion";
import { cachedRoute, loadRoads, type RoadGraph } from "@/lib/roads";

/* The city map: OpenStreetMap streets in greyscale, every ambulance and hospital, the incidents being served
   and the calls still waiting, and a dashed line from each busy ambulance to where it is going (the patient,
   then the hospital). Plain Leaflet: markers are created once and moved in place about 20 times a second, so
   ambulances are seen driving between the server's position updates, along real roads (lib/roads.ts) once the
   road network has loaded, in a straight line until then. */

export type MapData = {
  ambulances: Ambulance[];
  dispatches: Dispatch[];                      // active ones
  details: Map<number, IncidentDetail>;        // by incident id
  timeScale: number;
  avgSpeedKmph?: number | null;                // GET /health; to draw ambulances driving back to their station
  incidents?: Incident[];                      // more incidents to show, e.g. calls still waiting for an ambulance
  hospitals?: Hospital[];                      // shown all the time
  fit?: LatLon[];                              // what to frame (default: every ambulance and hospital)
};

/** A map of the city with greyscale OpenStreetMap streets, shared by every map in the dashboard. */
export function createBaseMap(el: HTMLElement): L.Map {
  const m = L.map(el, { zoomSnap: 0.25 });
  m.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(m);
  m.setView([30.33, 78.03], 11);
  // Leaflet measures its box once; tell it when the layout changes the box.
  const ro = new ResizeObserver(() => m.invalidateSize());
  ro.observe(el);
  m.on("unload", () => ro.disconnect());
  return m;
}

const icon = (html: string, size: number) => L.divIcon({ html, className: "m-icon", iconSize: [size, size] });
const incidentIcons = new Map<string, L.DivIcon>();
/** Incident marker: a diamond in its severity colour; it pulses while the call waits for an ambulance. */
export function incidentIcon(severity: Incident["severity"], waiting = false) {
  const key = `${severity}${waiting ? "-waiting" : ""}`;
  if (!incidentIcons.has(key))
    incidentIcons.set(key, icon(`<span class="m-inc${waiting ? " waiting" : ""}" style="--c: var(${SEVERITY_VAR[severity]})"></span>`, 12));
  return incidentIcons.get(key)!;
}
// One shape per phase (colour stays for urgency): free green, heading out ink, at the scene ink with a dot,
// carrying the patient ink with a cross, at the hospital an ink outline, driving back to the station a green
// outline (free for a new call), out of service hollow grey.
const AMB_ICON = Object.fromEntries((["free", "returning", "to_patient", "at_scene", "with_patient", "at_hospital", "out"] as Phase[])
  .map((p) => [p, icon(`<span class="m-amb ${p}"></span>`, 12)])) as Record<Phase, L.DivIcon>;
const HOSP_ICON = icon('<span class="m-hosp">+</span>', 16);
/** A hospital's marker, under everything else, named on hover. A click goes through to the map below it. */
export const hospitalMarker = (h: Hospital) =>
  L.marker([h.lat, h.lon], { icon: HOSP_ICON, keyboard: false, zIndexOffset: -500, bubblingMouseEvents: true })
    .bindTooltip(`${h.name} · ${h.bed_capacity} beds`, { direction: "top", offset: [0, -10] });
// The patient is still at the scene in these phases; after them they are in the ambulance.
const PATIENT_AT_SCENE = new Set<Ambulance["status"]>(["ASSIGNED", "EN_ROUTE", "AT_SCENE"]);

function sync<K, V extends L.Layer>(store: Map<K, V>, keep: Set<K>, map: L.Map) {
  for (const [k, layer] of store) if (!keep.has(k)) { map.removeLayer(layer); store.delete(k); }
}

export function CityMap({ data, className, onIncident }: { data: MapData; className?: string; onIncident?: (id: number) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const latest = useRef(data);
  latest.current = data;
  const openIncident = useRef(onIncident);
  openIncident.current = onIncident;

  useEffect(() => {
    const m = createBaseMap(el.current!);
    const amb = new Map<number, L.Marker>(), inc = new Map<number, L.Marker>(), hosp = new Map<number, L.Marker>(), line = new Map<number, L.Polyline>();
    const tips = new Map<L.Layer, string>();
    let fittedFor = 0;                         // how many points the view was last framed for
    let roads: RoadGraph | null = null;
    loadRoads().then((g) => { roads = g; }).catch(() => { /* no road file: keep straight lines */ });

    const tip = (layer: L.Marker, text: string) => {
      if (tips.get(layer) !== text) { layer.setTooltipContent(text); tips.set(layer, text); }
    };
    const FitControl = L.Control.extend({
      onAdd: () => {
        const b = L.DomUtil.create("button", "m-fit");
        b.type = "button";
        b.textContent = "Show all";
        L.DomEvent.disableClickPropagation(b);
        b.onclick = () => { fittedFor = 0; };
        return b;
      },
    });
    new FitControl({ position: "topright" }).addTo(m);

    const draw = () => {
      const { ambulances, dispatches, details, timeScale, avgSpeedKmph, incidents = [], hospitals = [], fit } = latest.current;
      const now = Date.now() / 1000;
      const byAmbulance = new Map(dispatches.map((d) => [d.ambulance_id, d]));
      const ambById = new Map(ambulances.map((a) => [a.id, a]));
      const seen = { amb: new Set<number>(), inc: new Set<number>(), hosp: new Set<number>(), line: new Set<number>() };

      // 1. Where each ambulance is now, where it is heading, and the road it takes there.
      const budget = { left: 3 };              // new routes worked out per frame (a few ms each)
      const placed = ambulances.map((a) => {
        const d = byAmbulance.get(a.id);
        const det = d && details.get(d.incident_id);
        const here: LatLon = [a.current_lat, a.current_lon];
        const at: LatLon | undefined = det ? [det.incident.lat, det.incident.lon] : undefined;
        const to: LatLon | undefined = det?.hospital ? [det.hospital.lat, det.hospital.lon] : undefined;
        const back = returnLeg(a, avgSpeedKmph);
        const leg = legOf(a.status, here, d, at, to) ?? back;
        // to the patient from where it stands; to the hospital from the patient; back from the hospital to its station
        const toPatient = a.status === "ASSIGNED" || a.status === "EN_ROUTE";
        const from = toPatient ? here : a.status === "TRANSPORTING" ? at : back?.from;
        const target = toPatient ? at : a.status === "TRANSPORTING" ? to : back?.to;
        const path = roads && from && target ? cachedRoute(roads, from, target, budget) : null;
        const pos = leg ? along(leg, now, timeScale, path) : here;
        const f = leg ? progress(leg, now, timeScale) : 0;
        const road = target ? (path ? ahead(path, f) : [pos, target]) : null;   // the dashed line still ahead
        return { a, d, road, moving: !!leg, pos };
      });

      // 2. Ambulances waiting at the same station or hospital would be drawn on top of each other: fan them out
      //    in a small ring around the spot, so every one of them can be seen and pointed at.
      const groups = new Map<string, typeof placed>();
      for (const p of placed) if (!p.moving) {
        const k = `${p.pos[0].toFixed(4)},${p.pos[1].toFixed(4)}`;
        groups.set(k, [...(groups.get(k) ?? []), p]);
      }
      const hospitalAt = new Set(hospitals.map((h) => `${h.lat.toFixed(4)},${h.lon.toFixed(4)}`));
      for (const [k, g] of groups) {
        const onHospital = hospitalAt.has(k);
        if (g.length < 2 && !onHospital) continue;
        const c = m.latLngToContainerPoint(g[0]!.pos);
        // wider around a hospital, so its marker stays visible in the middle of the ring
        const r = Math.max(g.length <= 6 ? 9 : 14, onHospital ? 17 : 0);
        g.forEach((p, k) => {
          const ang = (2 * Math.PI * k) / g.length - Math.PI / 2;
          const ll = m.containerPointToLatLng(L.point(c.x + r * Math.cos(ang), c.y + r * Math.sin(ang)));
          p.pos = [ll.lat, ll.lng];
        });
      }

      // 3. Ambulances, and a dashed line to where each busy one is going.
      for (const { a, d, road, pos } of placed) {
        const phase = phaseOf(a);
        const text = `${a.call_sign} · ${statusLabel(a)}`;
        let mk = amb.get(a.id);
        if (!mk) {
          mk = L.marker(pos, { icon: AMB_ICON[phase], keyboard: false }).bindTooltip(text, { direction: "top", offset: [0, -8] }).addTo(m);
          tips.set(mk, text);
          amb.set(a.id, mk);
        } else {
          mk.setLatLng(pos);
          if (mk.options.icon !== AMB_ICON[phase]) mk.setIcon(AMB_ICON[phase]);
          tip(mk, text);
        }
        mk.setZIndexOffset(phase === "free" || phase === "out" ? 0 : 1000);
        seen.amb.add(a.id);
        if (d && road) {
          const ln = line.get(d.id);
          if (ln) ln.setLatLngs(road);
          else line.set(d.id, L.polyline(road, { color: "#0F1214", weight: 1.5, dashArray: "4 4", interactive: false }).addTo(m));
          seen.line.add(d.id);
        }
      }

      // 4. Incidents: the ones whose patient is still at the scene, then the calls still waiting (they pulse).
      const shown = new Map<number, { i: Incident; waiting: boolean }>();
      const hospitalsShown = new Map(hospitals.map((h) => [h.id, h]));
      for (const d of dispatches) {
        const det = details.get(d.incident_id);
        if (!det) continue;
        const status = ambById.get(d.ambulance_id)?.status;
        if (status && PATIENT_AT_SCENE.has(status)) shown.set(det.incident.id, { i: det.incident, waiting: false });
        if (det.hospital) hospitalsShown.set(det.hospital.id, det.hospital);
      }
      for (const i of incidents) if (!shown.has(i.id)) shown.set(i.id, { i, waiting: i.status === "REPORTED" || i.status === "UNASSIGNED" });
      for (const { i, waiting } of shown.values()) {
        const ic = incidentIcon(i.severity, waiting);
        const text = `Incident #${i.id} · ${SEVERITY_LABEL[i.severity]}${waiting ? " · waiting for an ambulance" : ""}`;
        const mk = inc.get(i.id);
        if (!mk) {
          const n = L.marker([i.lat, i.lon], { icon: ic, keyboard: false, zIndexOffset: 500 }).bindTooltip(text, { direction: "top", offset: [0, -8] }).addTo(m);
          n.on("click", () => openIncident.current?.(i.id));
          tips.set(n, text);
          inc.set(i.id, n);
        } else {
          if (mk.options.icon !== ic) mk.setIcon(ic);
          tip(mk, text);
        }
        seen.inc.add(i.id);
      }

      // 5. Hospitals.
      for (const h of hospitalsShown.values()) {
        if (!hosp.has(h.id)) hosp.set(h.id, hospitalMarker(h).addTo(m));
        seen.hosp.add(h.id);
      }

      for (const [store, keep] of [[amb, seen.amb], [inc, seen.inc], [hosp, seen.hosp], [line, seen.line]] as const) sync(store as Map<number, L.Layer>, keep, m);

      // Frame once, again when there is more to show (hospitals arrive, a tracked call gets its ambulance),
      // and when "Show all" is pressed.
      const frame = fit?.length ? fit : [
        ...ambulances.map((a) => [a.current_lat, a.current_lon] as LatLon),
        ...hospitals.map((h) => [h.lat, h.lon] as LatLon),
      ];
      if (frame.length > fittedFor) {
        m.fitBounds(L.latLngBounds(frame), { padding: [40, 40], maxZoom: 14 });
        fittedFor = frame.length;
      }
    };

    // About 20 frames a second: smooth driving without redrawing more often than the eye needs. A timer, not
    // requestAnimationFrame, so the map keeps up to date while its tab is in the background (browsers pause
    // animation frames there but keep timers running, if more slowly).
    draw();
    const id = setInterval(draw, 50);
    return () => {
      clearInterval(id);
      m.remove();
    };
  }, []);

  return <div ref={el} role="region" aria-label="City map" className={clsx("w-full", className)} />;
}

/** Legend for the map's markers (Strip on Live). Ambulance phases by shape; urgency by colour. */
export function MapLegend() {
  const item = "inline-flex items-center gap-1.5";
  return (
    <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-muted">
      {(["free", "returning", "to_patient", "at_scene", "with_patient", "at_hospital"] as Phase[]).map((p) => (
        <span key={p} className={item}><span aria-hidden className={`m-amb ${p}`} />{PHASE_LABEL[p]}</span>
      ))}
      <span className={item}><span aria-hidden className="size-2.5 rotate-45 bg-critical" />Critical</span>
      <span className={item}><span aria-hidden className="size-2.5 rotate-45 bg-warning" />High</span>
      <span className={item}><span aria-hidden className="size-2.5 rotate-45 bg-ink" />Medium</span>
      <span className={item}><span aria-hidden className="size-2.5 rotate-45 bg-muted" />Low</span>
      <span className={item}><span aria-hidden className="m-hosp">+</span>Hospital</span>
    </span>
  );
}
