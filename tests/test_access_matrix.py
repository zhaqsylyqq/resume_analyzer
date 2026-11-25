"""APP-10: every registered route × every kind of visitor, against the table in app/policy.py.

Visitors: anonymous, the recruiter who owns the screening session in the URL, another
recruiter, an admin. Each check runs on fresh data (a route may delete what it is given).
The table must cover every route exactly: a route without a rule fails here (and is closed,
404 for everyone, at run time)."""
from __future__ import annotations

import re

import pytest
from flask import Blueprint

from app import policy
from app.services import storage
from app.services.schemas import FitAssessment

from .conftest import login, make_user

VISITORS = ("anonymous", "owner", "other", "admin")
TEXT = "Python-разработчик: Django, PostgreSQL, Docker, REST API, 3+ года опыта."


def _rules(app):
    """(endpoint, method, rule) for every route, HEAD/OPTIONS aside."""
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.endpoint, r.rule)):
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield rule.endpoint, method, rule


def test_every_route_has_exactly_one_rule(app):
    endpoints = {endpoint for endpoint, _, _ in _rules(app)}
    assert endpoints - set(policy.RULES) == set(), "routes without an access rule in app/policy.py"
    assert set(policy.RULES) - endpoints == set(), "rules for routes that do not exist"
    assert set(policy.RULES.values()) <= set(policy.LEVELS)


def test_the_public_routes_are_exactly_these():
    """Opening a route to everyone is the riskiest change: it has to be made here as well."""
    assert {e for e, r in policy.RULES.items() if r == policy.PUBLIC} == {
        "static", "core.health", "core.ready", "core.set_language",
        "auth.login", "auth.login_verify", "auth.register", "auth.reset_password",
    }
    assert policy.PUBLIC_ENDPOINTS == {e for e, r in policy.RULES.items() if r == policy.PUBLIC}


def test_a_route_without_a_rule_is_closed(app, caplog):
    bp = Blueprint("forgotten", __name__)

    @bp.get("/_forgotten")
    def page():
        return "secret"

    app.register_blueprint(bp)
    make_user(app, "root", role="admin")
    admin = app.test_client()
    login(admin, "root")
    assert admin.get("/_forgotten").status_code == 404  # even for an admin
    assert app.test_client().get("/_forgotten").status_code == 404
    assert "route forgotten.page has no access rule" in caplog.text
    assert {"forgotten.page"} == {e for e, _, _ in _rules(app)} - set(policy.RULES)  # what the test above catches


def test_unknown_rules_are_refused():
    with pytest.raises(ValueError, match="unknown access rule"):
        policy.declare("x.y", "everyone")


@pytest.fixture()
def visitors(app):
    return {name: make_user(app, name, role=role)
            for name, role in (("anna", "recruiter"), ("boris", "recruiter"), ("root", "admin"))}


NAMES = {"owner": "anna", "other": "boris", "admin": "root"}


def _client(app, visitor):
    """A fresh browser for each check: a route may sign its visitor out or clear the session."""
    c = app.test_client()
    if visitor != "anonymous":
        assert login(c, NAMES[visitor]).status_code == 302
    html = c.get("/login" if visitor == "anonymous" else "/account").get_data(as_text=True)
    return c, re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def _world(app, ids) -> dict[str, object]:
    """A fresh session of anna's with a vacancy and an analysed resume."""
    with app.app_context():
        db = storage.get_db()
        session_id = storage.create_session(db, "Матрица", "ru", [("Python", TEXT)], ids["anna"])
        vacancy_id = storage.list_vacancies(db, session_id)[0]["id"]
        resume_id = storage.add_resume(db, session_id, "cv.txt", f"sha-{session_id}", TEXT, storage.DONE)
        storage.save_scores(db, resume_id, [FitAssessment(vacancy_id=vacancy_id, score=80, verdict="strong",
                                                           summary="Подходит")])
    return {"session_id": session_id, "vacancy_id": vacancy_id, "resume_id": resume_id,
            "user_id": ids["boris"], "token": "not-a-real-token", "code": "ru", "filename": "css/app.css"}


def _url(rule, values) -> str:
    return re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", lambda m: str(values[m.group(1)]), rule.rule)


def _asks_to_sign_in(response) -> bool:
    """The policy's answer to an anonymous visitor (a script: the requests carry X-CSRF-Token)."""
    return response.status_code == 401 and response.is_json and response.get_json().get("error") == "unauthorized"


def _denied_logged(app, username) -> int:
    with app.app_context():
        return sum(1 for r in storage.list_audit(storage.get_db(), 10_000)
                   if r["action"] == "access_denied" and r["username"] == username)


def test_every_route_for_every_visitor(app, visitors, fake_gemini):
    ids = visitors
    checked = 0
    for endpoint, method, rule in _rules(app):
        expected = policy.RULES[endpoint]
        for visitor in VISITORS:
            values = _world(app, ids)
            url = _url(rule, values)
            denied_before = _denied_logged(app, "boris")
            browser, token = _client(app, visitor)
            response = browser.open(url, method=method, headers={"X-CSRF-Token": token},
                                    data={} if method == "POST" else None)
            where = f"{method} {url} ({endpoint}, {expected}) as {visitor}: {response.status_code}"
            checked += 1
            if expected == policy.PUBLIC:
                assert not _asks_to_sign_in(response), where
                continue
            if expected == policy.INTERNAL:  # OBS-01: the test client is local and not behind Cloudflare
                assert response.status_code == 200, where
                outside = browser.open(url, method=method, headers={"CF-Connecting-IP": "203.0.113.9"})
                assert outside.status_code == 404, where
                continue
            if visitor == "anonymous":
                assert _asks_to_sign_in(response), where
                continue
            refused = (expected == policy.ADMIN and visitor != "admin") or (expected == policy.OWNER and visitor == "other")
            if refused:
                assert response.status_code == 404, where
                if visitor == "other":  # SEC-03: a refusal of a signed-in user is on record
                    assert _denied_logged(app, "boris") == denied_before + 1, where
            else:
                assert response.status_code not in (401, 404), where
    assert checked >= 4 * 40  # all 41 routes (some with GET and POST) × 4 visitors
