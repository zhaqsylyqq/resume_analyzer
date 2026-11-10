from __future__ import annotations

import re

import pytest

from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(tmp_path / "test.db"),
            "SYNC_JOBS": True,
            # Pin the provider: a developer's .env (e.g. LLM_PROVIDER=deepseek with a real key)
            # must never route tests to a live API.
            "LLM_PROVIDER": "gemini",
            "DEEPSEEK_API_KEY": "",
            "GEMINI_API_KEY": "test-key",
            "GEMINI_MODEL": "test-model",
            "GEMINI_FALLBACK_MODEL": "",
        }
    )
    yield app


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Argon2 with production parameters is deliberately slow; tests use cheap ones."""
    from argon2 import PasswordHasher

    import app.auth as auth

    monkeypatch.setattr(auth, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


TEST_PASSWORD = "correct-horse-battery"


def make_user(app, username, role="recruiter", password=TEST_PASSWORD):
    from app.auth import hash_password
    from app.services import storage

    with app.app_context():
        return storage.create_user(storage.get_db(), username, hash_password(password), role)


def login(client, username, password=TEST_PASSWORD):
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    return client.post("/login", data={"csrf_token": token, "username": username, "password": password})


@pytest.fixture()
def anon_client(app):
    """A client that has not signed in."""
    return app.test_client()


@pytest.fixture()
def client(app):
    """Signed in as the recruiter 'tester' (A1: every page needs login)."""
    make_user(app, "tester")
    test_client = app.test_client()
    assert login(test_client, "tester").status_code == 302
    return test_client


@pytest.fixture()
def fake_gemini(app):
    """GeminiService backed by a scripted fake client; no network, no sleeping."""
    from app.services.gemini import GeminiService

    from .fakes import FakeClient, smart_handler

    service = GeminiService(FakeClient(handler=smart_handler()), "test-model", sleep=lambda s: None)
    app.extensions["gemini"] = service
    return service


@pytest.fixture()
def csrf(client):
    """Return a valid CSRF token for the test client's session."""
    html = client.get("/").get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf meta tag missing"
    return match.group(1)
