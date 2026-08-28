"""Dev playground: a server-held demo buyer agent so the console can show the *whole* signed
flow (negotiation → grant → mandates → policy checks → payment → webhook) without putting keys
in a browser. Only available when the payments backend is the in-memory sandbox."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bazaar.gateway.checkout import complete_session, run_turn, session_summary
from bazaar.gateway.state import BazaarState
from bazaar.razorpay_client.fake import FakeRazorpay
from bazaar.schemas.models import AgentTier, Segment
from bazaar.seller_agent.offer_engine import Quote
from bazaar.trust import keys
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate

router = APIRouter(prefix="/bazaar/v1/dev/playground", tags=["playground"])


class StartIn(BaseModel):
    merchant_id: str
    message: str = ""
    segment: Segment = Segment.NEW


class MessageIn(BaseModel):
    message: str


class CheckoutIn(BaseModel):
    max_amount_paise: int = Field(default=0, ge=0)
    human_confirmation: bool = True
    tamper: bool = False  # demo: flip the mandate amount after signing to show the decline


def _st(request: Request) -> BazaarState:
    st: BazaarState = request.app.state.bazaar
    if not isinstance(st.payments, FakeRazorpay):
        raise HTTPException(403, detail={"error": "playground_disabled", "hint": "only with BAZAAR_RAZORPAY=fake"})
    return st


def _demo_agent(st: BazaarState) -> dict[str, Any]:
    demo = getattr(st, "_demo_agent", None)
    if demo is None:
        priv = keys.generate()
        ident = st.registry.register(keys.public_bytes(priv), "console-demo", tier=AgentTier.T2_VERIFIED)
        bpriv = keys.generate()
        bkid = st.register_buyer_key(keys.b64u(keys.public_bytes(bpriv)))
        demo = {"priv": priv, "keyid": ident.keyid, "buyer_priv": bpriv, "buyer_keyid": bkid}
        st._demo_agent = demo  # type: ignore[attr-defined]
    return demo


@router.post("/sessions", status_code=201)
def start(body: StartIn, request: Request):
    st = _st(request)
    demo = _demo_agent(st)
    m = st.merchant(body.merchant_id)
    if m is None:
        raise HTTPException(404, detail={"error": "merchant_not_found"})
    if m.policy.kill_switch:
        raise HTTPException(409, detail={"error": "merchant_agent_disabled"})
    s = st.new_session(merchant_id=m.merchant_id, agent_keyid=demo["keyid"], tier=AgentTier.T2_VERIFIED, segment=body.segment, source="playground")
    st.audit.record({"session": s.session_id, "kind": "session", "action": "create", "outcome": "ok", "note": "console playground (demo agent, tier 2)"})
    if body.message:
        return run_turn(st, s, body.message, demo["keyid"], AgentTier.T2_VERIFIED)
    return {"session": session_summary(s), "turn": None}


@router.post("/sessions/{sid}/messages")
def message(sid: str, body: MessageIn, request: Request):
    st = _st(request)
    demo = _demo_agent(st)
    s = st.session(sid)
    if s is None or s.source != "playground":
        raise HTTPException(404, detail={"error": "session_not_found"})
    if s.status in ("completed", "canceled"):
        raise HTTPException(409, detail={"error": f"session_{s.status}"})
    return run_turn(st, s, body.message, demo["keyid"], AgentTier.T2_VERIFIED)


@router.post("/sessions/{sid}/checkout")
def checkout(sid: str, body: CheckoutIn, request: Request):
    st = _st(request)
    demo = _demo_agent(st)
    s = st.session(sid)
    if s is None or s.source != "playground":
        raise HTTPException(404, detail={"error": "session_not_found"})
    if s.quote is None:
        raise HTTPException(409, detail={"error": "no_quote_yet"})
    q = Quote.model_validate(s.quote)
    steps: list[dict[str, Any]] = []
    cap = body.max_amount_paise or q.total_paise
    buyer_ref = f"console-{sid[-6:]}"
    g = st.grants.issue(buyer_ref, demo["keyid"], s.merchant_id, max(100, cap), 30, single_use=True)
    steps.append({"step": "grant_issued", "ok": True, "detail": f"Scoped Payment Grant {g.grant_id}: merchant {s.merchant_id}, cap ₹{cap / 100:.0f}, 30 min, single-use"})
    cm = CheckoutMandate.open(buyer_ref, cap, pincode=q.pincode, merchant_ids=[s.merchant_id]).close(q.quote_id, s.merchant_id, q.total_paise)
    cm.sign(demo["buyer_priv"], demo["buyer_keyid"])
    pm = PaymentMandate.open(buyer_ref, cap).close(cm)
    pm.sign(demo["buyer_priv"], demo["buyer_keyid"])
    steps.append({"step": "mandates_signed", "ok": True, "detail": f"Checkout mandate {cm.mandate_id} (closed → quote {q.quote_id}, ₹{q.total_paise / 100:.2f}) and payment mandate {pm.mandate_id} signed by buyer key {demo['buyer_keyid']}"})
    if body.tamper:
        cm.amount_paise = 1
        steps.append({"step": "tamper", "ok": True, "detail": "Demo: mandate amount changed to ₹0.01 *after* signing"})
    res, s = complete_session(st, s, demo["keyid"], g.grant_id, cm, pm, body.human_confirmation)
    steps.append({"step": "policy_gate", "ok": res.allowed, "detail": f"{sum(c.passed for c in res.checks)}/{len(res.checks)} checks passed" + ("" if res.allowed else f" — {res.reason}"), "checks": [c.model_dump() for c in res.checks]})
    if res.allowed and res.needs_merchant_review:
        steps.append({"step": "merchant_review", "ok": True, "detail": "Merchant is review-first: parked until approved in Sessions"})
    elif res.allowed:
        steps.append({"step": "payment_link", "ok": True, "detail": f"Razorpay UPI payment link {s.payment_link_id} for order {s.order_id}", "payment": {"order_id": s.order_id, "payment_url": s.payment_url}})
        pay = st.payments.simulate_payment(s.order_id)  # type: ignore[attr-defined]
        steps.append({"step": "webhook_payment_captured", "ok": True, "detail": f"payment.captured {pay.id} (HMAC-verified) → stock committed, grant used, fairness ledger written"})
    return {"session": session_summary(s), "steps": steps}


@router.post("/sessions/{sid}/pay")
def pay(sid: str, request: Request, succeed: bool = True):
    """Sandbox only: the buyer pays (or fails) the issued Razorpay payment link."""
    st = _st(request)
    s = st.session(sid)
    if s is None or s.source != "playground":
        raise HTTPException(404, detail={"error": "session_not_found"})
    if not s.order_id:
        raise HTTPException(409, detail={"error": "no_payment_link_yet"})
    pay = st.payments.simulate_payment(s.order_id, succeed=succeed)  # type: ignore[attr-defined]
    return {"session": session_summary(st.session(sid)), "payment": pay.model_dump()}
