"""S2 — admin-editable settings stored in the database (no restart needed).

Until an admin saves them, defaults follow the environment: sign-up is open with an invite
code when INVITE_CODE is set (S1 behaviour), otherwise closed; the daily resume limit is
DAILY_RESUME_LIMIT (default 30).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .services import storage

MAX_LIMIT = 10_000


@dataclass(frozen=True)
class AppSettings:
    registration_open: bool
    require_code: bool
    invite_code: str
    daily_limit: int  # 0 = unlimited

    @property
    def signup_available(self) -> bool:
        return self.registration_open and (not self.require_code or bool(self.invite_code))


def load(db: sqlite3.Connection, config: dict[str, Any]) -> AppSettings:
    saved = storage.get_settings(db)
    env_code = config.get("INVITE_CODE", "") or ""
    try:
        limit = int(saved["daily_limit"]) if "daily_limit" in saved else int(config.get("DAILY_RESUME_LIMIT", 30))
    except ValueError:
        limit = 30
    return AppSettings(
        registration_open=saved.get("registration_open", "1" if env_code else "0") == "1",
        require_code=saved.get("require_code", "1") == "1",
        invite_code=saved.get("invite_code", env_code),
        daily_limit=max(0, min(limit, MAX_LIMIT)),
    )


def save(db: sqlite3.Connection, value: AppSettings) -> None:
    storage.save_settings(db, {
        "registration_open": "1" if value.registration_open else "0",
        "require_code": "1" if value.require_code else "0",
        "invite_code": value.invite_code,
        "daily_limit": str(value.daily_limit),
    })
