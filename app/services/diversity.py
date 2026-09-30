"""B2 — aggregate diversity metrics for a shortlist, never per candidate.

Compares the Top-N shortlist with the whole scored pool by gender and age band, using
only what resumes stated explicitly (everything else is "unknown"). Privacy safeguards:
- no report for pools smaller than MIN_POOL;
- groups with 1–2 people are suppressed (shown as "<3", no share);
- the adverse-impact ratio (four-fifths rule) is computed only for known groups with at
  least MIN_GROUP_FOR_RATIO people in the pool.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

AGE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("under_25", 0, 24),
    ("25_34", 25, 34),
    ("35_44", 35, 44),
    ("45_54", 45, 54),
    ("55_plus", 55, 200),
)
GENDERS = ("female", "male")
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


def _load(db: sqlite3.Connection, resume_ids: Iterable[int]) -> dict[int, tuple[str, str]]:
    ids = list(resume_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(f"SELECT resume_id, gender, age FROM demographics WHERE resume_id IN ({placeholders})", ids)
    return {row["resume_id"]: (row["gender"] or "unknown", age_band(row["age"])) for row in rows}


def _dimension(keys: list[str], pool: list[str], shortlist: list[str]) -> dict[str, Any]:
    groups = []
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


def report(db: sqlite3.Connection, pool_ids: list[int], shortlist_ids: list[int]) -> dict[str, Any]:
    demo = _load(db, pool_ids)
    unknown = ("unknown", "unknown")
    pool = [demo.get(i, unknown) for i in pool_ids]
    shortlist = [demo.get(i, unknown) for i in shortlist_ids]
    if len(pool) < MIN_POOL:
        return {"insufficient": True, "pool": len(pool), "shortlist": len(shortlist)}

    dimensions = {
        "gender": _dimension(list(GENDERS), [g for g, _ in pool], [g for g, _ in shortlist]),
        "age": _dimension([key for key, _, _ in AGE_BANDS], [a for _, a in pool], [a for _, a in shortlist]),
    }
    flags = [
        {"dimension": name, "group": g["key"], "ratio": g["ratio"]}
        for name, dim in dimensions.items()
        for g in dim["groups"]
        if g["flagged"]
    ]
    return {"insufficient": False, "pool": len(pool), "shortlist": len(shortlist), "dimensions": dimensions, "flags": flags}
