import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, type IncidentType, type Severity } from "@/api/client";
import { reportIncident, useHealth } from "@/api/queries";
import { INCIDENT_TYPE_LABEL, inServiceArea, serviceAreaText, SEVERITIES, SEVERITY_LABEL } from "@/lib/domain";
import { PLACES } from "@/lib/places";
import { Button } from "@/ui/Button";
import { Field, Input, NumberInput, Segmented, Select } from "@/ui/form";
import { Card, PageHeader, Strip } from "@/ui/layout";
import { LocationPicker } from "@/ui/LocationPicker";
import { Banner, Skeleton } from "@/ui/status";
import { toast } from "@/ui/Toast";

/* Report an emergency the way a call taker would. After sending, the incident's own page follows it live. */

const ON_MAP = "__map";
const TYPES = Object.keys(INCIDENT_TYPE_LABEL) as IncidentType[];

export default function Report() {
  const navigate = useNavigate();
  return <ReportForm onSent={(n) => navigate(`/incidents/${n}`)} />;
}

/* ---------------------------------------------------------------- the call */

function ReportForm({ onSent }: { onSent: (id: number) => void }) {
  const [place, setPlace] = useState<string>(PLACES[0]!.name);
  const [pos, setPos] = useState<{ lat: number; lon: number } | null>({ lat: PLACES[0]!.lat, lon: PLACES[0]!.lon });
  const [severity, setSeverity] = useState<Severity>("HIGH");
  const [type, setType] = useState<IncidentType>("ROAD_ACCIDENT");
  const [patients, setPatients] = useState(1);
  const [phone, setPhone] = useState("");
  const [description, setDescription] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);   // 503: saved, dispatched when the server is back
  const area = useHealth().data?.service_area;
  const outside = !!pos && !!area && !inServiceArea(area, pos.lat, pos.lon);

  const choosePlace = (name: string) => {
    setPlace(name);
    const p = PLACES.find((x) => x.name === name);
    if (p) setPos({ lat: p.lat, lon: p.lon });
  };
  const setCoord = (k: "lat" | "lon", v: string) => {
    const n = Number(v);
    if (v.trim() === "" || Number.isNaN(n)) return;
    setPlace(ON_MAP);
    setPos((p) => ({ lat: p?.lat ?? 30.32, lon: p?.lon ?? 78.03, [k]: n }));
  };

  async function send() {
    if (!pos || outside) return;
    setSending(true);
    setError(null);
    setNotice(null);
    try {
      const r = await reportIncident({
        lat: pos.lat, lon: pos.lon, severity, incident_type: type, patients,
        caller_phone: phone.trim() || null, description: description.trim() || null,
      });
      if (r.duplicate) toast.success(`Merged into #${r.incident_id}`, `${r.report_count} callers so far`);
      else if (r.patients > 1) toast.success(`Incident #${r.incident_id} created`, `${r.patients} patients, ${r.patients} ambulances`);
      else toast.success(`Incident #${r.incident_id} created`, r.message);
      onSent(r.incident_id);
    } catch (e) {
      // 503: the server is shutting down, but the call is saved and is dispatched when it is back
      if (e instanceof ApiError && e.status === 503) setNotice(e.message);
      else setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "Report emergency" }]} title="Report emergency" />
      <div className="grid items-start gap-5 xl:grid-cols-[440px_minmax(0,1fr)]">
        <Card className="grid gap-5 p-6">
          <Field label="Place" hint="Or click the map.">
            <Select label="Place" width="w-full" value={place} onChange={choosePlace}
              options={[...(place === ON_MAP ? [{ value: ON_MAP, label: "Picked on map" }] : []), ...PLACES.map((p) => ({ value: p.name, label: p.name }))]} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Latitude" htmlFor="lat"><Input id="lat" className="num" inputMode="decimal" value={pos?.lat ?? ""} onChange={(e) => setCoord("lat", e.target.value)} /></Field>
            <Field label="Longitude" htmlFor="lon"><Input id="lon" className="num" inputMode="decimal" value={pos?.lon ?? ""} onChange={(e) => setCoord("lon", e.target.value)} /></Field>
          </div>
          {outside && <p className="-mt-3 text-meta text-critical-text">Outside the service area ({serviceAreaText(area!)}).</p>}
          <Field label="Severity">
            <Segmented label="Severity" value={severity} onChange={setSeverity} options={SEVERITIES.map((s) => ({ value: s, label: SEVERITY_LABEL[s] }))} />
          </Field>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3">
            <Field label="Type">
              <Select label="Type" width="w-full" value={type} onChange={setType} options={TYPES.map((t) => ({ value: t, label: INCIDENT_TYPE_LABEL[t] }))} />
            </Field>
            <Field label="Patients">
              <NumberInput label="Patients" value={patients} onChange={setPatients} min={1} max={10} />
            </Field>
          </div>
          <Field label="Caller phone" htmlFor="phone">
            <Input id="phone" className="num" maxLength={20} placeholder="98765 43210" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
          <Field label="Notes" htmlFor="desc">
            <textarea id="desc" rows={3} maxLength={255} value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="What the caller says"
              className="w-full resize-y border border-line-strong bg-panel px-3 py-2 text-body text-ink placeholder:text-faint hover:border-ink focus-visible:outline-2 focus-visible:outline-focus" />
          </Field>
          {error && <Banner kind="error" title="Not accepted">{error}</Banner>}
          {notice && <Banner kind="warning" title="Saved, not dispatched yet">{notice}</Banner>}
          <Button variant="primary" disabled={!pos || outside || sending} onClick={send}>{sending ? "Sending…" : "Dispatch"}</Button>
        </Card>
        <Card>
          <Strip title="Location" aside={pos ? <span className="num">{pos.lat.toFixed(4)}, {pos.lon.toFixed(4)}</span> : "Click the map"} />
          {area ? (
            <LocationPicker value={pos} severity={severity} area={area} onPick={(lat, lon) => { setPlace(ON_MAP); setPos({ lat, lon }); }}
              className="h-[min(640px,calc(100dvh-260px))] min-h-[420px]" />
          ) : <Skeleton className="h-[min(640px,calc(100dvh-260px))] min-h-[420px]" />}
        </Card>
      </div>
    </div>
  );
}
