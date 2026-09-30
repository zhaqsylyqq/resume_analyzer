"""Build the demographics-free view of a candidate that fit scoring is allowed to see.

Scoring must not receive gender, age, photo, marital status or nationality. The profile
schema already separates those into `demographics`; this module drops them together with
name and contacts, replaces calendar dates with durations, and scrubs any leftover
mentions from free-text fields as a second line of defence.
"""
from __future__ import annotations

import re
from typing import Any

from .schemas import CandidateProfile

REDACTED = "[removed]"

_FLAGS = re.IGNORECASE | re.UNICODE
_PATTERNS = [
    # Contacts
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", _FLAGS),
    re.compile(r"(?:https?://|www\.)\S+", _FLAGS),
    re.compile(r"\+?\d[\d\s()\-]{8,}\d", _FLAGS),
    # Birth date / age
    re.compile(
        r"\b(?:дата рождения|год рождения|родил(?:ся|ась)|туған (?:күні|жылы)|date of birth|d\.o\.b\.?|born)\b(?:[^,.;\n]|\.(?=\d))*",
        _FLAGS,
    ),
    re.compile(r"\b(?:возраст|жасы|age)\s*[:\-–]?\s*\d{1,3}\b[^,.;\n]*", _FLAGS),
    re.compile(r"\b(?:мне|жасым|i am|i'm)\s+\d{2}\b(?:\s*(?:лет|года|год|жаста|жас|years?(?:\s+old)?))?", _FLAGS),
    re.compile(r"\b\d{2}\s*(?:years?\s+old|y\.o\.|жаста|жастамын)", _FLAGS),
    # Gender
    re.compile(r"\b(?:пол|жынысы|gender|sex)\s*[:\-–]\s*[^,.;\n]*", _FLAGS),
    re.compile(r"\b(?:мужчина|женщина|мужской|женский|ер адам|әйел адам|male|female)\b", _FLAGS),
    # Marital status / children
    re.compile(
        r"\b(?:семейное положение|отбасылық жағдайы|marital status)\s*[:\-–]?\s*[^,.;\n]*",
        _FLAGS,
    ),
    re.compile(
        r"\b(?:не\s+)?(?:женат|замужем|холост|разведена?|вдов(?:ец|а)|married|divorced|widowed|"
        r"үйленген|үйленбеген|тұрмыс құрған|тұрмыс құрмаған)\b",
        _FLAGS,
    ),
    re.compile(r"\b(?:есть|имею|двое|трое|\d)\s+(?:детей|ребенка|ребёнка|детьми)\b", _FLAGS),
    re.compile(r"\bбалалары?\s+бар\b", _FLAGS),
    # Nationality / ethnicity
    re.compile(r"\b(?:национальность|ұлты|nationality|ethnicity)\s*[:\-–]?\s*[^,.;\n]*", _FLAGS),
    # Photo
    re.compile(r"\b(?:фото|сурет|photo)\b", _FLAGS),
]


def scrub_text(text: str | None, name_tokens: tuple[str, ...] = ()) -> str:
    if not text:
        return ""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    for token in name_tokens:
        text = re.sub(rf"\b{re.escape(token)}\b", "[candidate]", text, flags=_FLAGS)
    return re.sub(r"\s{2,}", " ", text).strip()


def _name_tokens(profile: CandidateProfile) -> tuple[str, ...]:
    if not profile.full_name:
        return ()
    return tuple(part for part in re.split(r"[\s\-]+", profile.full_name) if len(part) >= 3)


def scoring_view(profile: CandidateProfile) -> dict[str, Any]:
    """Return only job-relevant facts, with free text scrubbed."""
    names = _name_tokens(profile)

    def clean(value: str | None) -> str:
        return scrub_text(value, names)

    def clean_list(values: list[str]) -> list[str]:
        return [c for c in (clean(v) for v in values) if c and c != REDACTED]

    return {
        "headline": clean(profile.headline),
        "summary": clean(profile.summary),
        "total_experience_years": profile.total_experience_years,
        "skills": clean_list(profile.skills),
        "experience": [
            {
                "title": clean(item.title),
                "organization": clean(item.organization),
                "duration_years": item.duration_years,
                "highlights": clean_list(item.highlights),
            }
            for item in profile.experience
        ],
        "education": [
            {"degree": clean(e.degree), "field": clean(e.field), "institution": clean(e.institution)}
            for e in profile.education
        ],
        "languages": [{"language": clean(l.language), "level": clean(l.level)} for l in profile.languages],
        "certifications": clean_list(profile.certifications),
    }
