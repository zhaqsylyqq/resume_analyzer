"""S2 — admin registration switches, email on sign-up, sign-ups per IP, daily resume limit."""
import re

import pytest

from app import settings as app_settings
from app.auth import SignupLimiter
from app.services import stats, storage

from .conftest import login, make_user
from .helpers import create_session, resume_text, upload


def csrf_of(client, path="/"):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


@pytest.fixture()
def admin(app):
    make_user(app, "root", role="admin")
    c = app.test_client()
    login(c, "root")
    return c


def save(admin, **fields):
    data = {"csrf_token": csrf_of(admin), "invite_code": "", "daily_limit": "30", **fields}
    return admin.post("/admin/settings", data={k: v for k, v in data.items() if v is not None})


def signup(client, username="newbie", email="newbie@example.kz", code=None, password="newbie-pass-1"):
    data = {"csrf_token": csrf_of(client, "/register"), "username": username, "email": email,
            "password": password, "password2": password}
    if code is not None:
        data["code"] = code
    return client.post("/register", data=data)


# Admin switches ----------------------------------------------------------------------------

def test_registration_is_closed_by_default(anon_client, admin):
    assert anon_client.get("/register").status_code == 404
    assert "Закрыта" in admin.get("/admin/users").get_data(as_text=True)


def test_open_registration_without_code(app, admin, anon_client):
    assert save(admin, registration_open="1").status_code == 302
    html = anon_client.get("/register").get_data(as_text=True)
    assert 'name="code"' not in html and 'name="email"' in html
    assert "/register" in anon_client.get("/login").get_data(as_text=True)
    response = signup(anon_client)
    assert response.status_code == 302 and anon_client.get("/sessions").status_code == 200
    with app.app_context():
        user = storage.get_user_by_name(storage.get_db(), "newbie")
    assert user["email"] == "newbie@example.kz" and user["role"] == "recruiter"


def test_code_checkbox_requires_the_code(admin, anon_client):
    save(admin, registration_open="1", require_code="1", invite_code="ITPM2026")
    assert 'name="code"' in anon_client.get("/register").get_data(as_text=True)
    assert signup(anon_client, code="nope").status_code == 400
    assert signup(anon_client, code="ITPM2026").status_code == 302


def test_closing_registration_takes_effect_at_once(admin, anon_client):
    save(admin, registration_open="1")
    assert anon_client.get("/register").status_code == 200
    save(admin)  # checkbox unticked
    assert anon_client.get("/register").status_code == 404


@pytest.mark.parametrize("fields,message", [
    ({"registration_open": "1", "require_code": "1", "invite_code": ""}, "Укажите код или снимите галочку"),
    ({"daily_limit": "-1"}, "Введите число от 0 до 10 000."),
    ({"daily_limit": "abc"}, "Введите число от 0 до 10 000."),
])
def test_settings_validation(admin, fields, message):
    response = save(admin, **fields)
    assert response.status_code == 400 and message in response.get_data(as_text=True)


def test_settings_are_admin_only_and_audited(app, admin, client):
    assert client.post("/admin/settings", data={"csrf_token": csrf_of(client), "registration_open": "1",
                                                "daily_limit": "5"}).status_code == 404
    save(admin, registration_open="1", daily_limit="12")
    with app.app_context():
        entry = storage.list_audit(storage.get_db())[0]
        current = app_settings.load(storage.get_db(), app.config)
    assert entry["action"] == "settings_update" and "registration=open" in entry["detail"] and "12" in entry["detail"]
    assert current.registration_open and not current.require_code and current.daily_limit == 12


def test_env_invite_code_is_the_default_until_saved(app, anon_client):
    app.config["INVITE_CODE"] = "FROM-ENV"
    assert 'name="code"' in anon_client.get("/register").get_data(as_text=True)
    assert signup(anon_client, code="FROM-ENV").status_code == 302


# Email and sign-ups per IP -------------------------------------------------------------------

@pytest.mark.parametrize("email,message", [("", "Укажите почту."), ("not-an-email", "Проверьте адрес почты."),
                                           ("a@b", "Проверьте адрес почты.")])
def test_signup_requires_a_valid_email(admin, anon_client, email, message):
    save(admin, registration_open="1")
    response = signup(anon_client, email=email)
    assert response.status_code == 400 and message in response.get_data(as_text=True)


def test_email_is_lower_cased_and_shown_to_admins(app, admin, anon_client):
    save(admin, registration_open="1")
    signup(anon_client, email="Aru.K@Example.KZ")
    assert "aru.k@example.kz" in admin.get("/admin/users").get_data(as_text=True)
    assert "aru.k@example.kz" in admin.get("/admin/stats").get_data(as_text=True)


