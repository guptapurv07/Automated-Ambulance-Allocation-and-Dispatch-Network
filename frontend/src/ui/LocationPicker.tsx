import L from "leaflet";
import { clsx } from "clsx";
import { useEffect, useRef } from "react";
import type { Severity } from "@/api/client";
import type { ServiceArea } from "@/lib/domain";
import { createBaseMap, incidentIcon } from "./CityMap";

/** Click the map to choose where the emergency is. The pin takes the chosen severity's colour. */
export function LocationPicker({ value, severity, area, onPick, className }: {
  value: { lat: number; lon: number } | null;
  severity: Severity;
  area: ServiceArea;            // outside it, clicks do nothing
  onPick: (lat: number, lon: number) => void;
  className?: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const pin = useRef<L.Marker | null>(null);
  const pick = useRef(onPick);
  pick.current = onPick;

  useEffect(() => {
    const m = createBaseMap(el.current!);
    m.getContainer().style.cursor = "crosshair";
    const box = L.latLngBounds([area.lat_min, area.lon_min], [area.lat_max, area.lon_max]);
    L.rectangle(box, { color: "#5D646B", weight: 1, dashArray: "6 4", fill: false, interactive: false }).addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => {
      if (box.contains(e.latlng)) pick.current(+e.latlng.lat.toFixed(5), +e.latlng.lng.toFixed(5));
    });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      pin.current = null;
    };
  }, []);   // the area is fixed while the page is open

  // Keep the pin on the chosen position and in view.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!value) {
      pin.current?.remove();
      pin.current = null;
      return;
    }
    const at: L.LatLngTuple = [value.lat, value.lon];
    if (!pin.current) pin.current = L.marker(at, { icon: incidentIcon(severity), keyboard: false }).addTo(m);
    else pin.current.setLatLng(at).setIcon(incidentIcon(severity));
    if (!m.getBounds().pad(-0.1).contains(at)) m.panTo(at);
  }, [value, severity]);

  return <div ref={el} role="region" aria-label="Map: click to choose the emergency's location" className={clsx("w-full", className)} />;
}
