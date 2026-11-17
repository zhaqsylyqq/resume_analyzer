"""QW-02 — daily quota of AI vacancy checks per recruiter; the dictionary check always works."""
import re

import pytest

from app import settings as app_settings
from app.services import storage

from .conftest import login, make_user
from .helpers import create_session
from .test_bias import BIASED, ai_service


def csrf_of(client, path="/"):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


@pytest.fixture()
def admin(app):
    make_user(app, "root", role="admin")
    c = app.test_client()
    login(c, "root")
    return c


def set_ai_limit(app, value):
    with app.app_context():
        db = storage.get_db()
        current = app_settings.load(db, app.config)
        app_settings.save(db, app_settings.AppSettings(current.registration_open, current.require_code,
                                                        current.invite_code, current.daily_limit, value))


def counting_service(app):
    service = ai_service({"findings": [], "rewrite": ""})
    app.extensions["gemini"] = service
    return service.backend.client.models.calls


def check(client, csrf):
    return client.post("/vacancies/bias-check", json={"text": BIASED, "language": "ru"}, headers={"X-CSRF-Token": csrf})


def test_json_check_stops_calling_ai_after_the_limit(app, client, csrf):
    set_ai_limit(app, 2)
    calls = counting_service(app)
    reports = [check(client, csrf).get_json() for _ in range(3)]
    assert [r["ai_checked"] for r in reports] == [True, True, False]
    assert len(calls) == 2
    assert reports[2]["ai_limited"] is True and "Лимит ИИ-проверок" in reports[2]["message"]
    assert reports[2]["findings"], "the dictionary check still runs"


def test_recheck_on_vacancy_page_respects_the_limit(app, client, csrf):
    set_ai_limit(app, 1)
    calls = counting_service(app)
    session_id = create_session(client, csrf)
    with app.app_context():
        vid = storage.list_vacancies(storage.get_db(), session_id)[0]["id"]
    url = f"/sessions/{session_id}/vacancies/{vid}/bias"
    client.post(url, data={"csrf_token": csrf})
    page = client.post(url, data={"csrf_token": csrf}, follow_redirects=True).get_data(as_text=True)
    assert len(calls) == 1
    assert "Лимит ИИ-проверок" in page


def test_zero_means_unlimited_and_admins_are_not_limited(app, admin, client, csrf):
    set_ai_limit(app, 0)
    calls = counting_service(app)
    for _ in range(3):
        assert check(client, csrf).get_json()["ai_checked"] is True
    set_ai_limit(app, 1)
    admin_csrf = csrf_of(admin)
    for _ in range(3):
        assert check(admin, admin_csrf).get_json()["ai_checked"] is True
    assert len(calls) == 6


def test_failed_ai_call_does_not_use_the_quota(app, client, csrf):
    set_ai_limit(app, 1)
    app.extensions.pop("gemini", None)
    app.config["GEMINI_API_KEY"] = ""  # AI not configured: dictionary only, nothing counted
    assert check(client, csrf).get_json()["ai_checked"] is False
    calls = counting_service(app)
    assert check(client, csrf).get_json()["ai_checked"] is True
    assert len(calls) == 1


def test_quota_is_counted_per_recruiter(app, client, csrf):
    set_ai_limit(app, 1)
    calls = counting_service(app)
    check(client, csrf)
    make_user(app, "second")
    other = app.test_client()
    login(other, "second")
    assert check(other, csrf_of(other)).get_json()["ai_checked"] is True
    assert len(calls) == 2


def test_admin_saves_the_ai_limit(app, admin):
    response = admin.post("/admin/settings", data={"csrf_token": csrf_of(admin), "invite_code": "",
                                                   "daily_limit": "30", "ai_check_limit": "7"})
    assert response.status_code == 302
    with app.app_context():
        assert app_settings.load(storage.get_db(), app.config).ai_check_limit == 7
    assert 'name="ai_check_limit"' in admin.get("/admin/users").get_data(as_text=True)


def test_invalid_ai_limit_is_rejected(admin):
    response = admin.post("/admin/settings", data={"csrf_token": csrf_of(admin), "invite_code": "",
                                                   "daily_limit": "30", "ai_check_limit": "-5"})
    assert response.status_code == 400


def test_default_comes_from_environment(app):
    app.config["DAILY_AI_CHECK_LIMIT"] = 4
    with app.app_context():
        assert app_settings.load(storage.get_db(), app.config).ai_check_limit == 4
