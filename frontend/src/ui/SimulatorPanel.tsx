import { useState } from "react";
import { useAmbulances } from "@/api/queries";
import { useSimulator } from "@/lib/simulator";
import { Button } from "./Button";
import { Segmented } from "./form";
import { Card, Strip } from "./layout";
import { StatusDot } from "./status";

const SPEEDS = [{ value: "5", label: "5 s" }, { value: "2", label: "2 s" }, { value: "1", label: "1 s" }];

/** Start / stop a stream of simulated emergency calls, or send a burst of them at once. */
export function SimulatorPanel() {
  const { running, everySeconds, created, merged, failed, lastError, start, stop, burst } = useSimulator();
  const fleet = useAmbulances().data?.length ?? 0;
  const [speed, setSpeed] = useState(String(everySeconds));
  const [busy, setBusy] = useState(false);
  const more = fleet + 5;                               // a burst bigger than the fleet, so some calls must wait
  const send = async (n: number) => {
    setBusy(true);
    await burst(n);
    setBusy(false);
  };
  return (
    <Card>
      <Strip title="Simulator" aside={<StatusDot status={running ? "ok" : "off"} label={running ? `Running · every ${everySeconds} s` : "Off"} />} />
      <div className="grid gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Segmented size="sm" label="How often" value={speed} options={SPEEDS}
            onChange={(v) => { setSpeed(v); if (running) start(Number(v)); }} />
          {running
            ? <Button size="sm" variant="secondary" onClick={stop}>Stop</Button>
            : <Button size="sm" variant="primary" onClick={() => start(Number(speed))}>Start</Button>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => send(10)}>+10 calls</Button>
          {fleet > 0 && <Button size="sm" variant="secondary" disabled={busy} onClick={() => send(more)}>+{more} calls</Button>}
        </div>
        <p className="num text-dense text-ink">
          {created} sent
          {merged > 0 && <span className="text-muted"> · {merged} merged</span>}
          {failed > 0 && <span className="text-critical-text"> · {failed} refused</span>}
        </p>
        {failed > 0 && lastError && <p className="text-meta text-critical-text">{lastError}</p>}
      </div>
    </Card>
  );
}
