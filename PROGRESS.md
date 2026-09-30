# Progress

## Status
| Phase | Scope | State |
|-------|-------|-------|
| 0 | Skeleton | done |
| 1 | F7 Gemini service | done |
| 2 | F4 parsing + F1 batch upload | next |
| 3 | F3 storage + F6 append | todo |
| 4 | F2 ranking UI + CSV | todo |
| 5 | B1 bias check + B2 diversity | todo |
| 6 | Polish, README, Dockerfile | todo |

## Done
- Phase 0: app factory, env-based config, CSRF + security headers, RU/KK i18n, design system
  (light/dark, responsive), error pages, pytest harness, `scripts/smoke_test.py`.

- Phase 1: `GeminiService` (extraction + multi-vacancy scoring, pydantic-validated JSON,
  retry once, backoff with retry hints, shared 429 cooldown, 503 fallback model,
  concurrency cap), `anonymize.scoring_view` + RU/KK/EN demographic scrubber, prompt
  injection fencing. Verified live with `scripts/smoke_test.py --pipeline` (RU and KK).

## Next
- Phase 2: `parsing.py` (PDF/DOCX/TXT with content sniffing, zip-bomb and size limits),
  batch upload endpoint + drag-and-drop UI, synthetic samples.

## Known issues / notes
- `gemini-2.5-flash` is no longer available to new API keys; default model is `gemini-3.8-flash`
  with `gemini-3.5-flash` as fallback. `gemini-3.8-flash` returns 503 under load at times.
