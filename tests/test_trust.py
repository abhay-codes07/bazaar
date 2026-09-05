import json
from datetime import datetime, timedelta, timezone

import pytest

from bazaar.schemas.models import AgentTier, Segment
from bazaar.seller_agent.offer_engine import CartLine, build_quote
from bazaar.trust import keys
from bazaar.trust.audit import AuditLog
from bazaar.trust.fairness_auditor import audit_merchant
from bazaar.trust.grants import GrantStore
from bazaar.trust.http_sig import TAG_PAY, NonceCache, SignatureError, sign_request, verify_request
from bazaar.trust.ledger import FairnessLedger, LedgerEntry
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate
from bazaar.trust.policy import PolicyEngine
from bazaar.trust.registry import AgentRegistry


@pytest.fixture
def agent_key():
    priv = keys.generate()
    return priv, keys.public_bytes(priv)


@pytest.fixture
def registry(agent_key):
    r = AgentRegistry()
    r.register(agent_key[1], operator="test-agent", tier=AgentTier.T2_VERIFIED)
    return r


def test_http_signature_roundtrip_and_seven_checks(agent_key, registry):
    priv, pub_raw = agent_key
    keyid = keys.keyid_for(pub_raw)
    body = b'{"hello": "world"}'
    hdrs = sign_request(priv, keyid, "POST", "bazaar.test", "/bazaar/v1/sessions", body, tag=TAG_PAY)
    lookup = lambda k: registry.get(k).public_key() if registry.get(k) else None  # noqa: E731
    nonces = NonceCache()
    v = verify_request(hdrs, "POST", "bazaar.test", "/bazaar/v1/sessions", body, lookup, nonces, required_tag=TAG_PAY)
    assert v.keyid == keyid and v.tag == TAG_PAY
    # replay
    with pytest.raises(SignatureError) as e:
        verify_request(hdrs, "POST", "bazaar.test", "/bazaar/v1/sessions", body, lookup, nonces)
    assert e.value.step == "replay"
    # tampered body
    with pytest.raises(SignatureError) as e:
        verify_request(hdrs, "POST", "bazaar.test", "/bazaar/v1/sessions", b'{"hello": "evil"}', lookup, NonceCache())
    assert e.value.step == "digest"
    # wrong path
    with pytest.raises(SignatureError) as e:
        verify_request(hdrs, "POST", "bazaar.test", "/bazaar/v1/other", body, lookup, NonceCache())
    assert e.value.step == "signature"
    # unknown key
    with pytest.raises(SignatureError) as e:
        verify_request(hdrs, "POST", "bazaar.test", "/bazaar/v1/sessions", body, lambda k: None, NonceCache())
    assert e.value.step == "key"
    # expired
    old = sign_request(priv, keyid, "GET", "bazaar.test", "/x", now=1_000_000)
    with pytest.raises(SignatureError) as e:
        verify_request(old, "GET", "bazaar.test", "/x", b"", lookup, NonceCache())
    assert e.value.step == "freshness"
    # wrong tag
    with pytest.raises(SignatureError) as e:
        verify_request(sign_request(priv, keyid, "GET", "bazaar.test", "/x"), "GET", "bazaar.test", "/x", b"", lookup, NonceCache(), required_tag=TAG_PAY)
    assert e.value.step == "tag"
    # missing headers
    with pytest.raises(SignatureError) as e:
        verify_request({}, "GET", "bazaar.test", "/x", b"", lookup, NonceCache())
    assert e.value.step == "headers"


def test_mandates_chain_and_tamper_evidence(agent_key):
    priv, pub_raw = agent_key
    pub = keys.public_from_bytes(pub_raw)
    cm = CheckoutMandate.open("buyer-1", 70000, pincode="560034", allowed_categories=["staples"])
    cm.sign(priv, "buyer-key")
    assert cm.verify(pub)
    closed = cm.close("q_abc", "m_000", 61234)
    assert closed.parent_digest == cm.digest() and closed.stage == "closed"
    closed.sign(priv, "buyer-key")
    assert closed.verify(pub)
    closed.amount_paise = 1  # tamper
    assert not closed.verify(pub)
    closed.amount_paise = 61234
    pm = PaymentMandate.open("buyer-1", 100000)
    with pytest.raises(ValueError):
        pm.close(cm)  # cannot close against an open checkout
    pmc = pm.close(closed)
    assert pmc.amount_paise == 61234 and pmc.checkout_mandate_digest == closed.digest()


