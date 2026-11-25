"""SEC-03: append-only audit log, structured log lines, new events, admin page with filters."""
from __future__ import annotations

import json
import re

import pytest

from app.services import migrations, storage

from .conftest import ON_POSTGRES, TEST_DATABASE_URL, TEST_PASSWORD, login, make_user
from .helpers import clear_audit, create_session
from .test_mfa import _enable_2fa, _reset_link, _second_step, _token, _user, admin_client  # noqa: F401 - fixture

postgres_only = pytest.mark.skipif(not ON_POSTGRES, reason="needs TEST_DATABASE_URL (PostgreSQL)")


def _actions(app, action=None):
    with app.app_context():
        rows = storage.list_audit(storage.get_db(), 1000)
    return [r for r in rows if action is None or r["action"] == action]


# The log cannot be changed --------------------------------------------------------------------

@pytest.mark.parametrize("statement", ["DELETE FROM audit_log", "UPDATE audit_log SET detail = 'hidden'",
                                       "UPDATE audit_log SET username = NULL WHERE id = 1"])
def test_audit_rows_cannot_be_changed_or_deleted(app, statement):
    with app.app_context():
        db = storage.get_db()
        storage.audit(db, "login", username="someone", detail="1.2.3.4")
        with pytest.raises(Exception, match="append-only"):
            db.execute(statement)
        db.rollback()
        assert [r["detail"] for r in storage.list_audit(db)] == ["1.2.3.4"]
        storage.audit(db, "logout", username="someone")  # adding still works
        assert len(storage.list_audit(db)) == 2


@postgres_only
def test_truncate_is_refused_even_with_cascade(app):
    with app.app_context():
        db = storage.get_db()
        storage.audit(db, "login", username="someone")
        for statement in ("TRUNCATE audit_log", "TRUNCATE users CASCADE"):
            with pytest.raises(Exception, match="append-only"):
                db.execute(statement)
            db.rollback()
        assert len(storage.list_audit(db)) == 1


def test_sqlite_guard_is_in_place_after_restart(tmp_path):
    from app import create_app

    path = str(tmp_path / "app.db")
    for _ in range(2):  # the guard is created once and kept (CREATE TRIGGER IF NOT EXISTS)
        app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": path, "GEMINI_API_KEY": "k"})
    with app.app_context():
        db = storage.get_db()
        names = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert {"audit_log_no_update", "audit_log_no_delete"} <= names


# Structured log lines ---------------------------------------------------------------------------

def test_every_event_is_also_a_json_log_line(app, caplog):
    caplog.set_level("INFO", logger="hrscreen.audit")
    with app.app_context():
        db = storage.get_db()
        storage.audit(db, "session_create", username="aru", session_id=7, detail="Осенний набор")
        row = storage.list_audit(db)[0]
    lines = [r.getMessage() for r in caplog.records if r.name == "hrscreen.audit"]
    assert len(lines) == 1 and lines[0].startswith("audit {")
    event = json.loads(lines[0][len("audit "):])
    assert event == {"event": "audit", "id": row["id"], "created_at": row["created_at"], "user_id": None,
                     "username": "aru", "action": "session_create", "session_id": 7, "detail": "Осенний набор"}


# New events -------------------------------------------------------------------------------------

def test_recruiter_on_an_admin_page_is_logged_once(app, client):
    assert client.get("/admin/audit").status_code == 404
    assert client.get("/admin/audit").status_code == 404  # the same within 10 minutes: one record
    denied = _actions(app, "access_denied")
    assert len(denied) == 1 and denied[0]["username"] == "tester" and "GET /admin/audit" in denied[0]["detail"]


def test_someone_elses_session_is_logged(app, client, csrf):
    session_id = create_session(client, csrf)
    make_user(app, "other")
    other = app.test_client()
    login(other, "other")
    assert other.get(f"/sessions/{session_id}").status_code == 404
    denied = _actions(app, "access_denied")
    assert len(denied) == 1 and denied[0]["username"] == "other" and denied[0]["session_id"] == session_id
    assert other.get("/sessions/99999").status_code == 404  # no such session: nothing to deny
    assert len(_actions(app, "access_denied")) == 1


