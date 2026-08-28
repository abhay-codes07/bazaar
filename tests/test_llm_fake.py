import pytest

from bazaar.llm import FakeLLM, LLMError, get_llm
from bazaar.llm.base import wrap_untrusted
from bazaar.llm.fake import _extract_data_blocks, handler


def test_unknown_task_raises():
    with pytest.raises(LLMError):
        FakeLLM().complete_json("nope", "", "", {})


def test_handler_registration_and_required_keys():
    @handler("echo_test")
    def _echo(system, user, schema):
        return {"echo": _extract_data_blocks(user).get("msg", "")}

    out = FakeLLM().complete_json("echo_test", "", wrap_untrusted("msg", "hi <b>"), {"required": ["echo"]})
    assert out == {"echo": "hi &lt;b&gt;"}
    with pytest.raises(LLMError):
        FakeLLM().complete_json("echo_test", "", "", {"required": ["missing"]})


def test_factory_default_is_fake():
    assert isinstance(get_llm(), FakeLLM)


def test_openai_backend_requires_key_and_parses_tool_call(monkeypatch):
    from types import SimpleNamespace

    from bazaar.llm.openai_client import OpenAILLM

    with pytest.raises(ValueError):
        OpenAILLM(api_key="")
    llm = OpenAILLM(api_key="sk-test")
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        call = SimpleNamespace(function=SimpleNamespace(arguments='{"name": "Basmati Rice", "category": "staples", "synonyms": [], "confidence": 0.9}'))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])

    monkeypatch.setattr(llm._client.chat.completions, "create", fake_create)
    out = llm.complete_json("normalize_product", "sys", "user", {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
    assert out["name"] == "Basmati Rice"
    assert captured["tool_choice"]["function"]["name"] == "answer_normalize_product" and captured["temperature"] == 0
