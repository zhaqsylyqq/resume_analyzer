"""S3 — example session for new users and deleting a vacancy."""
import re

import pytest

from app.services import stats, storage
from app.starter import STARTER_SESSION_TITLE, STARTER_VACANCY_TITLE, create_starter_session, starter_vacancy_text

from .conftest import login, make_user
from .helpers import VACANCY_TEXT, create_session, resume_text, status, upload


def csrf_of(client, path="/"):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def sessions_of(app, username):
    with app.app_context():
        db = storage.get_db()
        uid = storage.get_user_by_name(db, username)["id"]
        return [dict(r) for r in storage.list_sessions(db, owner_id=uid)]


@pytest.fixture()
def admin(app):
    make_user(app, "root", role="admin")
    c = app.test_client()
    login(c, "root")
    return c


def test_starter_text_is_the_python_vacancy():
    text = starter_vacancy_text()
    assert "Python 3" in text and "Django" in text and len(text) > 200


def test_self_registered_user_gets_the_example_session(app, admin, anon_client):
    admin.post("/admin/settings", data={"csrf_token": csrf_of(admin), "registration_open": "1", "invite_code": "",
                                        "daily_limit": "30"})
    anon_client.post("/register", data={"csrf_token": csrf_of(anon_client, "/register"), "username": "newbie",
                                        "email": "n@example.kz", "password": "newbie-pass-1", "password2": "newbie-pass-1"})
    sessions = sessions_of(app, "newbie")
    assert [s["title"] for s in sessions] == [STARTER_SESSION_TITLE] and sessions[0]["resume_count"] == 0
    with app.app_context():
        vacancies = storage.list_vacancies(storage.get_db(), sessions[0]["id"])
    assert [v["title"] for v in vacancies] == [STARTER_VACANCY_TITLE]
    assert vacancies[0]["bias_json"]  # dictionary bias check done, no AI call
    html = anon_client.get("/sessions").get_data(as_text=True)
    assert STARTER_SESSION_TITLE in html


def test_admin_created_and_cli_users_get_it_too(app, admin):
    admin.post("/admin/users", data={"csrf_token": csrf_of(admin), "username": "dana", "email": "",
                                     "password": "dana-pass-123", "role": "recruiter"})
    assert [s["title"] for s in sessions_of(app, "dana")] == [STARTER_SESSION_TITLE]
    app.test_cli_runner().invoke(args=["create-admin", "--username", "boss", "--password", "long-password-1"])
    assert [s["title"] for s in sessions_of(app, "boss")] == [STARTER_SESSION_TITLE]


def test_example_sessions_are_private_and_not_counted_as_user_activity(app, admin):
    for name in ("aru", "bek"):
        admin.post("/admin/users", data={"csrf_token": csrf_of(admin), "username": name, "email": "",
                                         "password": "long-pass-123", "role": "recruiter"})
    aru = app.test_client()
    login(aru, "aru", "long-pass-123")
    bek_session = sessions_of(app, "bek")[0]["id"]
    assert aru.get(f"/sessions/{bek_session}").status_code == 404
    with app.app_context():
        s = stats.summary(storage.get_db(), stats.make_period(None, 5))
    assert s["sessions"] == 0 and s["active"] == 0  # only sessions people create themselves count


def test_starter_can_be_switched_off(app, admin):
    app.config["STARTER_SESSION"] = False
    admin.post("/admin/users", data={"csrf_token": csrf_of(admin), "username": "plain", "email": "",
                                     "password": "long-pass-123", "role": "recruiter"})
    assert sessions_of(app, "plain") == []
    with app.app_context():
        assert create_starter_session(storage.get_db(), 1, enabled=False) is None


def test_example_session_works_like_any_other(app, admin, fake_gemini):
    admin.post("/admin/users", data={"csrf_token": csrf_of(admin), "username": "aru", "email": "",
                                     "password": "long-pass-123", "role": "recruiter"})
    aru = app.test_client()
    login(aru, "aru", "long-pass-123")
    sid = sessions_of(app, "aru")[0]["id"]
    token = csrf_of(aru)
    upload(aru, token, sid, [("a.txt", resume_text(1))])
    assert status(aru, sid)["progress"]["done"] == 1
    assert aru.post(f"/sessions/{sid}/delete", data={"csrf_token": token}).status_code == 302
    assert sessions_of(app, "aru") == []


# Deleting a vacancy ----------------------------------------------------------------------------

def test_delete_vacancy_keeps_session_and_resumes(app, client, csrf, fake_gemini):
    sid = create_session(client, csrf, vacancies=[("Python", VACANCY_TEXT), ("Data", VACANCY_TEXT + " SQL")])
    upload(client, csrf, sid, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    with app.app_context():
        python, data = [v["id"] for v in storage.list_vacancies(storage.get_db(), sid)]
    html = client.get(f"/sessions/{sid}").get_data(as_text=True)
    assert f"/sessions/{sid}/vacancies/{python}/delete" in html and "Удалить вакансию" in html
    response = client.post(f"/sessions/{sid}/vacancies/{python}/delete", data={"csrf_token": csrf}, follow_redirects=True)
    assert "Вакансия удалена." in response.get_data(as_text=True)
    with app.app_context():
        db = storage.get_db()
        assert [v["id"] for v in storage.list_vacancies(db, sid)] == [data]
        assert db.execute("SELECT COUNT(*) FROM scores WHERE vacancy_id = ?", (python,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM scores WHERE vacancy_id = ?", (data,)).fetchone()[0] == 2
        assert "vacancy_delete" in [r["action"] for r in storage.list_audit(db)]
    assert status(client, sid)["progress"]["done"] == 2
    assert client.get(f"/sessions/{sid}/vacancies/{python}").status_code == 404


def test_deleting_the_last_vacancy_leaves_an_empty_session_you_can_refill(app, client, csrf, fake_gemini):
    sid = create_session(client, csrf)
    upload(client, csrf, sid, [("a.txt", resume_text(1))])
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), sid)[0]["id"]
    client.post(f"/sessions/{sid}/vacancies/{vid}/delete", data={"csrf_token": csrf})
    assert client.get(f"/sessions/{sid}").status_code == 200
    client.post(f"/sessions/{sid}/vacancies", data={"csrf_token": csrf, "title": "Новая", "text": VACANCY_TEXT})
    with app.app_context():
        db = storage.get_db()
        assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 1  # existing resume scored again


def test_cannot_delete_another_users_vacancy(app, client, csrf):
    sid = create_session(client, csrf)
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), sid)[0]["id"]
    make_user(app, "bob")
    bob = app.test_client()
    login(bob, "bob")
    assert bob.post(f"/sessions/{sid}/vacancies/{vid}/delete", data={"csrf_token": csrf_of(bob)}).status_code == 404
    with app.app_context():
        assert storage.get_vacancy(storage.get_db(), sid, vid) is not None


def test_scores_for_a_vacancy_deleted_mid_scoring_are_skipped(app):
    from app.services.schemas import FitAssessment

    with app.app_context():
        db = storage.get_db()
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)])
        rid = storage.add_resume(db, sid, "a.txt", "h", "t", storage.DONE)
        vid = storage.list_vacancies(db, sid)[0]["id"]
        storage.delete_vacancy(db, sid, vid)
        storage.save_scores(db, rid, [FitAssessment(vacancy_id=vid, score=80, verdict="good", summary="ok")])
        assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0
