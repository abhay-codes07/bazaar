"""The seller loop must work with a *cautious* model that checks before quoting and uses
loose argument names — the behaviour observed from gpt-4o."""

from typing import Any

from bazaar.llm.base import LLM
from bazaar.schemas.models import AgentTier
from bazaar.seller_agent.agent import BuyerContext, SellerAgent, normalize_args


class ScriptedLLM(LLM):
    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete_json(self, task, system, user, schema) -> dict[str, Any]:
        self.calls += 1
        assert "<observations>" in system  # prompt tells the model about prior observations
        item = self.script.pop(0)
        return item(user) if callable(item) else item


def _grocer(merchants):
    m = next(m for m in merchants if m.vertical.value == "grocery").model_copy(deep=True)
    for p in m.products:
        p.stock = 30
    return m


def test_normalize_args_accepts_model_shapes():
    a = normalize_args("quote", {"product_id": "m_000-05", "quantity": "2", "pincode": 560034}, {"segment": "new"})
    assert a["lines"] == [{"sku": "m_000-05", "qty": 2}] and a["pincode"] == "560034" and a["segment"] == "new"
    a = normalize_args("quote", {"items": [{"id": "x", "count": 3}]}, {"pincode": "560034"})
    assert a["lines"] == [{"sku": "x", "qty": 3}] and a["pincode"] == "560034"
    a = normalize_args("apply_offer", {"rule_id": "NEW10"}, {"quote_id": "q_1"})
    assert a["quote_id"] == "q_1"


def test_cautious_model_checks_then_quotes_in_one_turn(merchants):
    m = _grocer(merchants)
    rice = next(p for p in m.products if p.name == "Basmati Rice")
    llm = ScriptedLLM([
        {"tool": "check_serviceability", "args": {"product_id": rice.sku, "pincode": "560034"}, "language": "en", "rationale": "check first"},
        lambda user: {"tool": "quote", "args": {"product_id": rice.sku, "quantity": 5, "pincode": "560034"}, "language": "en", "rationale": "serviceable per observations"} if "observations" in user else {"tool": "decline", "args": {"reason": "no observations passed"}, "language": "en", "rationale": ""},
    ])
    agent = SellerAgent(m, llm=llm)
    r = agent.handle("I need 5 kg basmati rice to 560034", BuyerContext(agent_keyid="k", tier=AgentTier.T2_VERIFIED, session_id="s"))
    assert r.ok and r.action == "quote" and r.result["lines"][0]["qty"] == 5 and llm.calls == 2
    assert "observations" not in r.state
    assert len(agent.audit.entries) == 2 and agent.audit.entries[0]["step"] == 0


def test_negative_serviceability_ends_the_turn(merchants):
    m = _grocer(merchants)
    llm = ScriptedLLM([{"tool": "check_serviceability", "args": {"pincode": "110001"}, "language": "en", "rationale": ""}])
    r = SellerAgent(m, llm=llm).handle("5 kg rice to 110001", BuyerContext(tier=AgentTier.T2_VERIFIED))
    assert r.ok and r.action == "check_serviceability" and not r.result["serves"] and llm.calls == 1


def test_loop_is_bounded(merchants):
    m = _grocer(merchants)
    llm = ScriptedLLM([{"tool": "search_products", "args": {"query": "rice"}, "language": "en", "rationale": ""}] * 5)
    r = SellerAgent(m, llm=llm).handle("2 kg rice to 560034", BuyerContext(tier=AgentTier.T2_VERIFIED))
    assert r.ok and r.action == "search_products" and llm.calls == 3
