import pytest
from flask import Blueprint


@pytest.fixture()
def app_with_post(app):
    bp = Blueprint("probe", __name__)

    @bp.post("/_probe")
    def probe():
        return {"ok": True}

    app.register_blueprint(bp)
    return app


def test_security_headers_present(client):
    headers = client.get("/").headers
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "same-origin"


def test_post_without_csrf_token_is_rejected(app_with_post):
    client = app_with_post.test_client()
    client.get("/")
    assert client.post("/_probe").status_code == 400


def test_post_with_wrong_csrf_token_is_rejected(app_with_post):
    client = app_with_post.test_client()
    client.get("/")
    assert client.post("/_probe", headers={"X-CSRF-Token": "nope"}).status_code == 400


def test_post_with_csrf_header_or_form_field_is_accepted(app_with_post, client):
    html = client.get("/").get_data(as_text=True)
    token = html.split('name="csrf-token" content="')[1].split('"')[0]
    assert client.post("/_probe", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.post("/_probe", data={"csrf_token": token}).status_code == 200


def test_session_cookie_is_httponly_and_samesite(client):
    client.get("/")
    cookie = client.get_cookie("session")
    assert cookie is not None
    assert cookie.http_only
    assert cookie.same_site == "Lax"
