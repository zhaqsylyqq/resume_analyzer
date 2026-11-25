"""OBS-01: /metrics, request ids, logs without personal data, Sentry."""
from __future__ import annotations

import json
import logging

import pytest
from prometheus_client import REGISTRY

from app import metrics, observability, policy
from app.config import ConfigError, load_settings

from .helpers import create_session, upload

NAME, EMAIL, PHONE = "Айгерим Сапарова", "aigerim.saparova@example.kz", "+7 701 123 45 67"
SECRET_PHRASE = "мой-уникальный-опыт-на-складе"


def _resume() -> bytes:
    return (f"{NAME}\n{EMAIL}, {PHONE}\nПол: женский. Возраст: 27\n"
            f"Python-разработчик, Django, PostgreSQL, Docker. {SECRET_PHRASE}.").encode()


def _value(name, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# /metrics ----------------------------------------------------------------------------------------

def test_metrics_show_http_queue_ai_and_resumes(app, client, csrf, fake_gemini):
    ok_before = _value("hrscreen_llm_requests_total", model="test-model", outcome="ok")
    done_before = _value("hrscreen_resumes_finished_total", status="done", reason="")
    session_id = create_session(client, csrf)
    assert upload(client, csrf, session_id, [("cv.txt", _resume())]).status_code == 201
    client.get("/")

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    text = response.get_data(as_text=True)
    assert 'hrscreen_http_requests_total{endpoint="core.index",method="GET",status="200"}' in text
    assert "hrscreen_http_request_duration_seconds_bucket" in text
    assert "hrscreen_database_up 1.0" in text
    assert 'hrscreen_queue_resumes{status="pending"} 0.0' in text
    assert "hrscreen_queue_oldest_pending_seconds 0.0" in text
    assert "hrscreen_resume_analysis_seconds_count" in text
    assert _value("hrscreen_llm_requests_total", model="test-model", outcome="ok") > ok_before
    assert _value("hrscreen_resumes_finished_total", status="done", reason="") == done_before + 1
    for leak in (NAME, EMAIL, SECRET_PHRASE, f"/sessions/{session_id}"):  # no ids, URLs or data in labels
        assert leak not in text


def test_waiting_resumes_and_their_age(app, client, csrf, fake_gemini):
    from app.services import storage

    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("cv.txt", _resume())])
    with app.app_context():
        db = storage.get_db()
        with db:
            db.execute("UPDATE resumes SET status = 'pending', created_at = '2026-01-01T00:00:00+00:00' WHERE session_id = ?",
                       (session_id,))
    text = client.get("/metrics").get_data(as_text=True)
    assert 'hrscreen_queue_resumes{status="pending"} 1.0' in text
    oldest = float(next(line.split()[1] for line in text.splitlines()
                        if line.startswith("hrscreen_queue_oldest_pending_seconds ")))
    assert oldest > 3600


def test_database_down_is_reported_not_raised():
    families = list(metrics.QueueCollector("/no/such/dir/at/all/app.db").collect())
    assert [f.name for f in families] == ["hrscreen_database_up"]
    assert families[0].samples[0].value == 0


@pytest.mark.parametrize("headers", [
    {"CF-Connecting-IP": "203.0.113.9"},  # came through the tunnel = the public domain
    {"CF-Ray": "8f00000000000000-FRA"},
    {"X-Forwarded-For": "203.0.113.9"},
])
def test_metrics_are_not_public(app, headers):
    response = app.test_client().get("/metrics", headers=headers)
    assert response.status_code == 404
    assert b"hrscreen_" not in response.data


def test_metrics_from_outside_need_the_token(app):
    outside = app.test_client()
    outside.environ_base["REMOTE_ADDR"] = "8.8.8.8"
    assert outside.get("/metrics").status_code == 404
    app.config["METRICS_TOKEN"] = "t" * 32
    assert outside.get("/metrics").status_code == 404
    assert outside.get("/metrics", headers={"Authorization": "Bearer " + "x" * 32}).status_code == 404
    assert outside.get("/metrics", headers={"Authorization": "Bearer " + "t" * 32}).status_code == 200
    behind_cf = outside.get("/metrics", headers={"Authorization": "Bearer " + "t" * 32, "CF-Ray": "x"})
    assert behind_cf.status_code == 200  # the token is enough for a hosted Prometheus


