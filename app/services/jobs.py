"""Background analysis of a session's pending resumes.

One worker thread per session drains the queue until no pending resumes remain, so files
appended while a job runs (F6) are picked up by the same job. Per-resume failures are
recorded and never stop the batch. With SYNC_JOBS (tests) the job runs inline.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, current_app

from . import storage
from .gemini import AnalysisFailed, GeminiError, GeminiService, GeminiUnavailable, get_gemini
from .schemas import VacancyInput

log = logging.getLogger(__name__)

_running: set[int] = set()
_lock = threading.Lock()


def is_running(session_id: int) -> bool:
    with _lock:
        return session_id in _running


def start(session_id: int) -> bool:
    """Start analysis for a session unless a job for it is already running."""
    app = current_app._get_current_object()
    service = get_gemini()
    with _lock:
        if session_id in _running:
            return False
        _running.add(session_id)
    if app.config["SYNC_JOBS"]:
        _run(app, service, session_id)
    else:
        threading.Thread(target=_run, args=(app, service, session_id), name=f"session-{session_id}", daemon=True).start()
    return True


def _run(app: Flask, service: GeminiService, session_id: int) -> None:
    try:
        with app.app_context():
            db = storage.connect(app.config["DATABASE"])
            try:
                session = storage.get_session(db, session_id)
                if session is None:
                    return
                language = session["language"]
                while True:
                    ids = storage.claim_pending(db, session_id)
                    if not ids:
                        break
                    workers = min(len(ids), app.config["GEMINI_CONCURRENCY"])
                    if workers <= 1 or app.config["SYNC_JOBS"]:
                        for resume_id in ids:
                            _analyze(app, service, resume_id, language)
                    else:
                        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"s{session_id}") as pool:
                            list(pool.map(lambda rid: _analyze(app, service, rid, language), ids))
            finally:
                db.close()
    except Exception:  # noqa: BLE001 - keep the worker from dying silently
        log.exception("Session %s job crashed", session_id)
    finally:
        with _lock:
            _running.discard(session_id)


def _analyze(app: Flask, service: GeminiService, resume_id: int, language: str) -> None:
    db = storage.connect(app.config["DATABASE"])
    try:
        row = storage.get_resume(db, resume_id)
        if row is None:
            return
        profile = storage.load_profile(row)
        if profile is None:
            profile = service.extract_profile(row["text"], language)
            storage.save_profile(db, resume_id, profile)
        vacancies = [VacancyInput(id=v["id"], title=v["title"], text=v["text"]) for v in storage.unscored_vacancies(db, resume_id)]
        if vacancies:
            storage.save_scores(db, resume_id, service.score(profile, vacancies, language))
        storage.set_resume_status(db, resume_id, storage.DONE)
    except AnalysisFailed:
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "invalid_output")
    except GeminiUnavailable:
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "unavailable")
    except GeminiError as exc:
        log.error("Gemini error for resume %s: %s", resume_id, exc)
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "api_error")
    except Exception:  # noqa: BLE001 - one bad resume must not stop the batch
        log.exception("Unexpected failure analyzing resume %s", resume_id)
        storage.set_resume_status(db, resume_id, storage.ANALYSIS_FAILED, "internal")
    finally:
        db.close()
