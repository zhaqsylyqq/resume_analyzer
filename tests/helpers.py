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
    return f"Кандидат {i}. Python-разработчик, Django, PostgreSQL, Docker, {i + 2} года опыта.".encode()


def status(client, session_id):
    return client.get(f"/sessions/{session_id}/status").get_json()
