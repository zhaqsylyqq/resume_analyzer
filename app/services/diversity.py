"""B2 — aggregate diversity metrics for a shortlist, never per candidate.

Compares the Top-N shortlist with the whole scored pool by gender, age band and resume
language (RU/KK/EN), using only what resumes stated explicitly (everything else is
"unknown"). All metrics are computed locally from stored data — no AI call. Privacy safeguards:
- no report for pools smaller than MIN_POOL;
- groups with 1–2 people are suppressed (shown as "<3", no share);
- the adverse-impact ratio (four-fifths rule) is computed only for known groups with at
  least MIN_GROUP_FOR_RATIO people in the pool.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from . import storage
from .db import Database

AGE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("under_25", 0, 24),
    ("25_34", 25, 34),
    ("35_44", 35, 44),
    ("45_54", 45, 54),
    ("55_plus", 55, 200),
)
GENDERS = ("female", "male")
LANGUAGES = ("ru", "kk", "en")
MIN_POOL = 5
SUPPRESS_BELOW = 3
MIN_GROUP_FOR_RATIO = 5
FOUR_FIFTHS = 0.8


def age_band(age: int | None) -> str:
    if age is None:
        return "unknown"
    for key, low, high in AGE_BANDS:
        if low <= age <= high:
            return key
    return "unknown"


def _language(profile_json: str | None) -> str:
    """resume_language from the stored profile (parsed here: JSON functions differ by database)."""
    try:
        value = json.loads(profile_json or "{}").get("resume_language")
    except (ValueError, AttributeError):
        return "unknown"
    return value if value in LANGUAGES else "unknown"


def _load(db: Database, resume_ids: Iterable[int]) -> dict[int, tuple[str, str, str]]:
    ids = list(resume_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    # Only "?" placeholders are interpolated; ids are bound.
    rows = db.execute(
        f"""
        SELECT r.id AS resume_id, d.gender, d.age, r.profile_json
        FROM resumes r LEFT JOIN demographics d ON d.resume_id = r.id
        WHERE r.id IN ({placeholders})
        """,  # noqa: S608
        ids,
    )
    out = {}
    for row in rows:  # SEC-02b: demographics and the profile are stored encrypted
        age = storage.unseal("demographics", "age", row["age"])
        out[row["resume_id"]] = (
            storage.unseal("demographics", "gender", row["gender"]) or "unknown",
            age_band(int(age) if age not in (None, "") else None),
            _language(storage.unseal("resumes", "profile_json", row["profile_json"])),
        )
    return out


def _dimension(keys: list[str], pool: list[str], shortlist: list[str]) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for key in keys + ["unknown"]:
        n_pool, n_short = pool.count(key), shortlist.count(key)
        rate = n_short / n_pool if n_pool else None
        groups.append({
            "key": key,
            "pool": n_pool,
            "shortlist": n_short,
            "pool_share": n_pool / len(pool) if pool else 0,
            "shortlist_share": n_short / len(shortlist) if shortlist else 0,
            "suppressed": 0 < n_pool < SUPPRESS_BELOW or 0 < n_short < SUPPRESS_BELOW,
            "rate": rate,
            "ratio": None,
            "flagged": False,
        })

    eligible = [g for g in groups if g["key"] != "unknown" and g["pool"] >= MIN_GROUP_FOR_RATIO]
    best = max((g["rate"] for g in eligible), default=0)
    if len(eligible) >= 2 and best > 0:
        for g in eligible:
            g["ratio"] = round(g["rate"] / best, 2)
            g["flagged"] = g["ratio"] < FOUR_FIFTHS
    known = sum(1 for k in pool if k != "unknown")
    return {"groups": groups, "known_share": known / len(pool) if pool else 0}


def report(db: Database, pool_ids: list[int], shortlist_ids: list[int]) -> dict[str, Any]:
    demo = _load(db, pool_ids)
    unknown = ("unknown", "unknown", "unknown")
    pool = [demo.get(i, unknown) for i in pool_ids]
    shortlist = [demo.get(i, unknown) for i in shortlist_ids]
    if len(pool) < MIN_POOL:
        return {"insufficient": True, "pool": len(pool), "shortlist": len(shortlist)}

    dimensions = {
        "gender": _dimension(list(GENDERS), [p[0] for p in pool], [p[0] for p in shortlist]),
        "age": _dimension([key for key, _, _ in AGE_BANDS], [p[1] for p in pool], [p[1] for p in shortlist]),
        "language": _dimension(list(LANGUAGES), [p[2] for p in pool], [p[2] for p in shortlist]),
    }
    flags = [
        {"dimension": name, "group": g["key"], "ratio": g["ratio"]}
        for name, dim in dimensions.items()
        for g in dim["groups"]
        if g["flagged"]
    ]
    return {"insufficient": False, "pool": len(pool), "shortlist": len(shortlist), "dimensions": dimensions, "flags": flags}
