from typing import Any

from bazaar.llm.base import LLM
from bazaar.llm.cache import CachedLLM


class Counting(LLM):
    name = "counting"
    _model = "m"

    def __init__(self):
        self.calls = 0

    def complete_json(self, task, system, user, schema) -> dict[str, Any]:
        self.calls += 1
        return {"n": self.calls, "echo": user}


def test_cache_serves_repeat_calls_and_persists(tmp_path):
    inner = Counting()
    c = CachedLLM(inner, tmp_path / "c.sqlite")
    a = c.complete_json("t", "sys", "hello", {"type": "object"})
    b = c.complete_json("t", "sys", "hello", {"type": "object"})
    assert a == b and inner.calls == 1 and c.stats() == {"hits": 1, "misses": 1, "stored": 1}
    c.complete_json("t", "sys", "other", {"type": "object"})
    assert inner.calls == 2
    # a fresh process sees the same cache
    inner2 = Counting()
    c2 = CachedLLM(inner2, tmp_path / "c.sqlite")
    assert c2.complete_json("t", "sys", "hello", {"type": "object"})["n"] == 1 and inner2.calls == 0


def test_openai_task_routing(monkeypatch):
    from types import SimpleNamespace

    from bazaar.llm.openai_client import OpenAILLM

    llm = OpenAILLM(api_key="sk-test", model="gpt-4o", task_models={"normalize_product": "gpt-4o-mini"})
    seen = []

    def fake_create(**kw):
        seen.append(kw["model"])
        call = SimpleNamespace(function=SimpleNamespace(arguments="{}"))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])

    monkeypatch.setattr(llm._client.chat.completions, "create", fake_create)
    llm.complete_json("normalize_product", "", "", {"type": "object"})
    llm.complete_json("seller_propose", "", "", {"type": "object"})
    assert seen == ["gpt-4o-mini", "gpt-4o"]
