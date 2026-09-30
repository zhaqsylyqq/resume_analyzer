"""F7 — Gemini analysis engine: resume extraction and fit scoring with validated JSON.

Reliability rules:
- Every call asks for JSON with a response schema; the reply is validated with pydantic.
  Invalid output is retried once, then AnalysisFailed is raised (caller marks the resume
  "analysis_failed" and continues the batch).
- Rate limits (429) and overload (5xx) are retried with exponential backoff and jitter,
  honouring the server's retry hint. On repeated 503 the fallback model is used.
- A process-wide semaphore caps concurrent requests; a shared cooldown pauses all workers
  after a 429 so the batch does not hammer the free tier.
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
from typing import Any, Callable, TypeVar

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

T = TypeVar("T", bound=BaseModel)


class GeminiError(Exception):
    """Non-retryable API failure (bad key, bad request, unknown model)."""


class GeminiUnavailable(GeminiError):
    """Retries exhausted on rate limits or overload."""


class AnalysisFailed(GeminiError):
    """The model returned invalid structured output twice."""


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8"))


def _fence(text: str, *tags: str) -> str:
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


class GeminiService:
    def __init__(
        self,
        client: Any,
        model: str,
        fallback_model: str | None = None,
        *,
        concurrency: int = 2,
        max_retries: int = 5,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not model:
            raise GeminiError("GEMINI_MODEL is not configured")
        self.client = client
        self.model = model
        self.fallback_model = fallback_model or None
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._slots = threading.BoundedSemaphore(max(1, concurrency))
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0

    # Public API -----------------------------------------------------------------------

    def extract_profile(self, resume_text: str, language: str = "ru") -> CandidateProfile:
        system = load_prompt("extract_system").substitute(
            today=date.today().isoformat(), language=LANGUAGE_NAMES.get(language, "Russian")
        )
        user = load_prompt("extract_user").substitute(resume=_fence(resume_text, "resume"))
        return self._generate(system, user, CandidateProfile)

    def score(
        self, profile: CandidateProfile, vacancies: list[VacancyInput], language: str = "ru"
    ) -> list[FitAssessment]:
        """Score one candidate against several vacancies in a single call."""
        if not vacancies:
            return []
        wanted = {v.id for v in vacancies}
        candidate = json.dumps(scoring_view(profile), ensure_ascii=False, indent=1)
        vacancy_json = json.dumps(
            [{"id": v.id, "title": v.title, "text": v.text[:MAX_VACANCY_CHARS]} for v in vacancies],
            ensure_ascii=False,
            indent=1,
        )
        system = load_prompt("score_system").substitute(language=LANGUAGE_NAMES.get(language, "Russian"))
        user = load_prompt("score_user").substitute(
            vacancy_ids=", ".join(str(i) for i in sorted(wanted)),
            candidate=_fence(candidate, "candidate", "vacancies"),
            vacancies=_fence(vacancy_json, "candidate", "vacancies"),
        )

        def covers_all(result: ScoringResult) -> bool:
            return wanted <= {a.vacancy_id for a in result.assessments}

        result = self._generate(system, user, ScoringResult, check=covers_all)
        by_id = {a.vacancy_id: a for a in result.assessments if a.vacancy_id in wanted}
        for assessment in by_id.values():
            assessment.verdict = verdict_for(assessment.score)  # keep verdict consistent with score
        return [by_id[v.id] for v in vacancies]

    # Internals ------------------------------------------------------------------------

    def _generate(
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
                log.warning("Invalid %s from Gemini (attempt %d): %s", schema.__name__, attempt, exc.error_count())
                continue
            if check is None or check(parsed):
                return parsed
            log.warning("Incomplete %s from Gemini (attempt %d)", schema.__name__, attempt)
        raise AnalysisFailed(f"invalid {schema.__name__} after retry")

    def _call(self, system: str, user: str, schema: type[BaseModel]) -> str | None:
        model = self.model
        overloads = 0
        for attempt in range(self.max_retries + 1):
            self._wait_for_cooldown()
            try:
                with self._slots:
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
                return response.text
            except errors.APIError as exc:
                if exc.code not in RETRYABLE_CODES:
                    raise GeminiError(f"Gemini API error {exc.code}: {exc.message}") from exc
                if attempt == self.max_retries:
                    raise GeminiUnavailable(f"Gemini unavailable after retries ({exc.code})") from exc
                delay = self._backoff(attempt, _retry_hint_seconds(exc))
                if exc.code == 429:
                    self._start_cooldown(delay)
                elif exc.code == 503:
                    overloads += 1
                    if overloads >= 2 and self.fallback_model and model != self.fallback_model:
                        log.info("Model %s overloaded, switching to fallback %s", model, self.fallback_model)
                        model = self.fallback_model
                log.info("Gemini %s, retrying in %.1fs (attempt %d)", exc.code, delay, attempt + 1)
                self._sleep(delay)
        raise GeminiUnavailable("unreachable")  # pragma: no cover

    def _backoff(self, attempt: int, hint: float | None) -> float:
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        if hint is not None:
            delay = max(delay, min(hint, self.max_delay))
        return delay + random.uniform(0, delay * 0.25)

    def _start_cooldown(self, seconds: float) -> None:
        with self._cooldown_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    def _wait_for_cooldown(self) -> None:
        remaining = self._cooldown_until - time.monotonic()
        if remaining > 0:
            self._sleep(remaining)


def get_gemini() -> GeminiService:
    """App-wide service instance; tests replace app.extensions['gemini'] with a fake client."""
    service = current_app.extensions.get("gemini")
    if service is None:
        cfg = current_app.config
        if not cfg.get("GEMINI_API_KEY"):
            raise GeminiError("GEMINI_API_KEY is not configured")
        service = GeminiService(
            genai.Client(api_key=cfg["GEMINI_API_KEY"]),
            cfg["GEMINI_MODEL"],
            cfg.get("GEMINI_FALLBACK_MODEL"),
            concurrency=cfg["GEMINI_CONCURRENCY"],
            max_retries=cfg["GEMINI_MAX_RETRIES"],
        )
        current_app.extensions["gemini"] = service
    return service
