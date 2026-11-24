"""SEC-01: two-factor sign-in, password change, password reset links."""
from __future__ import annotations

import json
import re
import time

import pytest

from app.services import storage, totp

from .conftest import TEST_PASSWORD, login, make_user

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # b"12345678901234567890", RFC 6238 test key


def _token(client, path="/account"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def _user(app, username):
    with app.app_context():
        return storage.get_user_by_name(storage.get_db(), username)


def _enable_2fa(app, client, username="tester"):
    csrf = _token(client)
    client.post("/account/2fa/start", data={"csrf_token": csrf})
    with client.session_transaction() as s:
        secret = s["mfa_setup_secret"]
    code = totp.code_at(secret, totp.current_step())
    page = client.post("/account/2fa/confirm", data={"csrf_token": csrf, "code": code}).get_data(as_text=True)
    codes = re.findall(r"<li>([a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4})</li>", page)
    return secret, codes


def _second_step(client, code):
    html = client.get("/login/verify").get_data(as_text=True)
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    return client.post("/login/verify", data={"csrf_token": csrf, "code": code})


# TOTP ----------------------------------------------------------------------------------------

def test_rfc6238_vector():
    # RFC 6238 appendix B, T = 59 s -> step 1, 8-digit "94287082": the last 6 digits.
    assert totp.code_at(RFC_SECRET, 1) == "287082"
    assert totp.verify(RFC_SECRET, "287 082", None, now=59) == 1


def test_drift_and_replay():
    now = 1_000_000.0
    step = totp.current_step(now)
    previous = totp.code_at(RFC_SECRET, step - 1)
    assert totp.verify(RFC_SECRET, previous, None, now=now) == step - 1
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 2), None, now=now) is None
    assert totp.verify(RFC_SECRET, previous, step - 1, now=now) is None  # already used
    assert totp.verify(RFC_SECRET, "12345", None, now=now) is None


def test_recovery_codes_are_one_time():
    codes = totp.generate_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    hashes = [totp.hash_recovery_code(c) for c in codes]
    left = totp.use_recovery_code(codes[3].upper().replace("-", " "), hashes)
    assert left is not None and len(left) == 9
    assert totp.use_recovery_code(codes[3], left) is None


def test_provisioning_uri_and_qr():
    uri = totp.provisioning_uri(RFC_SECRET, "anna")
    assert uri.startswith("otpauth://totp/Enbek%20Screen%3Aanna?secret=" + RFC_SECRET)
    assert totp.qr_svg_data_uri(uri).startswith("data:image/svg+xml")


# Enabling, signing in, disabling ---------------------------------------------------------------

def test_enable_shows_qr_and_recovery_codes(app, client):
    csrf = _token(client)
    client.post("/account/2fa/start", data={"csrf_token": csrf})
    page = client.get("/account").get_data(as_text=True)
    assert "data:image/svg+xml" in page
    bad = client.post("/account/2fa/confirm", data={"csrf_token": csrf, "code": "000000"})
    assert bad.status_code == 400
    secret, codes = _enable_2fa(app, client)
    user = _user(app, "tester")
    assert user["totp_secret"] == secret and len(codes) == 10
    assert len(json.loads(user["recovery_codes"])) == 10
    assert codes[0] not in user["recovery_codes"]  # only hashes are stored
    assert client.get("/account").status_code == 200  # this session stays signed in


def test_enabling_signs_out_other_sessions(app, client):
    other = app.test_client()
    assert login(other, "tester").status_code == 302
    _enable_2fa(app, client)
    assert other.get("/").status_code == 302  # back to the login page


def test_login_needs_the_code(app, client):
    secret, _ = _enable_2fa(app, client)
    browser = app.test_client()
    response = login(browser, "tester")
    assert response.headers["Location"].endswith("/login/verify")
    assert browser.get("/").status_code == 302  # not signed in yet
    assert _second_step(browser, "000000").status_code == 401
    step = totp.current_step() + 1  # the setup used the current step
    ok = _second_step(browser, totp.code_at(secret, step))
    assert ok.status_code == 302 and browser.get("/").status_code == 200
    # The same code cannot be used again.
    again = app.test_client()
    login(again, "tester")
    assert _second_step(again, totp.code_at(secret, step)).status_code == 401


