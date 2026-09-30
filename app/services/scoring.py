"""F2 — ranking, Top-N filtering and CSV export for one vacancy.

Ranking uses standard competition ranks (1, 2, 2, 4). The Top-N cut keeps candidates
tied with the N-th score so equal scores are never split arbitrarily. Export never
includes demographics and neutralizes spreadsheet formula injection.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

TOP_CHOICES = (5, 10, 20)
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class RankedCandidate:
    rank: int
    resume_id: int
    filename: str
    name: str | None
    headline: str | None
    experience_years: float | None
    email: str | None
    phone: str | None
    score: int
    verdict: str
    summary: str
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


def ranking(db: sqlite3.Connection, vacancy_id: int) -> list[RankedCandidate]:
    rows = db.execute(
        """
        SELECT r.id AS resume_id, r.filename, r.profile_json, s.score, s.verdict, s.summary, s.details_json
        FROM scores s JOIN resumes r ON r.id = s.resume_id
        WHERE s.vacancy_id = ? AND r.status = 'done'
        ORDER BY s.score DESC, r.id ASC
        """,
        (vacancy_id,),
    ).fetchall()

    ranked: list[RankedCandidate] = []
    for position, row in enumerate(rows, start=1):
        rank = ranked[-1].rank if ranked and ranked[-1].score == row["score"] else position
        profile = json.loads(row["profile_json"] or "{}")
        details = json.loads(row["details_json"] or "{}")
        contacts = profile.get("contacts") or {}
        ranked.append(
            RankedCandidate(
                rank=rank,
                resume_id=row["resume_id"],
                filename=row["filename"],
                name=profile.get("full_name"),
                headline=profile.get("headline"),
                experience_years=profile.get("total_experience_years"),
                email=contacts.get("email"),
                phone=contacts.get("phone"),
                score=row["score"],
                verdict=row["verdict"],
                summary=row["summary"],
                matched_skills=details.get("matched_skills", []),
                missing_skills=details.get("missing_skills", []),
                strengths=details.get("strengths", []),
                concerns=details.get("concerns", []),
            )
        )
    return ranked


def parse_top(value: str | None) -> int | None:
    """Top-N from a query string: a positive int, or None for 'all'."""
    try:
        n = int(value) if value not in (None, "", "all") else None
    except ValueError:
        return TOP_CHOICES[1]
    return n if n is None or 1 <= n <= 1000 else TOP_CHOICES[1]


def top_n(candidates: list[RankedCandidate], n: int | None) -> list[RankedCandidate]:
    """First n candidates plus anyone tied with the n-th score."""
    if n is None or n >= len(candidates):
        return list(candidates)
    cutoff = candidates[n - 1].score
    return [c for i, c in enumerate(candidates) if i < n or c.score == cutoff]


def summary_stats(candidates: list[RankedCandidate]) -> dict[str, float | int]:
    if not candidates:
        return {"count": 0, "average": 0, "best": 0, "strong": 0, "good": 0}
    return {
        "count": len(candidates),
        "average": round(sum(c.score for c in candidates) / len(candidates)),
        "best": candidates[0].score,
        "strong": sum(c.verdict == "strong" for c in candidates),
        "good": sum(c.verdict == "good" for c in candidates),
    }


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def to_csv(candidates: list[RankedCandidate], t: Callable[[str], str]) -> bytes:
    """UTF-8 with BOM and ';' delimiter so Excel in RU/KK locales opens it correctly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([t(f"csv.{col}") for col in (
        "rank", "name", "file", "score", "verdict", "summary", "matched", "missing",
        "strengths", "concerns", "headline", "experience", "email", "phone")])
    for c in candidates:
        writer.writerow([_safe_cell(v) for v in (
            c.rank, c.name or "", c.filename, c.score, t(f"verdict.{c.verdict}"), c.summary,
            ", ".join(c.matched_skills), ", ".join(c.missing_skills),
            "; ".join(c.strengths), "; ".join(c.concerns), c.headline or "",
            "" if c.experience_years is None else c.experience_years, c.email or "", c.phone or "")])
    return ("﻿" + buffer.getvalue()).encode("utf-8")
