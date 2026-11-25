# OBS-01 — Application instrumentation

## Why

Until now the only way to know how the site was doing was to read the console window. The
plan's SLIs (availability, page latency, analysis success, queue freshness) and the AI spend
could not be measured, and an unexpected error was seen only if someone watched the log.

## What

| Piece | How |
|---|---|
| `/metrics` (Prometheus text) | `app/metrics.py`. HTTP: `hrscreen_http_requests_total{method,endpoint,status}`, `hrscreen_http_request_duration_seconds{endpoint}`. AI: `hrscreen_llm_requests_total{model,outcome}` (ok / 429 / 402 / 5xx…), `hrscreen_llm_request_duration_seconds{model}`, `hrscreen_llm_tokens_total{kind}` (prompt, cache_hit, completion). Queue: `hrscreen_resumes_finished_total{status,reason}`, `hrscreen_resume_analysis_seconds`, and at scrape time from the database `hrscreen_queue_resumes{status}`, `hrscreen_queue_oldest_pending_seconds`, `hrscreen_database_up`. Plus the standard process/Python metrics |
| Who may read `/metrics` | New access level `INTERNAL` in `app/policy.py`: a request from loopback or a private network (10/8, 172.16/12, 192.168/16, fc00::/7) **without** Cloudflare/proxy headers (`CF-Connecting-IP`, `CF-Ray`, `X-Forwarded-For`, …), or any request with `Authorization: Bearer <METRICS_TOKEN>`. Everyone else — including signed-in admins through hrscreen.date — gets 404 |
| Request ids | Every request gets an id (a sane incoming `X-Request-ID` is kept), returned in `X-Request-ID` and written on every log line; the analysis of a resume logs as `resume-<id>` |
| Logs | `LOG_FORMAT=text` (default, laptop) or `json` (one object per line: time, level, logger, request_id, message, exception). Both pass messages and tracebacks through `scrub()`: e-mail addresses, phone numbers (10+ digits) and the input echoed by validation errors become `[email]`, `[phone]`, `[redacted]` |
| Sentry (optional) | `SENTRY_DSN` turns it on. `before_send` keeps only method and URL path of the request, drops user, cookies, headers, body, query string, local variables, server name; exception texts and breadcrumbs scrubbed; the request id is a tag. Errors reach Sentry through the logging integration (`log.exception`), including the worker's |
| Several processes | Laptop: one process, nothing to do. gunicorn: `PROMETHEUS_MULTIPROC_DIR` (compose sets `/tmp/prometheus`, a tmpfs) — the hooks in `gunicorn_conf.py` empty it at start and drop exited processes. Separate workers: `flask --app app worker --metrics-port 9101` (compose: inside its network, not published) |

Labels never carry user, session or resume ids or URLs: a series per value would overload
Prometheus and tell who did what.

## Not done here (on purpose)

- **OpenTelemetry spans** around AI calls: there is no collector to send them to until OBS-02
  (the cluster stack). The AI request duration histogram covers the question "how slow is the
  provider" now.
- **Token counts as rows of `llm_usage`**: that table counts AI checks per user for the daily
  limit; tokens are in the metrics and in the per-session log line (unchanged). A per-user cost
  report can be added when someone asks for it.
- Dashboards and alerts: OBS-02.

## Dependencies

`prometheus-client` 0.26 and `sentry-sdk` 2.76 (both in `requirements.lock` with hashes).

## Verification

- `tests/test_observability.py` (19): metric names and values after an upload with analysis;
  no names, e-mails, resume text or URLs in the metrics; queue size and age of a waiting resume;
  database down reported as `hrscreen_database_up 0`; `/metrics` is 404 through Cloudflare, for
  forwarded requests and from a public address, 200 with the token and from the private network;
  gunicorn multi-process mode; worker `--metrics-port`; request id kept/replaced and on log lines;
  `scrub()`; **a whole upload-and-analysis run** (with a broken PDF and an error with contacts)
  formatted by both log formatters contains no name, e-mail, phone or resume text; **an
  exception reaches Sentry** (in-memory transport) without cookies, query, contacts.
- `tests/test_access_matrix.py`: the new INTERNAL rule — 200 locally, 404 with a Cloudflare header.
- Threat model: new T-18 (personal data in logs, metrics, error reports).
