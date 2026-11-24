"""APP-01: typed, validated configuration."""
from __future__ import annotations

import pytest

from app.check_config import main
from app.config import ConfigError, load_secret_key, load_settings

PROD = {"APP_ENV": "production", "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sk-test",
        "FLASK_SECRET_KEY": "x" * 64}


def test_defaults_match_previous_behaviour():
    s = load_settings({})
    flask = s.to_flask()
    assert s.app_env == "development"
    assert flask["LLM_PROVIDER"] == "gemini"
    assert flask["GEMINI_CONCURRENCY"] == 2
    assert flask["RETENTION_DAYS"] == 90
    assert flask["DAILY_RESUME_LIMIT"] == 30
    assert flask["DAILY_AI_CHECK_LIMIT"] == 20
    assert flask["STARTER_SESSION"] is True
    assert flask["TRUSTED_PROXIES"] == ("127.0.0.1", "::1")
    assert flask["SESSION_COOKIE_SECURE"] is False
    assert flask["DEEPSEEK_MODEL"] == "deepseek-flash"


def test_every_problem_is_reported_at_once():
    with pytest.raises(ConfigError) as err:
        load_settings({"RETENTION_DAYS": "ninety", "GEMINI_CONCURRENCY": "0", "LLM_PROVIDER": "openai",
                       "STARTER_SESSION": "maybe", "TRUSTED_PROXIES": "127.0.0.1,not-an-ip",
                       "APP_ENV": "prod"})
    text = "\n".join(err.value.problems)
    for name in ("RETENTION_DAYS", "GEMINI_CONCURRENCY", "LLM_PROVIDER", "STARTER_SESSION",
                 "TRUSTED_PROXIES", "APP_ENV"):
        assert name in text
    assert len(err.value.problems) == 6


@pytest.mark.parametrize("raw, expected", [("1", True), ("true", True), ("ON", True), ("0", False), ("no", False)])
def test_booleans(raw, expected):
    assert load_settings({"STARTER_SESSION": raw}).starter_session is expected


def test_networks_accept_cidr():
    s = load_settings({"TRUSTED_PROXIES": "127.0.0.1, ::1, 172.16.0.0/12"})
    assert s.trusted_proxies == ("127.0.0.1", "::1", "172.16.0.0/12")


def test_secret_from_file(tmp_path):
    key = tmp_path / "key"
    key.write_text("  sk-from-file\n", encoding="utf-8")
    s = load_settings({"DEEPSEEK_API_KEY_FILE": str(key)})
    assert s.deepseek_api_key == "sk-from-file"
    assert dict(s.report())["DEEPSEEK_API_KEY"] == "set (from DEEPSEEK_API_KEY_FILE)"


def test_secret_file_problems(tmp_path):
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_settings({"GEMINI_API_KEY_FILE": str(tmp_path / "missing"),
                       "DEEPSEEK_API_KEY_FILE": str(empty),
                       "INVITE_CODE": "a", "INVITE_CODE_FILE": str(empty)})
    text = "\n".join(err.value.problems)
    assert "GEMINI_API_KEY_FILE: cannot read" in text
    assert "DEEPSEEK_API_KEY_FILE" in text and "is empty" in text
    assert "INVITE_CODE and INVITE_CODE_FILE are both set" in text


def test_report_never_shows_secret_values():
    s = load_settings({"DEEPSEEK_API_KEY": "sk-very-secret", "FLASK_SECRET_KEY": "k" * 64, "INVITE_CODE": "join-us"})
    shown = " ".join(value for _, value in s.report())
    assert "sk-very-secret" not in shown and "k" * 64 not in shown and "join-us" not in shown
    assert dict(s.report())["GEMINI_API_KEY"] == "not set"


def test_production_profile():
    s = load_settings(PROD)
    assert s.is_production and s.session_cookie_secure is True


def test_production_requires_the_provider_key_and_https_cookies():
    with pytest.raises(ConfigError) as err:
        load_settings({**PROD, "DEEPSEEK_API_KEY": "", "SESSION_COOKIE_SECURE": "0"})
    text = "\n".join(err.value.problems)
    assert "DEEPSEEK_API_KEY is required in production" in text
    assert "SESSION_COOKIE_SECURE=0 is not allowed" in text


def test_short_secret_key_and_plain_http_base_url_are_rejected():
    with pytest.raises(ConfigError) as err:
        load_settings({"FLASK_SECRET_KEY": "short", "DEEPSEEK_BASE_URL": "http://api.deepseek.com"})
    assert len(err.value.problems) == 2


def test_secret_key_generated_in_development_but_not_in_production(tmp_path):
    with pytest.raises(ConfigError):
        load_secret_key(str(tmp_path), production=True)
    key = load_secret_key(str(tmp_path))
    assert len(key) == 64
    assert load_secret_key(str(tmp_path), production=True) == key  # an existing key is fine


def test_create_app_uses_data_dir_and_secret_from_env(monkeypatch, tmp_path):
    from app import create_app

    for name in ("FLASK_SECRET_KEY", "FLASK_SECRET_KEY_FILE", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "s" * 64)
    app = create_app({"TESTING": True})
    assert app.instance_path == str(tmp_path / "data")
    assert app.config["DATABASE"] == str(tmp_path / "data" / "app.db")
    assert app.config["SECRET_KEY"] == "s" * 64
    assert not (tmp_path / "data" / ".secret_key").exists()


def test_create_app_refuses_bad_config(monkeypatch, tmp_path):
    from app import create_app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RETENTION_DAYS", "-1")
    with pytest.raises(ConfigError, match="RETENTION_DAYS"):
        create_app({"TESTING": True})


def test_config_check_command(monkeypatch, capsys):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-hidden")
    assert main() == 0
    out = capsys.readouterr().out
    assert "Configuration OK." in out and "sk-hidden" not in out
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "lots")
    assert main() == 1
    assert "GEMINI_MAX_RETRIES" in capsys.readouterr().out
