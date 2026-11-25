"""APP-06/07: rate limits shared by every process of the app.

Counters live in the application database (SQLite or PostgreSQL), so with several gunicorn
processes, several containers or a worker restart, "5 wrong passwords" still means 5 in total,
not 5 per process. A Redis backend could replace `DatabaseStore` later without touching callers.

Counting uses an approximate sliding window: the current fixed window plus the previous one
weighted by how much of it still overlaps the last `window` seconds. That avoids the "double
burst at the window edge" of plain fixed windows while costing one row per key and window.
"""
from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol

from . import db as dbapi

KEEP_SECONDS = 2 * 24 * 3600  # counters older than this are deleted now and then


def now() -> float:
    """The clock of every limiter built without its own. Tests pin it (tests/conftest.py): with
    the real clock, ten requests that straddle a window edge are weighted to just under ten."""
    return time.time()


def _now() -> float:
    return now()  # looked up at each call, so pinning `ratelimit.now` reaches existing limiters


class Store(Protocol):
    def incr(self, key: str, window: int, now: float) -> float: ...
    def count(self, key: str, window: int, now: float) -> float: ...
    def lock(self, key: str, until: float) -> None: ...
    def locked_until(self, key: str) -> float: ...
    def clear(self, key: str) -> None: ...
    def clear_counts(self, key: str) -> None: ...


def _weighted(current: int, previous: int, window: int, now: float) -> float:
    elapsed = (now % window) / window
    return current + previous * (1.0 - elapsed)


class MemoryStore:
    """One process only (tests, or a deliberate single-process setup)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = defaultdict(int)
        self._locks: dict[str, float] = {}
        self._mutex = threading.Lock()

    def incr(self, key: str, window: int, now: float) -> float:
        start = int(now // window) * window
        with self._mutex:
            self._counts[(key, start)] += 1
            return _weighted(self._counts[(key, start)], self._counts.get((key, start - window), 0), window, now)

    def count(self, key: str, window: int, now: float) -> float:
        start = int(now // window) * window
        with self._mutex:
            return _weighted(self._counts.get((key, start), 0), self._counts.get((key, start - window), 0), window, now)

    def lock(self, key: str, until: float) -> None:
        with self._mutex:
            self._locks[key] = until

    def locked_until(self, key: str) -> float:
        with self._mutex:
            return self._locks.get(key, 0.0)

    def clear(self, key: str) -> None:
        with self._mutex:
            self._locks.pop(key, None)
        self.clear_counts(key)

    def clear_counts(self, key: str) -> None:
        with self._mutex:
            for k in [k for k in self._counts if k[0] == key]:
                del self._counts[k]


class DatabaseStore:
    """Counters in the tables rate_counters / rate_locks of the app database."""

    def __init__(self, target: str | Callable[[], str]) -> None:
        self._target = target

    @property
    def target(self) -> str:
        return self._target() if callable(self._target) else self._target

    def _run(self, fn: Callable[[dbapi.Database], float | None]) -> float | None:
        db = dbapi.connect(self.target)
        try:
            with db:
                return fn(db)
        finally:
            db.close()

    def incr(self, key: str, window: int, now: float) -> float:
        start = int(now // window) * window

        def run(db: dbapi.Database) -> float:
            current = db.execute(
                """INSERT INTO rate_counters (key, window_start, count) VALUES (?, ?, 1)
                   ON CONFLICT (key, window_start) DO UPDATE SET count = rate_counters.count + 1
                   RETURNING count""",
                (key, start),
            ).fetchone()[0]
            previous = db.execute("SELECT count FROM rate_counters WHERE key = ? AND window_start = ?",
                                  (key, start - window)).fetchone()
            if random.random() < 0.01:  # noqa: S311 - housekeeping, not security
                db.execute("DELETE FROM rate_counters WHERE window_start < ?", (int(now) - KEEP_SECONDS,))
                db.execute("DELETE FROM rate_locks WHERE until < ?", (now,))
            return _weighted(int(current), int(previous[0]) if previous else 0, window, now)

        return float(self._run(run) or 0.0)

    def count(self, key: str, window: int, now: float) -> float:
        start = int(now // window) * window

        def run(db: dbapi.Database) -> float:
            rows = {r[0]: int(r[1]) for r in db.execute(
                "SELECT window_start, count FROM rate_counters WHERE key = ? AND window_start IN (?, ?)",
                (key, start, start - window))}
            return _weighted(rows.get(start, 0), rows.get(start - window, 0), window, now)

        return float(self._run(run) or 0.0)

    def lock(self, key: str, until: float) -> None:
        def run(db: dbapi.Database) -> None:
            db.execute("INSERT INTO rate_locks (key, until) VALUES (?, ?) "
                       "ON CONFLICT (key) DO UPDATE SET until = excluded.until", (key, until))

        self._run(run)

    def locked_until(self, key: str) -> float:
        row = self._run(lambda db: (r := db.execute("SELECT until FROM rate_locks WHERE key = ?", (key,)).fetchone())
                        and float(r[0]))
        return float(row or 0.0)

    def clear(self, key: str) -> None:
        def run(db: dbapi.Database) -> None:
            db.execute("DELETE FROM rate_counters WHERE key = ?", (key,))
            db.execute("DELETE FROM rate_locks WHERE key = ?", (key,))

        self._run(run)

    def clear_counts(self, key: str) -> None:
        def run(db: dbapi.Database) -> None:
            db.execute("DELETE FROM rate_counters WHERE key = ?", (key,))

        self._run(run)


class Limiter:
    """`limit` events per `window` seconds per key; optionally a lock-out after the limit."""

    def __init__(self, store: Store, limit: int, window: int, lockout: int = 0,
                 clock: Callable[[], float] | None = None) -> None:
        self.store, self.limit, self.window, self.lockout = store, limit, window, lockout
        self.clock = clock or _now

    def blocked(self, *keys: str) -> bool:
        now = self.clock()
        for key in keys:
            if self.store.locked_until(key) > now:
                return True
            if not self.lockout and self.store.count(key, self.window, now) >= self.limit:
                return True
        return False

    def hit(self, *keys: str) -> bool:
        """Count one event; True when a key went over its limit (and got locked, with lockout)."""
        now = self.clock()
        over = False
        for key in keys:
            if self.store.incr(key, self.window, now) >= self.limit:
                over = True
                if self.lockout:
                    self.store.lock(key, now + self.lockout)
                    self.store.clear_counts(key)  # after the lock-out the count starts from zero
        return over

    def retry_after(self, *keys: str) -> int:
        now = self.clock()
        locks = [self.store.locked_until(k) - now for k in keys]
        return max(1, int(max([*locks, self.window - (now % self.window)])))

    def reset(self, *keys: str) -> None:
        for key in keys:
            self.store.clear(key)


def store_for(app) -> Store:
    """RATE_LIMIT_STORE=database (default): shared by all processes; memory: this process only.
    The database is looked up at use time (tests point DATABASE elsewhere after create_app)."""
    if app.config.get("RATE_LIMIT_STORE", "database") == "memory":
        return MemoryStore()
    return DatabaseStore(lambda: str(app.config["DATABASE"]))
