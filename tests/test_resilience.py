"""Model-outage behaviour: the circuit breaker, the deterministic failover, and the guarantee
that a dead model never takes quotes or checkout down with it."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bazaar.gateway.app import create_app
from bazaar.gateway.state import BazaarState
from bazaar.llm import LLM, FakeLLM, LLMError
from bazaar.llm.resilience import ResilientLLM
from bazaar.schemas.models import AgentTier, Segment
from bazaar.seller_agent.agent import BuyerContext, SellerAgent


class FlakyLLM(LLM):
    """Fails for the first ``fail_first`` calls, then behaves like the offline backend."""

    name = "openai"  # impersonate a remote backend

    def __init__(self, fail_first: int = 10**9):
        self.fail_first = fail_first
        self.calls = 0
        self._ok = FakeLLM()

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise LLMError("connection refused")
        return self._ok.complete_json(task, system, user, schema)


def _ask(llm: LLM, merchants) -> dict:
    m = merchants[0]
    agent = SellerAgent(m, llm=llm)
    p = m.products[0]
    ctx = BuyerContext(agent_keyid="ak_test", tier=AgentTier.T2_VERIFIED, segment=Segment.NEW, session_id="s_test")
    r = agent.handle(f"I need 2 {p.unit.value} {p.name}, deliver to {m.base_pincode}", ctx)
    return {"ok": r.ok, "action": r.action, "response": r}


def test_failover_answers_when_primary_is_down(merchants):
    r = _ask(ResilientLLM(FlakyLLM(), threshold=3, cooldown_s=60), merchants)
    assert r["ok"] and r["action"] in ("quote", "check_serviceability", "search_products")


def test_circuit_opens_after_threshold_and_skips_primary():
    flaky = FlakyLLM()
    llm = ResilientLLM(flaky, threshold=3, cooldown_s=300)
    for _ in range(4):
        llm.complete_json("seller_propose", "s", "merchant_id: none\nstate: {}\n<data label=\"buyer_message\">hi</data>", {})
    assert llm.status()["circuit_open"] is True
    calls_when_open = flaky.calls
    llm.complete_json("seller_propose", "s", "merchant_id: none\nstate: {}\n<data label=\"buyer_message\">hi</data>", {})
    assert flaky.calls == calls_when_open, "open circuit must not hit the primary"
    assert llm.total_failovers >= 4


def test_circuit_recovers_after_cooldown(monkeypatch):
    flaky = FlakyLLM(fail_first=3)
    llm = ResilientLLM(flaky, threshold=3, cooldown_s=60)
    for _ in range(3):
        llm.complete_json("seller_propose", "s", "merchant_id: none\nstate: {}\n<data label=\"buyer_message\">hi</data>", {})
    assert llm.degraded
    monkeypatch.setattr("bazaar.llm.resilience.time.monotonic", lambda: 10**9)  # cooldown elapsed
    out = llm.complete_json("seller_propose", "s", "merchant_id: none\nstate: {}\n<data label=\"buyer_message\">hi</data>", {})
    assert out.get("tool")
    assert not llm.degraded and llm.consecutive_failures == 0


def test_fallback_answers_are_not_cached_for_the_primary(tmp_path):
    from bazaar.llm.cache import CachedLLM

    flaky = FlakyLLM(fail_first=1)
    cached = CachedLLM(flaky, tmp_path / "c.sqlite")
    llm = ResilientLLM(cached, threshold=3, cooldown_s=0)
    user = 'merchant_id: none\nstate: {}\n<data label="buyer_message">hello</data>'
    llm.complete_json("seller_propose", "s", user, {})  # fails → fallback
    assert cached.stats()["stored"] == 0, "degraded answers must not poison the cache"
    llm.complete_json("seller_propose", "s", user, {})  # recovered → cached
    assert cached.stats()["stored"] == 1


@pytest.fixture()
def chaos_client(merchants):
    st = BazaarState(llm=ResilientLLM(FakeLLM(), threshold=3, cooldown_s=60))
    for m in merchants[:2]:
        st.add_merchant(m)
    return TestClient(create_app(st), headers={"x-admin-token": st.settings.bazaar_admin_token}), st


def test_chaos_endpoint_and_visible_degraded_status(chaos_client):
    client, st = chaos_client
    assert client.get("/bazaar/v1/stats").json()["llm"]["degraded"] is False
    r = client.post("/bazaar/v1/dev/chaos", json={"model_down": True})
    assert r.status_code == 200 and r.json()["model_down"] is True
    # buyer keeps getting answers, and both the switch and the failovers are on the audit chain
    mid = next(iter(st.merchants))
    s = client.post("/bazaar/v1/dev/playground/sessions", json={"merchant_id": mid, "message": "Do you deliver to " + st.merchants[mid].base_pincode + "?", "segment": "new"})
    assert s.status_code == 201 and s.json()["turn"]["explanation"]
    stats = client.get("/bazaar/v1/stats").json()
    assert stats["llm"]["degraded"] is True and stats["llm"]["total_failovers"] >= 1
    actions = {e.get("action") for e in st.audit.entries}
    assert "chaos_model_down" in actions and "llm_failover" in actions
    client.post("/bazaar/v1/dev/chaos", json={"model_down": False})
    assert client.get("/bazaar/v1/stats").json()["llm"]["degraded"] is False
