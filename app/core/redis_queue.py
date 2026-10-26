"""The dispatch queue in Redis, shared by every server process.

It keeps the contract of DispatchQueue (app/core/queue.py) and uses the same
three schedulers, so the intake, the worker pool and the retry path cannot tell
which of the two they were given. It is the one used when REDIS_URL is set.

What it stores, in this database's namespace (see app/core/shared.py):

    queue            sorted set: member = incident id, score = the order to serve in
    queue:request    hash: incident id -> the waiting request, as JSON
    queue:seq        counter: arrival number, across all processes
    queue:stats      hash: the totals that GET /queue shows

A free worker takes the call with the lowest score with BZPOPMIN. Redis carries
out one command at a time, so two workers, of this process or another, can
never take the same call: the pop is the critical section that the condition
variable protects in the in-memory queue. A worker blocks inside BZPOPMIN until
a call arrives or its timeout runs out, as a worker waiting on the condition
variable does.

The score is fixed when the call is queued:

    fifo      its arrival number
    priority  severity rank x 10^12 + arrival number
    llf       deadline - expected travel time, in seconds since 1970. Laxity is
              deadline - now - travel, and `now` is the same for every waiting
              call at the moment of choosing, so the call with the lowest
              deadline - travel is the call with the least laxity.

The member is the incident id, so queueing a call that is already waiting
changes nothing: a call recovered at start-up while it is still queued is not
queued a second time.

If Redis stops answering, calls go to a queue in this process's own memory and
the process carries on alone; its workers empty that queue first once Redis is
back.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core import shared
from app.core.queue import (
    SEVERITY_RANK,
    DispatchQueue,
    DispatchRequest,
    QueueClosed,
    Scheduler,
)

# Queue one call, all in one step inside Redis: give it the next arrival
# number, work out its score, add it unless it is waiting already, keep its
# request and update the totals. %.17g writes the score with every digit, so
# nothing is lost on the way to the sorted set.
_PUT = """
local seq = redis.call('INCR', KEYS[3])
local score
if ARGV[2] == 'fifo' then
  score = seq
elseif ARGV[2] == 'priority' then
  score = tonumber(ARGV[3]) * 1e12 + seq
else
  score = tonumber(ARGV[3])
end
if redis.call('ZADD', KEYS[1], 'NX', string.format('%.17g', score), ARGV[1]) == 0 then
  return 0
end
redis.call('HSET', KEYS[2], ARGV[1], ARGV[4])
redis.call('HINCRBY', KEYS[4], 'enqueued_total', 1)
local depth = redis.call('ZCARD', KEYS[1])
if depth > tonumber(redis.call('HGET', KEYS[4], 'peak_depth') or '0') then
  redis.call('HSET', KEYS[4], 'peak_depth', depth)
