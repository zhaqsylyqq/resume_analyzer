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
        # AI provider: "gemini" or "deepseek"
        "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "gemini").strip().lower(),
        # DeepSeek (OpenAI-compatible API)
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        "DEEPSEEK_FALLBACK_MODEL": os.environ.get("DEEPSEEK_FALLBACK_MODEL", ""),
        "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        # Gemini
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", ""),
        "GEMINI_FALLBACK_MODEL": os.environ.get("GEMINI_FALLBACK_MODEL", ""),
        "GEMINI_CONCURRENCY": max(1, _int_env("GEMINI_CONCURRENCY", 2)),
        "GEMINI_MAX_RETRIES": max(0, _int_env("GEMINI_MAX_RETRIES", 5)),
        # R1: sessions untouched for this many days are deleted (0 = keep forever)
        "RETENTION_DAYS": max(0, _int_env("RETENTION_DAYS", 90)),
        # S1: sign-up code for /register (empty = only admins create users)
        "INVITE_CODE": os.environ.get("INVITE_CODE", "").strip(),
        # S2: default daily resume limit per recruiter until an admin saves another (0 = none)
        "DAILY_RESUME_LIMIT": max(0, _int_env("DAILY_RESUME_LIMIT", 30)),
        # S3: give every new user an example session with a Python vacancy
        "STARTER_SESSION": os.environ.get("STARTER_SESSION", "1") != "0",
        # S1: hours added to UTC when statistics group events by day (Kazakhstan: +5)
        "STATS_UTC_OFFSET": _int_env("STATS_UTC_OFFSET", 5),
        # SIS 10-11 fix: peers allowed to tell the real client IP (CF-Connecting-IP / X-Forwarded-For).
        # cloudflared runs on the same machine, so the default trusts loopback only;
        # addresses or networks, e.g. "127.0.0.1,::1,172.16.0.0/12" for docker-compose.
        "TRUSTED_PROXIES": tuple(p.strip() for p in os.environ.get("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if p.strip()),
        # Uploads
        "MAX_FILES_PER_BATCH": 50,
        "MAX_FILE_BYTES": 5 * 1024 * 1024,
        "MAX_CONTENT_LENGTH": 60 * 1024 * 1024,
        # Longer resumes are cut: the tail is rarely useful and costs input tokens.
        "MAX_RESUME_CHARS": 20_000,
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
