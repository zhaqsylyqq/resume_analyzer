# Spec F7 — Gemini analysis engine

## Goal
Turn raw resume text into a structured candidate profile, and score an anonymized profile
against one or more vacancies — reliably, fairly and cheaply enough for the free tier.

## Interface (`app/services/gemini.py`)
- `GeminiService.extract_profile(resume_text, language) -> CandidateProfile`
- `GeminiService.score(profile, vacancies, language) -> list[FitAssessment]`
  (one call per candidate covering all vacancies not yet scored; result in vacancy order)
- `get_gemini()` returns the app-wide instance built from config.
- Errors: `GeminiError` (non-retryable, e.g. bad key/model), `GeminiUnavailable`
  (retries exhausted), `AnalysisFailed` (invalid output twice).

## Schemas (`app/services/schemas.py`)
- `CandidateProfile`: headline, summary, total years, skills, experience (title,
  organization, duration in years, highlights), education (no years), languages,
  certifications, contacts, resume language, and a separate `demographics` block
  (gender/age/birth year/marital status/nationality/photo — explicit statements only).
- `FitAssessment`: vacancy_id, score 0–100, verdict, summary, matched/missing skills,
  strengths, concerns.

## Fairness
- Scoring sees only `anonymize.scoring_view(profile)`: no name, contacts, demographics or
  calendar dates (durations only); free text is regex-scrubbed for gender, age, birth date,
  marital status, children, nationality and photo mentions (RU/KK/EN).
- The scoring prompt tells the model to ignore any demographic demands in the vacancy.
- Verdict is derived from the score bands (85/65/40) so labels are always consistent.

## Reliability
- JSON mode with `response_schema`, `temperature=0`; replies validated with pydantic.
  Invalid or incomplete (missing vacancy) output → one retry → `AnalysisFailed`.
- 429/5xx → exponential backoff with jitter, honouring `RetryInfo.retryDelay`;
  a 429 starts a shared cooldown for all workers. Two 503s → `GEMINI_FALLBACK_MODEL`.
- A process-wide semaphore limits concurrent requests to `GEMINI_CONCURRENCY`.

## Security
- Resume and vacancy text are untrusted: wrapped in tags, tags inside the text are
  neutralized, and the system prompt forbids following embedded instructions.
- Model and key only from the environment; the key is never logged.

## Acceptance
- Unit tests with a fake client cover parsing, retry, backoff, fallback, non-retryable
  errors, range validation, missing vacancies and absence of demographics in prompts.
- `python scripts/smoke_test.py --pipeline` extracts and scores a synthetic resume.
