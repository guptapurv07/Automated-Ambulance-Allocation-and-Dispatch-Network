import type { ReactNode } from "react";
import { useHealth, useQueueStats } from "@/api/queries";
import { DEFAULT_TIME_SCALE, LOCKING_LABEL, POLICY_LABEL, QUEUE_BACKEND_LABEL, REDIS_LABEL, SCHEDULER_LABEL, serviceAreaText, speedText } from "@/lib/domain";
import { Button } from "@/ui/Button";
import { Card, CardHeader, Figure, Figures, PageHeader } from "@/ui/layout";
import { Banner, Chip, Skeleton, StatusDot } from "@/ui/status";

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid gap-px border border-line bg-line">
      {rows.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-4 bg-panel px-4 py-2.5">
          <dt className="text-dense text-muted">{k}</dt>
          <dd className="num m-0 text-right text-dense text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const running = (v: boolean, on = "Running", off = "Stopped") => <StatusDot status={v ? "ok" : "off"} label={v ? on : off} />;

export default function System() {
  const health = useHealth();
  const queue = useQueueStats();
  const h = health.data;
  const q = queue.data;

  if (health.isPending) return <div className="grid gap-5 px-4 py-5 md:px-8 md:py-7"><Skeleton className="h-24 w-1/2" /><Skeleton className="h-64" /></div>;
  if (health.isError || !h)
    return (
      <div className="px-4 py-5 md:px-8 md:py-7">
        <Banner kind="error" title="The dispatch server is not reachable." actions={<Button size="sm" variant="secondary" onClick={() => health.refetch()}>Retry</Button>}>
          {(health.error as Error | null)?.message ?? "No answer."} Start it with <span className="mono">uvicorn app.main:app</span>.
        </Banner>
      </div>
    );

  const ok = h.status === "ok";
  const dbDown = h.database !== "connected", redisDown = h.redis === "unreachable";
  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "System" }]} title="System"
        aside={<Chip size="sm" tone={ok ? "success" : "warning"} dot>{ok ? "Running" : "Degraded"}</Chip>} />

      {dbDown && <Banner kind="warning" title="Database unreachable">Calls are queued; nothing is assigned until MySQL is back.</Banner>}
      {redisDown && <Banner kind="warning" title="Redis unreachable">Calls queued in Redis wait until it answers.</Banner>}

      <Figures className="sm:grid-cols-2 xl:grid-cols-4">
        <Figure label="Database" value={dbDown ? "Unreachable" : "Connected"} />
        <Figure label="Redis" value={REDIS_LABEL[h.redis] ?? h.redis} />
        <Figure label="Free ambulances" value={h.ambulances_available == null ? "—" : String(h.ambulances_available)} />
        {/* Calls on the queue for a worker. A call with no free ambulance waits in the database, not here. */}
        <Figure label="In queue" value={q ? String(q.queue.depth) : "—"} detail={q ? `Peak ${q.queue.peak_depth}` : undefined} />
      </Figures>

      <div className="grid items-start gap-5 xl:grid-cols-2">
        <Card className="grid gap-4 p-6">
          <CardHeader title="Engine" />
          <Rows rows={[
            ["Policy", POLICY_LABEL[h.dispatch_policy] ?? h.dispatch_policy],
            ["Locking", LOCKING_LABEL[h.locking_mode] ?? h.locking_mode],
            ["Queue order", SCHEDULER_LABEL[h.queue_scheduler] ?? h.queue_scheduler],
            ["Queue store", q ? QUEUE_BACKEND_LABEL[q.queue.backend] ?? q.queue.backend : "—"],
            ["Merge repeat calls", h.dedup_enabled ? "On" : "Off"],
            ["Average speed", h.avg_speed_kmph == null ? "—" : `${h.avg_speed_kmph} km/h`],
            ["Lock wait timeout", h.lock_wait_timeout_seconds == null ? "—" : `${h.lock_wait_timeout_seconds} s`],
            ["Service area", h.service_area ? serviceAreaText(h.service_area) : "—"],
          ]} />
        </Card>
        <Card className="grid gap-4 p-6">
          <CardHeader title="Clock" />
          {q ? <Rows rows={[
            ["Clock", running(q.clock.running)],
            ["Leader", q.clock.leader ? "This server" : "Another server"],
            ["Speed", `${speedText(q.clock.time_scale)}${q.clock.time_scale !== DEFAULT_TIME_SCALE ? ` · default ${speedText(DEFAULT_TIME_SCALE)}` : ""}`],
            ["Ticks", q.clock.ticks],
            ["Transitions", q.clock.transitions],
            ["Retries", q.clock.requeued],
          ]} /> : <Skeleton className="h-40" />}
        </Card>
        <Card className="grid gap-4 p-6">
          <CardHeader title="Queue" />
          {q ? <Rows rows={[
            ["Order", SCHEDULER_LABEL[q.queue.scheduler] ?? q.queue.scheduler],
            ["Depth", q.queue.depth],
            ["Peak", q.queue.peak_depth],
            ["Enqueued", q.queue.enqueued_total],
            ["Dequeued", q.queue.dequeued_total],
          ]} /> : <Skeleton className="h-40" />}
        </Card>
        <Card className="grid gap-4 p-6">
          <CardHeader title="Workers" />
          {q ? <Rows rows={[
            ["Threads", q.workers.size],
            ["Pool", running(q.workers.running)],
            ["Handled", q.workers.handled],
            ["Failed", q.workers.failed],
          ]} /> : <Skeleton className="h-40" />}
        </Card>
      </div>
    </div>
  );
}