def test_wrong_current_password_and_wrong_2fa_codes(app, client):
    csrf = _token(client)
    page = client.post("/account/password", data={"csrf_token": csrf, "current": "wrong-password",
                                                   "password": "new-pass-12345", "password2": "new-pass-12345"})
    assert page.status_code == 400
    assert [r["detail"] for r in _actions(app, "password_change_failed")] == ["wrong current password"]

    client.post("/account/2fa/start", data={"csrf_token": csrf})
    assert client.post("/account/2fa/confirm", data={"csrf_token": csrf, "code": "000000"}).status_code == 400
    _enable_2fa(app, client)
    csrf = _token(client)
    client.post("/account/2fa/disable", data={"csrf_token": csrf, "current": "wrong", "code": "000000"})
    client.post("/account/2fa/recovery", data={"csrf_token": csrf, "current": TEST_PASSWORD, "code": "000000"})
    assert sorted(r["detail"] for r in _actions(app, "mfa_failed")) == [
        "disable: wrong password", "enable: wrong code", "recovery codes: wrong code"]


def test_recovery_code_sign_in_has_its_own_event(app, client):
    _, codes = _enable_2fa(app, client)
    browser = app.test_client()
    login(browser, "tester")
    assert _second_step(browser, codes[0]).status_code == 302
    used = _actions(app, "mfa_recovery_used")
    assert len(used) == 1 and used[0]["detail"].endswith("9 code(s) left")


def test_reset_link_events(app, client, admin_client):  # noqa: F811 - fixture
    link = _reset_link(app, admin_client).replace("http://localhost", "")
    created = _actions(app, "password_reset_link")
    assert len(created) == 1 and created[0]["username"] == "boss" and created[0]["detail"] == "tester: link created"

    browser = app.test_client()
    assert browser.get("/reset/not-a-real-token").status_code == 404
    assert browser.get("/reset/another-fake-token").status_code == 404  # same address, 10 minutes: once
    failed = _actions(app, "password_reset_failed")
    assert len(failed) == 1 and failed[0]["detail"].endswith("unknown or expired link")

    csrf = _token(browser, link)
    data = {"csrf_token": csrf, "password": "reset-pass-1234", "password2": "reset-pass-1234"}
    assert browser.post(link, data=data).status_code == 302
    assert len(_actions(app, "password_reset")) == 1


def test_admin_switching_off_2fa_is_mfa_reset(app, client, admin_client):  # noqa: F811 - fixture
    _enable_2fa(app, client)
    user_id = _user(app, "tester")["id"]
    admin_client.post(f"/admin/users/{user_id}", data={"csrf_token": _token(admin_client, "/admin/users"),
                                                       "action": "disable_2fa"})
    reset = _actions(app, "mfa_reset")
    assert len(reset) == 1 and reset[0]["detail"] == "tester: switched off by an admin"
    assert _user(app, "tester")["totp_secret"] is None


def test_cli_events(app):
    make_user(app, "anna")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["reset-link", "anna"]).exit_code == 0
    assert [r["detail"] for r in _actions(app, "password_reset_link")] == ["anna: link created"]


def test_every_action_has_a_label_in_both_languages(app):
    from app.auth import AUDIT_DANGER, _audit_actions
    from app.i18n import TRANSLATIONS

    actions = _audit_actions()
    for name in ("access_denied", "mfa_failed", "mfa_recovery_used", "mfa_reset", "password_change_failed",
                 "password_reset_link", "password_reset_failed", "login", "session_purge"):
        assert name in actions
    assert AUDIT_DANGER <= set(actions)
    assert all(f"audit.{a}" in TRANSLATIONS["kk"] for a in actions)


