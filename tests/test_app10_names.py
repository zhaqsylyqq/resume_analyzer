"""APP-10: the renamed AI layer and setting keep their old names working."""
from __future__ import annotations

import pytest

from app.config import ConfigError, load_settings


def test_old_module_and_class_names():
    from app.services import gemini, llm

    assert gemini.GeminiService is llm.LLMService is llm.GeminiService
    assert gemini.get_gemini is llm.get_llm
    assert gemini.GeminiError is llm.GeminiError and gemini.usage_scope is llm.usage_scope


def test_llm_concurrency_and_its_old_name():
    assert load_settings({}).to_flask()["LLM_CONCURRENCY"] == 2
    old = load_settings({"GEMINI_CONCURRENCY": "3"}).to_flask()
    assert old["LLM_CONCURRENCY"] == old["GEMINI_CONCURRENCY"] == 3
    both = load_settings({"LLM_CONCURRENCY": "4", "GEMINI_CONCURRENCY": "3"}).to_flask()
    assert both["LLM_CONCURRENCY"] == 4  # the new name wins
    with pytest.raises(ConfigError, match="LLM_CONCURRENCY=0 is out of range"):
        load_settings({"LLM_CONCURRENCY": "0"})


def test_the_service_is_shared_under_both_keys(app):
    from app.services.llm import LLMService, get_llm

    app.extensions.pop("gemini", None)
    with app.app_context():
        service = get_llm()
        assert isinstance(service, LLMService) and app.extensions["llm"] is service and get_llm() is service


def test_auth_names_still_import_from_app_auth():
    from app import auth
    from app.auth import limits, passwords

    assert auth.hash_password is passwords.hash_password and auth.client_ip is limits.client_ip
    assert auth.deny.__module__ == "app.policy" and auth.current_settings.__module__ == "app.settings"
