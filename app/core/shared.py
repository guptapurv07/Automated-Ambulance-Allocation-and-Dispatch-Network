"""What several server processes share, kept in Redis.

One server process keeps three things in its own memory: the dispatch queue,
the duplicate check's lock and the simulation clock. That is all one process
needs. Started side by side (two servers on one database, or
`uvicorn --workers 2`), each process would have its own copy of all three, and
none would see the others':

    queue   a call is ordered only against the calls that reached the same process
    lock    two processes can each decide that the same accident is a new emergency
    clock   every process moves the same ambulances

With REDIS_URL set they share one copy of each, in Redis:

    queue   app/core/redis_queue.py: one queue that every process's workers take from
    lock    `held()` below: one report at a time across all processes
    clock   `Lease` below: one process at a time runs the clock

MySQL stays the record of what happened, and its row locks and unique keys
still decide every allocation; Redis only coordinates the processes. If Redis
stops answering, each process carries on alone, exactly as a single server
does, and says so in its log. Without REDIS_URL nothing here is used, and the
redis package is not even needed.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings

try:
    import redis
except ImportError:  # only needed when REDIS_URL is set
    redis = None

logger = logging.getLogger("dispatch.redis")

# Redis's errors, to catch; an empty tuple, which catches nothing, without the package.
REDIS_ERRORS: tuple[type[Exception], ...] = (redis.RedisError,) if redis else ()

_client = None
_client_lock = threading.Lock()

# Problems already reported, so that a Redis that has gone away is logged once
# and not on every call.
_reported: set[str] = set()


def enabled() -> bool:
    """Is Redis configured? Without it, every process keeps its own state."""
    return bool(get_settings().redis_url)


def client():
    """The connection to Redis, made on first use.

    One client serves every thread: it keeps a pool of connections and gives
    each command one that no other thread is using at that moment.
    """
    global _client
    with _client_lock:
        if _client is None:
            if redis is None:
                raise RuntimeError(
                    "REDIS_URL is set but the redis package is not installed: "
                    "pip install -r requirements.txt"
                )
            _client = redis.Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                # Short, so that a Redis that stops answering is noticed within
                # seconds and the process carries on alone.
                socket_connect_timeout=1.0,
                socket_timeout=3.0,
                health_check_interval=30,
            )
        return _client


def key(name: str) -> str:
    """A key in this database's own namespace.

    Each MySQL database has its own, so a benchmark on a scratch database never
    touches the queue of the server that runs the demonstration.
    """
    return f"ambulance:{get_settings().db_name}:{name}"


def where() -> str:
    """host:port/db of the Redis in use, for the log. Never shows a password."""
    mav_kwargs = client().connection_pool.connection_kwargs
    return f"{mav_kwargs.get('host')}:{mav_kwargs.get('port')}/{mav_kwargs.get('db', 0)}"


def check() -> None:
    """At start-up: with REDIS_URL set, Redis has to answer. Raises otherwise."""
    try:
        client().ping()
    except REDIS_ERRORS as exc:
        raise RuntimeError(
            f"REDIS_URL is set but Redis does not answer ({exc}). "
            "Start it with:  redis-server   or leave REDIS_URL empty in .env"
        ) from None


def report_failure(what: str, exc: Exception) -> None:
    """Log that Redis failed for `what`, the first time only."""
    if what not in _reported:
        _reported.add(what)
        logger.warning(
            "Redis is not answering (%s: %s); this server carries on alone", what, exc
        )


def report_success(what: str) -> None:
    """Log that Redis answers again for `what`, if it had failed."""
    if what in _reported:
        _reported.discard(what)
        logger.warning("Redis is answering again (%s)", what)


@contextmanager
def held(name: str, lease_seconds: float = 10.0, wait_seconds: float = 10.0) -> Iterator[bool]:
    """Hold the lock `name`, shared by every server process, for the block.

    Yields True while it is held. It yields False, and the block runs all the
    same, when Redis is not configured, is not answering, or the lock could not
    be had within `wait_seconds`; the caller's own in-process lock then guards
    the block, as on a single server.

    The lock is a key set only if it does not exist yet (SET NX) to a value
    known only to the holder, with a lease: a process that dies while holding
    it frees it after `lease_seconds`. It is given back only by the process
    that holds it, checked and deleted in one step inside Redis.
    """
    if not enabled():
        yield False
        return
    mav_lock = client().lock(
        key(f"lock:{name}"),
        timeout=lease_seconds,
        # While another process holds it, ask again every 2 ms. Only one thread
        # per process asks, because callers take their in-process lock first.
        sleep=0.002,
        blocking_timeout=wait_seconds,
    )
    try:
        mav_got = mav_lock.acquire()
    except REDIS_ERRORS as exc:
        report_failure(f"lock {name}", exc)
        mav_got = False
    else:
        report_success(f"lock {name}")
        if not mav_got:
            logger.warning(
                "lock %s was still held by another process after %.0fs; going ahead without it",
                name,
                wait_seconds,
            )
    try:
        yield mav_got
    finally:
        if mav_got:
            try:
                mav_lock.release()
            except REDIS_ERRORS as exc:
                # Its lease ran out while the block was still running, or Redis
                # went away. Either way it is no longer this process's lock.
                logger.warning("lock %s could not be given back: %s", name, exc)


class Lease:
    """A role that only one server process may have at a time, such as running the clock.

    The holder renews the lease every time it does the work. If it stops (a
    crash, a shutdown, a laptop that went to sleep), the lease runs out after
    `seconds` and the next process to ask takes the role over: a leader
    election that needs no vote. Without Redis every process has the role.
    """

    # Renew, or give up, only a lease this process still holds: checked and
    # done in one step inside Redis, so a lease another process has taken over
    # in the meantime is never touched.
    _RENEW = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
    )
    _GIVE_UP = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )

    def __init__(self, name: str, seconds: float) -> None:
        self.name = name
        self.held = False
        self._announced = False  # the first answer is logged either way
        self._key = key(f"lease:{name}")
        self._token = uuid.uuid4().hex
        self._ms = int(seconds * 1000)

    def hold(self) -> bool:
        """Claim the role, or renew it. True if this process has it now."""
        if not enabled():
            self.held = True
            return True
        try:
            mav_client = client()
            mav_have = bool(
                mav_client.set(self._key, self._token, nx=True, px=self._ms)
            ) or bool(mav_client.eval(self._RENEW, 1, self._key, self._token, self._ms))
        except REDIS_ERRORS as exc:
            # Better every process doing the work than none: each step of the
            # clock is decided again under the ambulance's row lock, so a step
            # that two processes both try is made once and refused once.
            report_failure(f"lease {self.name}", exc)
            mav_have = True
        else:
            report_success(f"lease {self.name}")
        if mav_have != self.held or not self._announced:
            logger.info(
                "%s the %s", "this process now runs" if mav_have else "another process runs", self.name
            )
            self._announced = True
        self.held = mav_have
        return mav_have

    def give_up(self) -> None:
        """Hand the role over at once, instead of letting the lease run out."""
        if enabled() and self.held:
            try:
                client().eval(self._GIVE_UP, 1, self._key, self._token)
            except REDIS_ERRORS:
                pass  # it runs out by itself
        self.held = False


def clear_namespace() -> int:
    """Delete every key of this database's namespace. Returns how many.

    For `sim.seed --reset`: a reset database starts its ids from 1 again, and a
    queue entry left over from before would then name a different incident.
    """
    mav_client = client()
    mav_keys = list(mav_client.scan_iter(match=key("*"), count=500))
    for mav_start in range(0, len(mav_keys), 500):
        mav_client.delete(*mav_keys[mav_start : mav_start + 500])
    return len(mav_keys)
