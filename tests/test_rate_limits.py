"""APP-06/07: rate limits shared through the database, and 429 on the expensive actions."""
from __future__ import annotations

import re
import time

import pytest

from app.services import ratelimit

from .conftest import login, make_user

REAL_NOW = ratelimit.now  # taken before the autouse fixture pins the clock


@pytest.fixture(params=["memory", "database"])
def store(request, app):
    if request.param == "memory":
        return ratelimit.MemoryStore()
    return ratelimit.DatabaseStore(lambda: str(app.config["DATABASE"]))


def test_sliding_window_counts_part_of_the_previous_window(store):
    now = [1_000_000.0]  # start of a 60 s window (1_000_000 is divisible by 40, not 60: use window 40)
    limiter = ratelimit.Limiter(store, limit=3, window=40, clock=lambda: now[0])
    for _ in range(3):
        assert not limiter.blocked("k")
        limiter.hit("k")
    assert limiter.blocked("k")
    now[0] += 40  # next window starts: the 3 old events still weigh fully at its very start
    assert limiter.blocked("k")
    now[0] += 30  # 3/4 of the window passed: 3 * 0.25 = 0.75 left from the previous window
    assert not limiter.blocked("k")
    now[0] += 40  # two windows later nothing is left
    assert store.count("k", 40, now[0]) == 0


def test_lockout_and_reset(store):
    now = [5000.0]
    limiter = ratelimit.Limiter(store, limit=2, window=60, lockout=300, clock=lambda: now[0])
    assert limiter.hit("a") is False
    assert limiter.hit("a") is True
    assert limiter.blocked("a")
    assert 290 <= limiter.retry_after("a") <= 300
    assert store.count("a", 60, now[0]) == 0  # counts start again after the lock-out
    now[0] += 301
    assert not limiter.blocked("a")
    limiter.hit("a")
    limiter.reset("a")
    assert store.count("a", 60, now[0]) == 0 and store.locked_until("a") == 0


def test_database_counters_are_shared_between_processes(app):
    """Two stores = two gunicorn processes / containers: they see one counter."""
    first = ratelimit.DatabaseStore(str(app.config["DATABASE"]))
    second = ratelimit.DatabaseStore(str(app.config["DATABASE"]))
    a = ratelimit.Limiter(first, limit=4, window=60, clock=lambda: 120.0)
    b = ratelimit.Limiter(second, limit=4, window=60, clock=lambda: 120.0)
    a.hit("shared")
    b.hit("shared")
    a.hit("shared")
    assert not b.blocked("shared")
    b.hit("shared")
    assert a.blocked("shared") and b.blocked("shared")


def test_old_rows_are_cleaned_up_now_and_then(app, monkeypatch):
    store = ratelimit.DatabaseStore(str(app.config["DATABASE"]))
    store.incr("old", 60, 1000.0)
    store.lock("old", 1500.0)
    monkeypatch.setattr(ratelimit.random, "random", lambda: 0.0)
    store.incr("new", 60, 1000.0 + ratelimit.KEEP_SECONDS + 600)
    assert store.count("old", 60, 1000.0) == 0
    assert store.locked_until("old") == 0


def test_limiters_without_a_clock_use_the_real_time(monkeypatch):
    monkeypatch.setattr(ratelimit, "now", REAL_NOW)
    assert abs(ratelimit.Limiter(ratelimit.MemoryStore(), 1, 60).clock() - time.time()) < 5


def test_a_burst_across_a_window_edge_is_not_counted_low():
    """Why tests pin the clock: 10 hits straddling the edge weigh just under a limit of 10."""
    clock = [119.5]
    limiter = ratelimit.Limiter(ratelimit.MemoryStore(), 10, 60, clock=lambda: clock[0])
    for _ in range(9):
        limiter.hit("k")
    clock[0] = 120.5
    limiter.hit("k")
    assert not limiter.blocked("k")  # 1 + 9 × (1 - 0.5/60) ≈ 9.93: the approximation, accepted


def test_store_for_follows_the_setting(app):
    assert isinstance(ratelimit.store_for(app), ratelimit.DatabaseStore)
    app.config["RATE_LIMIT_STORE"] = "memory"
    assert isinstance(ratelimit.store_for(app), ratelimit.MemoryStore)


def test_login_failures_are_counted_in_the_database(app, anon_client):
    """Failed logins land in rate_counters, so every process sees the same lock-out."""
    from app.services import storage

    make_user(app, "victim")
    for _ in range(3):
        login(anon_client, "victim", "wrong-password")
    with app.app_context():
        rows = storage.get_db().execute("SELECT count(*) FROM rate_counters").fetchone()[0]
    assert rows >= 1


def _token(client, page="/"):
    html = client.get(page).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def test_ai_check_is_limited_per_user(app, client, csrf, fake_gemini):
    def check(c, token):
        return c.post("/vacancies/bias-check", json={"text": "Ищем молодого сотрудника до 30 лет"},
                      headers={"X-CSRF-Token": token, "Accept": "application/json"})

    for _ in range(10):
        assert check(client, csrf).status_code == 200
    response = check(client, csrf)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.get_json()["code"] == 429
    assert "Слишком много" in response.get_json()["message"]

    make_user(app, "other")  # another recruiter is not affected
    other = app.test_client()
    login(other, "other")
    assert check(other, _token(other)).status_code == 200


def test_reset_form_is_limited_per_address_and_only_on_post(app, anon_client):
    from app.auth import create_reset_link
    from app.services import storage

    make_user(app, "forgetful")
    with app.test_request_context():
        db = storage.get_db()
        path = create_reset_link(db, storage.get_user_by_name(db, "forgetful")).split("localhost", 1)[1]
    for _ in range(14):  # looking at the form costs nothing
        assert anon_client.get(path).status_code == 200
    token = _token(anon_client, path)
    for _ in range(10):  # too short: 400 each time, but each attempt counts
        assert anon_client.post(path, data={"csrf_token": token, "password": "x"}).status_code == 400
    response = anon_client.post(path, data={"csrf_token": token, "password": "x"})
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert "Слишком много" in response.get_data(as_text=True)


def test_account_secrets_are_limited(client, csrf):
    for _ in range(10):
        assert client.post("/account/password", data={"csrf_token": csrf, "current": "nope"}).status_code != 429
    assert client.post("/account/password", data={"csrf_token": csrf, "current": "nope"}).status_code == 429


def test_limits_can_be_switched_off(app, client, csrf, fake_gemini):
    app.config["RATE_LIMITS_ENABLED"] = False
    for _ in range(12):
        response = client.post("/vacancies/bias-check", json={"text": "Ищем сотрудника на склад"},
                               headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200
