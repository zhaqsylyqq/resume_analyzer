"""S1 — sign-up with an invite code and the usage statistics page."""
import io
import re
from datetime import datetime, timezone

import pytest
from openpyxl import load_workbook

from app.services import stats, storage

from .conftest import login, make_user
from .helpers import VACANCY_TEXT, create_session, resume_text, upload


def csrf_of(client, path):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def register(client, code, username="newbie", password="newbie-pass-1", password2=None, email="newbie@example.kz"):
    return client.post("/register", data={"csrf_token": csrf_of(client, "/register"), "code": code,
                                          "username": username, "email": email, "password": password,
                                          "password2": password if password2 is None else password2})


@pytest.fixture()
def invite(app):
    app.config["INVITE_CODE"] = "ITPM2026"


# Sign-up --------------------------------------------------------------------------------

def test_register_is_hidden_without_invite_code(app, anon_client):
    assert anon_client.get("/register").status_code == 404
    assert "/register" not in anon_client.get("/login").get_data(as_text=True)


def test_register_with_the_right_code_creates_a_signed_in_recruiter(app, anon_client, invite):
    assert "/register" in anon_client.get("/login").get_data(as_text=True)
    response = register(anon_client, "ITPM2026")
    assert response.status_code == 302 and response.headers["Location"] == "/"
    assert anon_client.get("/sessions").status_code == 200  # signed in
    with app.app_context():
        user = storage.get_user_by_name(storage.get_db(), "newbie")
        actions = [r["action"] for r in storage.list_audit(storage.get_db())]
    assert user["role"] == "recruiter" and user["active"] == 1
    assert "register" in actions and "login" in actions


@pytest.mark.parametrize("kwargs,message", [
    ({"code": "wrong"}, "Неверный код приглашения."),
    ({"code": "ITPM2026", "username": "x"}, "Логин: 3–64"),
    ({"code": "ITPM2026", "password": "short"}, "не короче 10"),
    ({"code": "ITPM2026", "password2": "something-else"}, "Пароли не совпадают."),
    ({"code": "ITPM2026", "username": "TESTER"}, "Такой логин уже есть"),
])
def test_register_validation(app, anon_client, invite, kwargs, message):
    make_user(app, "tester")
    response = register(anon_client, **kwargs)
    assert response.status_code == 400 and message in response.get_data(as_text=True)
    with app.app_context():
        assert storage.get_user_by_name(storage.get_db(), "newbie") is None


def test_guessing_the_code_is_rate_limited(app, anon_client, invite):
    codes = [register(anon_client, f"guess{i}", username=f"user{i}").status_code for i in range(5)]
    assert codes == [400, 400, 400, 400, 429]
    assert register(anon_client, "ITPM2026").status_code == 429  # even the right code, for 15 minutes


def test_register_cannot_create_an_admin(app, anon_client, invite):
    anon_client.post("/register", data={"csrf_token": csrf_of(anon_client, "/register"), "code": "ITPM2026",
                                        "username": "sneaky", "email": "s@example.kz", "password": "sneaky-pass-1",
                                        "password2": "sneaky-pass-1",
                                        "role": "admin"})
    with app.app_context():
        assert storage.get_user_by_name(storage.get_db(), "sneaky")["role"] == "recruiter"


# Statistics ----------------------------------------------------------------------------

def add_audit(db, when, action, user_id=None, username=None, session_id=None):
    db.execute("INSERT INTO audit_log (created_at, user_id, username, action, session_id) VALUES (?, ?, ?, ?, ?)",
               (when, user_id, username, action, session_id))


@pytest.fixture()
def history(app):
    """Three recruiters over three days, plus one who registered but never came back."""
    ids = {name: make_user(app, name) for name in ("aru", "bek", "dina", "erlan")}
    with app.app_context():
        db = storage.get_db()
        db.execute("DELETE FROM audit_log")
        for name, rows in {
            "aru": [("2026-11-01T04:00:00+00:00", "login"), ("2026-11-01T04:05:00+00:00", "session_create"),
                    ("2026-11-01T04:30:00+00:00", "export"), ("2026-11-09T10:00:00+00:00", "login")],
            "bek": [("2026-11-08T21:00:00+00:00", "login"),  # 02:00 on 9 Nov in Kazakhstan (UTC+5)
                    ("2026-11-08T21:10:00+00:00", "session_create")],
            "dina": [("2026-11-09T09:00:00+00:00", "login"), ("2026-11-09T09:01:00+00:00", "login")],
        }.items():
            for when, action in rows:
                add_audit(db, when, action, ids[name], name)
        sid = storage.create_session(db, "s", "ru", [("v", VACANCY_TEXT)], owner_id=ids["aru"])
        for i in range(3):
            rid = storage.add_resume(db, sid, f"{i}.txt", f"h{i}", "t", storage.DONE)
            db.execute("UPDATE resumes SET analyzed_at = '2026-11-01T04:10:00+00:00' WHERE id = ?", (rid,))
        db.commit()
    return ids