def test_recovery_code_signs_in_once(app, client):
    _, codes = _enable_2fa(app, client)
    browser = app.test_client()
    login(browser, "tester")
    assert _second_step(browser, codes[0]).status_code == 302
    assert len(json.loads(_user(app, "tester")["recovery_codes"])) == 9
    other = app.test_client()
    login(other, "tester")
    assert _second_step(other, codes[0]).status_code == 401
    with app.app_context():
        actions = [r["detail"] for r in storage.list_audit(storage.get_db()) if r["action"] == "login"]
    assert any("recovery code" in (d or "") for d in actions)


def test_code_step_expires(app, client, monkeypatch):
    _enable_2fa(app, client)
    browser = app.test_client()
    login(browser, "tester")
    real = time.time
    monkeypatch.setattr("app.auth.time.time", lambda: real() + 301)
    response = browser.get("/login/verify")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")


def test_second_step_is_rate_limited(app, client):
    _enable_2fa(app, client)
    browser = app.test_client()
    login(browser, "tester")
    statuses = [_second_step(browser, "000000").status_code for _ in range(5)]
    assert statuses[-1] == 429


def test_disable_needs_password_and_code(app, client):
    secret, _ = _enable_2fa(app, client)
    csrf = _token(client)
    step = totp.current_step() + 1
    wrong = client.post("/account/2fa/disable", data={"csrf_token": csrf, "current": "nope", "code": "1"})
    assert wrong.status_code == 400
    ok = client.post("/account/2fa/disable",
                     data={"csrf_token": csrf, "current": TEST_PASSWORD, "code": totp.code_at(secret, step)})
    assert ok.status_code == 302
    assert _user(app, "tester")["totp_secret"] is None
    assert client.get("/account").status_code == 200


def test_new_recovery_codes(app, client):
    secret, old = _enable_2fa(app, client)
    csrf = _token(client)
    page = client.post("/account/2fa/recovery", data={"csrf_token": csrf, "current": TEST_PASSWORD,
                                                      "code": totp.code_at(secret, totp.current_step() + 1)})
    new = re.findall(r"<li>([a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4})</li>", page.get_data(as_text=True))
    assert len(new) == 10 and not set(new) & set(old)


# Password change -------------------------------------------------------------------------------

def test_change_password(app, client):
    csrf = _token(client)
    bad = client.post("/account/password", data={"csrf_token": csrf, "current": "wrong", "password": "x" * 12,
                                                 "password2": "x" * 12})
    assert bad.status_code == 400
    other = app.test_client()
    login(other, "tester")
    ok = client.post("/account/password", data={"csrf_token": csrf, "current": TEST_PASSWORD,
                                                "password": "new-password-123", "password2": "new-password-123"})
    assert ok.status_code == 302
    assert client.get("/account").status_code == 200  # still signed in here
    assert other.get("/").status_code == 302  # signed out elsewhere
    assert login(app.test_client(), "tester", "new-password-123").status_code == 302


# Password reset links --------------------------------------------------------------------------

