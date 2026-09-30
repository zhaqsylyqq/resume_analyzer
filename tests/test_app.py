from app.i18n import TRANSLATIONS
from app.security import is_safe_redirect


def test_home_page_renders_in_russian_by_default(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<html lang="ru">' in html
    assert TRANSLATIONS["ru"]["home.title"] in html


def test_language_switch_sets_cookie_and_renders_kazakh(client):
    response = client.get("/lang/kk?next=/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    html = client.get("/").get_data(as_text=True)
    assert '<html lang="kk">' in html
    assert TRANSLATIONS["kk"]["home.title"] in html


def test_language_switch_ignores_unknown_language_and_open_redirect(client):
    response = client.get("/lang/xx?next=https://evil.example")
    assert response.headers["Location"] == "/"
    assert "lang=" not in response.headers.get("Set-Cookie", "")


def test_translations_have_identical_keys():
    assert set(TRANSLATIONS["ru"]) == set(TRANSLATIONS["kk"])


def test_healthz(client):
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_unknown_page_returns_localized_404(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert TRANSLATIONS["ru"]["error.404"] in response.get_data(as_text=True)


def test_json_clients_get_json_errors(client):
    response = client.get("/no-such-page", headers={"Accept": "application/json"})
    assert response.status_code == 404
    assert response.get_json()["code"] == 404
    assert response.get_json()["message"] == TRANSLATIONS["ru"]["error.404"]


def test_wildcard_accept_gets_html_error_page(client):
    response = client.get("/no-such-page", headers={"Accept": "*/*"})
    assert response.mimetype == "text/html"


def test_is_safe_redirect():
    assert is_safe_redirect("/sessions/1?top=5")
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)"):
        assert not is_safe_redirect(bad)