def test_private_network_scrape_is_allowed(app):
    inside = app.test_client()
    inside.environ_base["REMOTE_ADDR"] = "172.18.0.5"  # Prometheus in the compose network
    assert inside.get("/metrics").status_code == 200
    assert policy.RULES["metrics"] == policy.INTERNAL


def test_several_processes_are_added_up(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    registry = metrics.process_registry()
    assert registry is not REGISTRY
    from app import gunicorn_conf

    (tmp_path / "counter_1.db").write_bytes(b"")
    gunicorn_conf.on_starting(None)
    assert not list(tmp_path.glob("*.db"))
    gunicorn_conf.child_exit(None, type("W", (), {"pid": 12345})())


def test_multiprocess_folder_is_created_on_import(tmp_path):
    """gunicorn imports the app (and creates metrics) before its hooks run: no crash on a fresh /tmp."""
    import subprocess
    import sys

    folder = tmp_path / "not" / "yet" / "there"
    result = subprocess.run([sys.executable, "-c", "import app.metrics"], capture_output=True, text=True,
                            env={**__import__("os").environ, "PROMETHEUS_MULTIPROC_DIR": str(folder)})
    assert result.returncode == 0, result.stderr
    assert folder.is_dir()


def test_worker_serves_its_own_metrics(app, monkeypatch):
    from app import worker as worker_module

    started = []
    monkeypatch.setattr("prometheus_client.start_http_server", lambda port: started.append(port))
    monkeypatch.setattr(worker_module, "get_llm", lambda: object())
    monkeypatch.setattr(worker_module.Worker, "run", lambda self: None)
    result = app.test_cli_runner().invoke(args=["worker", "--metrics-port", "9101"])
    assert result.exit_code == 0, result.output
    assert started == [9101]


# Request ids and logs ---------------------------------------------------------------------------

def test_request_id_is_returned_and_kept_when_sane(app, client):
    first = client.get("/healthz")
    assert len(first.headers["X-Request-ID"]) == 32
    assert client.get("/healthz", headers={"X-Request-ID": "trace-abc-12345"}).headers["X-Request-ID"] == "trace-abc-12345"
    junk = client.get("/healthz", headers={"X-Request-ID": "<script>alert(1)</script>"}).headers["X-Request-ID"]
    assert "<" not in junk and len(junk) == 32


def test_scrub_removes_contacts_and_echoed_input():
    text = (f"user {EMAIL} phone {PHONE} or 87011234567; "
            "1 validation error\n  Input should be valid [type=x, input_value='Айгерим, 27 лет', input_type=str]")
    clean = observability.scrub(text)
    assert EMAIL not in clean and PHONE not in clean and "87011234567" not in clean and "Айгерим" not in clean
    assert "[email]" in clean and "[phone]" in clean and "input_value=[redacted]" in clean
    assert observability.scrub("resume 42 took 3.5 s, 2026-11-25") == "resume 42 took 3.5 s, 2026-11-25"


def _format_all(records, formatter) -> str:
    return "\n".join(formatter.format(r) for r in records)


def test_logs_of_a_whole_run_hold_no_personal_data(app, client, csrf, fake_gemini, caplog):
    caplog.set_level(logging.DEBUG)
    session_id = create_session(client, csrf)
    assert upload(client, csrf, session_id, [("cv.txt", _resume()), ("broken.pdf", b"%PDF-1.4 broken")]).status_code == 201
    client.get(f"/sessions/{session_id}")
    logging.getLogger("test").error("contact %s failed", EMAIL)  # a careless message is cleaned too
    try:
        raise ValueError(f"bad input_value='{NAME}', input_type=str from {PHONE}")
    except ValueError:
        logging.getLogger("test").exception("boom")

    for formatter in (observability.JsonFormatter(), observability.TextFormatter()):
        output = _format_all(caplog.records, formatter)
        for leak in (NAME, "Сапарова", EMAIL, PHONE, SECRET_PHRASE):
            assert leak not in output, (type(formatter).__name__, leak)
    lines = [json.loads(line) for line in _format_all(caplog.records, observability.JsonFormatter()).splitlines()
             if line.startswith("{")]
    assert lines and all({"time", "level", "logger", "request_id", "message"} <= set(line) for line in lines)
    assert any(line.get("exception") for line in lines)
    assert any(line["request_id"].startswith("resume-") or len(line["request_id"]) == 32 for line in lines)


def test_log_lines_carry_the_request_id(app, caplog):
    caplog.set_level(logging.INFO)
    rid = "abcdef-request-1"

    @app.get("/_obs_log")
    def _obs_log():
        logging.getLogger("app.test").info("inside the request")
        return "ok"

    policy.declare("_obs_log", policy.PUBLIC)
    app.test_client().get("/_obs_log", headers={"X-Request-ID": rid})
    record = next(r for r in caplog.records if r.getMessage() == "inside the request")
    assert record.request_id == rid
    assert f"[{rid}]" in observability.TextFormatter().format(record)


def test_configure_logging_switches_the_format():
    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        observability.configure_logging("json")
        assert isinstance(handler.formatter, observability.JsonFormatter)
        observability.configure_logging("text")
        assert isinstance(handler.formatter, observability.TextFormatter)
    finally:
        root.removeHandler(handler)


# Settings ---------------------------------------------------------------------------------------

def test_settings():
    settings = load_settings({"LOG_FORMAT": "json", "METRICS_TOKEN": "x" * 30, "SENTRY_DSN": "https://k@o1.ingest.sentry.io/1"})
    assert settings.to_flask()["LOG_FORMAT"] == "json"
    with pytest.raises(ConfigError) as err:
        load_settings({"LOG_FORMAT": "xml", "METRICS_TOKEN": "short", "SENTRY_DSN": "http://insecure"})
    for name in ("LOG_FORMAT", "METRICS_TOKEN", "SENTRY_DSN"):
        assert name in str(err.value)


# Sentry -----------------------------------------------------------------------------------------

class _Capture:
    events: list[dict] = []


def test_errors_reach_sentry_without_personal_data(app):
    import sentry_sdk
    from sentry_sdk.transport import Transport

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.type == "event":
                    _Capture.events.append(item.payload.json)

    _Capture.events.clear()
    app.config["SENTRY_DSN"] = "https://public@o1.ingest.sentry.io/1"
    assert observability.init_sentry(app, transport=MemoryTransport)

    @app.get("/_obs_crash")
    def _obs_crash():
        raise RuntimeError(f"cannot score {EMAIL} {PHONE}")

    policy.declare("_obs_crash", policy.PUBLIC)
    try:
        response = app.test_client().get("/_obs_crash?name=" + NAME, headers={"Cookie": "session=" + SECRET_PHRASE})
        assert response.status_code == 500
        sentry_sdk.flush()
    finally:
        sentry_sdk.init(dsn=None)  # off again for the other tests

    assert _Capture.events, "the error did not reach Sentry"
    for event in _Capture.events:  # the source lines around a frame are code (this test's own text), not data
        for exc in (event.get("exception") or {}).get("values") or []:
            for frame in (exc.get("stacktrace") or {}).get("frames") or []:
                for key in ("pre_context", "context_line", "post_context"):
                    frame.pop(key, None)
    dumped = json.dumps(_Capture.events, ensure_ascii=False)
    for leak in (EMAIL, PHONE, SECRET_PHRASE, NAME, "session=", "csrf"):
        assert leak not in dumped, leak
    event = _Capture.events[-1]
    assert event["tags"]["request_id"]
    assert "RuntimeError" in dumped


def test_sentry_is_off_without_a_dsn(app):
    assert observability.init_sentry(app) is False


def test_scrub_event_drops_request_details():
    event = {"request": {"method": "POST", "url": "https://hrscreen.date/x?email=a@b.kz", "cookies": "s=1",
                         "headers": {"Cookie": "s"}, "data": {"p": "1"}},
             "user": {"ip_address": "1.2.3.4"}, "extra": {"x": 1}, "server_name": "laptop",
             "exception": {"values": [{"value": f"bad {EMAIL}", "stacktrace": {"frames": [{"vars": {"t": NAME}}]}}]},
             "logentry": {"message": f"for {EMAIL}", "params": [PHONE]}, "message": PHONE,
             "breadcrumbs": {"values": [{"message": EMAIL}]}}
    clean = observability.scrub_event(event)
    assert clean["request"] == {"method": "POST", "url": "https://hrscreen.date/x"}
    dumped = json.dumps(clean, ensure_ascii=False)
    for leak in (EMAIL, PHONE, NAME, "1.2.3.4", "laptop", "cookies"):
        assert leak not in dumped