NOW = datetime(2026, 11, 10, 6, 0, 0, tzinfo=timezone.utc)  # 11:00 on 10 Nov in Kazakhstan


def test_summary_all_time(app, history):
    with app.app_context():
        s = stats.summary(storage.get_db(), stats.make_period(None, 5, NOW))
    assert s == {"users_total": 4, "users_new": 4, "signed_in": 3, "active": 2, "sessions": 2,
                 "resumes": 3, "exports": 1, "logins": 5}


def test_last_7_days_and_local_day_boundaries(app, history):
    with app.app_context():
        db = storage.get_db()
        period = stats.make_period(7, 5, NOW)  # 4 Nov 00:00 local .. now
        s = stats.summary(db, period)
        days = {d["day"]: d for d in stats.daily(db, period)}
    assert (s["signed_in"], s["active"], s["sessions"], s["exports"], s["resumes"]) == (3, 1, 1, 0, 0)
    assert len(days) == 7 and "2026-11-04" in days and "2026-11-10" in days
    # bek signed in at 21:00 UTC on 8 Nov = 9 Nov in Kazakhstan, together with aru and dina.
    assert days["2026-11-09"]["users"] == 3 and days["2026-11-09"]["logins"] == 4
    assert days["2026-11-09"]["sessions"] == 1 and days["2026-11-08"]["users"] == 0


def test_per_user_table(app, history):
    with app.app_context():
        rows = {u["username"]: u for u in stats.per_user(storage.get_db(), stats.make_period(None, 5, NOW))}
    assert (rows["aru"]["logins"], rows["aru"]["sessions"], rows["aru"]["exports"], rows["aru"]["resumes"]) == (2, 1, 1, 3)
    assert rows["aru"]["first_login"].startswith("2026-11-01") and rows["aru"]["last_login"].startswith("2026-11-09")
    assert rows["erlan"]["logins"] == 0 and rows["erlan"]["first_login"] is None


def test_stats_page_and_excel_for_admin(app, history):
    make_user(app, "root", role="admin")
    admin = app.test_client()
    login(admin, "root")
    html = admin.get("/admin/stats").get_data(as_text=True)
    assert "Статистика использования" in html and "aru" in html and "<svg class=\"usage-chart\"" in html
    assert "Активные (создали сессию)" in html
    response = admin.get("/admin/stats.xlsx")
    assert response.status_code == 200 and response.mimetype.endswith("spreadsheetml.sheet")
    wb = load_workbook(io.BytesIO(response.data))
    assert wb.sheetnames == ["Сводка", "По дням", "Пользователи", "Журнал"]
    summary = {row[0]: row[1] for row in wb["Сводка"].iter_rows(min_row=2, values_only=True)}
    assert summary["Входили в систему"] == 4  # aru, bek, dina + root signing in for this test
    assert summary["Зарегистрировано"] == 5
    users = [row[0] for row in wb["Пользователи"].iter_rows(min_row=2, values_only=True)]
    assert set(users) == {"aru", "bek", "dina", "erlan", "root"}
    assert wb["Журнал"].max_row > 5
    # Downloading the report is audited but is not counted as a shortlist export.
    with app.app_context():
        actions = [r["action"] for r in storage.list_audit(storage.get_db())]
    assert actions[0] == "stats_export"
    assert admin.get("/admin/stats?days=7").status_code == 200
    assert admin.get("/admin/stats?days=abc").status_code == 200  # falls back to all time
    assert "Статистика" in admin.get("/").get_data(as_text=True)  # nav link


def test_stats_are_admin_only(client):
    assert client.get("/admin/stats").status_code == 404
    assert client.get("/admin/stats.xlsx").status_code == 404


def test_stats_page_with_an_empty_database(app):
    make_user(app, "root", role="admin")
    admin = app.test_client()
    login(admin, "root")
    with app.app_context():
        storage.get_db().execute("DELETE FROM audit_log")
        storage.get_db().commit()
    assert admin.get("/admin/stats?days=30").status_code == 200


def test_real_flow_is_counted(app, client, csrf, fake_gemini):
    sid = create_session(client, csrf)
    upload(client, csrf, sid, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), sid)[0]["id"]
    client.get(f"/sessions/{sid}/vacancies/{vid}/export.pdf")
    with app.app_context():
        s = stats.summary(storage.get_db(), stats.make_period(None, 5))
    assert (s["signed_in"], s["active"], s["sessions"], s["resumes"], s["exports"]) == (1, 1, 1, 2, 1)
