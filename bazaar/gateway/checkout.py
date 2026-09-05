"""Checkout orchestration shared by the Bazaar API and the ACP/UCP adapters."""

from __future__ import annotations

from typing import Any

from bazaar.gateway.sessions import Session
from bazaar.gateway.state import BazaarState
from bazaar.schemas.models import AgentTier
from bazaar.seller_agent.agent import BuyerContext
from bazaar.seller_agent.offer_engine import Quote
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate
from bazaar.trust.policy import PolicyResult


def complete_session(state: BazaarState, s: Session, agent_keyid: str, grant_id: str, checkout_mandate: CheckoutMandate | None, payment_mandate: PaymentMandate | None, human_confirmation: bool) -> tuple[PolicyResult, Session]:
    m = state.merchants[s.merchant_id]
    if s.quote is None:
        res = PolicyResult(allowed=False, checks=[{"name": "quote_present", "passed": False, "detail": "no quote in session"}])  # type: ignore[list-item]
        return res, s
    q = Quote.model_validate(s.quote)
    res = state.policy.check_checkout(m, q, agent_keyid, grant_id, checkout_mandate, payment_mandate, state.buyer_pub, human_confirmation)
    s.last_checks = [c.model_dump() for c in res.checks]
    s.agent_keyid = agent_keyid or s.agent_keyid
    if not res.allowed:
        # a declined attempt is not terminal: the buyer may retry with a corrected mandate/grant
        s.touch()
        state.audit.record({"session": s.session_id, "kind": "checkout", "action": "complete", "outcome": "declined", "checks": s.last_checks, "note": res.reason})
        return res, s
    s.grant_id = grant_id
    # hold stock for the payment window if not already held. A failed reservation means another
    # in-flight checkout already holds the last units — decline rather than oversell (the policy
    # gate reads raw stock; the reservation is the atomic claim two buyers cannot both win).
    if not s.reservation_id:
        r = state.agent(s.merchant_id).tools.reserve(q.quote_id)
        if not r.ok:
            from bazaar.trust.policy import Check

            res.checks.append(Check(name="stock_reserved", passed=False, detail=r.reason))
            res.allowed = False
            s.last_checks = [c.model_dump() for c in res.checks]
            s.touch()
            state.audit.record({"session": s.session_id, "kind": "checkout", "action": "complete", "outcome": "declined", "checks": s.last_checks, "note": f"reservation failed: {r.reason}"})
            return res, s
        s.reservation_id = r.result["reservation_id"]
    # reserve the amount on the grant for the payment window so a concurrent checkout on the
    # same single-use grant is refused (the use is only recorded at capture, so without this a
    # second complete between checkout and capture would double-spend the grant)
    if grant_id and state.grants.get(grant_id):
        state.grants.reserve_pending(grant_id, q.total_paise, s.session_id)
    if res.needs_merchant_review:
        s.status = "awaiting_merchant_review"
        s.touch()
        state.audit.record({"session": s.session_id, "kind": "checkout", "action": "complete", "outcome": "awaiting_merchant_review", "checks": s.last_checks, "note": "review-first merchant; payment link issued after approval"})
        return res, s
    state.audit.record({"session": s.session_id, "kind": "checkout", "action": "complete", "outcome": "ok", "checks": s.last_checks, "note": "all policy checks passed"})
    state.issue_payment(s)
    return res, s


def approve_review(state: BazaarState, s: Session) -> tuple[bool, Session]:
    if s.status != "awaiting_merchant_review":
        raise ValueError("session is not awaiting review")
    m = state.merchants[s.merchant_id]
    q = Quote.model_validate(s.quote)
    # anything can have changed between park and approve — re-verify the mutable state (the
    # signed mandates were checked at park time and are immutable, so we re-check what is not:
    # kill switch, grant still usable, stock still held/available, quote still fresh)
    from datetime import datetime, timezone

    from bazaar.trust.policy import Check

    g = state.grants.get(s.grant_id) if s.grant_id else None
    grant_ok = g is not None and g.usable_for(m.merchant_id, s.agent_keyid, q.total_paise, session_id=s.session_id)[0]
    tools = state.agent(m.merchant_id).tools
    stock_ok = bool(s.reservation_id) or all((m.product(ln.sku).stock if m.product(ln.sku) else 0) - tools._reserved_qty(ln.sku) >= ln.qty for ln in q.lines)
    checks = [
        Check(name="kill_switch_off", passed=not m.policy.kill_switch),
        Check(name="quote_fresh", passed=datetime.now(timezone.utc) <= q.valid_until),
        Check(name="grant_usable", passed=grant_ok, detail="" if grant_ok else "grant revoked/exhausted since review"),
        Check(name="items_in_stock", passed=stock_ok),
    ]
    if not all(c.passed for c in checks):
        s.status = "declined"
        s.last_checks = [c.model_dump() for c in checks]
        if s.reservation_id:
            tools.release(s.reservation_id)
            s.reservation_id = ""
        if s.grant_id:
            state.grants.release_pending(s.grant_id, s.session_id)
        s.touch()
        state.audit.record({"session": s.session_id, "kind": "checkout", "action": "merchant_approved", "outcome": "declined", "checks": s.last_checks, "note": "state changed since review; approval rejected"})
        return False, s
    state.audit.record({"session": s.session_id, "kind": "checkout", "action": "merchant_approved", "outcome": "ok"})
    return True, state.issue_payment(s)


def cancel_session(state: BazaarState, s: Session, reason: str) -> Session:
    if s.status in ("completed",):
        raise ValueError("completed sessions cannot be canceled; use refund")
    if s.reservation_id:
        state.agent(s.merchant_id).tools.release(s.reservation_id)
        s.reservation_id = ""
    if s.grant_id:
        state.grants.release_pending(s.grant_id, s.session_id)
    s.status = "canceled"
    s.touch()
    state.audit.record({"session": s.session_id, "kind": "checkout", "action": "cancel", "outcome": "ok", "note": reason})
    return s


def session_summary(s: Session) -> dict[str, Any]:
    d = s.public()
    d["turns"] = [{k: t.get(k) for k in ("action", "ok", "explanation", "audit_id", "language")} for t in s.turns]
    return d


def run_turn(state: BazaarState, s: Session, message: str, caller_keyid: str, tier: AgentTier) -> dict[str, Any]:
    """One buyer message through the merchant's Seller Agent; updates the session."""
    agent = state.agent(s.merchant_id)
    ctx = BuyerContext(agent_keyid=caller_keyid or s.agent_keyid, tier=tier, segment=s.segment, session_id=s.session_id)
    r = agent.handle(message, ctx, s.state)
    s.state = r.state
    s.language = r.language or s.language
    s.turns.append(r.model_dump(mode="json"))
    if r.ok and r.action in ("quote", "apply_offer"):
        s.quote = r.result
        s.status = "ready_for_payment" if s.status in ("open", "ready_for_payment") else s.status
    if r.ok and r.action == "reserve":
        s.reservation_id = r.result["reservation_id"]
    s.touch()
    return {"session": session_summary(s), "turn": r.model_dump(mode="json")}
