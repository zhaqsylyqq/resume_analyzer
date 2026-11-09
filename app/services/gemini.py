"""F7 — Gemini analysis engine: resume extraction and fit scoring with validated JSON.

Reliability rules:
- Every call asks for JSON with a response schema; the reply is validated with pydantic.
  Invalid output is retried once, then AnalysisFailed is raised (caller marks the resume
  "analysis_failed" and continues the batch).
- Rate limits (429) and overload (503) put that model on a shared cooldown (circuit
  breaker) for the backoff delay, honouring the server's retry hint. Quotas are per model,
  so while a model cools down every worker goes straight to the next model in the
  fallback chain; only when all models are cooling down do workers wait. Other 5xx errors
  back off exponentially with jitter.
- A 429 caused by a *daily* quota (free tier: a few dozen requests per model per day)
  takes that model out for DAILY_QUOTA_BLOCK seconds. When every model is out for that
  reason, QuotaExhausted is raised immediately instead of retrying for minutes.
- A process-wide semaphore caps concurrent requests.
- HTTP 402 (prepaid balance used up) raises BillingExhausted at once and keeps failing fast
  for BILLING_BLOCK seconds, so a batch does not keep calling an empty account.

The provider is pluggable: GeminiBackend wraps the google-genai client, and
openai_compat.OpenAICompatBackend talks to OpenAI-compatible APIs such as DeepSeek
(LLM_PROVIDER selects one). Retries, cooldowns and validation are shared.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any, Callable, Sequence, TypeVar

from flask import current_app
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from .anonymize import scoring_view
from .schemas import CandidateProfile, FitAssessment, ScoringResult, VacancyInput

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
LANGUAGE_NAMES = {"ru": "Russian", "kk": "Kazakh"}
MAX_VACANCY_CHARS = 8_000
RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})
DAILY_QUOTA_BLOCK = 3600.0  # re-probe a model with an exhausted daily quota once an hour
MAX_MODEL_WAIT = 300.0  # longer than any per-minute cooldown; beyond this, give up fast
BILLING_BLOCK = 60.0  # after a 402, fail without calling until the user had time to top up

T = TypeVar("T", bound=BaseModel)


class GeminiError(Exception):
    """Non-retryable API failure (bad key, bad request, unknown model)."""


class GeminiUnavailable(GeminiError):
    """Retries exhausted on rate limits or overload."""


class QuotaExhausted(GeminiUnavailable):
    """Every configured model has used up its daily quota."""


class BillingExhausted(GeminiUnavailable):
    """The provider account has no prepaid balance left (HTTP 402)."""


class ProviderError(Exception):
    """An API failure from any provider, normalized for the shared retry logic."""

    def __init__(self, code: int, message: str, retry_hint: float | None = None, daily_quota: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retry_hint = retry_hint
        self.daily_quota = daily_quota


def parse_models(value: str | Sequence[str] | None) -> list[str]:
    """Accept 'a, b' or ['a', 'b']; drop blanks and duplicates, keep order."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    return list(dict.fromkeys(m.strip() for m in items if m and m.strip()))


def _is_daily_quota(exc: errors.APIError) -> bool:
    details = exc.details if isinstance(exc.details, dict) else {}
    for item in (details.get("error") or {}).get("details") or []:
        for violation in (item.get("violations") or []) if isinstance(item, dict) else []:
            if "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


class AnalysisFailed(GeminiError):
    """The model returned invalid structured output twice."""


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8"))


def fence(text: str, *tags: str) -> str:
    """Stop untrusted text from closing or opening our delimiter tags."""
    for tag in tags:
        text = re.sub(rf"</?\s*{tag}\s*>", f"[{tag}]", text, flags=re.IGNORECASE)
    return text


def _retry_hint_seconds(exc: errors.APIError) -> float | None:
    details = exc.details if isinstance(exc.details, dict) else {}
    for item in (details.get("error") or {}).get("details") or []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


def verdict_for(score: int) -> str:
    if score >= 85:
        return "strong"
    if score >= 65:
        return "good"
    if score >= 40:
        return "partial"
    return "weak"


