"""Real AI calls to verify the configured provider, key, model and the app's schemas.

Usage:
    python scripts/smoke_test.py               # structured JSON round-trip
    python scripts/smoke_test.py --list-models # list models available to the key
    python scripts/smoke_test.py --pipeline    # extract + score one synthetic resume
    python scripts/smoke_test.py --bias        # AI bias check of the biased sample vacancy

LLM_PROVIDER selects the provider: "gemini" (GEMINI_API_KEY, GEMINI_MODEL) or "deepseek"
(DEEPSEEK_API_KEY, DEEPSEEK_MODEL). Keys are read from the environment (or .env) and are
never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
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


def ping(service, model: str) -> int:
    result = service.generate(
        "You are a test endpoint.",
        'Reply with ok=true and the ISO code of the language of: "Сәлеметсіз бе"',
        Ping,
    )
    print(f"OK: model={model} response={result.model_dump()}")
    return 0 if result.ok else 1


def pipeline(service) -> int:
    from app.services.schemas import VacancyInput

    profile = service.extract_profile(SAMPLE_RESUME, "ru")
    print(f"extract: headline={profile.headline!r} years={profile.total_experience_years} "
          f"skills={len(profile.skills)} demographics={profile.demographics.model_dump()}")
    for kk in (False, True):
        [assessment] = service.score(profile, [VacancyInput(id=7, title="Python Developer", text=SAMPLE_VACANCY)],
                                     "kk" if kk else "ru")
        print(f"score[{'kk' if kk else 'ru'}]: {assessment.score} {assessment.verdict} — {assessment.summary}")
    return 0


def bias_check(service) -> int:
    from app.services import bias

    text = (Path(__file__).resolve().parent.parent / "samples" / "vacancy_accountant_biased_ru.txt").read_text(encoding="utf-8")
    report = bias.check(text, "ru", service)
    print(f"bias: ai_checked={report['ai_checked']} counts={report['counts']}")
    for f in report["findings"]:
        print(f"  [{f['source']}/{f['severity']}/{f['category']}] {f['phrase']!r} {f['explanation'][:80]}")
    print(f"rewrite: {report['rewrite'][:160]!r}")
    return 0 if report["ai_checked"] else 1


def build(provider: str):
    """Return (raw client for --list-models, backend-or-client for GeminiService, model, fallback)."""
    if provider == "deepseek":
        from app.services.openai_compat import deepseek_backend

        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise SystemExit("FAIL: DEEPSEEK_API_KEY is not set (check .env)")
        backend = deepseek_backend(api_key, os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com")
        return (backend.client, backend, os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash",
                os.environ.get("DEEPSEEK_FALLBACK_MODEL"))
    if provider == "gemini":
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("FAIL: GEMINI_API_KEY is not set (check .env)")
        client = genai.Client(api_key=api_key)
        return client, client, os.environ.get("GEMINI_MODEL"), os.environ.get("GEMINI_FALLBACK_MODEL")
    raise SystemExit(f"FAIL: unknown LLM_PROVIDER {provider!r} (use 'gemini' or 'deepseek')")


def list_models(provider: str, client) -> None:
    if provider == "deepseek":
        for m in client.models.list():
            print(m.id)
        return
    for m in client.models.list():
        if "generateContent" in (getattr(m, "supported_actions", None) or []):
            print(m.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-models", action="store_true")
    group.add_argument("--pipeline", action="store_true")
    group.add_argument("--bias", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
    raw_client, client, model, fallback = build(provider)
    print(f"provider={provider}")

    from app.services.gemini import GeminiError, GeminiService

    try:
        if args.list_models:
            list_models(provider, raw_client)
            return 0
        if not model:
            print("FAIL: the model is not set (GEMINI_MODEL / DEEPSEEK_MODEL in .env)")
            return 1
        service = GeminiService(client, model, fallback)
        if args.bias:
            return bias_check(service)
        return pipeline(service) if args.pipeline else ping(service, model)
    except GeminiError as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - report any failure as a smoke-test failure
        print(f"FAIL: {type(exc).__name__}: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
