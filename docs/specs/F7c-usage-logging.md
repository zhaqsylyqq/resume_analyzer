# F7c — Token usage per batch and DeepSeek in `.env.example`

## Goal
Let the owner see what a screening batch cost without opening the provider dashboard, and
document the DeepSeek setup in the committed settings template.

## Behaviour
- Each backend reads the token counts the API already returns with every reply:
  - DeepSeek / OpenAI-compatible: `usage.prompt_tokens`, `usage.completion_tokens` and the
    cache-hit part of the prompt (`usage.prompt_cache_hit_tokens`, or
    `usage.prompt_tokens_details.cached_tokens` on other OpenAI-compatible APIs);
  - Gemini: `usage_metadata.prompt_token_count`, `candidates_token_count`,
    `cached_content_token_count`.
  Missing fields count as 0; a reply without usage data still counts as one request.
- The counts go into a `UsageCounter` bound to the current thread with
  `usage_scope(counter)`. The job runner creates one counter per batch (one `_run` call)
  and binds it around every resume, including resumes analyzed in worker threads.
  Requests that fail (HTTP errors) report no usage and are not counted.
- When a batch finishes and made at least one request, the runner logs one INFO line:
  `Session 12 batch done: 20 resumes, 40 requests, prompt 55210 tokens (cache hit 33100),
  completion 17480 tokens`. Nothing else is logged: no resume text, prompts or keys.
- `.env.example` starts with a DeepSeek block (the owner's provider) and keeps the Gemini
  settings as a commented alternative. Shared settings stay uncommented.

## Edge cases
- Calls outside a batch (bias check, smoke test) have no bound counter; usage is ignored.
- A batch where every resume was already analyzed makes no request and logs nothing.
- Counters are thread-safe: two worker threads add to the same counter.

## Acceptance criteria
- A finished batch logs exactly one usage line with the summed numbers (test with a fake
  DeepSeek client that returns usage).
- Gemini usage metadata is read the same way (unit test).
- `.env.example` documents both providers.