def test_grants_are_scoped_revocable_and_single_use():
    gs = GrantStore()
    events = []
    gs.on_event(lambda ev, d: events.append(ev))
    g = gs.issue("buyer-1", "ak_1", "m_000", 50000)
    assert g.usable_for("m_000", "ak_1", 50000)[0]
    assert not g.usable_for("m_001", "ak_1", 100)[0]
    assert not g.usable_for("m_000", "ak_2", 100)[0]
    assert "exceeds" in g.usable_for("m_000", "ak_1", 50001)[1]
    gs.use(g.grant_id, 40000, "s1", "order_1")
    assert not g.usable_for("m_000", "ak_1", 100)[0]  # single-use
    gs.revoke(g.grant_id, "buyer cancelled")
    assert g.revoked and events == ["grant.issued", "grant.used", "grant.revoked"]
    g2 = gs.issue("b", "ak_1", "m_000", 10000, single_use=False)
    gs.use(g2.grant_id, 6000, "s", "o")
    assert g2.remaining_paise == 4000 and not g2.usable_for("m_000", "ak_1", 4001)[0]


def _grocer(merchants):
    m = next(m for m in merchants if m.vertical.value == "grocery").model_copy(deep=True)
    for p in m.products:
        p.stock = 50
    return m


def test_policy_engine_full_checkout_gate(merchants, agent_key, registry):
    priv, pub_raw = agent_key
    keyid = keys.keyid_for(pub_raw)
    m = _grocer(merchants)
    rice = next(p for p in m.products if p.name == "Basmati Rice")
    q = build_quote(m, [CartLine(sku=rice.sku, qty=5)], m.base_pincode, Segment.NEW, ["NEW10"])
    grants = GrantStore()
    eng = PolicyEngine(registry, grants)
    buyer_priv = keys.generate()
    buyer_pub = keys.public_from_bytes(keys.public_bytes(buyer_priv))
    lookup = lambda k: buyer_pub if k == "buyer-key" else None  # noqa: E731

    cm = CheckoutMandate.open("b1", 100000, pincode=m.base_pincode).close(q.quote_id, m.merchant_id, q.total_paise)
    cm.sign(buyer_priv, "buyer-key")
    pm = PaymentMandate.open("b1", 100000).close(cm)
    pm.sign(buyer_priv, "buyer-key")
    g = grants.issue("b1", keyid, m.merchant_id, 100000)

    res = eng.check_checkout(m, q, keyid, g.grant_id, cm, pm, lookup, human_confirmation=True)
    assert res.allowed, res.reason
    assert len(res.checks) >= 20

    # each of these must fail on exactly the named check
    bad = eng.check_checkout(m, q, keyid, g.grant_id, cm, pm, lookup, human_confirmation=False)
    assert not bad.allowed and "human_confirmation" in bad.reason
    other = registry.register(keys.public_bytes(keys.generate()), "other")
    bad = eng.check_checkout(m, q, other.keyid, g.grant_id, cm, pm, lookup, human_confirmation=True)
    assert "grant_usable" in bad.reason and "agent_tier_sufficient" in bad.reason
    small = CheckoutMandate.open("b1", 100).close(q.quote_id, m.merchant_id, q.total_paise)
    small.sign(buyer_priv, "buyer-key")
    bad = eng.check_checkout(m, q, keyid, g.grant_id, small, pm, lookup, human_confirmation=True)
    assert "checkout_within_max" in bad.reason and "payment_binds_checkout" in bad.reason
    m.policy.kill_switch = True
    bad = eng.check_checkout(m, q, keyid, g.grant_id, cm, pm, lookup, human_confirmation=True)
    assert "kill_switch_off" in bad.reason
    m.policy.kill_switch = False
    m.policy.review_first = True
    assert eng.check_checkout(m, q, keyid, g.grant_id, cm, pm, lookup, human_confirmation=True).needs_merchant_review


def test_refund_policy_rate_limit(merchants, registry):
    m = _grocer(merchants)
    m.policy.refunds_per_hour = 2
    eng = PolicyEngine(registry, GrantStore())
    assert eng.check_refund(m, "ak", 100, 1000, True).allowed
    assert not eng.check_refund(m, "ak", 100, 1000, False).allowed
    assert eng.check_refund(m, "ak", 100, 1000, True).allowed
    r = eng.check_refund(m, "ak", 100, 1000, True)
    assert not r.allowed and "refund_rate_limit" in r.reason
    assert not eng.check_refund(m, "ak", 2000, 1000, True).allowed


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.record({"session": "s1", "proposal": {"tool": "quote"}, "checks": [{"name": "x", "passed": True}], "outcome": "ok", "note": f"n{i}"})
    assert log.verify_chain() == (True, -1)
    root = log.merkle_root()
    reloaded = AuditLog(tmp_path / "audit.jsonl")
    assert reloaded.verify_chain() == (True, -1) and reloaded.merkle_root() == root
    # tamper with the file
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[2])
    e["note"] = "edited"
    lines[2] = json.dumps(e)
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert AuditLog(tmp_path / "audit.jsonl").verify_chain() == (False, 2)
    tl = log.replay("s1")
    assert len(tl) == 5 and tl[0]["action"] == "quote" and tl[0]["checks_passed"] == 1


