"""SEC-04: fill a throw-away copy of the app for the OWASP ZAP scan (CI only, never the live site).

Creates the recruiter DAST_USER (password DAST_PASSWORD), the example session of a new user and
one analysed resume with its score and pipeline status, so the scanner reaches every page a
signed-in recruiter sees. No AI request is made. Settings come from the environment, the same
as the server being scanned (DATA_DIR, FLASK_SECRET_KEY, DATA_KEY, APP_ENV).

    python ci/dast_seed.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.services import storage  # noqa: E402
from app.services.schemas import CandidateProfile, FitAssessment  # noqa: E402
from app.starter import create_starter_session  # noqa: E402

RESUME = """Сауле Тестова
Python-разработчик, ТОО «Пример», Алматы — 2021 – настоящее время
- REST API на Django и FastAPI, PostgreSQL, Docker
Образование: КБТУ, бакалавр, Информационные системы
"""


def main() -> int:
    username, password = os.environ["DAST_USER"], os.environ["DAST_PASSWORD"]
    app = create_app()
    with app.app_context():
        db = storage.get_db()
        user_id = storage.create_user(db, username, hash_password(password), "recruiter")
        session_id = create_starter_session(db, user_id)
        if session_id is None:
            raise SystemExit("STARTER_SESSION is off: the scan needs the example session")
        [vacancy] = storage.list_vacancies(db, session_id)
        resume_id = storage.add_resume(db, session_id, "saule_testova.txt",
                                       hashlib.sha256(RESUME.encode()).hexdigest(), RESUME, storage.DONE)
        if resume_id is None:
            raise SystemExit("the database is not empty: seed a fresh DATA_DIR")
        storage.save_profile(db, resume_id, CandidateProfile(
            full_name="Сауле Тестова", headline="Python-разработчик", summary="Backend на Python, 4 года.",
            total_experience_years=4, skills=["Python", "Django", "PostgreSQL", "Docker"]))
        storage.save_scores(db, resume_id, [FitAssessment(
            vacancy_id=vacancy["id"], score=82, verdict="good", summary="Подходит по основным требованиям.",
            matched_skills=["Python", "Django"], missing_skills=["Kubernetes"])])
        storage.set_candidate_status(db, vacancy["id"], resume_id, "shortlist", "Позвать на интервью", user_id)
    print(f"seeded: user {username}, session {session_id}, vacancy {vacancy['id']}, resume {resume_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
