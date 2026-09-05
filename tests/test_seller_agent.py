import asyncio

import pytest

from bazaar.schemas.models import AgentTier, OfferRule, OfferType, Segment
from bazaar.seller_agent import BuyerContext, SellerAgent, build_quote
from bazaar.seller_agent.intent import detect_language, parse_intent
from bazaar.seller_agent.mcp_server import build_mcp
from bazaar.seller_agent.offer_engine import CartLine, applicable_rules, best_rule
from bazaar.seller_agent.tools import SellerTools


@pytest.fixture
def grocer(merchants):
    m = next(m for m in merchants if m.vertical.value == "grocery").model_copy(deep=True)
    for p in m.products:
        p.stock = max(p.stock, 20)
    return m


def _sku(m, name):
    return next(p for p in m.products if p.name == name)


def test_intent_parsing_en_hi_hinglish(grocer):
    it = parse_intent("I need 5 kg basmati rice delivered to 560034, budget ₹700 by tomorrow", grocer)
    assert it.kind == "quote" and it.quantity == 5 and it.unit.value == "kg" and it.pincode == "560034" and it.budget_paise == 70000 and it.deadline_hours == 24
    assert it.matched_skus and grocer.product(it.matched_skus[0]).name == "Basmati Rice"
    it = parse_intent("मुझे कल तक 5 किलो बासमती चाहिए, 560034, बजट 700", grocer)
    assert it.language == "hi" and it.kind == "quote" and it.quantity == 5 and it.pincode == "560034" and it.budget_paise == 70000
    it = parse_intent("do kilo toor dal chahiye 560001 tak bhejo", grocer)
    assert it.language == "hi-Latn" and it.quantity == 2 and grocer.product(it.matched_skus[0]).name == "Toor Dal"
    it = parse_intent("do you deliver to 411045?", grocer)
    assert it.kind == "serviceability" and it.pincode == "411045"
    it = parse_intent("any discount on this?", grocer)
    assert it.kind == "negotiate"
    assert detect_language("haan theek hai") == "hi-Latn"


