"""APP-08: readiness check and graceful shutdown."""
from __future__ import annotations

import signal
import threading
import time

import pytest

from app import install_shutdown_handlers
from app.services import jobs, storage

from .helpers import create_session, resume_text, status, upload


@pytest.fixture(autouse=True)
def _not_draining():
    jobs._reset_for_tests()
    yield
    jobs._reset_for_tests()


def test_readyz_is_public_and_ready(app):
    response = app.test_client().get("/readyz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ready"}


def test_readyz_reports_shutdown(app):
    assert jobs.drain(0) is True  # nothing in flight
    response = app.test_client().get("/readyz")
    assert response.status_code == 503
    assert response.get_json() == {"status": "shutting_down"}
    assert app.test_client().get("/healthz").status_code == 200  # still alive while draining


def test_readyz_reports_database_failure(app, monkeypatch):
    import sqlite3

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(storage, "get_db", lambda: Broken())
    response = app.test_client().get("/readyz")
    assert response.status_code == 503
    assert response.get_json() == {"status": "database_unavailable"}


def test_uploads_while_draining_stay_pending_for_the_next_start(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    jobs.drain(0)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    assert status(client, session_id)["progress"]["pending"] == 1
    assert len(fake_gemini.client.models.calls) == 0
    jobs._reset_for_tests()  # next start
    assert jobs.resume_pending(app) == [session_id]
    assert status(client, session_id)["progress"]["done"] == 1


def test_drain_waits_for_the_resume_in_flight(monkeypatch):
    batch = jobs._Batch(in_flight=1)
    monkeypatch.setitem(jobs._batches, 999, batch)

    def finish_soon():
        time.sleep(0.3)
        with jobs._lock:
            batch.in_flight = 0

    threading.Thread(target=finish_soon).start()
    started = time.monotonic()
    assert jobs.drain(5) is True
    assert 0.2 < time.monotonic() - started < 3


def test_drain_gives_up_after_the_timeout(monkeypatch, caplog):
    monkeypatch.setitem(jobs._batches, 998, jobs._Batch(in_flight=1))
    started = time.monotonic()
    assert jobs.drain(0.3) is False
    assert time.monotonic() - started < 2
    assert "still being analyzed" in caplog.text


def test_shutdown_timeout_setting():
    from app.config import ConfigError, load_settings

    assert load_settings({}).to_flask()["SHUTDOWN_TIMEOUT"] == 25
    assert load_settings({"SHUTDOWN_TIMEOUT": "5"}).shutdown_timeout == 5
    with pytest.raises(ConfigError):
        load_settings({"SHUTDOWN_TIMEOUT": "-1"})


def test_signal_handlers(app, monkeypatch):
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    drained = []
    monkeypatch.setattr(jobs, "drain", lambda timeout: drained.append(timeout) or True)
    try:
        handled = install_shutdown_handlers(app)
        assert int(signal.SIGTERM) in handled and int(signal.SIGINT) in handled
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit) as stop:
            handler(signal.SIGTERM, None)
        assert stop.value.code == 0
        with pytest.raises(KeyboardInterrupt):
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert drained == [25, 25]
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


def test_signal_handlers_skipped_outside_the_main_thread(app):
    result = []
    thread = threading.Thread(target=lambda: result.append(install_shutdown_handlers(app)))
    thread.start()
    thread.join()
    assert result == [[]]
