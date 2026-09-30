import re

from app import create_app
from app.services import storage
from app.services.schemas import CandidateProfile

from .helpers import VACANCY_TEXT, create_session, resume_text, status, upload


def calls_by_kind(service):
    kinds = {"extract": 0, "score": 0, "score_ids": []}
    for call in service.client.models.calls:
        if call["config"].response_schema is CandidateProfile:
            kinds["extract"] += 1
        else:
            kinds["score"] += 1
            kinds["score_ids"].append(re.search(r"Vacancy ids to assess: ([\d, ]+)", call["contents"]).group(1))
    return kinds


def test_appending_resumes_analyzes_only_new_ones(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    with app.app_context():
        first_scores = storage.get_db().execute("SELECT resume_id, created_at FROM scores ORDER BY resume_id").fetchall()
    fake_gemini.client.models.calls.clear()

    body = upload(client, csrf, session_id, [("c.txt", resume_text(3)), ("a-copy.txt", resume_text(1))]).get_json()
    assert len(body["accepted"]) == 1 and body["rejected"][0]["error"] == "duplicate"
    assert calls_by_kind(fake_gemini) | {"score_ids": None} == {"extract": 1, "score": 1, "score_ids": None}
    assert status(client, session_id)["progress"]["done"] == 3
    with app.app_context():
        again = storage.get_db().execute(
            "SELECT resume_id, created_at FROM scores WHERE resume_id IN (?, ?) ORDER BY resume_id",
            (first_scores[0][0], first_scores[1][0]),
        ).fetchall()
    assert [tuple(r) for r in again] == [tuple(r) for r in first_scores]


def test_adding_a_vacancy_scores_only_the_missing_pairs(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [(f"{i}.txt", resume_text(i)) for i in range(3)])
    fake_gemini.client.models.calls.clear()

    response = client.post(f"/sessions/{session_id}/vacancies",
                           data={"csrf_token": csrf, "title": "Data Engineer", "text": VACANCY_TEXT + " Airflow, Spark."})
    assert response.status_code == 302
    calls = calls_by_kind(fake_gemini)
    with app.app_context():
        new_id = storage.list_vacancies(storage.get_db(), session_id)[-1]["id"]
        assert storage.get_db().execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 6
    assert calls["extract"] == 0
    assert calls["score_ids"] == [str(new_id)] * 3


def test_adding_an_invalid_vacancy_shows_error(app, client, csrf):
    session_id = create_session(client, csrf)
    response = client.post(f"/sessions/{session_id}/vacancies", data={"csrf_token": csrf, "title": "X", "text": "short"},
                           follow_redirects=True)
    assert "от 30 до 20 000 символов" in response.get_data(as_text=True)
    with app.app_context():
        assert len(storage.list_vacancies(storage.get_db(), session_id)) == 1


def test_vacancy_limit(app, client, csrf):
    session_id = create_session(client, csrf, vacancies=[(f"V{i}", VACANCY_TEXT) for i in range(10)])
    response = client.post(f"/sessions/{session_id}/vacancies",
                           data={"csrf_token": csrf, "title": "V11", "text": VACANCY_TEXT}, follow_redirects=True)
    assert "Не больше 10 вакансий" in response.get_data(as_text=True)


def test_restart_recovers_interrupted_work(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    with app.app_context():
        db = storage.get_db()
        db.execute("DELETE FROM scores")
        db.execute("UPDATE resumes SET status = 'processing'")
        db.commit()

    restarted = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": app.config["DATABASE"], "SYNC_JOBS": True})
    restarted.extensions["gemini"] = fake_gemini
    client2 = restarted.test_client()
    html = client2.get(f"/sessions/{session_id}").get_data(as_text=True)
    assert "Продолжить анализ" in html
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    assert client2.post(f"/sessions/{session_id}/analyze", headers={"X-CSRF-Token": token}).get_json()["started"]
    assert status(client2, session_id)["progress"]["done"] == 1


def test_history_lists_sessions_with_counts(client, csrf, fake_gemini):
    first = create_session(client, csrf, title="Первая")
    create_session(client, csrf, title="Вторая")
    upload(client, csrf, first, [("a.txt", resume_text(1))])
    html = client.get("/sessions").get_data(as_text=True)
    assert "Первая" in html and "Вторая" in html
    assert "Вакансий: 1 · Резюме: 1" in html
    assert "Первая" in client.get("/").get_data(as_text=True)


def test_reopened_session_shows_stored_resumes(client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    html = client.get(f"/sessions/{session_id}").get_data(as_text=True)
    assert "Айгерим Сапарова" in html and "Python-разработчик" in html


def test_delete_session_cascades(app, client, csrf, fake_gemini):
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    response = client.post(f"/sessions/{session_id}/delete", data={"csrf_token": csrf})
    assert response.status_code == 302
    assert client.get(f"/sessions/{session_id}").status_code == 404
    with app.app_context():
        db = storage.get_db()
        for table in ("vacancies", "resumes", "scores", "demographics"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_unknown_session_is_404(client):
    assert client.get("/sessions/12345").status_code == 404
    assert client.get("/sessions/12345/status", headers={"Accept": "application/json"}).status_code == 404


def test_claim_pending_is_exclusive(app):
    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)])
        for i in range(3):
            storage.add_resume(db, sid, f"{i}.txt", f"h{i}", "text", storage.PENDING)
        assert len(storage.claim_pending(db, sid)) == 3
        assert storage.claim_pending(db, sid) == []
        assert storage.progress(db, sid)["processing"] == 3
