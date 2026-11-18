"""Application configuration (APP-01).

All settings come from the environment (or `.env`, loaded by create_app). They are parsed
into one typed object, checked once at start-up, and a bad value stops the app with a list
of every problem instead of being silently replaced by a default.

Secrets can also come from files: `FLASK_SECRET_KEY_FILE=/run/secrets/flask_key` reads the
key from that file. Docker and Kubernetes mount secrets this way, so they never appear in
`docker inspect` or in the process environment.

`APP_ENV` picks the profile:
- `development` (default): convenient defaults for a laptop.
- `production`: stricter — the AI key must be set, cookies are HTTPS-only, and the app will not
  invent a new session secret (a new key would log everyone out on every replica restart).
- `test`: used by the test suite.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

ENVIRONMENTS = ("development", "production", "test")
PROVIDERS = ("gemini", "deepseek")
SECRET_NAMES = ("FLASK_SECRET_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "INVITE_CODE")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(RuntimeError):
    """One or more settings are invalid. `problems` lists each one."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    # AI provider: "gemini" or "deepseek"
    llm_provider: str = "gemini"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_fallback_model: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_fallback_model: str = ""
    gemini_concurrency: int = 2
    gemini_max_retries: int = 5
    # R1: sessions untouched for this many days are deleted (0 = keep forever)
    retention_days: int = 90
    # S1: sign-up code for /register (empty = only admins create users)
    invite_code: str = ""
    # S2 / QW-02: defaults per recruiter per 24 h until an admin saves others (0 = no limit)
    daily_resume_limit: int = 30
    daily_ai_check_limit: int = 20
    # S3: give every new user an example session with a Python vacancy
    starter_session: bool = True
    # S1: hours added to UTC when statistics group events by day (Kazakhstan: +5)
    stats_utc_offset: int = 5
    # Peers allowed to tell the real client IP and scheme (QW-04). cloudflared runs on the same
    # machine, so the default trusts loopback only; e.g. "127.0.0.1,::1,172.16.0.0/12" for compose.
    trusted_proxies: tuple[str, ...] = ("127.0.0.1", "::1")
    session_cookie_secure: bool = False
    flask_secret_key: str = ""
    # Where the SQLite database and the generated secret live; default: Flask's instance folder
    data_dir: str = ""
    # APP-08: seconds a stopping server waits for resumes being analyzed (keep below the
    # orchestrator's grace period: Docker 10 s by default, Kubernetes 30 s)
    shutdown_timeout: int = 25
    # Sources of secrets read from *_FILE, for the config report (never the values)
    secret_sources: dict[str, str] = field(default_factory=dict)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def to_flask(self) -> dict[str, Any]:
        """The Flask config keys the rest of the app reads (names unchanged since before APP-01)."""
        return {
            "APP_ENV": self.app_env,
            "LLM_PROVIDER": self.llm_provider,
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
            "DEEPSEEK_MODEL": self.deepseek_model,
            "DEEPSEEK_FALLBACK_MODEL": self.deepseek_fallback_model,
            "DEEPSEEK_BASE_URL": self.deepseek_base_url,
            "GEMINI_API_KEY": self.gemini_api_key,
            "GEMINI_MODEL": self.gemini_model,
            "GEMINI_FALLBACK_MODEL": self.gemini_fallback_model,
            "GEMINI_CONCURRENCY": self.gemini_concurrency,
            "GEMINI_MAX_RETRIES": self.gemini_max_retries,
            "RETENTION_DAYS": self.retention_days,
            "INVITE_CODE": self.invite_code,
            "DAILY_RESUME_LIMIT": self.daily_resume_limit,
            "DAILY_AI_CHECK_LIMIT": self.daily_ai_check_limit,
            "STARTER_SESSION": self.starter_session,
            "STATS_UTC_OFFSET": self.stats_utc_offset,
            "TRUSTED_PROXIES": self.trusted_proxies,
            "SHUTDOWN_TIMEOUT": self.shutdown_timeout,
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
            "SESSION_COOKIE_SECURE": self.session_cookie_secure,
            # UI
            "LANGUAGES": ("ru", "kk"),
            "DEFAULT_LANGUAGE": "ru",
        }

    def report(self) -> list[tuple[str, str]]:
        """(NAME, value) pairs for `flask config-check`; secrets are shown only as set/unset."""
        rows = []
        for f in fields(self):
            if f.name == "secret_sources":
                continue
            name = f.name.upper()
            value = getattr(self, f.name)
            if name in SECRET_NAMES:
                source = self.secret_sources.get(name)
                shown = ("set" + (f" (from {source})" if source else "")) if value else "not set"
            elif isinstance(value, tuple):
                shown = ",".join(value)
            else:
                shown = str(value)
            rows.append((name, shown))
        return rows


