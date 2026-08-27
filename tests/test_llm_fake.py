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
