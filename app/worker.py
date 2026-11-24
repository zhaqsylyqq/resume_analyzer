"""APP-04 — the analysis worker: `flask --app app worker`.

With WORKER_MODE=external the web process only saves uploaded resumes as `pending`; one or
more worker processes (on the same machine or other ones) analyze them:

- each worker runs `--threads` threads (default LLM_CONCURRENCY), each in a loop:
  pick the next session with pending resumes (round-robin, so a small batch is not stuck
  behind a big one, as Q1), claim ONE resume atomically (PostgreSQL: FOR UPDATE SKIP LOCKED,
  so several workers never take the same resume), analyze it, repeat; when the queue is
  empty, wait WORKER_POLL_SECONDS;
- every minute a worker moves resumes stuck in `processing` for STALE_PROCESSING_MINUTES
  (their worker died) back to `pending`;
- SIGTERM / Ctrl+C: stop taking resumes, finish the ones in hand (up to SHUTDOWN_TIMEOUT),
  exit 0 — the same contract as the web server (APP-08).

The AI call and the result handling are the same code as inline mode (jobs._analyze).
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from .services import jobs, storage
from .services.llm import GeminiError, LLMService, UsageCounter, get_llm

log = logging.getLogger("app.worker")
REQUEUE_EVERY = 60.0


class Worker:
    def __init__(self, app: Flask, service: LLMService, threads: int) -> None:
        self.app = app
        self.service = service
        self.threads = max(1, threads)
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._rotation: deque[int] = deque()
        self.analyzed = 0
        self.busy = 0

    # Picking work -----------------------------------------------------------------------------

    def _next_session(self, db: Any) -> int | None:
        """Round-robin over sessions with pending resumes, shared by this process's threads."""
        with self._lock:
            pending = storage.sessions_with_pending(db)
            if not pending:
                self._rotation.clear()
                return None
            for session_id in pending:  # newcomers join the back of the line
                if session_id not in self._rotation:
                    self._rotation.append(session_id)
            for _ in range(len(self._rotation)):
                session_id = self._rotation[0]
                self._rotation.rotate(-1)
                if session_id in pending:
                    return session_id
                self._rotation.remove(session_id)  # finished meanwhile
        return None

    def work_once(self, db: Any) -> bool:
        """Analyze one resume; False when there was nothing to do."""
        session_id = self._next_session(db)
        if session_id is None:
            return False
        ids = storage.claim_pending(db, session_id, 1)
        if not ids:
            return True  # another worker took it; look again right away
        session = storage.get_session(db, session_id)
        language = session["language"] if session is not None else "ru"
        usage = UsageCounter()
        with self._lock:
            self.busy += 1
        try:
            jobs._analyze(self.app, self.service, ids[0], language, usage)
        finally:
            with self._lock:
                self.busy -= 1
                self.analyzed += 1
        if usage.requests:
            log.info("Resume %s analyzed: %d requests, prompt %d tokens (cache hit %d), completion %d tokens",
                     ids[0], usage.requests, usage.prompt_tokens, usage.cache_hit_tokens, usage.completion_tokens)
        return True

    # Loops ----------------------------------------------------------------------------------

    def _thread_loop(self) -> None:
        poll = float(self.app.config["WORKER_POLL_SECONDS"])
        with self.app.app_context():
            db = storage.connect(self.app.config["DATABASE"])
            try:
                while not self.stop.is_set():
                    try:
                        if not self.work_once(db):
                            self.stop.wait(poll)
                    except Exception:  # noqa: BLE001 - a database hiccup must not kill the worker
                        log.exception("Worker loop error; retrying in %ss", poll)
                        db.close()
                        self.stop.wait(poll)
                        db = storage.connect(self.app.config["DATABASE"])
            finally:
                db.close()

    def requeue_stale(self) -> int:
        minutes = int(self.app.config["STALE_PROCESSING_MINUTES"])
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        db = storage.connect(self.app.config["DATABASE"])
        try:
            moved = storage.requeue_stale(db, cutoff)
        finally:
            db.close()
        if moved:
            log.warning("Requeued %d resume(s) left in processing for over %d minutes", moved, minutes)
        return moved

    def run(self) -> None:
        log.info("Worker started: %d thread(s), database %s", self.threads,
                 "PostgreSQL" if str(self.app.config["DATABASE"]).startswith("postgres") else "SQLite")
        self.requeue_stale()
        threads = [threading.Thread(target=self._thread_loop, name=f"worker-{i}", daemon=True)
                   for i in range(self.threads)]
        for t in threads:
            t.start()
        last_requeue = time.monotonic()
        while not self.stop.is_set():
            self.stop.wait(1.0)
            if time.monotonic() - last_requeue >= REQUEUE_EVERY:
                last_requeue = time.monotonic()
                try:
                    self.requeue_stale()
                except Exception:  # noqa: BLE001
                    log.exception("Requeue of stale resumes failed")
        deadline = time.monotonic() + float(self.app.config["SHUTDOWN_TIMEOUT"])
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        left = sum(t.is_alive() for t in threads)
        if left:
            log.warning("Stopped with %d resume(s) unfinished; they are requeued after %s minutes",
                        left, self.app.config["STALE_PROCESSING_MINUTES"])
        log.info("Worker stopped cleanly after %d resume(s)", self.analyzed)


@click.command("worker")
@click.option("--threads", type=click.IntRange(min=1, max=16), default=None,
              help="Parallel analyses in this process (default: LLM_CONCURRENCY).")
@with_appcontext
def worker_command(threads: int | None) -> None:
    """Analyze pending resumes until stopped (use with WORKER_MODE=external)."""
    app = current_app._get_current_object()  # type: ignore[attr-defined]
    try:
        service = get_llm()
    except GeminiError as exc:
        raise click.ClickException(f"AI provider not configured: {exc}") from None
    if not jobs.external(app):
        click.echo("Note: WORKER_MODE is not 'external'; the web process analyzes resumes too.", err=True)
    worker = Worker(app, service, threads or int(app.config["LLM_CONCURRENCY"]))

    def stop(signum: int, _frame: Any) -> None:
        log.info("Received %s: finishing resumes in hand (up to %ss)", signal.Signals(signum).name,
                 app.config["SHUTDOWN_TIMEOUT"])
        worker.stop.set()

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None and threading.current_thread() is threading.main_thread():
            signal.signal(sig, stop)
    worker.run()


def init_worker(app: Flask) -> None:
    app.cli.add_command(worker_command)
