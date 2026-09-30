# Spec B2 — Aggregate diversity metrics per shortlist

## Goal
Show whether the Top-N shortlist under-represents a group compared with everyone scored,
without ever exposing or using a single candidate's demographics.

## Data
- Only what resumes state explicitly (extraction prompt forbids inferring gender from
  names). Stored in the separate `demographics` table as gender and age; everything else
  is `unknown`. Never passed to scoring, never shown per candidate, never exported.

## Metrics (`app/services/diversity.py`)
- Dimensions: gender (female, male, unknown) and age band (<25, 25–34, 35–44, 45–54, 55+,
  unknown); pool = all scored candidates for the vacancy, shortlist = current Top-N.
- Per group: pool/shortlist counts and shares, selection rate = shortlist / pool.
- Adverse-impact ratio (four-fifths rule): group rate ÷ best group rate, for known groups
  with ≥ 5 people in the pool and at least two such groups; flagged below 0.8.
- Share of the pool with a known value is shown for transparency.

## Privacy safeguards
- No report when the pool has fewer than 5 candidates.
- Groups with 1–2 people in pool or shortlist are suppressed ("<3", no bars, no share).
- Unknown is never flagged.

## UI
Ranking sidebar card: shortlist-vs-pool bars per group, known-share note, warnings for
flagged groups, method note, hint when the shortlist equals the whole pool.

## Acceptance
Tests for age banding, minimum pool, four-fifths flagging, balanced shortlist, suppression,
unknown handling, and rendering without per-candidate data.
