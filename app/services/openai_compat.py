"""F7 — OpenAI-compatible chat backend for GeminiService (DeepSeek and similar APIs).

These APIs offer JSON mode (`response_format: json_object`) but not Gemini-style response
schemas, so the JSON Schema of the expected pydantic model is appended to the system
prompt; GeminiService still validates every reply and retries once. A blank reply (DeepSeek
documents this as a known JSON-mode issue) is returned as None and counts as invalid.
HTTP errors become ProviderError so retries, cooldowns and the 402 fast-fail are shared.
"""
from __future__ import annotations

import json
from typing import Any

import openai
from pydantic import BaseModel

from .gemini import ProviderError, record_usage

JSON_INSTRUCTION = (
    "\n\nOutput format: reply with one json object only, without markdown fences or any other "
    "text. It must conform to this JSON Schema:\n{schema}"
)
DEFAULT_MAX_TOKENS = 8192  # a profile or a multi-vacancy assessment is far smaller; stops runaway replies
REQUEST_TIMEOUT = 300.0  # DeepSeek may hold a request under load before inference starts
DEEPSEEK_EXTRA_BODY = {"thinking": {"type": "disabled"}}  # thinking is on by default and costs output tokens


def _retry_after(exc: openai.APIStatusError) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after", ""))
    except ValueError:
        return None


def _error_message(exc: openai.APIStatusError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return exc.message


class OpenAICompatBackend:
    """One chat-completions request in JSON mode; `client` is an `openai.OpenAI` instance."""

    def __init__(
        self, client: Any, *, extra_body: dict[str, Any] | None = None, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> None:
        self.client = client
        self.extra_body = extra_body
        self.max_tokens = max_tokens

    def complete(self, model: str, system: str, user: str, schema: type[BaseModel]) -> str | None:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system + JSON_INSTRUCTION.format(schema=schema_json)},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=self.max_tokens,
                extra_body=self.extra_body,
            )
        except openai.APIStatusError as exc:
            raise ProviderError(exc.status_code, _error_message(exc), _retry_after(exc)) from exc
        except openai.APIConnectionError as exc:  # includes timeouts; treated like an overload
            raise ProviderError(503, f"connection failed: {type(exc).__name__}") from exc
        _record(getattr(response, "usage", None))
        if not response.choices:
            return None
        content = (response.choices[0].message.content or "").strip()
        return content or None


def _record(usage: Any) -> None:
    """Token counts from an OpenAI-style `usage` object (DeepSeek adds prompt_cache_hit_tokens)."""
    cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if cache_hit is None:
        cache_hit = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0)
    record_usage(getattr(usage, "prompt_tokens", 0), cache_hit, getattr(usage, "completion_tokens", 0))


def deepseek_backend(api_key: str, base_url: str) -> OpenAICompatBackend:
    # SDK retries are off: GeminiService owns retries, backoff and the 402 fast-fail.
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=REQUEST_TIMEOUT, max_retries=0)
    return OpenAICompatBackend(client, extra_body=DEEPSEEK_EXTRA_BODY)
