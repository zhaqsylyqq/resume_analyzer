"""OBS-01: Prometheus metrics of the web app, the analysis queue and the AI calls.

Labels are low-cardinality on purpose: the endpoint *name* (not the URL), the HTTP status, the
model name, an outcome word. Never a user, session or resume id: those would create a series
per value and say who did what.

Processes: counters live in the process that counts. The laptop runs one process (waitress,
analysis threads inside), so /metrics shows everything. Under gunicorn set
PROMETHEUS_MULTIPROC_DIR (an empty folder) and every web process writes its numbers there;
separate `flask --app app worker` processes serve their own numbers with `--metrics-port`.
Queue sizes are read from the database at scrape time, so any copy shows the same queue.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

CONTENT_TYPE = CONTENT_TYPE_LATEST

# Multi-process mode writes a file per metric as soon as the metric is created (at import, in
# the gunicorn master too, before any hook runs): the folder must exist by then (INF-05 finding).
if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
    os.makedirs(os.environ["PROMETHEUS_MULTIPROC_DIR"], exist_ok=True)

HTTP_REQUESTS = Counter(
    "hrscreen_http_requests_total", "HTTP requests answered, by route name and status code.",
    ["method", "endpoint", "status"])
HTTP_DURATION = Histogram(
    "hrscreen_http_request_duration_seconds", "Time to answer an HTTP request, by route name.",
    ["endpoint"], buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))
LLM_REQUESTS = Counter(
    "hrscreen_llm_requests_total", "Requests to the AI provider, by model and outcome "
    "(ok, or the HTTP code of a failure: 429 rate limit, 402 balance, 5xx provider error).",
    ["model", "outcome"])
LLM_DURATION = Histogram(
    "hrscreen_llm_request_duration_seconds", "Time of one AI request (successful or not).",
    ["model"], buckets=(0.5, 1, 2, 5, 10, 20, 40, 80, 160, 300))
LLM_TOKENS = Counter(
    "hrscreen_llm_tokens_total", "AI tokens used: prompt (cache_hit is the cached part of it) and completion.",
    ["kind"])
RESUMES_FINISHED = Counter(
    "hrscreen_resumes_finished_total", "Resumes that left the queue: done, analysis_failed or "
    "parse_failed, with the error code of a failure.", ["status", "reason"])
ANALYSIS_DURATION = Histogram(
    "hrscreen_resume_analysis_seconds", "Time to analyze one resume (profile and scores).",
    buckets=(1, 2.5, 5, 10, 20, 40, 80, 160, 300, 600))

QUEUE_STATUSES = ("pending", "processing")


def outcome_of(code: int | None) -> str:
    return "ok" if code is None else str(code)


def record_tokens(prompt: int, cache_hit: int, completion: int) -> None:
    for kind, value in (("prompt", prompt), ("cache_hit", cache_hit), ("completion", completion)):
        if value:
            LLM_TOKENS.labels(kind).inc(value)


class QueueCollector(Collector):
    """Queue sizes and the age of the oldest waiting resume, read from the database now."""

    def __init__(self, database: str) -> None:
        self.database = database

    def collect(self) -> Iterator[Any]:
        from .services import storage

        up = GaugeMetricFamily("hrscreen_database_up", "1 when the database answered this scrape.")
        sizes = GaugeMetricFamily("hrscreen_queue_resumes", "Resumes waiting or being analyzed now.",
                                  labels=["status"])
        oldest = GaugeMetricFamily("hrscreen_queue_oldest_pending_seconds",
                                   "Age of the oldest resume still waiting (0 when none).")
        try:
            db = storage.connect(self.database)
            try:
                counts = {r[0]: int(r[1]) for r in db.execute(
                    "SELECT status, COUNT(*) FROM resumes WHERE status IN (?, ?) GROUP BY status", QUEUE_STATUSES)}
                first = db.execute("SELECT MIN(created_at) FROM resumes WHERE status = ?", ("pending",)).fetchone()[0]
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - a scrape must answer even when the database does not
            up.add_metric([], 0)
            yield up
            return
        up.add_metric([], 1)
        for status in QUEUE_STATUSES:
            sizes.add_metric([status], counts.get(status, 0))
        oldest.add_metric([], _age_seconds(first))
        yield from (up, sizes, oldest)


def _age_seconds(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        moment = datetime.fromisoformat(str(created_at))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())


def process_registry() -> CollectorRegistry:
    """This process's counters, or every gunicorn process's with PROMETHEUS_MULTIPROC_DIR."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY


def exposition(database: str | None) -> bytes:
    parts: Iterable[bytes] = [generate_latest(process_registry())]
    if database:
        queue = CollectorRegistry(auto_describe=False)
        queue.register(QueueCollector(database))
        parts = [*parts, generate_latest(queue)]
    return b"".join(parts)