class _Reader:
    """Reads and validates raw values, collecting every problem instead of stopping at the first."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = env
        self.problems: list[str] = []
        self.secret_sources: dict[str, str] = {}

    def text(self, name: str, default: str = "") -> str:
        return self.env.get(name, default).strip()

    def secret(self, name: str) -> str:
        path = self.env.get(f"{name}_FILE", "").strip()
        if not path:
            return self.env.get(name, "").strip()
        if self.env.get(name, "").strip():
            self.problems.append(f"{name} and {name}_FILE are both set; keep one")
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            self.problems.append(f"{name}_FILE: cannot read {path} ({exc.strerror or exc})")
            return ""
        if not value:
            self.problems.append(f"{name}_FILE: {path} is empty")
        self.secret_sources[name] = f"{name}_FILE"
        return value

    def integer(self, name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
        raw = self.env.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            self.problems.append(f"{name}={raw!r} is not a whole number")
            return default
        if minimum is not None and value < minimum or maximum is not None and value > maximum:
            low = "" if minimum is None else f"from {minimum}"
            high = "" if maximum is None else f"to {maximum}"
            self.problems.append(f"{name}={value} is out of range (allowed {' '.join(p for p in (low, high) if p)})")
            return default
        return value

    def boolean(self, name: str, default: bool) -> bool:
        raw = self.env.get(name)
        if raw is None:
            return default
        value = raw.strip().lower()
        if value in _TRUE:
            return True
        if value in _FALSE:
            return False
        self.problems.append(f"{name}={raw!r} must be 1/0 (or true/false)")
        return default

    def choice(self, name: str, default: str, allowed: tuple[str, ...]) -> str:
        value = self.text(name, default).lower() or default
        if value not in allowed:
            self.problems.append(f"{name}={value!r} must be one of: {', '.join(allowed)}")
            return default
        return value

    def networks(self, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        raw = self.env.get(name)
        if raw is None:
            return default
        items = tuple(p.strip() for p in raw.split(",") if p.strip())
        for item in items:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                self.problems.append(f"{name}: {item!r} is not an IP address or network")
        return items


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Parse and validate settings; raise ConfigError listing every problem."""
    r = _Reader(os.environ if env is None else env)
    app_env = r.choice("APP_ENV", "development", ENVIRONMENTS)
    production = app_env == "production"
    settings = Settings(
        app_env=app_env,
        llm_provider=r.choice("LLM_PROVIDER", "gemini", PROVIDERS),
        deepseek_api_key=r.secret("DEEPSEEK_API_KEY"),
        deepseek_model=r.text("DEEPSEEK_MODEL") or "deepseek-flash",
        deepseek_fallback_model=r.text("DEEPSEEK_FALLBACK_MODEL"),
        deepseek_base_url=r.text("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        gemini_api_key=r.secret("GEMINI_API_KEY"),
        gemini_model=r.text("GEMINI_MODEL"),
        gemini_fallback_model=r.text("GEMINI_FALLBACK_MODEL"),
        gemini_concurrency=r.integer("GEMINI_CONCURRENCY", 2, minimum=1, maximum=16),
        gemini_max_retries=r.integer("GEMINI_MAX_RETRIES", 5, minimum=0, maximum=20),
        retention_days=r.integer("RETENTION_DAYS", 90, minimum=0),
        invite_code=r.secret("INVITE_CODE"),
        daily_resume_limit=r.integer("DAILY_RESUME_LIMIT", 30, minimum=0),
        daily_ai_check_limit=r.integer("DAILY_AI_CHECK_LIMIT", 20, minimum=0),
        starter_session=r.boolean("STARTER_SESSION", True),
        stats_utc_offset=r.integer("STATS_UTC_OFFSET", 5, minimum=-12, maximum=14),
        trusted_proxies=r.networks("TRUSTED_PROXIES", ("127.0.0.1", "::1")),
        # Production is served over HTTPS (Cloudflare), so Secure cookies are the default there.
        session_cookie_secure=r.boolean("SESSION_COOKIE_SECURE", production),
        flask_secret_key=r.secret("FLASK_SECRET_KEY"),
        data_dir=r.text("DATA_DIR"),
        shutdown_timeout=r.integer("SHUTDOWN_TIMEOUT", 25, minimum=0, maximum=600),
        secret_sources=r.secret_sources,
    )

    if settings.deepseek_base_url and not settings.deepseek_base_url.startswith("https://"):
        r.problems.append("DEEPSEEK_BASE_URL must start with https://")
    if settings.flask_secret_key and len(settings.flask_secret_key) < 32:
        r.problems.append("FLASK_SECRET_KEY is too short (use at least 32 characters, e.g. 64 hex digits)")
    if production:
        key_name = "DEEPSEEK_API_KEY" if settings.llm_provider == "deepseek" else "GEMINI_API_KEY"
        if not (settings.deepseek_api_key if settings.llm_provider == "deepseek" else settings.gemini_api_key):
            r.problems.append(f"{key_name} is required in production (LLM_PROVIDER={settings.llm_provider})")
        if not settings.session_cookie_secure:
            r.problems.append("SESSION_COOKIE_SECURE=0 is not allowed in production (the site is served over HTTPS)")
    if r.problems:
        raise ConfigError(r.problems)
    return settings


def settings_from_env() -> dict[str, Any]:
    """Flask config mapping from the current environment (kept for older callers and tests)."""
    return load_settings().to_flask()


def load_secret_key(instance_path: str, production: bool = False) -> str:
    """The session secret kept in the instance folder, generated on first start.

    In production a missing key is an error: it must come from FLASK_SECRET_KEY(_FILE) or an
    existing instance/.secret_key, so restarts and replicas never invalidate everyone's login.
    """
    path = Path(instance_path) / ".secret_key"
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    if production:
        raise ConfigError(["FLASK_SECRET_KEY (or FLASK_SECRET_KEY_FILE) is required in production"])
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key

