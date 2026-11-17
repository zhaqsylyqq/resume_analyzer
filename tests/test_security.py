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


# QW-04: HTTPS scheme behind a trusted proxy ---------------------------------------------------

def test_hsts_sent_when_trusted_proxy_reports_https(client):
    headers = client.get("/", headers={"X-Forwarded-Proto": "https"}).headers
    assert headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_hsts_from_cloudflare_visitor_header(client):
    """waitress strips X-Forwarded-Proto; cloudflared also sends CF-Visitor."""
    headers = client.get("/", headers={"CF-Visitor": '{"scheme":"https"}'}).headers
    assert "Strict-Transport-Security" in headers
    assert "Strict-Transport-Security" not in client.get("/", headers={"CF-Visitor": "not json"}).headers
    untrusted = client.get("/", headers={"CF-Visitor": '{"scheme":"https"}'}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert "Strict-Transport-Security" not in untrusted.headers


def test_no_hsts_over_plain_http(client):
    assert "Strict-Transport-Security" not in client.get("/").headers


def test_forwarded_proto_from_untrusted_peer_is_ignored(client):
    response = client.get("/", headers={"X-Forwarded-Proto": "https"}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert "Strict-Transport-Security" not in response.headers


def test_request_scheme_and_external_urls_follow_the_trusted_proxy(app):
    from flask import request, url_for

    bp = Blueprint("scheme_probe", __name__)

    @bp.get("/_scheme")
    def scheme():
        return {"scheme": request.scheme, "url": url_for("auth.login", _external=True)}

    app.register_blueprint(bp)
    app.config["TRUSTED_PROXIES"] = ("127.0.0.1",)
    import app.auth as auth
    from app.auth import PUBLIC_ENDPOINTS  # the probe is outside login: call it as a public endpoint
    auth.PUBLIC_ENDPOINTS = PUBLIC_ENDPOINTS | {"scheme_probe.scheme"}
    try:
        c = app.test_client()
        behind = c.get("/_scheme", headers={"X-Forwarded-Proto": "https", "Host": "hrscreen.date"}).get_json()
        direct = c.get("/_scheme", headers={"X-Forwarded-Proto": "https"},
                       environ_base={"REMOTE_ADDR": "203.0.113.9"}).get_json()
    finally:
        auth.PUBLIC_ENDPOINTS = PUBLIC_ENDPOINTS
    assert behind == {"scheme": "https", "url": "https://hrscreen.date/login"}
    assert direct["scheme"] == "http"


def test_trusted_proxy_matching():
    from app.security import is_trusted_proxy

    assert is_trusted_proxy("127.0.0.1", ("127.0.0.1", "::1"))
    assert is_trusted_proxy("172.18.0.5", ("172.16.0.0/12",))
    assert not is_trusted_proxy("203.0.113.9", ("127.0.0.1", "172.16.0.0/12"))
    assert not is_trusted_proxy("not-an-ip", ("127.0.0.1",))
    assert not is_trusted_proxy(None, ("127.0.0.1",))