@pytest.fixture()
def admin_client(app):
    make_user(app, "boss", role="admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _reset_link(app, admin_client, username="tester"):
    user_id = _user(app, username)["id"]
    csrf = _token(admin_client, "/admin/users")
    page = admin_client.post(f"/admin/users/{user_id}", data={"csrf_token": csrf, "action": "reset_link"})
    assert page.status_code == 200
    return re.search(r'value="(http://localhost/reset/[^"]+)"', page.get_data(as_text=True)).group(1)


def test_reset_link_sets_a_new_password_once(app, client, admin_client):
    link = _reset_link(app, admin_client)
    path = link.replace("http://localhost", "")
    browser = app.test_client()
    assert "tester" in browser.get(path).get_data(as_text=True)
    csrf = _token(browser, path)
    mismatch = browser.post(path, data={"csrf_token": csrf, "password": "a" * 12, "password2": "b" * 12})
    assert mismatch.status_code == 400
    done = browser.post(path, data={"csrf_token": csrf, "password": "reset-pass-1234", "password2": "reset-pass-1234"})
    assert done.status_code == 302
    assert login(app.test_client(), "tester", "reset-pass-1234").status_code == 302
    assert client.get("/").status_code == 302  # the old session is gone
    assert browser.get(path).status_code == 404  # used
    with app.app_context():
        assert storage.get_db().execute("SELECT COUNT(*) FROM password_resets WHERE token_hash LIKE '%' || ?",
                                        (path.rsplit("/", 1)[1],)).fetchone()[0] == 0  # token not stored


def test_reset_link_expires_and_is_replaced(app, client, admin_client):
    first = _reset_link(app, admin_client).replace("http://localhost", "")
    second = _reset_link(app, admin_client).replace("http://localhost", "")
    browser = app.test_client()
    assert browser.get(first).status_code == 404  # replaced by the newer link
    with app.app_context():
        db = storage.get_db()
        db.execute("UPDATE password_resets SET expires_at = '2000-01-01T00:00:00+00:00'")
        db.commit()
    assert browser.get(second).status_code == 404
    assert browser.get("/reset/made-up-token").status_code == 404


def test_reset_keeps_two_factor_on(app, client, admin_client):
    _enable_2fa(app, client)
    path = _reset_link(app, admin_client).replace("http://localhost", "")
    browser = app.test_client()
    csrf = _token(browser, path)
    browser.post(path, data={"csrf_token": csrf, "password": "reset-pass-1234", "password2": "reset-pass-1234"})
    response = login(app.test_client(), "tester", "reset-pass-1234")
    assert response.headers["Location"].endswith("/login/verify")


def test_admin_switches_off_two_factor(app, client, admin_client):
    _enable_2fa(app, client)
    user_id = _user(app, "tester")["id"]
    csrf = _token(admin_client, "/admin/users")
    assert "2FA" in admin_client.get("/admin/users").get_data(as_text=True)
    admin_client.post(f"/admin/users/{user_id}", data={"csrf_token": csrf, "action": "disable_2fa"})
    assert _user(app, "tester")["totp_secret"] is None
    assert login(app.test_client(), "tester").headers["Location"].endswith("/")


def test_recruiter_cannot_create_reset_links(app, client):
    make_user(app, "other")
    user_id = _user(app, "other")["id"]
    response = client.post(f"/admin/users/{user_id}", data={"csrf_token": _token(client), "action": "reset_link"})
    assert response.status_code == 404


# Admin MFA requirement and CLI -----------------------------------------------------------------

def test_admins_must_enable_two_factor_when_required(app, admin_client):
    app.config["ADMIN_MFA_REQUIRED"] = True
    response = admin_client.get("/admin/users")
    assert response.status_code == 302 and response.headers["Location"].endswith("/account")
    assert admin_client.get("/account").status_code == 200
    _enable_2fa(app, admin_client, "boss")
    assert admin_client.get("/admin/users").status_code == 200


def test_recruiters_are_not_forced(app, client):
    app.config["ADMIN_MFA_REQUIRED"] = True
    assert client.get("/").status_code == 200


def test_cli_reset_2fa_and_reset_link(app, client):
    _enable_2fa(app, client)
    runner = app.test_cli_runner()
    result = runner.invoke(args=["reset-2fa", "tester"])
    assert result.exit_code == 0 and _user(app, "tester")["totp_secret"] is None
    link = runner.invoke(args=["reset-link", "tester", "--base-url", "https://hrscreen.date"])
    assert link.exit_code == 0 and link.output.startswith("https://hrscreen.date/reset/")
    assert runner.invoke(args=["reset-2fa", "nobody"]).exit_code == 1


def test_setting_from_environment():
    from app.config import load_settings

    assert load_settings({}).admin_mfa_required is False
    assert load_settings({"ADMIN_MFA_REQUIRED": "1"}).to_flask()["ADMIN_MFA_REQUIRED"] is True