# The admin page: pages and filters -------------------------------------------------------------

@pytest.fixture()
def admin(app):
    make_user(app, "root", role="admin")
    c = app.test_client()
    login(c, "root")
    with app.app_context():
        clear_audit(storage.get_db())
    return c


def _add(app, when, action, username, detail=None):
    with app.app_context():
        db = storage.get_db()
        with db:
            db.execute("INSERT INTO audit_log (created_at, username, action, detail) VALUES (?, ?, ?, ?)",
                       (when, username, action, detail))


def _rows(page):
    return re.findall(r"<tr>\s*<td class=\"small\"><time datetime=\"([^\"]+)\"", page)


def test_pages_of_fifty(app, admin):
    for i in range(60):
        _add(app, f"2026-11-20T10:{i:02d}:00+00:00", "login", "aru", f"n{i}")
    first = admin.get("/admin/audit").get_data(as_text=True)
    assert len(_rows(first)) == 50 and "n59" in first and "n10" in first and "n9<" not in first
    assert 'rel="next" href="/admin/audit?page=2"' in first and 'rel="prev"' not in first
    second = admin.get("/admin/audit?page=2").get_data(as_text=True)
    assert len(_rows(second)) == 10 and "n0<" in second and 'rel="prev"' in second and 'rel="next"' not in second
    assert "nothing" not in second
    empty = admin.get("/admin/audit?page=9").get_data(as_text=True)
    assert len(_rows(empty)) == 0 and "Нет событий по этим условиям" in empty


def test_filters(app, admin):
    _add(app, "2026-11-08T18:00:00+00:00", "login", "Aru")           # 8 Nov, 23:00 local (UTC+5)
    _add(app, "2026-11-08T21:00:00+00:00", "login_failed", "bek")    # 9 Nov, 02:00 local
    _add(app, "2026-11-09T12:00:00+00:00", "login", "aru")           # 9 Nov, 17:00 local
    _add(app, "2026-11-10T03:00:00+00:00", "export", "dina")         # 10 Nov, 08:00 local

    def times(query):
        return _rows(admin.get("/admin/audit?" + query).get_data(as_text=True))

    assert len(times("")) == 4
    assert times("action=login") == ["2026-11-09T12:00:00+00:00", "2026-11-08T18:00:00+00:00"]
    assert times("user=ARU") == ["2026-11-09T12:00:00+00:00", "2026-11-08T18:00:00+00:00"]  # any letter case
    assert times("from=2026-11-09&to=2026-11-09") == ["2026-11-09T12:00:00+00:00", "2026-11-08T21:00:00+00:00"]
    assert times("from=2026-11-10") == ["2026-11-10T03:00:00+00:00"]
    assert times("to=2026-11-08") == ["2026-11-08T18:00:00+00:00"]
    assert times("action=login&user=aru&from=2026-11-09") == ["2026-11-09T12:00:00+00:00"]
    page = admin.get("/admin/audit?action=login&user=aru").get_data(as_text=True)
    assert 'value="aru"' in page and '<option value="login" selected>' in page and "Сбросить" in page


def test_bad_filter_values_are_ignored(app, admin):
    _add(app, "2026-11-09T12:00:00+00:00", "login", "aru")
    for query in ("action=drop%20table", "from=yesterday&to=31.12.2026", "page=abc", "page=-5", "page=99999999"):
        response = admin.get("/admin/audit?" + query)
        assert response.status_code == 200, query
    assert len(_rows(admin.get("/admin/audit?action=nope&from=x").get_data(as_text=True))) == 1


def test_filters_pass_through_pages(app, admin):
    for i in range(55):
        _add(app, f"2026-11-20T10:{i:02d}:00+00:00", "export", "aru")
    _add(app, "2026-11-20T11:00:00+00:00", "login", "aru")
    page = admin.get("/admin/audit?action=export&user=aru").get_data(as_text=True)
    assert "page=2" in page and "action=export" in page and "user=aru" in page
    assert len(_rows(admin.get("/admin/audit?action=export&user=aru&page=2").get_data(as_text=True))) == 5


