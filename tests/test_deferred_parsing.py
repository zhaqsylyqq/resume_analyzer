"""APP-05: with WORKER_MODE=external the worker, not the web process, extracts the text."""
from __future__ import annotations

import io

import pytest

from app.services import storage
from app.worker import Worker

from .fakes import make_docx, make_pdf
from .helpers import create_session, resume_text, status


@pytest.fixture()
def external(app, fake_gemini, monkeypatch):
    app.config.update(WORKER_MODE="external", SYNC_JOBS=False)

    def no_parsing_in_web(*a, **k):
        raise AssertionError("the web process must not extract text in external mode")

    monkeypatch.setattr("app.routes.screening.parse_file", no_parsing_in_web)
    return app


def _upload(client, csrf, session_id, files):
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    return client.post(f"/sessions/{session_id}/resumes", data=data, headers={"X-CSRF-Token": csrf},
                       content_type="multipart/form-data")


def _drain(app, service):
    worker = Worker(app, service, threads=1)
    with app.app_context():
        db = storage.connect(app.config["DATABASE"])
        try:
            while worker.work_once(db):
                pass
        finally:
            db.close()


def _rows(app, session_id):
    with app.app_context():
        db = storage.get_db()
        return {r["filename"]: storage.get_resume(db, r["id"]) for r in storage.list_resumes(db, session_id)}


def test_upload_only_stores_the_file_then_the_worker_parses(external, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    pdf = make_pdf(["Aigerim Saparova", "Python developer, Django, PostgreSQL, Docker, 5 years of experience."])
    docx = make_docx(["Ержан Ахметов", "Python-разработчик: Django, PostgreSQL, Docker, 4 года."])
    response = _upload(client, csrf, session_id, [("a.pdf", pdf), ("b.docx", docx), ("c.txt", resume_text(3))])
    assert response.status_code == 201 and len(response.get_json()["accepted"]) == 3
    rows = _rows(external, session_id)
    assert all(r["status"] == storage.PENDING and r["text"] == "" for r in rows.values())
    assert storage.raw_file(rows["a.pdf"]) == pdf
    assert fake_gemini.client.models.calls == []

    _drain(external, fake_gemini)
    rows = _rows(external, session_id)
    assert all(r["status"] == storage.DONE for r in rows.values())
    assert all(storage.raw_file(r) is None for r in rows.values())  # the original document is not kept
    assert "Django" in rows["a.pdf"]["text"] and "Ахметов" in rows["b.docx"]["text"]


def test_cheap_checks_still_answer_at_once(external, client, csrf):
    session_id = create_session(client, csrf)
    big = b"x" * (external.config["MAX_FILE_BYTES"] + 10)
    body = _upload(client, csrf, session_id, [("photo.png", b"\x89PNG"), ("big.txt", big), ("empty.txt", b"   ")]).get_json()
    assert sorted(r["error"] for r in body["rejected"]) == ["empty", "too_large", "unsupported_type"]
    assert body["accepted"] == []


def test_a_broken_file_fails_in_the_worker_without_calling_the_ai(external, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    assert _upload(client, csrf, session_id, [("broken.pdf", b"%PDF-1.4 not really a pdf")]).status_code == 201
    _drain(external, fake_gemini)
    row = _rows(external, session_id)["broken.pdf"]
    assert row["status"] == storage.PARSE_FAILED and row["error"] and storage.raw_file(row) is None
    assert fake_gemini.client.models.calls == []
    progress = status(client, session_id)["progress"]
    assert progress["parse_failed"] == 1 and progress["pending"] == 0


def test_duplicates_are_caught_before_parsing(external, client, csrf):
    session_id = create_session(client, csrf)
    _upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    body = _upload(client, csrf, session_id, [("again.txt", resume_text(1))]).get_json()
    assert body["rejected"][0]["error"] == "duplicate"


def test_inline_mode_keeps_parsing_in_the_web_process(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    _upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    row = _rows(app, session_id)["a.txt"]
    assert row["text"] and storage.raw_file(row) is None
