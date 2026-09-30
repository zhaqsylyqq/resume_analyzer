"""Test doubles for the Gemini client. No test ever calls the real API."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from google.genai import errors


def api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}] if retry_delay else []
    return errors.APIError(code, {"error": {"code": code, "message": f"fake {code}", "status": "X", "details": details}})


@dataclass
class _Response:
    text: str | None


@dataclass
class FakeModels:
    """Replays scripted replies. A reply is a str/dict (JSON body), an Exception, or a callable."""

    replies: list[Any] = field(default_factory=list)
    handler: Callable[[dict[str, Any]], Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate_content(self, *, model: str, contents: str, config: Any) -> _Response:
        call = {"model": model, "contents": contents, "config": config}
        self.calls.append(call)
        reply = self.replies.pop(0) if self.replies else self.handler(call) if self.handler else None
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, (dict, list)):
            reply = json.dumps(reply, ensure_ascii=False)
        return _Response(reply)


class FakeClient:
    def __init__(self, replies: list[Any] | None = None, handler: Callable[[dict[str, Any]], Any] | None = None):
        self.models = FakeModels(list(replies or []), handler)


PROFILE = {
    "full_name": "Айгерим Сапарова",
    "contacts": {"email": "aigerim@example.kz", "phone": "+7 701 000 00 00"},
    "headline": "Python-разработчик",
    "summary": "Айгерим — backend-разработчик, 5 лет опыта с Python и Django. Замужем.",
    "total_experience_years": 5,
    "skills": ["Python", "Django", "PostgreSQL", "Docker"],
    "experience": [
        {"title": "Backend Developer", "organization": "Kaspi", "duration_years": 3, "highlights": ["Разработала API платежей"]},
        {"title": "Junior Developer", "organization": "EPAM", "duration_years": 2, "highlights": []},
    ],
    "education": [{"degree": "Бакалавр", "field": "Информационные системы", "institution": "КБТУ"}],
    "languages": [{"language": "Английский", "level": "B2"}],
    "certifications": [],
    "resume_language": "ru",
    "demographics": {"gender": "female", "age": 27, "birth_year": 1999, "marital_status": "замужем", "nationality": "казашка", "photo_mentioned": True},
}


def scoring_reply(vacancy_ids: list[int], score: int = 80) -> dict[str, Any]:
    return {
        "assessments": [
            {
                "vacancy_id": vid,
                "score": score,
                "verdict": "weak",  # deliberately inconsistent; service derives it from score
                "summary": "Хорошее соответствие.",
                "matched_skills": ["Python"],
                "missing_skills": ["Kubernetes"],
                "strengths": ["API"],
                "concerns": [],
            }
            for vid in vacancy_ids
        ]
    }
