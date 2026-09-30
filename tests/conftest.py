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
            "GEMINI_API_KEY": "test-key",
            "GEMINI_MODEL": "test-model",
            "GEMINI_FALLBACK_MODEL": "",
        }
    )
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def csrf(client):
    """Return a valid CSRF token for the test client's session."""
    html = client.get("/").get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf meta tag missing"
    return match.group(1)
