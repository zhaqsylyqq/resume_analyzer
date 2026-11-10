"""SIS 10-11 fixes: real client IP behind Cloudflare Tunnel, one account per email,
and no audit-log flood from ranking page refreshes."""
import pytest

from app.auth import MAX_SIGNUPS_PER_IP
from app.services import storage

from .conftest import login, make_user
from .helpers import create_session
from .test_registration_settings import csrf_of, save


@pytest.fixture()
def open_signup(app):
    make_user(app, "root", role="admin")
    admin = app.test_client()
    login(admin, "root")
    save(admin, registration_open="1")
    return admin


def signup(app, n, email=None, headers=None, environ=None):
    client = app.test_client()
    data = {"csrf_token": csrf_of(client, "/register"), "username": f"student{n}", "email": email or f"s{n}@example.kz",
            "password": "classmate-2026", "password2": "classmate-2026"}
    return client.post("/register", data=data, headers=headers or {}, environ_base=environ or {})


# Fix 1: client IP -----------------------------------------------------------------------------

def test_classmates_behind_the_tunnel_are_not_one_ip(app, open_signup):
    """Before the fix every visitor was 127.0.0.1 and the 6th sign-up of the hour failed."""
    codes = [signup(app, i, headers={"CF-Connecting-IP": f"5.34.1.{i}"}).status_code for i in range(10)]
    assert codes == [302] * 10


def test_one_real_ip_is_still_limited(app, open_signup):
    codes = [signup(app, i, headers={"CF-Connecting-IP": "5.34.1.1"}).status_code for i in range(MAX_SIGNUPS_PER_IP + 1)]
    assert codes[:-1] == [302] * MAX_SIGNUPS_PER_IP and codes[-1] == 429


def test_header_from_untrusted_peer_is_ignored(app, open_signup):
    """A visitor reaching the server directly cannot fake the header to dodge the limit."""
    direct = {"REMOTE_ADDR": "203.0.113.7"}
    codes = [signup(app, i, headers={"CF-Connecting-IP": f"9.9.9.{i}"}, environ=direct).status_code
             for i in range(MAX_SIGNUPS_PER_IP + 1)]
    assert codes[-1] == 429


def test_forwarded_for_last_hop_and_garbage_header(app, open_signup):
    assert signup(app, 1, headers={"X-Forwarded-For": "1.1.1.1, 5.34.2.2"}).status_code == 302
    assert signup(app, 2, headers={"CF-Connecting-IP": "not-an-ip"}).status_code == 302
    with app.app_context():
        details = [r["detail"] for r in storage.list_audit(storage.get_db()) if r["action"] == "register"]
    assert set(details) >= {"5.34.2.2", "127.0.0.1"}


def test_login_lockout_is_per_visitor(app):
    make_user(app, "aru")
    bad = app.test_client()
    for _ in range(5):
        r = bad.post("/login", data={"csrf_token": csrf_of(bad, "/login"), "username": "x", "password": "wrong-pass-1"},
                     headers={"CF-Connecting-IP": "6.6.6.6"})
    assert r.status_code == 429
    good = app.test_client()
    r = good.post("/login", data={"csrf_token": csrf_of(good, "/login"), "username": "aru", "password": "correct-horse-battery"},
                  headers={"CF-Connecting-IP": "7.7.7.7"})
    assert r.status_code == 302


# Fix 2: one account per email ---------------------------------------------------------------------

def test_duplicate_email_rejected_case_insensitive(app, open_signup):
    assert signup(app, 1, email="same@example.kz", headers={"CF-Connecting-IP": "5.1.1.1"}).status_code == 302
    response = signup(app, 2, email="Same@Example.KZ", headers={"CF-Connecting-IP": "5.1.1.2"})
    assert response.status_code == 400
    assert "Эта почта уже используется" in response.get_data(as_text=True)
    with app.app_context():
        assert storage.get_user_by_name(storage.get_db(), "student2") is None


def test_admin_cannot_create_second_account_with_same_email(app, open_signup):
    signup(app, 1, email="taken@example.kz")
    response = open_signup.post("/admin/users", data={"csrf_token": csrf_of(open_signup, "/admin/users"),
                                                       "username": "twin", "email": "taken@example.kz",
                                                       "password": "classmate-2026", "role": "recruiter"})
    assert response.status_code == 400 and "Эта почта уже используется" in response.get_data(as_text=True)


def test_admin_may_create_users_without_email(app, open_signup):
    for name in ("nomail1", "nomail2"):
        r = open_signup.post("/admin/users", data={"csrf_token": csrf_of(open_signup, "/admin/users"), "username": name,
                                                   "email": "", "password": "classmate-2026", "role": "recruiter"})
        assert r.status_code == 302


# Fix 3: ranking views logged once per 10 minutes -----------------------------------------------

def test_ranking_refresh_does_not_flood_audit(app, client, fake_gemini, csrf):
    sid = create_session(client, csrf)
    vacancy = f"/sessions/{sid}/vacancies/1"
    for top in ("5", "10", "all", "5"):
        assert client.get(f"{vacancy}?top={top}").status_code == 200
    with app.app_context():
        rows = [r for r in storage.list_audit(storage.get_db()) if r["action"] == "ranking_open"]
    assert len(rows) == 1


def test_trusted_proxy_networks(app, open_signup):
    """docker-compose: cloudflared comes from the Docker network, trusted as a CIDR range."""
    app.config["TRUSTED_PROXIES"] = ("127.0.0.1", "172.16.0.0/12", "not-a-network")
    docker = {"REMOTE_ADDR": "172.18.0.3"}
    codes = [signup(app, i, headers={"CF-Connecting-IP": f"5.34.3.{i}"}, environ=docker).status_code for i in range(7)]
    assert codes == [302] * 7
