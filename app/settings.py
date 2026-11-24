"""S2 — admin-editable settings stored in the database (no restart needed).

Until an admin saves them, defaults follow the environment: sign-up is open with an invite
code when INVITE_CODE is set (S1 behaviour), otherwise closed; the daily resume limit is
DAILY_RESUME_LIMIT (default 30). QW-02 adds the daily limit of AI vacancy checks per
recruiter (DAILY_AI_CHECK_LIMIT, default 20).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .services import storage
from .services.db import Database

MAX_LIMIT = 10_000


@dataclass(frozen=True)
class AppSettings:
    registration_open: bool
    require_code: bool
    invite_code: str
    daily_limit: int  # 0 = unlimited
    ai_check_limit: int = 20  # QW-02: AI vacancy checks per recruiter per 24 h; 0 = unlimited

    @property
    def signup_available(self) -> bool:
        return self.registration_open and (not self.require_code or bool(self.invite_code))


def _int_setting(saved: dict[str, str], key: str, default: Any, fallback: int) -> int:
    try:
        return int(saved[key]) if key in saved else int(default)
    except (TypeError, ValueError):
        return fallback


def load(db: Database, config: dict[str, Any]) -> AppSettings:
    saved = storage.get_settings(db)
    env_code = config.get("INVITE_CODE", "") or ""
    limit = _int_setting(saved, "daily_limit", config.get("DAILY_RESUME_LIMIT", 30), 30)
    ai_limit = _int_setting(saved, "ai_check_limit", config.get("DAILY_AI_CHECK_LIMIT", 20), 20)
    return AppSettings(
        registration_open=saved.get("registration_open", "1" if env_code else "0") == "1",
        require_code=saved.get("require_code", "1") == "1",
        invite_code=saved.get("invite_code", env_code),
        daily_limit=max(0, min(limit, MAX_LIMIT)),
        ai_check_limit=max(0, min(ai_limit, MAX_LIMIT)),
    )


def save(db: Database, value: AppSettings) -> None:
    storage.save_settings(db, {
        "registration_open": "1" if value.registration_open else "0",
        "require_code": "1" if value.require_code else "0",
        "invite_code": value.invite_code,
        "daily_limit": str(value.daily_limit),
        "ai_check_limit": str(value.ai_check_limit),
    })
