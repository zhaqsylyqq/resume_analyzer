"""Shared HTTP helpers for route tests."""
from __future__ import annotations

import io

VACANCY_TEXT = "Требуется Python-разработчик: Django, PostgreSQL, Docker, опыт от 3 лет."


def create_session(client, csrf, vacancies=(("Python Developer", VACANCY_TEXT),), title="Осенний набор", language="ru"):
    data = {"csrf_token": csrf, "title": title, "language": language,
            "vacancy_title": [v[0] for v in vacancies], "vacancy_text": [v[1] for v in vacancies]}
    response = client.post("/sessions", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)
    return int(response.headers["Location"].rsplit("/", 1)[1])


def upload(client, csrf, session_id, files):
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    return client.post(f"/sessions/{session_id}/resumes", data=data, headers={"X-CSRF-Token": csrf},
                       content_type="multipart/form-data")


def resume_text(i: int) -> bytes:
    """Name and demographics are in the text: since SEC-02a they are extracted locally, not by the AI."""
    return (f"Айгерим Сапарова\nПол: женский. Возраст: 27\n"
            f"Кандидат {i}. Python-разработчик, Django, PostgreSQL, Docker, {i + 2} года опыта.").encode()


def status(client, session_id):
    return client.get(f"/sessions/{session_id}/status").get_json()


def clear_audit(db):
    """Empty the append-only audit log (SEC-03) for a test: lift the guard, delete, restore it."""
    from app.services import storage

    if db.is_postgres:
        with db:
            db.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change")
            db.execute("DELETE FROM audit_log")
            db.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_change")
    else:
        db.commit()
        db.raw.executescript("DROP TRIGGER audit_log_no_delete; DELETE FROM audit_log;" + storage.AUDIT_GUARD)