end
return 1
"""

# How long one BZPOPMIN waits when the caller asked to wait without a limit, so
# that a worker still notices, now and then, that the queue has been closed.
_WAIT_SLICE_SECONDS = 1.0


class RedisDispatchQueue:
    """DispatchQueue's contract, with the waiting calls kept in Redis."""

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler
        self._keys = [
            shared.key("queue"),
            shared.key("queue:request"),
            shared.key("queue:seq"),
            shared.key("queue:stats"),
        ]
        # Where calls go while Redis is not answering.
        self._local = DispatchQueue(scheduler=scheduler)
        self._closed = False
        self._put_script = None

    @property
    def scheduler(self) -> Scheduler:
        """The policy in use. The retry path applies the same one."""
        return self._scheduler

    @property
    def scheduler_name(self) -> str:
        return self._scheduler.name

    def put(self, request: DispatchRequest) -> None:
        """Add a request to the shared queue; a waiting worker of any process takes it."""
        if self._closed:
            raise QueueClosed("Queue is shut down; not accepting work")
        try:
            if self._put_script is None:
                self._put_script = shared.client().register_script(_PUT)
            self._put_script(
                keys=self._keys,
                args=[
                    str(request.incident_id),
                    self._scheduler.name,
                    self._score_input(request),
                    _to_json(request),
                ],
            )
        except shared.REDIS_ERRORS as exc:
            shared.report_failure("queue", exc)
            self._local.put(request)
        else:
            shared.report_success("queue")

    def get(self, timeout: float | None = None) -> DispatchRequest | None:
        """Block until a request is available, then return it.

        Returns None if the wait timed out, or if the queue was closed and
        nothing is left to take -- which is how workers learn it is time to exit.
        """
        # Calls that were kept in memory while Redis was not answering go first.
        mav_request = self._local.get(timeout=0)
        if mav_request is not None:
            return mav_request
        while True:
            try:
                mav_member = self._pop(timeout)
            except shared.REDIS_ERRORS as exc:
                shared.report_failure("queue", exc)
                # Wait on the in-memory queue instead, so the worker does not spin.
                return self._local.get(timeout=timeout if timeout is not None else _WAIT_SLICE_SECONDS)
            shared.report_success("queue")
            if mav_member is not None:
                return self._take_request(mav_member)
            if timeout is not None or self._closed:
                return None

    def _pop(self, timeout: float | None) -> str | None:
        """The id of the call with the lowest score, removed from the queue."""
        mav_client = shared.client()
        if self._closed or timeout == 0:
            # Shutting down: take what is waiting, without waiting for more.
            mav_popped = mav_client.zpopmin(self._keys[0])
            return mav_popped[0][0] if mav_popped else None
        mav_popped = mav_client.bzpopmin(
            self._keys[0], timeout=timeout if timeout is not None else _WAIT_SLICE_SECONDS
        )
        return mav_popped[1] if mav_popped else None

    def _take_request(self, member: str) -> DispatchRequest:
        """The stored request of a call just popped, removed from the store."""
        try:
            mav_pipe = shared.client().pipeline()
            mav_pipe.hget(self._keys[1], member)
            mav_pipe.hdel(self._keys[1], member)
            mav_pipe.hincrby(self._keys[3], "dequeued_total", 1)
            mav_payload = mav_pipe.execute()[0]
        except shared.REDIS_ERRORS as exc:
            # The call itself is already out of the queue; its id is all the
            # worker needs to load it.
            shared.report_failure("queue", exc)
            mav_payload = None
        return _from_json(member, mav_payload)

    def close(self) -> None:
        """Stop accepting work. Waiting workers notice within one wait."""
        self._closed = True
        self._local.close()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def waiting(self, incident_ids: list[int]) -> set[int]:
        """Which of these incidents are in the queue right now.

        Raises if Redis is not answering, since the answer would be a guess.
        """
        mav_pipe = shared.client().pipeline(transaction=False)
        for mav_id in incident_ids:
            mav_pipe.zscore(self._keys[0], str(mav_id))
        mav_in_redis = {
            mav_id for mav_id, mav_score in zip(incident_ids, mav_pipe.execute()) if mav_score is not None
        }
        return mav_in_redis | (self._local.incident_ids() & set(incident_ids))

    @property
    def depth(self) -> int:
        """How many requests are waiting right now, in every process."""
        try:
            return int(shared.client().zcard(self._keys[0])) + self._local.depth
        except shared.REDIS_ERRORS:
            return self._local.depth

    def stats(self) -> dict[str, int | str]:
        mav_local = self._local.stats()
        try:
            mav_pipe = shared.client().pipeline(transaction=False)
            mav_pipe.zcard(self._keys[0])
            mav_pipe.hgetall(self._keys[3])
            mav_depth, mav_totals = mav_pipe.execute()
        except shared.REDIS_ERRORS:
            return {**mav_local, "backend": "memory (Redis is not answering)"}
        return {
            "backend": "redis",
            "scheduler": self._scheduler.name,
            "depth": int(mav_depth) + int(mav_local["depth"]),
            "peak_depth": max(int(mav_totals.get("peak_depth", 0)), int(mav_local["peak_depth"])),
            "enqueued_total": int(mav_totals.get("enqueued_total", 0)) + int(mav_local["enqueued_total"]),
            "dequeued_total": int(mav_totals.get("dequeued_total", 0)) + int(mav_local["dequeued_total"]),
        }

    def _score_input(self, request: DispatchRequest) -> str:
        """What the put script needs to work out the request's score."""
        if self._scheduler.name == "priority":
            return str(SEVERITY_RANK.get(request.severity, len(SEVERITY_RANK)))
        if self._scheduler.name == "llf":
            return repr(request.deadline_at.timestamp() - request.travel_seconds)
        return "0"


def _to_json(request: DispatchRequest) -> str:
    return json.dumps({
        "severity": request.severity,
        "deadline_at": request.deadline_at.isoformat(),
        "travel_seconds": request.travel_seconds,
        "enqueued_at": request.enqueued_at.isoformat(),
    })


def _from_json(member: str, payload: str | None) -> DispatchRequest:
    if not payload:
        # The details were lost (Redis restarted, say). The worker needs only
        # the incident id; the rest is for its log line.
        return DispatchRequest(
            incident_id=int(member), severity="UNKNOWN", deadline_at=datetime.now(timezone.utc)
        )
    mav_data = json.loads(payload)
    return DispatchRequest(
        incident_id=int(member),
        severity=mav_data["severity"],
        deadline_at=datetime.fromisoformat(mav_data["deadline_at"]),
        travel_seconds=float(mav_data["travel_seconds"]),
        enqueued_at=datetime.fromisoformat(mav_data["enqueued_at"]),
    )
