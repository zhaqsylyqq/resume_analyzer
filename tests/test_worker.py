"""APP-04: separate analysis worker processes (WORKER_MODE=external)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.services import storage
from app.worker import Worker

from .helpers import create_session, resume_text, status, upload


@pytest.fixture()
def external(app, fake_gemini):
    app.config.update(WORKER_MODE="external", SYNC_JOBS=False, WORKER_POLL_SECONDS=1)
    return app


def test_web_only_queues_in_external_mode(external, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    state = status(client, session_id)
    assert state["progress"]["pending"] == 2 and state["running"] is True
    assert fake_gemini.client.models.calls == []  # the web process called no AI


def test_worker_analyzes_the_queue(external, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    worker = Worker(external, fake_gemini, threads=1)
    with external.app_context():
        db = storage.connect(external.config["DATABASE"])
        try:
            assert worker.work_once(db) and worker.work_once(db)
            assert worker.work_once(db) is False  # empty now
        finally:
            db.close()
    state = status(client, session_id)
    assert state["progress"]["done"] == 2 and state["running"] is False


def test_sessions_take_turns(external, client, csrf, fake_gemini):
    big = create_session(client, csrf, title="big")
    upload(client, csrf, big, [(f"{i}.txt", resume_text(i)) for i in range(4)])
    small = create_session(client, csrf, title="small")
    upload(client, csrf, small, [("s.txt", resume_text(99))])
    worker = Worker(external, fake_gemini, threads=1)
    with external.app_context():
        db = storage.connect(external.config["DATABASE"])
        try:
            worker.work_once(db)
            worker.work_once(db)
        finally:
            db.close()
    # After two turns the small session is finished, although the big one was first.
    assert status(client, small)["progress"]["done"] == 1


def test_run_and_stop(external, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [(f"{i}.txt", resume_text(i)) for i in range(3)])
    worker = Worker(external, fake_gemini, threads=2)
    runner = threading.Thread(target=worker.run)
    runner.start()
    for _ in range(100):
        if status(client, session_id)["progress"]["done"] == 3:
            break
        threading.Event().wait(0.1)
    worker.stop.set()
    runner.join(10)
    assert not runner.is_alive()
    assert status(client, session_id)["progress"]["done"] == 3 and worker.analyzed == 3


def test_stale_processing_is_requeued(external, client, csrf):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    with external.app_context():
        db = storage.get_db()
        first, second = (r["id"] for r in storage.list_resumes(db, session_id))
        with db:
            db.execute("UPDATE resumes SET status = ?, started_at = ? WHERE id = ?", (storage.PROCESSING, old, first))
            db.execute("UPDATE resumes SET status = ?, started_at = ? WHERE id = ?",
                       (storage.PROCESSING, storage.now(), second))
    worker = Worker(external, None, threads=1)  # type: ignore[arg-type]
    assert worker.requeue_stale() == 1  # only the one started an hour ago
    assert status(client, session_id)["progress"]["processing"] == 1


def test_web_restart_keeps_workers_resumes(external, client, csrf):
    from app import create_app

    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    with external.app_context():
        db = storage.get_db()
        storage.claim_pending(db, session_id, 1)  # a worker is analyzing it
    create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": external.config["DATABASE"],
                "WORKER_MODE": "external"})
    assert status(client, session_id)["progress"]["processing"] == 1


def test_worker_command_needs_the_ai_key(app):
    app.config.update(GEMINI_API_KEY="", LLM_PROVIDER="gemini")
    app.extensions.pop("gemini", None)
    result = app.test_cli_runner().invoke(args=["worker"])
    assert result.exit_code == 1 and "AI provider not configured" in result.output


def test_worker_settings():
    from app.config import ConfigError, load_settings

    s = load_settings({"WORKER_MODE": "external", "WORKER_POLL_SECONDS": "5"})
    assert s.worker_mode == "external" and s.to_flask()["WORKER_POLL_SECONDS"] == 5
    assert load_settings({}).worker_mode == "inline"
    with pytest.raises(ConfigError, match="WORKER_MODE"):
        load_settings({"WORKER_MODE": "celery"})


def test_gunicorn_config_loads():
    from app import gunicorn_conf

    assert gunicorn_conf.worker_class == "gthread" and gunicorn_conf.graceful_timeout == 25
