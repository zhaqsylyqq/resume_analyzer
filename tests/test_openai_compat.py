"""OpenAI-compatible backend (DeepSeek) and the shared 402 fast-fail."""
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.services.gemini import BillingExhausted, GeminiError, GeminiService, GeminiUnavailable, get_gemini
from app.services.openai_compat import DEEPSEEK_EXTRA_BODY, OpenAICompatBackend
from app.services.schemas import CandidateProfile, VacancyInput

from .fakes import PROFILE, FakeClient, api_error, scoring_reply
from .helpers import create_session, resume_text, status, upload


def status_error(code, message="fake", retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(code, headers=headers, request=httpx.Request("POST", "https://api.test/chat/completions"))
    return openai.APIStatusError(message, response=response, body={"error": {"message": message}})


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) or reply is None else json.dumps(reply, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeOpenAI:
    def __init__(self, replies):
        self.chat = SimpleNamespace(completions=FakeCompletions(replies))


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def make_service(replies, extra_body=DEEPSEEK_EXTRA_BODY):
    fake_time = FakeTime()
    backend = OpenAICompatBackend(FakeOpenAI(replies), extra_body=extra_body)
    service = GeminiService(backend, "deepseek-flash", sleep=fake_time.sleep, clock=fake_time.clock)
    return service, backend.client.chat.completions, fake_time


def test_request_uses_json_mode_schema_in_prompt_and_disables_thinking():
    service, completions, _ = make_service([PROFILE])
    profile = service.extract_profile("Резюме ...")
    assert isinstance(profile, CandidateProfile) and profile.skills[0] == "Python"
    call = completions.calls[0]
    assert call["model"] == "deepseek-flash"
    assert call["response_format"] == {"type": "json_object"}
    assert call["temperature"] == 0
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    system, user = call["messages"]
    assert system["role"] == "system" and "json" in system["content"]
    assert '"total_experience_years"' in system["content"]  # the JSON Schema rides in the prompt
    assert user["role"] == "user" and "<resume>" in user["content"]


def test_scoring_works_through_the_compat_backend():
    service, _, _ = make_service([scoring_reply([1, 2], score=90)])
    profile = CandidateProfile.model_validate(PROFILE)
    assessments = service.score(profile, [VacancyInput(id=1, title="A", text="Python"),
                                          VacancyInput(id=2, title="B", text="SQL")])
    assert [a.vacancy_id for a in assessments] == [1, 2]
    assert assessments[0].verdict == "strong"


@pytest.mark.parametrize("blank", ["", "   \n", None])
def test_blank_reply_counts_as_invalid_and_is_retried(blank):
    service, completions, _ = make_service([blank, PROFILE])
    assert service.extract_profile("text").skills[0] == "Python"
    assert len(completions.calls) == 2


def test_rate_limit_is_retried_with_backoff_and_retry_after_hint():
    service, completions, fake_time = make_service([status_error(429, retry_after="7"), PROFILE])
    service.extract_profile("text")
    assert len(completions.calls) == 2
    assert fake_time.sleeps and fake_time.sleeps[0] >= 7


def test_connection_error_is_retried_like_an_overload():
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://api.test/chat/completions"))
    service, completions, _ = make_service([error, PROFILE])
    service.extract_profile("text")
    assert len(completions.calls) == 2


def test_bad_key_is_not_retried():
    service, completions, _ = make_service([status_error(401, "Authentication Fails")])
    with pytest.raises(GeminiError) as info:
        service.extract_profile("text")
    assert not isinstance(info.value, GeminiUnavailable)
    assert "401" in str(info.value)
    assert len(completions.calls) == 1


def test_insufficient_balance_fails_fast_without_further_calls():
    service, completions, fake_time = make_service([status_error(402, "Insufficient Balance"), PROFILE])
    with pytest.raises(BillingExhausted):
        service.extract_profile("text")
    with pytest.raises(BillingExhausted):  # next resume fails instantly, no API call
        service.extract_profile("text")
    assert len(completions.calls) == 1 and fake_time.sleeps == []
    fake_time.now += 61  # the user topped up and pressed "Retry failed"
    assert service.extract_profile("text").skills[0] == "Python"


def test_gemini_402_is_handled_the_same_way():
    service = GeminiService(FakeClient([api_error(402)]), "m", sleep=lambda s: None)
    with pytest.raises(BillingExhausted):
        service.extract_profile("text")


def test_billing_exhausted_marks_resumes_with_a_clear_reason(app, client, csrf):
    replies = [status_error(402, "Insufficient Balance")]
    backend = OpenAICompatBackend(FakeOpenAI(replies), extra_body=DEEPSEEK_EXTRA_BODY)
    app.extensions["gemini"] = GeminiService(backend, "deepseek-flash", sleep=lambda s: None)
    session_id = create_session(client, csrf)
    upload(client, csrf, session_id, [("a.txt", resume_text(1)), ("b.txt", resume_text(2))])
    resumes = status(client, session_id)["resumes"]
    assert [r["status"] for r in resumes] == ["analysis_failed"] * 2
    assert all("Закончился баланс" in r["message"] for r in resumes)
    assert len(backend.client.chat.completions.calls) == 1  # second resume failed without calling


def test_get_gemini_builds_deepseek_service_from_config(app):
    app.extensions.pop("gemini", None)
    app.config.update(LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="test-key", DEEPSEEK_MODEL="deepseek-flash",
                      DEEPSEEK_BASE_URL="https://api.test")
    with app.app_context():
        service = get_gemini()
    assert isinstance(service.backend, OpenAICompatBackend)
    assert service.model == "deepseek-flash"
    assert service.backend.extra_body == DEEPSEEK_EXTRA_BODY
    assert str(service.backend.client.base_url).startswith("https://api.test")


def test_get_gemini_requires_deepseek_key(app):
    app.extensions.pop("gemini", None)
    app.config.update(LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="")
    with app.app_context(), pytest.raises(GeminiError, match="DEEPSEEK_API_KEY"):
        get_gemini()


def test_get_gemini_rejects_unknown_provider(app):
    app.extensions.pop("gemini", None)
    app.config.update(LLM_PROVIDER="mystery")
    with app.app_context(), pytest.raises(GeminiError, match="LLM_PROVIDER"):
        get_gemini()
