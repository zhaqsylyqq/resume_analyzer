# Spec B1 — Bias-language check for vacancy texts

## Goal
Warn HR when a vacancy contains discriminatory or exclusionary wording, explain why, and
offer a neutral alternative — before it narrows the candidate pool.

## Detection (`app/services/bias.py`)
1. **Lexicon (always, instant):** RU/KK/EN regex rules in categories age, gender,
   appearance, family, ethnicity, religion, health; severity `high` (explicit demand) or
   `medium` (coded/subtle). Inclusive phrasing ("мужчины и женщины", "ер мен әйел",
   "независимо от пола", "men and women") suppresses gender/age hits inside it.
   Legitimate requirements (experience, language levels) are not flagged.
2. **Gemini (on demand):** structured `BiasReport` with exact quoted phrases, category,
   severity, explanation, suggestion and a full neutral rewrite, in the report language.
   Quotes not literally present in the text are dropped. On any Gemini error the
   lexicon report is returned with `ai_checked = false`.
- Findings are merged per overlapping span (AI > rule, high > medium, longer > shorter)
  and sorted by position.

## Storage and triggers
- A lexicon report is stored in `vacancies.bias_json` when a session or vacancy is
  created; it carries a text hash and is recomputed if the text changes.
- `POST /sessions/<id>/vacancies/<vid>/bias` runs the AI check and stores it.
- `POST /vacancies/bias-check` (JSON, CSRF) checks draft text on the new-session form.

## UI
- New-session form: "Check wording" per vacancy with inline findings.
- Session page: badge with the number of findings per vacancy.
- Ranking sidebar: findings with category, explanation and suggestion; vacancy text with
  escaped `<mark>` highlights; copyable neutral rewrite; "Deep check with AI" button.

## Acceptance
Tests: every category in RU/KK/EN, no false positives on inclusive/legitimate text, the
biased sample is flagged in 5+ categories and both clean samples in none, AI merge and
hallucination filtering, AI failure fallback, HTML escaping, routes and CSRF.
`python scripts/smoke_test.py --bias` verifies the live AI path.
