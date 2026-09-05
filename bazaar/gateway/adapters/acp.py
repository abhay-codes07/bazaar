"""Agentic Commerce Protocol (OpenAI/Stripe) adapter.

Maps ACP checkout sessions onto Bazaar sessions so a ChatGPT-style buyer works unchanged:

* ``POST /acp/{merchant}/checkout_sessions``            create (items + fulfillment address)
* ``POST /acp/{merchant}/checkout_sessions/{id}``       update items/address
* ``POST /acp/{merchant}/checkout_sessions/{id}/complete``  pay with a delegated payment token
* ``POST /acp/{merchant}/checkout_sessions/{id}/cancel``
* ``GET  /acp/{merchant}/checkout_sessions/{id}``
* ``POST /acp/{merchant}/delegate_payment``             ACP "delegated payment": buyer authorises
  via the platform; Bazaar issues a Scoped Payment Grant and holds a delegated buyer key so it
  can close AP2-shaped mandates on the buyer's behalf — the same policy gate then applies.

Statuses follow ACP: ``not_ready_for_payment | ready_for_payment | in_progress | completed | canceled``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from bazaar.gateway.auth import identify
from bazaar.gateway.checkout import cancel_session, complete_session
from bazaar.gateway.sessions import Session
from bazaar.seller_agent.offer_engine import Quote
from bazaar.trust import keys
from bazaar.trust.http_sig import TAG_PAY
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate

router = APIRouter(prefix="/acp", tags=["acp"])


class Item(BaseModel):
    id: str
    quantity: int = Field(ge=1)


class Address(BaseModel):
    name: str = ""
    line_one: str = ""
    city: str = ""
    state: str = ""
    country: str = "IN"
    postal_code: str = ""


class CreateIn(BaseModel):
    items: list[Item]
    fulfillment_address: Address | None = None
    buyer: dict[str, Any] | None = None


class UpdateIn(BaseModel):
    items: list[Item] | None = None
    fulfillment_address: Address | None = None


class DelegateIn(BaseModel):
    buyer_ref: str
    allowance: dict[str, Any]  # {"max_amount": paise, "expires_in_minutes": 30}


class CompleteIn(BaseModel):
    payment_data: dict[str, Any]  # {"token": "spg_...", "provider": "razorpay"}
    buyer: dict[str, Any] | None = None
    human_confirmation: bool = False  # must be asserted explicitly; omitting it cannot satisfy the human-present gate


def _state(request: Request):
    return request.app.state.bazaar


def _acp_status(s: Session) -> str:
    return {"open": "not_ready_for_payment", "ready_for_payment": "ready_for_payment", "awaiting_merchant_review": "in_progress", "in_progress": "in_progress", "completed": "completed", "canceled": "canceled", "declined": "canceled"}[s.status]


def _render(s: Session, m) -> dict[str, Any]:
    q = Quote.model_validate(s.quote) if s.quote else None
    line_items = []
    totals = []
    if q:
        line_items = [{"id": ln.sku, "item": {"id": ln.sku, "quantity": ln.qty}, "base_amount": ln.unit_price_paise * ln.qty, "discount": 0, "subtotal": ln.subtotal_paise, "tax": ln.gst_paise, "total": ln.subtotal_paise + ln.gst_paise} for ln in q.lines]
        totals = [
            {"type": "items_base_amount", "display_text": "Items", "amount": q.subtotal_paise},
            {"type": "items_discount", "display_text": "Discount", "amount": q.discount_paise},
            {"type": "subtotal", "display_text": "Subtotal", "amount": q.subtotal_paise - q.discount_paise},
            {"type": "fulfillment", "display_text": "Delivery", "amount": q.delivery_fee_paise},
            {"type": "tax", "display_text": "GST", "amount": q.gst_paise},
            {"type": "total", "display_text": "Total", "amount": q.total_paise},
        ]
    msgs = []
    failed = [c["name"] for c in s.last_checks if not c["passed"]]
    if failed and s.status in ("ready_for_payment", "open", "declined"):
        msgs.append({"type": "error", "code": "policy_declined", "content": "; ".join(failed)})
    return {
        "id": s.session_id,
        "status": _acp_status(s),
        "currency": "inr",
        "line_items": line_items,
        "totals": totals,
        "fulfillment_options": [{"type": "shipping", "id": "standard", "title": f"Delivery in ~{q.eta_hours} h", "subtotal": q.delivery_fee_paise, "total": q.delivery_fee_paise}] if q else [],
        "payment_provider": {"provider": "razorpay", "supported_payment_methods": ["upi", "card"]},
        "order": {"id": s.order_id, "checkout_session_id": s.session_id, "permalink_url": s.payment_url} if s.order_id else None,
        "messages": msgs,
        "extensions": {"in.razorpay.bazaar.india": {"pincode": q.pincode if q else "", "cod_allowed": q.cod_allowed if q else False, "gst_paise": q.gst_paise if q else 0}},
    }


def _quote_for(st, s: Session, items: list[Item], pincode: str) -> None:
    tools = st.agent(s.merchant_id).tools
    r = tools.quote([{"sku": i.id, "qty": i.quantity} for i in items], pincode, s.segment.value)
    if not r.ok:
        raise HTTPException(422, detail={"type": "invalid_request", "code": "quote_failed", "message": r.reason})
    s.quote = r.result
    s.state.update({"quote_id": r.result["quote_id"], "pincode": pincode})
    s.status = "ready_for_payment" if pincode else "open"
    st.audit.record({"session": s.session_id, "kind": "acp", "action": "quote", "outcome": "ok", "note": f"{len(items)} item(s) to {pincode or '?'}"})


@router.post("/{merchant_id}/checkout_sessions", status_code=201)
async def create(merchant_id: str, body: CreateIn, request: Request):
    st = _state(request)
    caller = await identify(request, st)
    m = st.merchant(merchant_id)
    if m is None:
        raise HTTPException(404, detail={"type": "invalid_request", "code": "merchant_not_found"})
    if m.policy.kill_switch:
        raise HTTPException(409, detail={"type": "invalid_request", "code": "merchant_unavailable"})
    s = st.new_session(merchant_id=merchant_id, agent_keyid=caller.keyid, tier=caller.tier, source="acp")
    _quote_for(st, s, body.items, body.fulfillment_address.postal_code if body.fulfillment_address else "")
    return _render(s, m)


@router.get("/{merchant_id}/checkout_sessions/{sid}")
def get(merchant_id: str, sid: str, request: Request):
    st = _state(request)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"type": "invalid_request", "code": "session_not_found"})
    return _render(s, st.merchant(merchant_id))


@router.post("/{merchant_id}/checkout_sessions/{sid}")
async def update(merchant_id: str, sid: str, body: UpdateIn, request: Request):
    st = _state(request)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"type": "invalid_request", "code": "session_not_found"})
    if s.status not in ("open", "ready_for_payment"):
        raise HTTPException(409, detail={"type": "invalid_request", "code": f"session_{s.status}"})
    items = body.items or ([Item(id=ln["sku"], quantity=ln["qty"]) for ln in s.quote["lines"]] if s.quote else [])
    pincode = body.fulfillment_address.postal_code if body.fulfillment_address else s.state.get("pincode", "")
    _quote_for(st, s, items, pincode)
    return _render(s, st.merchant(merchant_id))


@router.post("/{merchant_id}/delegate_payment", status_code=201)
async def delegate_payment(merchant_id: str, body: DelegateIn, request: Request):
    """Buyer (via the platform) authorises the agent to pay this merchant up to an allowance."""
    st = _state(request)
    caller = await identify(request, st, required_tag=TAG_PAY)
    if st.merchant(merchant_id) is None:
        raise HTTPException(404, detail={"type": "invalid_request", "code": "merchant_not_found"})
    max_amount = int(body.allowance.get("max_amount", 0))
    ttl = int(body.allowance.get("expires_in_minutes", 30))
    priv = keys.generate()
    kid = st.register_buyer_key(keys.b64u(keys.public_bytes(priv)))
    st.delegated_buyer_keys[body.buyer_ref] = (kid, priv)
    g = st.grants.issue(body.buyer_ref, caller.keyid, merchant_id, max_amount, ttl, single_use=True)
    st.audit.record({"session": "", "kind": "acp", "action": "delegate_payment", "outcome": "ok", "money": {"grant_id": g.grant_id, "max_amount_paise": max_amount}, "note": f"buyer {body.buyer_ref} via {caller.operator}"})
    return {"id": g.grant_id, "created": g.expires_at.isoformat(), "metadata": {"buyer_ref": body.buyer_ref, "merchant_id": merchant_id, "max_amount": max_amount}}


@router.post("/{merchant_id}/checkout_sessions/{sid}/complete")
async def complete(merchant_id: str, sid: str, body: CompleteIn, request: Request):
    st = _state(request)
    ik = request.headers.get("idempotency-key")
    key = f"acp:{sid}:{ik}" if ik else None
    if key and key in st.idempotency:
        code, payload = st.idempotency[key]
        return Response(json.dumps(payload), status_code=code, media_type="application/json", headers={"Idempotent-Replayed": "true"})
    caller = await identify(request, st, required_tag=TAG_PAY)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"type": "invalid_request", "code": "session_not_found"})
    if s.status != "ready_for_payment":
        raise HTTPException(409, detail={"type": "invalid_request", "code": f"session_{_acp_status(s)}"})
    token = body.payment_data.get("token", "")
    g = st.grants.get(token)
    if g is None:
        raise HTTPException(422, detail={"type": "invalid_request", "code": "invalid_payment_token"})
    kid_priv = st.delegated_buyer_keys.get(g.buyer_ref)
    if kid_priv is None:
        raise HTTPException(422, detail={"type": "invalid_request", "code": "no_delegated_authorization"})
    kid, priv = kid_priv
    q = Quote.model_validate(s.quote)
    cm = CheckoutMandate.open(g.buyer_ref, g.max_amount_paise, pincode=q.pincode, merchant_ids=[merchant_id]).close(q.quote_id, merchant_id, q.total_paise)
    cm.sign(priv, kid)
    pm = PaymentMandate.open(g.buyer_ref, g.max_amount_paise).close(cm)
    pm.sign(priv, kid)
    res, s = complete_session(st, s, caller.keyid, token, cm, pm, body.human_confirmation)
    payload = _render(s, st.merchant(merchant_id))
    payload["policy"] = {"allowed": res.allowed, "checks": [c.model_dump() for c in res.checks]}
    code = 200 if res.allowed else 422
    if key:
        st.idempotency[key] = (code, payload)
    return Response(json.dumps(payload, default=str), status_code=code, media_type="application/json")


@router.post("/{merchant_id}/checkout_sessions/{sid}/cancel")
def cancel(merchant_id: str, sid: str, request: Request):
    st = _state(request)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"type": "invalid_request", "code": "session_not_found"})
    try:
        cancel_session(st, s, "acp cancel")
    except ValueError as e:
        raise HTTPException(409, detail={"type": "invalid_request", "code": str(e)}) from e
    return _render(s, st.merchant(merchant_id))
