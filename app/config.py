"""Application configuration. Secrets come only from the environment."""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def settings_from_env() -> dict[str, Any]:
    """Build settings from the current environment (call after load_dotenv)."""
    return {
        # Gemini
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", ""),
        "GEMINI_FALLBACK_MODEL": os.environ.get("GEMINI_FALLBACK_MODEL", ""),
        "GEMINI_CONCURRENCY": max(1, _int_env("GEMINI_CONCURRENCY", 2)),
        "GEMINI_MAX_RETRIES": 5,
        # Uploads
        "MAX_FILES_PER_BATCH": 50,
        "MAX_FILE_BYTES": 5 * 1024 * 1024,
        "MAX_CONTENT_LENGTH": 60 * 1024 * 1024,
        "MAX_RESUME_CHARS": 30_000,
        # Background jobs run inline when True (tests)
        "SYNC_JOBS": False,
        # Cookies
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        # UI
        "LANGUAGES": ("ru", "kk"),
        "DEFAULT_LANGUAGE": "ru",
    }


def load_secret_key(instance_path: str) -> str:
    """Return FLASK_SECRET_KEY, or a random key persisted in the instance folder."""
    key = os.environ.get("FLASK_SECRET_KEY")
    if key:
        return key
    path = Path(instance_path) / ".secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key
