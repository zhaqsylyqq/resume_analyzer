"""Background analysis with a fair, round-robin queue across sessions (Q1).

One process-wide pool of GEMINI_CONCURRENCY worker threads serves every session. Sessions
with queued resumes form a rotation; a worker takes the next session, moves it to the back
and analyzes one of its resumes, so sessions take turns resume by resume and a small batch
is not stuck behind a big one. Per-resume failures are recorded and never stop a batch.
A session counts as running until its last in-flight resume finishes; then the token usage
of its batch is logged once (F7c). With SYNC_JOBS (tests) the rotation runs inline.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field

from flask import Flask, current_app

from . import storage
from .gemini import (
    AnalysisFailed,
    BillingExhausted,
    GeminiError,
    GeminiService,
    GeminiUnavailable,
    QuotaExhausted,
    UsageCounter,
    get_gemini,
    usage_scope,
)
from .schemas import VacancyInput

log = logging.getLogger(__name__)


@dataclass
class _Batch:
    usage: UsageCounter = field(default_factory=UsageCounter)
    analyzed: int = 0
    in_flight: int = 0


_lock = threading.Lock()
_rotation: deque[int] = deque()  # sessions with pending resumes, next turn first
_batches: dict[int, _Batch] = {}  # sessions that are running (queued or in flight)
_workers = 0


def is_running(session_id: int) -> bool:
    with _lock:
        return session_id in _batches


def start(session_id: int) -> bool:
    """Queue a session for analysis; returns False if it was already queued."""
    app = current_app._get_current_object()
    service = get_gemini()
    with _lock:
        added = session_id not in _rotation
        if added:
            _rotation.append(session_id)
            _batches.setdefault(session_id, _Batch())
    if app.config["SYNC_JOBS"]:
        _worker(app, service)
    else:
        _spawn_workers(app, service)
    return added


def _spawn_workers(app: Flask, service: GeminiService) -> None:
    global _workers
    with _lock:
        wanted = max(1, app.config["GEMINI_CONCURRENCY"]) - _workers
        _workers += max(0, wanted)
    for i in range(max(0, wanted)):
        threading.Thread(target=_worker_thread, args=(app, service), name=f"analysis-{i}", daemon=True).start()


def _worker_thread(app: Flask, service: GeminiService) -> None:
    global _workers
    try:
        _worker(app, service)
    finally:
        with _lock:
            _workers -= 1
        # Work queued in the instant this worker was exiting gets a new worker.
        with _lock:
            orphaned = bool(_rotation) and _workers == 0
        if orphaned:
            _spawn_workers(app, service)


def _worker(app: Flask, service: GeminiService) -> None:
    """Take turns between sessions until no session has pending resumes."""
    try:
        with app.app_context():
            db = storage.connect(app.config["DATABASE"])
            try:
                while _work_once(app, service, db):
                    pass
            finally:
                db.close()
    except Exception:  # noqa: BLE001 - keep the worker from dying silently
        log.exception("Analysis worker crashed")


def _work_once(app: Flask, service: GeminiService, db) -> bool:
    """Analyze one resume of the next session in the rotation; False when idle."""
    with _lock:
        if not _rotation:
            return False
        session_id = _rotation[0]
        _rotation.rotate(-1)  # this session goes to the back: the others get the next turns
        batch = _batches.setdefault(session_id, _Batch())
        batch.in_flight += 1

    ids = storage.claim_pending(db, session_id, 1)
    if not ids:
        # Decide under the same lock start() uses, so a resume queued right now either
        # gets claimed later or puts the session back into the rotation.
        with _lock:
            batch.in_flight -= 1
            if storage.progress(db, session_id)[storage.PENDING] == 0:
                if session_id in _rotation:
                    _rotation.remove(session_id)
                _finish_if_done(session_id)
        return True

    language = _session_language(db, session_id)
    try:
        _analyze(app, service, ids[0], language, batch.usage)
    finally:
        with _lock:
            batch.in_flight -= 1
            batch.analyzed += 1
            if session_id not in _rotation:
                _finish_if_done(session_id)
    return True


def _finish_if_done(session_id: int) -> None:
    """Caller holds _lock. Close the batch once nothing is queued or in flight."""
    batch = _batches.get(session_id)
    if batch is not None and batch.in_flight == 0 and session_id not in _rotation:
        del _batches[session_id]
        _log_usage(session_id, batch.analyzed, batch.usage)


def _session_language(db, session_id: int) -> str:
    session = storage.get_session(db, session_id)
    return session["language"] if session is not None else "ru"


def _log_usage(session_id: int, analyzed: int, usage: UsageCounter) -> None:
    """One INFO line per batch so the owner can see the cost; counts only, never content."""
    if usage.requests:
        log.info(
            "Session %s batch done: %d resumes, %d requests, prompt %d tokens (cache hit %d), completion %d tokens",
            session_id, analyzed, usage.requests, usage.prompt_tokens, usage.cache_hit_tokens,
            usage.completion_tokens,
        )


def eta_seconds(db, session_id: int, workers: int) -> int | None:
    """Q1: estimated seconds until this session is analyzed, None when unknown or idle."""
    remaining = _remaining(db)
    mine = remaining.get(session_id, 0)
    if not mine:
        return None
    average = storage.average_analysis_seconds(db)
    if average is None:
        return None
    # Round-robin: before our last resume, every other session gets at most as many turns.
    turns = sum(min(n, mine) for n in remaining.values())
    return max(1, round(average * turns / max(1, workers)))


def _remaining(db) -> dict[int, int]:
    rows = db.execute(
        "SELECT session_id, COUNT(*) AS n FROM resumes WHERE status IN (?, ?) GROUP BY session_id",
        (storage.PENDING, storage.PROCESSING),
    )
    return {row["session_id"]: row["n"] for row in rows}


def _analyze(app: Flask, service: GeminiService, resume_id: int, language: str,
             usage: UsageCounter | None = None) -> None:
    with usage_scope(usage or UsageCounter()):
        _analyze_one(app, service, resume_id, language)


def _analyze_one(app: Flask, service: GeminiService, resume_id: int, language: str) -> None:
    db = storage.connect(app.config["DATABASE"])
    try:
        row = storage.get_resume(db, resume_id)
        if row is None:
            return
        profile = storage.load_profile(row)
        if profile is None:
            profile = service.extract_profile(row["text"], language)
            storage.save_profile(db, resume_id, profile)
        # Loop so a vacancy added while this resume was being scored is scored too.
        for _ in range(3):
            vacancies = [VacancyInput(id=v["id"], title=v["title"], text=v["text"])
                         for v in storage.unscored_vacancies(db, resume_id)]
            if not vacancies:
                break
            storage.save_scores(db, resume_id, service.score(profile, vacancies, language))
        storage.set_resume_status(db, resume_id, storage.DONE)
    except AnalysisFailed:
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "invalid_output")
    except QuotaExhausted:
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "daily_quota")
    except BillingExhausted:
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "billing_exhausted")
    except GeminiUnavailable:
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "unavailable")
    except GeminiError as exc:
        log.error("AI error for resume %s: %s", resume_id, exc)
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "api_error")
    except Exception:  # noqa: BLE001 - one bad resume must not stop the batch
        log.exception("Unexpected failure analyzing resume %s", resume_id)
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "internal")
    finally:
        db.close()
