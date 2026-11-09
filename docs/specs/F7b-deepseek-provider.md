# F7b — DeepSeek provider and prepaid-balance handling

## Goal
Run the analysis engine on DeepSeek, a cheap OpenAI-compatible API, as an alternative to
Gemini. The Gemini paid tier asked for a $30 first prepayment; DeepSeek works with about $5.
Also handle an empty prepaid balance (HTTP 402) cleanly for both providers.

## Behaviour
- `LLM_PROVIDER` selects the provider: `gemini` (default, unchanged) or `deepseek`.
- DeepSeek settings: `DEEPSEEK_API_KEY` (required), `DEEPSEEK_MODEL` (default
  `deepseek-flash`), `DEEPSEEK_FALLBACK_MODEL` (optional chain), `DEEPSEEK_BASE_URL`
  (default `https://api.deepseek.com`). `GEMINI_CONCURRENCY` and `GEMINI_MAX_RETRIES`
  apply to either provider.
- `GeminiService` keeps the shared logic: validation, one retry on invalid output, backoff,
  cooldowns, fallback chain, concurrency cap. The request itself goes through a backend:
  `GeminiBackend` (google-genai) or `OpenAICompatBackend` (openai SDK).
- DeepSeek requests:
  - use JSON mode (`response_format: {"type": "json_object"}`) with `temperature = 0`;
  - DeepSeek has no response schemas, so the JSON Schema of the expected pydantic model is
    appended to the system prompt, which also contains the word "json" as the API requires;
  - set `max_tokens` to 8192;
  - turn thinking mode off (`thinking: {"type": "disabled"}`): it is on by default and
    spends output tokens;
  - turn the SDK's own retries off and use a 300 s timeout.
- A blank DeepSeek reply (a documented JSON-mode issue) counts as invalid output and is
  retried once.
- Errors from either provider become `ProviderError(code, …)`:
  - 429, 500, 502, 503, 504 and connection errors are retried with backoff, honouring
    `Retry-After`;
  - other 4xx errors fail at once (`api_error`);
  - **402** raises `BillingExhausted` immediately. For the next 60 s every call fails
    without a request, so a batch stops spending calls on an empty account. Resumes get the
    code `billing_exhausted`, and the UI shows an RU/KK message asking the user to top up
    and press "Retry failed".
- `scripts/smoke_test.py` honours `LLM_PROVIDER` for the ping, `--pipeline`, `--bias` and
  `--list-models` modes.

## Cost
`deepseek-flash` costs $0.15 / $0.30 per 1M input tokens (cache miss) and $0.60 / $1.20 per
1M output tokens, off-peak / peak. Peak is 01:00–04:00 and 06:00–10:00 UTC on weekdays. At
about 5 000 input and 1 100 output tokens per resume (two calls), that is roughly
$0.0015–0.003 per resume. The schema in the system prompt adds input tokens, but the prefix
is the same on every call, so DeepSeek's prompt cache bills most of it at the cache-hit
price.

The live batch on 2026-11-10 measured less than that estimate. 20 resumes × 2 vacancies
used 55k prompt tokens (60% cache hit) and 17.5k completion tokens, about
$0.0007–0.0014 per resume. $5 therefore covers roughly 3 500–7 000 resumes.

## Acceptance criteria
- With `LLM_PROVIDER=deepseek` and a key, uploading resumes produces profiles and scores.
- Tests, with the provider mocked, cover:
  - request shape;
  - blank-reply retry;
  - 429 with a `Retry-After` hint;
  - connection error;
  - 401 not retried;
  - 402 fast-fail on both providers, end to end in the job runner;
  - provider selection and a missing key.
- Gemini behaviour and its existing tests are unchanged.
