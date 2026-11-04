# Progress

## Status
| Phase | Scope | State |
|-------|-------|-------|
| 0 | Skeleton | done |
| 1 | F7 Gemini service | done |
| 2 | F4 parsing + F1 batch upload | done |
| 3 | F3 storage + F6 append | done |
| 4 | F2 ranking UI + CSV | done |
| 5 | B1 bias check + B2 diversity | done |
| 6 | Polish, README, Dockerfile | done |
| 7 | Sprint 2 (SIS 8–9): US7 Excel/PDF export, language diversity, API savings | done |

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

- Phase 4: ranking with competition ranks and tie-preserving Top-N, ranking page (tabs,
  stats, score bars, verdicts, expandable details, mobile cards), CSV export (BOM, `;`,
  formula-injection safe, RFC 5987 filename). Live e2e run against real Gemini: 6 samples
  uploaded, scan rejected, sensible ranking, CSV OK. Found and fixed: per-model circuit
  breaker for 429/503 with instant fallback; retry budget now 10 (configurable).
- Phase 5: B1 lexicon (RU/KK/EN, inclusive-phrase suppression) + Gemini bias check with
  verified quotes and neutral rewrite; B2 shortlist-vs-pool metrics with four-fifths rule
  and small-cell suppression. Live AI check found all 7 issues in the biased sample.

- Phase 6: README, Dockerfile (waitress, non-root, health check, instance volume),
  `.dockerignore`, GitHub Actions CI (pytest), WCAG AA contrast fixes (dark text on the
  brand orange), localized JSON errors, quieter httpx logs. Fixed two job-runner races
  (resume queued as a job finishes; vacancy added mid-scoring) with regression tests.
  Verified on waitress: every page, assets under CSP, ranking with B1/B2 panels, CSV,
  Kazakh UI and the bias-check API.

- Fix (after phase 6): users hit "Gemini overloaded" constantly. Root cause was the
  free-tier **daily** quota (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, 20 per
  model), not overload. `GEMINI_FALLBACK_MODEL` is now a comma-separated chain; a daily-
  quota 429 takes the model out for an hour and switches instantly; when every model is
  out, resumes fail fast with a dedicated "daily limit" message. Verified live: fell
  through two exhausted models to `gemini-3.5-flash-lite` in 15 s with good results.

- Phase 7 (Sprint 2, SIS 8–9): US7 export — Excel (`.xlsx`, summary sheet + one sheet per
  vacancy, numbers stay numeric, formula-injection safe) and a PDF shortlist report with the
  bundled DejaVu Sans font (RU/KK letters); whole-session Excel export from the session page;
  Excel/PDF/CSV buttons on the ranking page. B2 now also compares resume language (RU/KK/EN).
  API savings: cheapest flash-lite model in `.env.example`, compact JSON in the scoring
  prompt, resumes cut at 20 000 chars, 5 retries instead of 10. Exports and diversity make
  no AI calls (covered by a test). 14 new tests, 194 in total.

## Next (backlog)
- F5 Market feedback on the vacancy (explicitly out of scope for now).
- Build and run the Docker image (Docker daemon was not running during phase 6).
- Optional: proactive client-side RPM limiter to avoid free-tier 429s entirely; an ETA on
  the session page; OCR for scanned PDFs if it ever becomes in scope.

## Known issues / notes
- `gemini-2.5-flash` is no longer available to new API keys; default model is `gemini-3.8-flash`
  with `gemini-3.5-flash` as fallback. On the free tier `gemini-3.8-flash` is mostly 429/503,
  so traffic flows to the fallback; throughput is roughly 4–5 requests/minute, i.e. about
  30–40 s per resume (2 calls each). A paid key or `GEMINI_MODEL=gemini-3.5-flash` avoids
  the wasted first attempt.