def test_fairness_ledger_and_auditor(merchants):
    m = _grocer(merchants)
    rep = audit_merchant(m)
    assert rep.passed and rep.cohorts > 100 and rep.rules_checked == len(m.offer_rules)
    led = FairnessLedger()
    for s in ("s1", "s2"):
        led.record(LedgerEntry(merchant_id="m", rule_id="NEW10", rule_version=1, segment_predicate="new", inputs_hash="h1", discount_paise=500, session_id=s))
    assert led.inconsistencies() == []
    led.record(LedgerEntry(merchant_id="m", rule_id="NEW10", rule_version=1, segment_predicate="new", inputs_hash="h1", discount_paise=900, session_id="s3"))
    inc = led.inconsistencies()
    assert len(inc) == 1 and set(inc[0]["outcomes"]) == {500, 900}


def test_registry_tiers_and_revocation(agent_key):
    r = AgentRegistry()
    a = r.register(agent_key[1], "op")
    assert a.tier == AgentTier.T1_SIGNED and a.max_order_paise == 20_000_00
    r.set_tier(a.keyid, AgentTier.T3_VETTED, "razorpay vetted")
    assert r.get(a.keyid).max_order_paise == 500_000_00
    r.revoke(a.keyid, "abuse")
    assert r.get(a.keyid).revoked and r.events[-1]["event"] == "revoked"
    assert datetime.now(timezone.utc) - a.registered_at < timedelta(minutes=1)


def test_policy_requires_a_person_above_the_rbi_threshold(merchants, agent_key, registry):
    """RBI e-mandate framework (Apr 2026): no AFA-free debit above ₹15,000. Even a human-not-present
    mandate held by a verified agent must stop and ask a person above the merchant's threshold."""
    priv, pub_raw = agent_key
    keyid = keys.keyid_for(pub_raw)
    m = _grocer(merchants)
    rice = next(p for p in m.products if p.name == "Basmati Rice")
    rice.stock = 10_000
    qty = 15_000_00 // rice.price_paise + 2  # comfortably above ₹15,000
    q = build_quote(m, [CartLine(sku=rice.sku, qty=qty)], m.base_pincode, Segment.NEW, [])
    assert q.total_paise > m.policy.human_present_above_paise
    m.policy.max_order_paise = q.total_paise + 1  # the amount cap alone would allow it
    grants = GrantStore()
    eng = PolicyEngine(registry, grants)
    buyer_priv = keys.generate()
    buyer_pub = keys.public_from_bytes(keys.public_bytes(buyer_priv))
    lookup = lambda k: buyer_pub if k == "buyer-key" else None  # noqa: E731
    cm = CheckoutMandate.open("b1", q.total_paise, pincode=m.base_pincode, human_present=False).close(q.quote_id, m.merchant_id, q.total_paise)
    cm.sign(buyer_priv, "buyer-key")
    pm = PaymentMandate.open("b1", q.total_paise).close(cm)
    pm.sign(buyer_priv, "buyer-key")
    g = grants.issue("b1", keyid, m.merchant_id, q.total_paise)

    unattended = eng.check_checkout(m, q, keyid, g.grant_id, cm, pm, lookup, human_confirmation=False)
    assert not unattended.allowed and "human_present_above_threshold" in unattended.reason
    assert "human_confirmation" not in unattended.reason  # the mandate itself did not ask; the amount did
    confirmed = eng.check_checkout(m, q, keyid, g.grant_id, cm, pm, lookup, human_confirmation=True)
    assert all(c.passed for c in confirmed.checks if c.name == "human_present_above_threshold")
    # below the threshold the check is not even emitted — small baskets stay frictionless
    small = build_quote(m, [CartLine(sku=rice.sku, qty=1)], m.base_pincode, Segment.NEW, [])
    names = {c.name for c in eng.check_checkout(m, small, keyid, g.grant_id, cm, pm, lookup, human_confirmation=False).checks}
    assert "human_present_above_threshold" not in names


def test_cod_gate_rules(merchants):
    from bazaar.schemas.models import AgentTier
    from bazaar.seller_agent.rto import COD_VALUE_CAP_PAISE, cod_gate

    m = next(x for x in merchants if x.serviceability.cod_allowed)
    ok = cod_gate(m, m.base_pincode, 50_000, AgentTier.T2_VERIFIED)
    assert ok.allowed
    assert not cod_gate(m, m.base_pincode, COD_VALUE_CAP_PAISE + 1, AgentTier.T2_VERIFIED).allowed
    assert not cod_gate(m, m.base_pincode, 50_000, AgentTier.T0_UNSIGNED).allowed
    assert not cod_gate(m, "110001", 50_000, AgentTier.T2_VERIFIED).allowed
    no_cod = m.model_copy(deep=True)
    no_cod.serviceability.cod_allowed = False
    assert not cod_gate(no_cod, m.base_pincode, 50_000, AgentTier.T2_VERIFIED).allowed
