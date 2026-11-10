"""S3 — every new user starts with an example screening session.

The session holds one vacancy, "Python-разработчик (Middle/Senior)", and no resumes, so a
newcomer can upload sample resumes right away. It is an ordinary session owned by the
user: they can add vacancies, delete the vacancy or delete the whole session like any
other. Creating it makes no AI call (the bias check is the
local lexicon only) and is not counted as the user's own "session_create" in statistics.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .services import bias, storage

STARTER_SESSION_TITLE = "Пример: Python-разработчик"
STARTER_VACANCY_TITLE = "Python-разработчик (Middle/Senior)"
STARTER_VACANCY_FILE = Path(__file__).resolve().parent / "data" / "starter_vacancy_ru.txt"


def starter_vacancy_text() -> str:
    return STARTER_VACANCY_FILE.read_text(encoding="utf-8").strip()


def create_starter_session(db: sqlite3.Connection, user_id: int, enabled: bool = True) -> int | None:
    """Create the example session for a new user; returns its id (None when disabled)."""
    if not enabled:
        return None
    text = starter_vacancy_text()
    session_id = storage.create_session(db, STARTER_SESSION_TITLE, "ru", [(STARTER_VACANCY_TITLE, text)],
                                        owner_id=user_id)
    for vacancy in storage.list_vacancies(db, session_id):
        storage.save_bias_report(db, vacancy["id"], bias.check(vacancy["text"]))
    return session_id
