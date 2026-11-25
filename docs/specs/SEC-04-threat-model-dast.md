# SEC-04 — Threat model review, prompt-injection tests, DAST

## Why

The plan's threat table (section 8) was written before most protections existed. Two of its
threats were only "planned": prompt injection in resumes (T-05) and an outside check of the
running site. The plan places SEC-04 after CD-02 (a staging environment); staging does not
exist yet, so the scan runs against a throw-away copy started inside CI instead.

## Changes

### Threat model

`docs/security/threat-model.md`: every threat T-01…T-14 checked against the code — what is in
place (with the file), which test or CI check proves it, what is still missing and which task
closes it. Three threats added: T-15 (script or formula in a resume or in the AI's answer),
T-16 (cached copies of pages with personal data, cross-site leaks), T-17 (CSRF).

### Prompt injection (T-05)

- `llm.fence` now neutralises `</resume>` in any spelling: spaces, attributes, upper case,
  full-width (NFKC), `&lt;…&gt;`, look-alike brackets; and role tags (`<system>`, `<assistant>`)
  and chat tokens (`<|im_start|>`) in all untrusted text.
- `LLMService.generate` rejects an answer that repeats a line (40+ characters) of its own
  system prompt, like an invalid answer: retried once, then the resume fails.
- `services/injection.py` `signals(text)`: eight patterns in RU/KK/EN (ignore instructions,
  reveal prompt, new role, score request, note to the AI, chat markup, tag escape, active
  content). The result is stored in the profile (`injection_signals`, set locally, never by
  the AI); the ranking shows a red "Проверьте вручную" badge with an explanation, and the
  worker writes an `injection_suspected` audit event (marked as dangerous in `/admin/audit`).
  The resume is still analysed: the recruiter decides.
- `samples/adversarial/`: 10 hostile resumes (one per attack, see the threat model).
- `scripts/smoke_test.py --adversarial`: the samples against the real model, once per release
  (about 20 requests); a score of 65+ for these weak candidates means an injection worked.

### Headers (T-16)

`Cache-Control: no-store` on every response except static files; `Cross-Origin-Resource-Policy:
same-origin` and `Cross-Origin-Embedder-Policy: require-corp` next to the existing COOP.

### DAST

`.github/workflows/dast.yml` (weekly, on demand, on pull requests that touch the app): installs
the app, generates keys for the run, fills an empty SQLite with `ci/dast_seed.py` (a recruiter,
the example session, one scored resume), starts waitress, signs in with `ci/dast_login.py` and
runs the ZAP 2.17 baseline (image pinned by digest) with the session cookie on every request.
The spider also submits the forms it finds (only the copy can change). `.zap/rules.tsv`: FAIL for protections the app already has, INFO for
facts, IGNORE for "ZAP is out of date". `ci/dast_summary.py` writes the findings table to the
job summary; the HTML/JSON/Markdown reports are kept 30 days. Report-only until 2026-12-01.

## First scan (2026-11-25)

54 URLs, no server error, nothing on a FAIL rule and nothing above "informational". The four
informational findings are recorded and decided in the threat model (DAST findings). Found
while reading the run: the spider submitted a form although the workflow asked it not to (the
baseline's automation plan overrides those options), so the options were removed and the docs
corrected. The same push showed a flaky test (`test_ai_check_is_limited_per_user`, 1 run in
2): ten requests that straddle a window edge are weighted to just under the limit; every
limiter without its own clock now reads `ratelimit.now()`, which the tests pin.

## Not done here

- A run of the scan before the push: ZAP could not be started in the development sandbox.
- The real-model check (`--adversarial`): it spends the AI balance, so it runs only when the
  owner agrees.

## Verification

- `tests/test_prompt_injection.py`: all 10 samples flagged, none of the 20 sample resumes and
  none of 8 near-miss lines ("TOEFL score: 100", "JavaScript: React", "писал system prompt");
  hostile text stays inside exactly one pair of tags in the extraction and the scoring prompts
  (second-order injection too); every spelling of the tag is defused; out-of-range scores,
  foreign vacancies, self-chosen verdicts and extra fields do not get through; an echoed prompt
  is retried and then rejected; through the app: 10 audit events, 10 badges, the clean resume
  not flagged, `<script>` from the model shown as text, no formula in the CSV.
- `tests/test_security.py`: the new headers; static files stay cacheable.
- The seed and sign-in scripts were run against a local production-mode copy (cookie works,
  pages open).
