from __future__ import annotations

import os
import re

import pytest

from app import create_app

# APP-02: TEST_DATABASE_URL=postgresql://… runs the whole suite on PostgreSQL (CI does both).
# The database is wiped: never point it at real data.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
# A developer's .env may point DATABASE_URL at the live PostgreSQL database (the laptop does).
# Tests must never touch it: empty values win over .env (python-dotenv never overrides them).
for _name in ("DATABASE_URL", "DATABASE_URL_FILE", "BACKUP_RESTORE_URL", "BACKUP_RESTORE_URL_FILE"):
    os.environ[_name] = ""
# SEC-02b: a fixed test data key, so no test creates or reads <instance>/.data_key (on the
# laptop that is the live data folder) and a developer's DATA_KEY never encrypts test data.
TEST_DATA_KEY = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
os.environ.update(DATA_KEY=TEST_DATA_KEY, DATA_KEY_FILE="", DATA_KEY_OLD="", DATA_KEY_OLD_FILE="")
ON_POSTGRES = bool(TEST_DATABASE_URL)
PG_TABLES = ("rate_counters", "rate_locks", "password_resets", "llm_usage", "candidate_status", "audit_log", "settings", "scores",
             "demographics", "resumes", "vacancies", "sessions", "users")


def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: needs the SQLite file (file backups and the like)")


def pytest_collection_modifyitems(config, items):
    if ON_POSTGRES:
        skip = pytest.mark.skip(reason="SQLite-only test (TEST_DATABASE_URL is PostgreSQL)")
        for item in items:
            if "sqlite_only" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def _postgres_schema():
    if not ON_POSTGRES:
        return None
    import psycopg

    from app.services import migrations

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    migrations.upgrade(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


def _database_for_test(tmp_path, url):
    if not url:
        return str(tmp_path / "test.db")
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn, conn.transaction():
        # SEC-03: the audit log refuses TRUNCATE; the owner lifts the guard for this transaction.
        conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_truncate")
        conn.execute(f"TRUNCATE {', '.join(PG_TABLES)} RESTART IDENTITY CASCADE")
        conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_truncate")
    return url


@pytest.fixture()
def app(tmp_path, _postgres_schema):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": _database_for_test(tmp_path, _postgres_schema),
            "SYNC_JOBS": True,
            # Pin the provider: a developer's .env (e.g. LLM_PROVIDER=deepseek with a real key)
            # must never route tests to a live API.
            "LLM_PROVIDER": "gemini",
            "DEEPSEEK_API_KEY": "",
            "GEMINI_API_KEY": "test-key",
            "GEMINI_MODEL": "test-model",
            "GEMINI_FALLBACK_MODEL": "",
            # Same for settings a developer may have in .env (S1/S2): tests start from defaults.
            "INVITE_CODE": "",
            "DAILY_RESUME_LIMIT": 30,
            "STARTER_SESSION": True,
        }
    )
    yield app


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Argon2 with production parameters is deliberately slow; tests use cheap ones."""
    from argon2 import PasswordHasher

    from app.auth import passwords

    monkeypatch.setattr(passwords, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


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
