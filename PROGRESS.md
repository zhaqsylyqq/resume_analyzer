# Progress

## Status
| Phase | Scope | State |
|-------|-------|-------|
| 0 | Skeleton | done |
| 1 | F7 Gemini service | done |
| 2 | F4 parsing + F1 batch upload | done |
| 3 | F3 storage + F6 append | done |
| 4 | F2 ranking UI + CSV | next |
| 5 | B1 bias check + B2 diversity | todo |
| 6 | Polish, README, Dockerfile | todo |

## Done
- Phase 0: app factory, env-based config, CSRF + security headers, RU/KK i18n, design system
  (light/dark, responsive), error pages, pytest harness, `scripts/smoke_test.py`.

- Phase 1: `GeminiService` (extraction + multi-vacancy scoring, pydantic-validated JSON,
  retry once, backoff with retry hints, shared 429 cooldown, 503 fallback model,
  concurrency cap), `anonymize.scoring_view` + RU/KK/EN demographic scrubber, prompt
  injection fencing. Verified live with `scripts/smoke_test.py --pipeline` (RU and KK).

- Phase 2: `parsing.py` (PDF/DOCX/TXT, signature sniffing, zip-bomb/page/size limits,
  encoding fallback), batch upload endpoint with duplicate detection, background job
  runner (`jobs.py`), session create form (multi-vacancy), session workspace with
  drag-and-drop + live status polling, 20 synthetic RU/KK samples + 3 bad files
  (`scripts/make_samples.py`). The SQLite schema (`storage.py`) was built here because
  uploads need a session to attach to.

- Phase 3: add-vacancy flow scoring only missing pairs, "continue analysis" after restart,
  flash messages, shared test helpers, tests for append-only behaviour, recovery, history
  and cascade delete.

## Next
- Phase 4: `scoring.py` ranking + Top-N, ranking page per vacancy (score bars, matched/
  missing skills, expandable details), CSV export (UTF-8 BOM, formula-injection safe).

## Known issues / notes
- `gemini-2.5-flash` is no longer available to new API keys; default model is `gemini-3.8-flash`
  with `gemini-3.5-flash` as fallback. `gemini-3.8-flash` returns 503 under load at times.
