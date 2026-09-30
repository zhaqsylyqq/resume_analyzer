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


def smart_handler(profile: dict[str, Any] | None = None, score_for: Callable[[str], int] | None = None,
                  bias: dict[str, Any] | None = None):
    """Answer extraction with a profile, bias checks with `bias`, scoring with every requested id."""
    import re

    from app.services.schemas import BiasReport, CandidateProfile

    def handle(call: dict[str, Any]) -> Any:
        if call["config"].response_schema is CandidateProfile:
            return profile or PROFILE
        if call["config"].response_schema is BiasReport:
            return bias or {"findings": [], "rewrite": ""}
        ids = [int(i) for i in re.search(r"Vacancy ids to assess: ([\d, ]+)", call["contents"]).group(1).split(",")]
        return scoring_reply(ids, score_for(call["contents"]) if score_for else 80)

    return handle


def make_pdf(lines: list[str]) -> bytes:
    """Minimal one-page PDF with a Helvetica text layer (ASCII only)."""
    ops = " ".join(f"({line}) Tj 0 -16 Td" for line in lines)
    content = f"BT /F1 12 Tf 72 720 Td {ops} ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import io

    from docx import Document

    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


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