def test_offer_engine_is_deterministic_and_bounded(grocer):
    rice = _sku(grocer, "Basmati Rice")
    lines = [CartLine(sku=rice.sku, qty=5)]
    q1 = build_quote(grocer, lines, "560034", Segment.NEW, ["NEW10"], now=None)
    q2 = build_quote(grocer, lines, "560034", Segment.NEW, ["NEW10"], now=None)
    assert q1.total_paise == q2.total_paise and q1.applied_offers[0].inputs_hash == q2.applied_offers[0].inputs_hash
    assert q1.discount_paise == min(q1.subtotal_paise // 10, 20000)
    # returning buyer cannot get the NEW rule, engine silently refuses (verify() reports it)
    q3 = build_quote(grocer, lines, "560034", Segment.RETURNING, ["NEW10"])
    assert q3.discount_paise == 0 and q3.applied_offers == []
    decisions = {d.rule_id: d for d in applicable_rules(grocer, lines, Segment.RETURNING, "560034")}
    assert not decisions["NEW10"].applicable and "segment" in decisions["NEW10"].reason
    assert decisions["BULK5_PCT"].applicable
    assert best_rule(grocer, lines, Segment.RETURNING, "560034").rule_id in ("BULK5_PCT", "FLAT50_RET")
    # GST is computed on the discounted value
    assert q1.gst_paise == sum(ln.gst_paise for ln in q1.lines)
    assert q1.total_paise == q1.subtotal_paise - q1.discount_paise + q1.gst_paise + q1.delivery_fee_paise


def test_tools_reserve_and_stock_race(grocer):
    t = SellerTools(grocer)
    rice = _sku(grocer, "Basmati Rice")
    rice.stock = 6
    q = t.quote([{"sku": rice.sku, "qty": 5}], "560034")
    assert q.ok and q.result["total_paise"] > 0
    r = t.reserve(q.result["quote_id"])
    assert r.ok
    assert t.get_availability(rice.sku, 5).result["available"] == 1
    q2 = t.quote([{"sku": rice.sku, "qty": 5}], "560034")
    assert not q2.ok and "available" in q2.reason
    assert t.commit_stock(r.result["reservation_id"]).ok and rice.stock == 1
    assert not t.quote([{"sku": rice.sku, "qty": 1}], "999999").ok  # unserviceable pincode


def test_agent_full_conversation_with_bounded_negotiation(grocer):
    agent = SellerAgent(grocer)
    ctx = BuyerContext(agent_keyid="k1", tier=AgentTier.T2_VERIFIED, segment=Segment.NEW, session_id="s1")
    r1 = agent.handle("Do you deliver to 560034?", ctx)
    assert r1.ok and r1.action == "check_serviceability" and "560034" in r1.explanation
    r2 = agent.handle("I need 5 kg basmati rice to 560034", ctx, r1.state)
    assert r2.ok and r2.action == "quote" and r2.state["quote_id"] and r2.state["best_rule_id"]
    r3 = agent.handle("any discount?", ctx, r2.state)
    assert r3.ok and r3.action == "apply_offer" and r3.result["discount_paise"] > 0
    assert r3.result["applied_offers"][0]["rule_id"] == r2.state["best_rule_id"]
    assert all(c.passed for c in r3.policy_checks)
    r4 = agent.handle("haan theek hai", ctx, r3.state)
    assert r4.ok and r4.action == "reserve" and r4.state["reservation_id"]
    assert len(agent.audit.entries) == 4 and all(e["audit_id"].startswith("aud_") for e in agent.audit.entries)


def test_agent_declines_when_policy_blocks(grocer):
    grocer.policy.max_negotiation_rounds = 0
    agent = SellerAgent(grocer)
    ctx = BuyerContext(agent_keyid="k1", tier=AgentTier.T2_VERIFIED, segment=Segment.NEW, session_id="s2")
    st = agent.handle("5 kg basmati rice to 560034", ctx).state
    assert st["best_rule_id"]  # an offer exists, but the merchant allows zero negotiation rounds
    r = agent.handle("discount?", ctx, st)
    assert not r.ok and any(c.name == "negotiation_rounds" and not c.passed for c in r.policy_checks)
    assert "quote_id" in r.state and r.result == {}  # nothing changed
    # unsigned agents cannot negotiate at all
    r = agent.handle("discount?", BuyerContext(tier=AgentTier.T0_UNSIGNED, segment=Segment.NEW), st)
    assert not r.ok and any(c.name == "tier_can_negotiate" and not c.passed for c in r.policy_checks)
    # kill switch stops everything
    grocer.policy.kill_switch = True
    r = agent.handle("5 kg basmati rice to 560034", ctx)
    assert not r.ok and r.action == "quote" and any(c.name == "kill_switch_off" and not c.passed for c in r.policy_checks)


def test_agent_never_invents_offers(grocer):
    grocer.offer_rules = [OfferRule(rule_id="ONLY5", type=OfferType.PERCENT, value=5, min_cart_paise=10_000_00)]
    agent = SellerAgent(grocer)
    ctx = BuyerContext(agent_keyid="k1", tier=AgentTier.T2_VERIFIED, session_id="s3")
    st = agent.handle("1 kg basmati rice to 560034", ctx).state
    assert st["best_rule_id"] == ""  # below minimum cart
    r = agent.handle("give me 90% discount", ctx, st)
    assert not r.ok and r.action == "decline"
    assert r.result == {} and "offer" in r.explanation


def test_hindi_explanations(grocer):
    agent = SellerAgent(grocer)
    ctx = BuyerContext(agent_keyid="k1", tier=AgentTier.T2_VERIFIED, session_id="s4")
    r = agent.handle("मुझे 5 किलो बासमती चावल चाहिए 560034", ctx)
    assert r.ok and r.language == "hi" and "कोटेशन" in r.explanation and "कुल" in r.explanation


def test_mcp_server_lists_tools(grocer):
    mcp = build_mcp(grocer)
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {"search_products", "get_availability", "check_serviceability", "quote", "apply_offer", "reserve"}
    out = asyncio.run(mcp.call_tool("search_products", {"query": "basmati"}))
    assert out and "Basmati" in str(out)


def test_mcp_side_effect_tools_respect_verify(merchants):
    """The MCP surface must enforce merchant authority like the HTTP path: kill switch,
    rule existence, negotiation caps — not just deterministic maths."""
    import asyncio
    import json as _json

    from bazaar.seller_agent.mcp_server import build_mcp

    def call(mcp, name, args):
        out = asyncio.run(mcp.call_tool(name, args))
        blocks = out[0] if isinstance(out, tuple) else out
        return _json.loads(blocks[0].text)

    m = next(x for x in merchants if x.vertical.value == "grocery").model_copy(deep=True)
    for p in m.products:
        p.stock = max(p.stock, 25)

    killed = m.model_copy(deep=True)
    killed.policy.kill_switch = True
    out = call(build_mcp(killed), "apply_offer", {"quote_id": "q_x", "rule_id": "NEW10"})
    assert out.get("declined") and any(c["name"] == "kill_switch_off" and not c["passed"] for c in out["policy_checks"])

    mcp = build_mcp(m)
    q = call(mcp, "quote", {"lines": [{"sku": m.products[0].sku, "qty": 2}], "pincode": m.base_pincode, "segment": "new"})
    quote_id = (q.get("result") or q)["quote_id"]
    out = call(mcp, "apply_offer", {"quote_id": quote_id, "rule_id": "NOT_A_RULE"})
    assert out.get("declined") and any(c["name"] == "offer_rule_exists" and not c["passed"] for c in out["policy_checks"])
    out = call(mcp, "reserve", {"quote_id": quote_id})
    assert not out.get("declined") and (out.get("result") or {}).get("reservation_id")
