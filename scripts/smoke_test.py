"""Real Gemini calls to verify GEMINI_API_KEY, GEMINI_MODEL and the app's schemas.

Usage:
    python scripts/smoke_test.py               # structured JSON round-trip
    python scripts/smoke_test.py --list-models # list models available to the key
    python scripts/smoke_test.py --pipeline    # extract + score one synthetic resume

The key is read from the environment (or .env) and is never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

SAMPLE_RESUME = """Иванова Мария Сергеевна
Дата рождения: 14.02.1994. Замужем. Email: maria.test@example.com
Senior Python Developer, ТОО «Тест Софт», Алматы — 03.2020 – настоящее время
- Разработала микросервисы на FastAPI и Django, PostgreSQL, Redis
- Внедрила CI/CD в GitLab, Docker, Kubernetes
Python Developer, АО «Пример» — 06.2017 – 02.2020
- REST API, Celery, RabbitMQ
Образование: КБТУ, бакалавр, Информационные системы, 2016
Языки: русский, казахский, английский B2
"""

SAMPLE_VACANCY = """Ищем Middle/Senior Python-разработчика.
Обязательно: Python 3, Django или FastAPI, PostgreSQL, Docker, опыт от 3 лет.
Будет плюсом: Kubernetes, Kafka. Английский не ниже B1."""


class Ping(BaseModel):
    ok: bool
    language: str


def ping(client: genai.Client, model: str) -> int:
    response = client.models.generate_content(
        model=model,
        contents='Reply with ok=true and the ISO code of the language of: "Сәлеметсіз бе"',
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Ping,
            temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    result = Ping.model_validate_json(response.text)
    print(f"OK: model={model} response={result.model_dump()}")
    return 0 if result.ok else 1


def pipeline(client: genai.Client, model: str) -> int:
    from app.services.gemini import GeminiService
    from app.services.schemas import VacancyInput

    service = GeminiService(client, model, os.environ.get("GEMINI_FALLBACK_MODEL"))
    profile = service.extract_profile(SAMPLE_RESUME, "ru")
    print(f"extract: headline={profile.headline!r} years={profile.total_experience_years} "
          f"skills={len(profile.skills)} demographics={profile.demographics.model_dump()}")
    for kk in (False, True):
        [assessment] = service.score(profile, [VacancyInput(id=7, title="Python Developer", text=SAMPLE_VACANCY)],
                                     "kk" if kk else "ru")
        print(f"score[{'kk' if kk else 'ru'}]: {assessment.score} {assessment.verdict} — {assessment.summary}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-models", action="store_true")
    group.add_argument("--pipeline", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL")
    if not api_key:
        print("FAIL: GEMINI_API_KEY is not set (check .env)")
        return 1
    client = genai.Client(api_key=api_key)

    try:
        if args.list_models:
            for m in client.models.list():
                if "generateContent" in (getattr(m, "supported_actions", None) or []):
                    print(m.name)
            return 0
        if not model:
            print("FAIL: GEMINI_MODEL is not set (check .env)")
            return 1
        return pipeline(client, model) if args.pipeline else ping(client, model)
    except errors.APIError as exc:
        print(f"FAIL: Gemini API error {exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001 - report any failure as a smoke-test failure
        print(f"FAIL: {type(exc).__name__}: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