def test_admin_can_add_a_user_with_or_without_email(app, admin):
    token = csrf_of(admin)
    admin.post("/admin/users", data={"csrf_token": token, "username": "withmail", "email": "w@example.kz",
                                     "password": "long-pass-123", "role": "recruiter"})
    admin.post("/admin/users", data={"csrf_token": token, "username": "nomail", "email": "",
                                     "password": "long-pass-123", "role": "recruiter"})
    bad = admin.post("/admin/users", data={"csrf_token": token, "username": "badmail", "email": "x@",
                                           "password": "long-pass-123", "role": "recruiter"})
    assert bad.status_code == 400
    with app.app_context():
        db = storage.get_db()
        assert storage.get_user_by_name(db, "withmail")["email"] == "w@example.kz"
        assert storage.get_user_by_name(db, "nomail")["email"] is None


def test_sixth_signup_from_one_ip_within_an_hour_is_refused(admin, app):
    save(admin, registration_open="1")
    for i in range(5):
        assert signup(app.test_client(), username=f"user{i}", email=f"u{i}@example.kz").status_code == 302
    response = signup(app.test_client(), username="user5", email="u5@example.kz")
    assert response.status_code == 429 and "Попробуйте через час" in response.get_data(as_text=True)


def test_signup_limiter_window():
    now = [0.0]
    limiter = SignupLimiter(clock=lambda: now[0])
    for _ in range(5):
        limiter.record("1.2.3.4")
    assert limiter.blocked("1.2.3.4") and not limiter.blocked("5.6.7.8")
    now[0] += 3601
    assert not limiter.blocked("1.2.3.4")


# Daily resume limit ---------------------------------------------------------------------------

def test_recruiter_daily_limit_accepts_up_to_the_limit(app, admin, client, csrf, fake_gemini):
    save(admin, daily_limit="3")
    sid = create_session(client, csrf)
    response = upload(client, csrf, sid, [(f"cv{i}.txt", resume_text(i)) for i in range(5)])
    data = response.get_json()
    assert len(data["accepted"]) == 3
    assert [r["error"] for r in data["rejected"]] == ["daily_limit", "daily_limit"]
    assert "3 резюме за 24 часа" in data["rejected"][0]["message"]
    assert data["remaining_label"] == "Можно загрузить ещё 0 из 3 резюме за 24 часа."
    # A second session does not reset the counter.
    other = create_session(client, csrf, title="Другая")
    assert upload(client, csrf, other, [("x.txt", resume_text(9))]).get_json()["accepted"] == []
    assert "Можно загрузить ещё 0 из 3" in client.get(f"/sessions/{sid}").get_data(as_text=True)


def test_parse_failures_and_duplicates_do_not_use_the_limit(app, admin, client, csrf, fake_gemini):
    save(admin, daily_limit="2")
    sid = create_session(client, csrf)
    upload(client, csrf, sid, [("empty.txt", b" "), ("a.txt", resume_text(1))])
    upload(client, csrf, sid, [("a.txt", resume_text(1))])  # duplicate
    assert len(upload(client, csrf, sid, [("b.txt", resume_text(2))]).get_json()["accepted"]) == 1


def test_admins_and_zero_limit_are_unlimited(app, admin, client, csrf, fake_gemini):
    save(admin, daily_limit="1")
    token = csrf_of(admin)
    sid = create_session(admin, token)
    assert len(upload(admin, token, sid, [(f"cv{i}.txt", resume_text(i)) for i in range(4)]).get_json()["accepted"]) == 4
    save(admin, daily_limit="0")
    own = create_session(client, csrf)
    data = upload(client, csrf, own, [(f"cv{i}.txt", resume_text(i)) for i in range(4)]).get_json()
    assert len(data["accepted"]) == 4 and data["remaining_label"] is None


def test_stats_excel_has_the_email_column(app, admin, anon_client):
    import io

    from openpyxl import load_workbook

    save(admin, registration_open="1")
    signup(anon_client, email="mail@example.kz")
    wb = load_workbook(io.BytesIO(admin.get("/admin/stats.xlsx").data))
    rows = list(wb["Пользователи"].iter_rows(values_only=True))
    assert rows[0][1] == "Почта" and ("newbie", "mail@example.kz") in [(r[0], r[1]) for r in rows[1:]]


def test_old_users_table_gets_email_column(tmp_path):
    import sqlite3

    from app import create_app

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
        last_login_at TEXT);
        INSERT INTO users (username, password_hash, role, created_at) VALUES ('old', 'x', 'admin', '2026-11-01');""")
    db.commit()
    db.close()
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(path), "RETENTION_DAYS": 0})
    with app.app_context():
        assert storage.get_user_by_name(storage.get_db(), "old")["email"] is None
