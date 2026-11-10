"""A1 — login, roles, per-owner scoping, rate limiting, audit log and the create-admin CLI."""
import re

import pytest

from app import create_app
from app.auth import LoginLimiter, verify_password
from app.services import storage

from .conftest import TEST_PASSWORD, login, make_user
from .helpers import VACANCY_TEXT, create_session, resume_text, status, upload


def csrf_of(client, path="/"):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def audit_actions(app):
    with app.app_context():
        return [(r["action"], r["username"], r["session_id"]) for r in storage.list_audit(storage.get_db())][::-1]


# Login required ------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/sessions", "/sessions/new", "/sessions/1", "/admin/users", "/admin/audit"])
def test_pages_redirect_to_login(anon_client, path):
    response = anon_client.get(path)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")


def test_redirect_keeps_the_requested_page_and_returns_there(app, anon_client):
    make_user(app, "anna")
    response = anon_client.get("/sessions?x=1")
    assert response.headers["Location"] == "/login?next=/sessions?x%3D1"
    token = csrf_of(anon_client, "/login")
    response = anon_client.post("/login", data={"csrf_token": token, "username": "anna", "password": TEST_PASSWORD,
                                                "next": "/sessions?x=1"})
    assert response.headers["Location"] == "/sessions?x=1"


def test_login_ignores_external_next(app, anon_client):
    make_user(app, "anna")
    token = csrf_of(anon_client, "/login")
    response = anon_client.post("/login", data={"csrf_token": token, "username": "anna", "password": TEST_PASSWORD,
                                                "next": "https://evil.example"})
    assert response.headers["Location"] == "/"


def test_json_requests_get_401(anon_client):
    response = anon_client.get("/sessions/1/status", headers={"Accept": "application/json"})
    assert response.status_code == 401 and response.get_json()["error"] == "unauthorized"
    token = csrf_of(anon_client, "/login")
    response = anon_client.post("/vacancies/bias-check", json={"text": "Требуется девушка до 30 лет"},
                                headers={"X-CSRF-Token": token})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/healthz", "/login", "/lang/kk", "/static/css/app.css"])
def test_public_endpoints_need_no_login(anon_client, path):
    assert anon_client.get(path).status_code in (200, 302)
    if path == "/healthz":
        assert anon_client.get(path).get_json() == {"status": "ok"}


def test_login_page_is_localized_and_has_csrf(anon_client):
    html = anon_client.get("/login").get_data(as_text=True)
    assert 'name="csrf_token"' in html and "Вход" in html
    anon_client.get("/lang/kk")
    assert "Кіру" in anon_client.get("/login").get_data(as_text=True)


def test_login_without_csrf_is_rejected(app, anon_client):
    make_user(app, "anna")
    anon_client.get("/login")
    assert anon_client.post("/login", data={"username": "anna", "password": TEST_PASSWORD}).status_code == 400


# Credentials -------------------------------------------------------------------------------

def test_wrong_password_and_unknown_user_look_the_same(app, anon_client):
    make_user(app, "anna")
    bad_password = login(anon_client, "anna", "wrong-password!")
    unknown = login(anon_client, "nobody", "wrong-password!")
    assert bad_password.status_code == unknown.status_code == 401
    message = "Неверный логин или пароль."
    assert message in bad_password.get_data(as_text=True) and message in unknown.get_data(as_text=True)
    assert anon_client.get("/sessions").status_code == 302  # still signed out


def test_username_is_case_insensitive_and_password_is_hashed(app, anon_client):
    make_user(app, "Anna")
    assert login(anon_client, "anna").status_code == 302
    with app.app_context():
        stored = storage.get_user_by_name(storage.get_db(), "ANNA")["password_hash"]
    assert stored.startswith("$argon2id$") and TEST_PASSWORD not in stored
    assert verify_password(stored, TEST_PASSWORD) and not verify_password(stored, "nope")
    assert not verify_password("not-a-hash", TEST_PASSWORD)