class GeminiBackend:
    """One Gemini request through the google-genai client; errors become ProviderError."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def complete(self, model: str, system: str, user: str, schema: type[BaseModel]) -> str | None:
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as exc:
            raise ProviderError(
                exc.code, str(exc.message), _retry_hint_seconds(exc), exc.code == 429 and _is_daily_quota(exc)
            ) from exc
        return response.text


class GeminiService:
    """Analysis engine. `client` is a google-genai client or any backend with `complete()`."""

    def __init__(
        self,
        client: Any,
        model: str,
        fallback_models: str | Sequence[str] | None = None,
        *,
        concurrency: int = 2,
        max_retries: int = 5,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not model:
            raise GeminiError("AI model is not configured (GEMINI_MODEL / DEEPSEEK_MODEL)")
        self.client = client
        self.backend = client if callable(getattr(client, "complete", None)) else GeminiBackend(client)
        self.model = model
        self.fallback_models = [m for m in parse_models(fallback_models) if m != model]
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._clock = clock
        self._slots = threading.BoundedSemaphore(max(1, concurrency))
        self._models = [model, *self.fallback_models]
        self._blocked_until = {m: 0.0 for m in self._models}
        self._billing_blocked_until = 0.0
        self._breaker_lock = threading.Lock()

    # Public API -----------------------------------------------------------------------

    def extract_profile(self, resume_text: str, language: str = "ru") -> CandidateProfile:
        system = load_prompt("extract_system").substitute(
            today=date.today().isoformat(), language=LANGUAGE_NAMES.get(language, "Russian")
        )
        user = load_prompt("extract_user").substitute(resume=fence(resume_text, "resume"))
        return self.generate(system, user, CandidateProfile)

    def score(
        self, profile: CandidateProfile, vacancies: list[VacancyInput], language: str = "ru"
    ) -> list[FitAssessment]:
        """Score one candidate against several vacancies in a single call."""
        if not vacancies:
            return []
        wanted = {v.id for v in vacancies}
        # Compact JSON (no indentation) keeps the prompt short: fewer input tokens per request.
        candidate = json.dumps(scoring_view(profile), ensure_ascii=False, separators=(",", ":"))
        vacancy_json = json.dumps(
            [{"id": v.id, "title": v.title, "text": v.text[:MAX_VACANCY_CHARS]} for v in vacancies],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system = load_prompt("score_system").substitute(language=LANGUAGE_NAMES.get(language, "Russian"))
        user = load_prompt("score_user").substitute(
            vacancy_ids=", ".join(str(i) for i in sorted(wanted)),
            candidate=fence(candidate, "candidate", "vacancies"),
            vacancies=fence(vacancy_json, "candidate", "vacancies"),
        )

        def covers_all(result: ScoringResult) -> bool:
            return wanted <= {a.vacancy_id for a in result.assessments}

        result = self.generate(system, user, ScoringResult, check=covers_all)
        by_id = {a.vacancy_id: a for a in result.assessments if a.vacancy_id in wanted}
        for assessment in by_id.values():
            assessment.verdict = verdict_for(assessment.score)  # keep verdict consistent with score
        return [by_id[v.id] for v in vacancies]

    # Internals ------------------------------------------------------------------------

    def generate(
        self,
        system: str,
        user: str,
        schema: type[T],
        check: Callable[[T], bool] | None = None,
    ) -> T:
        for attempt in (1, 2):
            text = self._call(system, user, schema)
            try:
                parsed = schema.model_validate_json(text or "")
            except ValidationError as exc:
                log.warning("Invalid %s from the AI (attempt %d): %s", schema.__name__, attempt, exc.error_count())
                continue
            if check is None or check(parsed):
                return parsed
            log.warning("Incomplete %s from the AI (attempt %d)", schema.__name__, attempt)
        raise AnalysisFailed(f"invalid {schema.__name__} after retry")

    def _call(self, system: str, user: str, schema: type[BaseModel]) -> str | None:
        for attempt in range(self.max_retries + 1):
            if self._clock() < self._billing_blocked_until:
                raise BillingExhausted("AI provider balance is exhausted (HTTP 402)")
            model, wait = self._pick_model()
            if wait > MAX_MODEL_WAIT:
                raise QuotaExhausted("daily quota exhausted for every configured model")
            if wait > 0:
                log.info("All AI models cooling down, waiting %.1fs", wait)
                self._sleep(wait)
            try:
                with self._slots:
                    return self.backend.complete(model, system, user, schema)
            except ProviderError as exc:
                if exc.code == 402:
                    with self._breaker_lock:
                        self._billing_blocked_until = self._clock() + BILLING_BLOCK
                    log.warning("AI provider balance exhausted (402); failing fast for %.0fs", BILLING_BLOCK)
                    raise BillingExhausted("AI provider balance is exhausted (HTTP 402)") from exc
                if exc.code not in RETRYABLE_CODES:
                    raise GeminiError(f"AI API error {exc.code}: {exc.message}") from exc
                if exc.daily_quota:
                    self._block(model, DAILY_QUOTA_BLOCK)
                    log.warning("Daily quota exhausted for %s; switching models", model)
                    continue
                if attempt == self.max_retries:
                    raise GeminiUnavailable(f"AI unavailable after retries ({exc.code})") from exc
                delay = self._backoff(attempt, exc.retry_hint)
                if exc.code in (429, 503):
                    self._block(model, delay)
                    log.info("AI %s on %s, cooling it down for %.1fs (attempt %d)", exc.code, model, delay, attempt + 1)
                else:
                    log.info("AI %s on %s, retrying in %.1fs (attempt %d)", exc.code, model, delay, attempt + 1)
                    self._sleep(delay)
        if self._pick_model()[1] > MAX_MODEL_WAIT:
            raise QuotaExhausted("daily quota exhausted for every configured model")
        raise GeminiUnavailable("AI unavailable after retries")

    def _backoff(self, attempt: int, hint: float | None) -> float:
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        if hint is not None:
            delay = max(delay, min(hint, self.max_delay))
        return delay + random.uniform(0, delay * 0.25)

    def _block(self, model: str, seconds: float) -> None:
        with self._breaker_lock:
            self._blocked_until[model] = max(self._blocked_until[model], self._clock() + seconds)

    def _pick_model(self) -> tuple[str, float]:
        """First model not cooling down (main preferred), else the one that recovers first."""
        with self._breaker_lock:
            now = self._clock()
            for model in self._models:
                if self._blocked_until[model] <= now:
                    return model, 0.0
            model = min(self._models, key=self._blocked_until.__getitem__)
            return model, self._blocked_until[model] - now


def get_gemini() -> GeminiService:
    """App-wide service instance; tests replace app.extensions['gemini'] with a fake client."""
    service = current_app.extensions.get("gemini")
    if service is None:
        cfg = current_app.config
        provider = cfg.get("LLM_PROVIDER", "gemini")
        if provider == "deepseek":
            from .openai_compat import deepseek_backend

            if not cfg.get("DEEPSEEK_API_KEY"):
                raise GeminiError("DEEPSEEK_API_KEY is not configured")
            client: Any = deepseek_backend(cfg["DEEPSEEK_API_KEY"], cfg["DEEPSEEK_BASE_URL"])
            model, fallback = cfg["DEEPSEEK_MODEL"], cfg.get("DEEPSEEK_FALLBACK_MODEL")
        elif provider == "gemini":
            if not cfg.get("GEMINI_API_KEY"):
                raise GeminiError("GEMINI_API_KEY is not configured")
            client = genai.Client(api_key=cfg["GEMINI_API_KEY"])
            model, fallback = cfg["GEMINI_MODEL"], cfg.get("GEMINI_FALLBACK_MODEL")
        else:
            raise GeminiError(f"Unknown LLM_PROVIDER {provider!r} (use 'gemini' or 'deepseek')")
        service = GeminiService(
            client,
            model,
            fallback,
            concurrency=cfg["GEMINI_CONCURRENCY"],
            max_retries=cfg["GEMINI_MAX_RETRIES"],
        )
        current_app.extensions["gemini"] = service
    return service