# Least privileges for the app's database role (PostgreSQL) ----------------------------------------

def test_db_app_role_is_postgresql_only(app):
    result = app.test_cli_runner().invoke(args=["db-app-role", "hrscreen"])
    if ON_POSTGRES:
        assert result.exit_code != 0  # the test user is the owner or a superuser: refused, see below
    else:
        assert result.exit_code != 0 and "PostgreSQL only" in result.output


@postgres_only
def test_restrict_refuses_unknown_owner_and_superuser_roles(app):
    with pytest.raises(migrations.RoleError, match="does not exist"):
        migrations.restrict_app_role(TEST_DATABASE_URL, "no_such_role_sec03")
    import psycopg

    with psycopg.connect(TEST_DATABASE_URL) as conn:
        me, superuser = conn.execute("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    with pytest.raises(migrations.RoleError, match="superuser" if superuser else "owns the tables"):
        migrations.restrict_app_role(TEST_DATABASE_URL, me)


APP_ROLE = "hrscreen_sec03_app"
APP_ROLE_PASSWORD = "sec03-test-only"  # noqa: S105 - a throwaway role of the test database


def _drop_app_role(conn):
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone():
        conn.execute(f"DROP OWNED BY {APP_ROLE}")  # its privileges in this database
        conn.execute(f"DROP ROLE {APP_ROLE}")


@pytest.fixture()
def app_role_url():
    """A throwaway login role, as the app would use in production (not the owner)."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        can = conn.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user").fetchone()[0]
        if not can:
            pytest.skip("the test database user may not create roles")
        _drop_app_role(conn)
        conn.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}'")
    params = conninfo_to_dict(TEST_DATABASE_URL)
    yield f"postgresql://{APP_ROLE}:{APP_ROLE_PASSWORD}@{params.get('host', '127.0.0.1')}:{params.get('port', 5432)}/{params['dbname']}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        _drop_app_role(conn)


@postgres_only
def test_app_role_may_add_but_never_change_the_log(app, app_role_url):
    import psycopg

    statements = migrations.restrict_app_role(TEST_DATABASE_URL, APP_ROLE)
    assert any("REVOKE UPDATE, DELETE, TRUNCATE" in s and "audit_log" in s for s in statements)
    with psycopg.connect(app_role_url) as conn:
        conn.execute("INSERT INTO audit_log (created_at, action) VALUES ('2026-11-24T00:00:00+00:00', 'login')")
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('k', 'v', 'now')")
        conn.execute("DELETE FROM settings WHERE key = 'k'")  # ordinary tables: full rights
        conn.commit()
        for statement, error in (("DELETE FROM audit_log", "permission denied"),
                                 ("UPDATE audit_log SET detail = 'x'", "permission denied"),
                                 ("TRUNCATE audit_log", "permission denied"),
                                 ("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_change", "must be owner"),
                                 ("DROP TABLE audit_log", "must be owner"),
                                 ("UPDATE alembic_version SET version_num = 'x'", "permission denied")):
            with pytest.raises(psycopg.Error, match=error):
                conn.execute(statement)
            conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


@postgres_only
def test_the_app_runs_as_the_limited_role(app_role_url):
    from app import create_app

    migrations.restrict_app_role(TEST_DATABASE_URL, APP_ROLE)
    limited = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": app_role_url, "GEMINI_API_KEY": "k"})
    make_user(limited, "limited")  # start-up (migrations at head) and normal writes work
    client = limited.test_client()
    assert login(client, "limited").status_code == 302
    assert _actions(limited, "login")[0]["username"] == "limited"
    result = limited.test_cli_runner().invoke(args=["db-app-role", APP_ROLE, "--owner-url", TEST_DATABASE_URL])
    assert result.exit_code == 0 and "OK: hrscreen_sec03_app" in result.output