def test_inactive_user_cannot_log_in(app, anon_client):
    user_id = make_user(app, "anna")
    with app.app_context():
        storage.update_user(storage.get_db(), user_id, active=0)
    assert login(anon_client, "anna").status_code == 401


def test_logout_requires_post_and_clears_the_session(client):
    assert client.get("/logout").status_code == 405
    token = csrf_of(client)
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302 and response.headers["Location"] == "/login"
    assert client.get("/sessions").status_code == 302


def test_login_replaces_the_anonymous_session(app, anon_client):
    make_user(app, "anna")
    before = csrf_of(anon_client, "/login")
    login(anon_client, "anna")
    assert csrf_of(anon_client) != before  # fresh session: no fixation of the old one


# Rate limiting ---------------------------------------------------------------------------

def test_sixth_failed_login_is_refused_even_with_the_right_password(app, anon_client):
    make_user(app, "anna")
    codes = [login(anon_client, "anna", "wrong-password!").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    response = login(anon_client, "anna")
    assert response.status_code == 429 and "15 минут" in response.get_data(as_text=True)


def test_limiter_window_and_lockout_expire():
    now = [0.0]
    limiter = LoginLimiter(clock=lambda: now[0])
    for _ in range(4):
        limiter.fail("user:a")
    now[0] += 16 * 60  # the window passed: old failures no longer count
    limiter.fail("user:a")
    assert not limiter.blocked("user:a")
    for _ in range(4):
        limiter.fail("user:a")
    assert limiter.blocked("user:a")
    now[0] += 15 * 60 + 1
    assert not limiter.blocked("user:a")


# Ownership -------------------------------------------------------------------------------

@pytest.fixture()
def two_recruiters(app, client, csrf, fake_gemini):
    """'tester' owns a session with a scored resume; 'bob' is another recruiter."""
    session_id = create_session(client, csrf, title="Сессия Тестера")
    upload(client, csrf, session_id, [("a.txt", resume_text(1))])
    make_user(app, "bob")
    bob = app.test_client()
    login(bob, "bob")
    with app.app_context():
        vacancy_id = storage.list_vacancies(storage.get_db(), session_id)[0]["id"]
    return session_id, vacancy_id, bob


def test_recruiter_cannot_read_export_or_change_another_recruiters_session(two_recruiters):
    sid, vid, bob = two_recruiters
    token = csrf_of(bob)
    gets = [f"/sessions/{sid}", f"/sessions/{sid}/status", f"/sessions/{sid}/vacancies/{vid}",
            f"/sessions/{sid}/vacancies/{vid}/export.csv", f"/sessions/{sid}/vacancies/{vid}/export.xlsx",
            f"/sessions/{sid}/vacancies/{vid}/export.pdf", f"/sessions/{sid}/export.xlsx"]
    for path in gets:
        assert bob.get(path, headers={"Accept": "application/json"}).status_code == 404, path
    posts = [(f"/sessions/{sid}/delete", {}), (f"/sessions/{sid}/analyze", {}),
             (f"/sessions/{sid}/vacancies", {"title": "X", "text": VACANCY_TEXT}),
             (f"/sessions/{sid}/vacancies/{vid}/bias", {})]
    for path, data in posts:
        assert bob.post(path, data={"csrf_token": token, **data}).status_code == 404, path
    assert upload(bob, token, sid, [("b.txt", resume_text(2))]).status_code == 404
    # Bob's lists do not mention the session; the session is intact.
    assert "Сессия Тестера" not in bob.get("/sessions").get_data(as_text=True)
    assert "Сессия Тестера" not in bob.get("/").get_data(as_text=True)


def test_owner_still_has_full_access(two_recruiters, client):
    sid, vid, _ = two_recruiters
    assert status(client, sid)["progress"]["done"] == 1
    assert client.get(f"/sessions/{sid}/vacancies/{vid}/export.xlsx").status_code == 200
    assert "Сессия Тестера" in client.get("/sessions").get_data(as_text=True)


def test_admin_sees_every_session_including_ownerless_ones(app, two_recruiters):
    sid, vid, _ = two_recruiters
    with app.app_context():
        legacy = storage.create_session(storage.get_db(), "Старая сессия", "ru", [("v", VACANCY_TEXT)])
    make_user(app, "root", role="admin")
    admin = app.test_client()
    login(admin, "root")
    html = admin.get("/sessions").get_data(as_text=True)
    assert "Сессия Тестера" in html and "Старая сессия" in html and "Владелец: tester" in html
    assert admin.get(f"/sessions/{legacy}").status_code == 200
    assert admin.get(f"/sessions/{sid}/vacancies/{vid}/export.pdf").status_code == 200
    assert admin.post(f"/sessions/{sid}/delete", data={"csrf_token": csrf_of(admin)}).status_code == 302


def test_new_sessions_belong_to_their_creator(app, client, csrf):
    sid = create_session(client, csrf)
    with app.app_context():
        db = storage.get_db()
        assert storage.get_session(db, sid)["owner_id"] == storage.get_user_by_name(db, "tester")["id"]


# Admin: users ------------------------------------------------------------------------------

@pytest.fixture()
def admin(app):
    make_user(app, "root", role="admin")
    admin_client = app.test_client()
    login(admin_client, "root")
    return admin_client


def test_recruiters_do_not_see_admin_pages(client):
    assert client.get("/admin/users").status_code == 404
    assert client.get("/admin/audit").status_code == 404
    assert client.post("/admin/users", data={"csrf_token": csrf_of(client), "username": "x" * 5,
                                             "password": "p" * 12, "role": "admin"}).status_code == 404
    assert "/admin/users" not in client.get("/").get_data(as_text=True)


def test_admin_creates_a_recruiter_who_can_log_in(app, admin, anon_client):
    token = csrf_of(admin)
    response = admin.post("/admin/users", data={"csrf_token": token, "username": "dana", "password": "dana-secret-1",
                                                "role": "recruiter"})
    assert response.status_code == 302
    assert "dana" in admin.get("/admin/users").get_data(as_text=True)
    assert login(anon_client, "dana", "dana-secret-1").status_code == 302


@pytest.mark.parametrize("username,password,role,message", [
    ("ab", "long-enough-1", "recruiter", "Логин: 3–64"),
    ("bad name", "long-enough-1", "recruiter", "Логин: 3–64"),
    ("dana", "short", "recruiter", "не короче 10"),
    ("dana", "long-enough-1", "superuser", "Выберите роль"),
    ("ROOT", "long-enough-1", "recruiter", "Такой логин уже есть"),
])
def test_admin_user_form_validation(admin, username, password, role, message):
    response = admin.post("/admin/users", data={"csrf_token": csrf_of(admin), "username": username,
                                                "password": password, "role": role})
    assert response.status_code == 400 and message in response.get_data(as_text=True)


def test_deactivation_and_password_change_sign_the_user_out(app, admin, client):
    with app.app_context():
        tester_id = storage.get_user_by_name(storage.get_db(), "tester")["id"]
    token = csrf_of(admin)
    admin.post(f"/admin/users/{tester_id}", data={"csrf_token": token, "action": "password", "password": "brand-new-pass"})
    assert client.get("/sessions").status_code == 302  # old session is no longer valid
    assert login(client, "tester", "brand-new-pass").status_code == 302
    admin.post(f"/admin/users/{tester_id}", data={"csrf_token": token, "action": "toggle"})
    assert client.get("/sessions").status_code == 302
    assert login(client, "tester", "brand-new-pass").status_code == 401


def test_admin_cannot_lock_themselves_out(app, admin):
    with app.app_context():
        root_id = storage.get_user_by_name(storage.get_db(), "root")["id"]
    token = csrf_of(admin)
    for data in ({"action": "toggle"}, {"action": "role", "role": "recruiter"}):
        response = admin.post(f"/admin/users/{root_id}", data={"csrf_token": token, **data}, follow_redirects=True)
        assert "Нельзя отключить себя" in response.get_data(as_text=True)
    with app.app_context():
        root = storage.get_user(storage.get_db(), root_id)
    assert root["active"] == 1 and root["role"] == "admin"
    # Changing their own password keeps the admin signed in.
    admin.post(f"/admin/users/{root_id}", data={"csrf_token": token, "action": "password", "password": "another-pass-1"})
    assert admin.get("/admin/users").status_code == 200


def test_admin_promotes_a_recruiter(app, admin, client):
    with app.app_context():
        tester_id = storage.get_user_by_name(storage.get_db(), "tester")["id"]
    admin.post(f"/admin/users/{tester_id}", data={"csrf_token": csrf_of(admin), "action": "role", "role": "admin"})
    assert client.get("/admin/users").status_code == 200


# Audit log ---------------------------------------------------------------------------------

def test_audit_records_open_export_and_delete(app, client, csrf, fake_gemini, admin):
    sid = create_session(client, csrf, title="Аудит")
    upload(client, csrf, sid, [("a.txt", resume_text(1))])
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), sid)[0]["id"]
    client.get(f"/sessions/{sid}")
    client.get(f"/sessions/{sid}/vacancies/{vid}?top=5")
    client.get(f"/sessions/{sid}/vacancies/{vid}/export.xlsx?top=5")
    client.post(f"/sessions/{sid}/delete", data={"csrf_token": csrf})
    actions = [a for a in audit_actions(app) if a[1] == "tester" and a[0] != "login"]
    assert actions == [("session_create", "tester", sid), ("session_open", "tester", sid),
                       ("ranking_open", "tester", sid), ("export", "tester", sid), ("session_delete", "tester", sid)]
    with app.app_context():
        detail = [r["detail"] for r in storage.list_audit(storage.get_db()) if r["action"] == "export"][0]
    assert detail == f"xlsx vacancy={vid} top=5"
    html = admin.get("/admin/audit").get_data(as_text=True)
    assert "Удаление сессии" in html and "Выгрузка" in html


