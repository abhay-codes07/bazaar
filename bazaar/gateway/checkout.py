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
    if res.needs_merchant_review:
        s.status = "awaiting_merchant_review"
        s.touch()
        state.audit.record({"session": s.session_id, "kind": "checkout", "action": "complete", "outcome": "awaiting_merchant_review", "checks": s.last_checks, "note": "review-first merchant; payment link issued after approval"})
        return res, s
    state.audit.record({"session": s.session_id, "kind": "checkout", "action": "complete", "outcome": "ok", "checks": s.last_checks, "note": "all policy checks passed"})
    state.issue_payment(s)
    return res, s


def approve_review(state: BazaarState, s: Session) -> Session:
    if s.status != "awaiting_merchant_review":
        raise ValueError("session is not awaiting review")
    state.audit.record({"session": s.session_id, "kind": "checkout", "action": "merchant_approved", "outcome": "ok"})
    return state.issue_payment(s)


def cancel_session(state: BazaarState, s: Session, reason: str) -> Session:
    if s.status in ("completed",):
        raise ValueError("completed sessions cannot be canceled; use refund")
    if s.reservation_id:
        state.agent(s.merchant_id).tools.release(s.reservation_id)
        s.reservation_id = ""
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