def test_failed_login_is_audited_without_the_password(app, anon_client):
    login(anon_client, "ghost", "my-secret-guess")
    entry = [r for r in audit_actions(app) if r[0] == "login_failed"]
    assert entry == [("login_failed", "ghost", None)]
    with app.app_context():
        assert "my-secret-guess" not in str([dict(r) for r in storage.list_audit(storage.get_db())])


# Migration and CLI ---------------------------------------------------------------------------

def test_old_database_gets_owner_column_and_keeps_sessions(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, title TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'ru',
                               created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO sessions (title, created_at, updated_at) VALUES ('Старая', '2026-01-01', '2026-01-01');
    """)
    db.commit()
    db.close()
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(path), "RETENTION_DAYS": 0})
    with app.app_context():
        row = storage.get_session(storage.get_db(), 1)
    assert row["title"] == "Старая" and row["owner_id"] is None


def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--username", "boss"], input="long-password-1\nlong-password-1\n")
    assert result.exit_code == 0 and "Admin 'boss' created." in result.output
    with app.app_context():
        user = storage.get_user_by_name(storage.get_db(), "boss")
    assert user["role"] == "admin" and user["password_hash"].startswith("$argon2id$")
    duplicate = runner.invoke(args=["create-admin", "--username", "BOSS", "--password", "long-password-1"])
    assert duplicate.exit_code != 0 and "already exists" in duplicate.output
    short = runner.invoke(args=["create-admin", "--username", "boss2", "--password", "short"])
    assert short.exit_code != 0 and "at least 10" in short.output
