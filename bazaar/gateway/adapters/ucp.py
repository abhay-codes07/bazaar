"""Universal Commerce Protocol (Google/Shopify) adapter.

UCP buyers (Gemini / AI Mode / Shopify-style agents) speak checkout sessions with AP2 mandates.
Bazaar maps them 1:1 onto its own sessions, so the same policy gate applies.

* ``GET  /ucp/{merchant}/.well-known/ucp``            merchant profile (capabilities + india extension)
* ``POST /ucp/{merchant}/checkout-sessions``          {line_items:[{item:{id},quantity}], fulfillment:{postal_code}}
* ``PUT  /ucp/{merchant}/checkout-sessions/{id}``     update line items / fulfillment
* ``POST /ucp/{merchant}/checkout-sessions/{id}/complete``
      {payment:{handler:"razorpay", grant_id}, checkout_mandate, payment_mandate, human_confirmation}
* ``GET  /ucp/{merchant}/checkout-sessions/{id}``
* ``POST /ucp/{merchant}/checkout-sessions/{id}/cancel``

Statuses follow UCP: ``incomplete | ready_for_complete | complete | canceled``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from bazaar.compiler.exports import well_known_ucp
from bazaar.gateway.auth import identify
from bazaar.gateway.checkout import cancel_session, complete_session
from bazaar.gateway.sessions import Session
from bazaar.seller_agent.offer_engine import Quote
from bazaar.trust.http_sig import TAG_PAY
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate

router = APIRouter(prefix="/ucp", tags=["ucp"])


class LineItem(BaseModel):
    item: dict[str, Any]  # {"id": sku}
    quantity: int = Field(ge=1)


class Fulfillment(BaseModel):
    postal_code: str = ""
    country: str = "IN"


class CreateIn(BaseModel):
    line_items: list[LineItem]
    fulfillment: Fulfillment | None = None
    buyer: dict[str, Any] | None = None


class UpdateIn(BaseModel):
    line_items: list[LineItem] | None = None
    fulfillment: Fulfillment | None = None


class CompleteIn(BaseModel):
    payment: dict[str, Any]  # {"handler": "razorpay", "grant_id": "spg_..."}
    checkout_mandate: dict[str, Any] | None = None
    payment_mandate: dict[str, Any] | None = None
    human_confirmation: bool = False  # must be asserted explicitly; omitting it cannot satisfy the human-present gate


def _state(request: Request):
    return request.app.state.bazaar


def _status(s: Session) -> str:
    return {"open": "incomplete", "ready_for_payment": "ready_for_complete", "awaiting_merchant_review": "ready_for_complete", "in_progress": "complete", "completed": "complete", "canceled": "canceled", "declined": "ready_for_complete"}[s.status]


def _render(s: Session, base: str) -> dict[str, Any]:
    q = Quote.model_validate(s.quote) if s.quote else None
    failed = [c["name"] for c in s.last_checks if not c["passed"]]
    return {
        "id": s.session_id,
        "status": _status(s),
        "currency": "INR",
        "line_items": [{"id": ln.sku, "item": {"id": ln.sku, "title": ln.name}, "quantity": ln.qty, "unit_price": ln.unit_price_paise, "subtotal": ln.subtotal_paise, "tax": ln.gst_paise} for ln in q.lines] if q else [],
        "totals": [{"type": "subtotal", "amount": q.subtotal_paise}, {"type": "discount", "amount": q.discount_paise}, {"type": "fulfillment", "amount": q.delivery_fee_paise}, {"type": "tax", "amount": q.gst_paise}, {"type": "total", "amount": q.total_paise}] if q else [],
        "fulfillment": {"postal_code": q.pincode, "eta_hours": q.eta_hours, "cod_allowed": q.cod_allowed} if q else None,
        "payment": {"handlers": [{"id": "razorpay", "methods": ["upi", "card", "netbanking"], "mandates": ["ap2.checkout", "ap2.payment"], "grant_types": ["scoped_grant"]}], "status": "captured" if s.payment_id else ("link_issued" if s.payment_url else "unpaid")},
        "order": {"id": s.order_id, "payment_url": s.payment_url, "payment_id": s.payment_id} if s.order_id else None,
        "messages": [{"type": "error", "code": "policy_declined", "content": ", ".join(failed)}] if failed and s.status != "completed" else [],
        "extensions": {"in.razorpay.bazaar.india": {"gst_paise": q.gst_paise if q else 0, "pincode": q.pincode if q else ""}},
        "links": {"self": f"{base}/ucp/{s.merchant_id}/checkout-sessions/{s.session_id}", "replay": f"{base}/bazaar/v1/sessions/{s.session_id}/replay"},
    }


def _quote(st, s: Session, items: list[LineItem], pincode: str) -> None:
    r = st.agent(s.merchant_id).tools.quote([{"sku": i.item.get("id", ""), "qty": i.quantity} for i in items], pincode, s.segment.value)
    if not r.ok:
        raise HTTPException(422, detail={"error": "quote_failed", "message": r.reason})
    s.quote = r.result
    s.state.update({"quote_id": r.result["quote_id"], "pincode": pincode})
    s.status = "ready_for_payment" if pincode else "open"
    st.audit.record({"session": s.session_id, "kind": "ucp", "action": "quote", "outcome": "ok", "note": f"{len(items)} line(s) to {pincode or '?'}"})


@router.get("/{merchant_id}/.well-known/ucp")
def profile(merchant_id: str, request: Request):
    st = _state(request)
    m = st.merchant(merchant_id)
    if m is None:
        raise HTTPException(404, detail={"error": "merchant_not_found"})
    return well_known_ucp(m, str(request.base_url).rstrip("/"))


@router.post("/{merchant_id}/checkout-sessions", status_code=201)
async def create(merchant_id: str, body: CreateIn, request: Request):
    st = _state(request)
    caller = await identify(request, st)
    m = st.merchant(merchant_id)
    if m is None:
        raise HTTPException(404, detail={"error": "merchant_not_found"})
    if m.policy.kill_switch:
        raise HTTPException(409, detail={"error": "merchant_unavailable"})
    s = st.new_session(merchant_id=merchant_id, agent_keyid=caller.keyid, tier=caller.tier, source="ucp")
    _quote(st, s, body.line_items, body.fulfillment.postal_code if body.fulfillment else "")
    return _render(s, str(request.base_url).rstrip("/"))


@router.get("/{merchant_id}/checkout-sessions/{sid}")
def get(merchant_id: str, sid: str, request: Request):
    st = _state(request)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"error": "session_not_found"})
    return _render(s, str(request.base_url).rstrip("/"))


async def _require_owner(request, st, s):
    if s.agent_keyid:
        caller = await identify(request, st)
        if caller.keyid != s.agent_keyid:
            raise HTTPException(403, detail={"error": "session_belongs_to_another_agent"})


@router.put("/{merchant_id}/checkout-sessions/{sid}")
async def update(merchant_id: str, sid: str, body: UpdateIn, request: Request):
    st = _state(request)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"error": "session_not_found"})
    await _require_owner(request, st, s)
    if s.status not in ("open", "ready_for_payment"):
        raise HTTPException(409, detail={"error": f"session_{_status(s)}"})
    items = body.line_items or ([LineItem(item={"id": ln["sku"]}, quantity=ln["qty"]) for ln in s.quote["lines"]] if s.quote else [])
    pincode = body.fulfillment.postal_code if body.fulfillment else s.state.get("pincode", "")
    _quote(st, s, items, pincode)
    return _render(s, str(request.base_url).rstrip("/"))


@router.post("/{merchant_id}/checkout-sessions/{sid}/complete")
async def complete(merchant_id: str, sid: str, body: CompleteIn, request: Request):
    st = _state(request)
    ik = request.headers.get("idempotency-key")
    key = f"ucp:{sid}:{ik}" if ik else None
    if key and key in st.idempotency:
        code, payload = st.idempotency[key]
        return Response(json.dumps(payload), status_code=code, media_type="application/json", headers={"Idempotent-Replayed": "true"})
    caller = await identify(request, st, required_tag=TAG_PAY)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"error": "session_not_found"})
    if s.status != "ready_for_payment":
        raise HTTPException(409, detail={"error": f"session_{_status(s)}"})
    if body.payment.get("handler", "razorpay") != "razorpay":
        raise HTTPException(422, detail={"error": "unsupported_payment_handler"})
    cm = CheckoutMandate.model_validate(body.checkout_mandate) if body.checkout_mandate else None
    pm = PaymentMandate.model_validate(body.payment_mandate) if body.payment_mandate else None
    res, s = complete_session(st, s, caller.keyid, body.payment.get("grant_id", ""), cm, pm, body.human_confirmation)
    payload = _render(s, str(request.base_url).rstrip("/"))
    payload["policy"] = {"allowed": res.allowed, "needs_merchant_review": res.needs_merchant_review, "checks": [c.model_dump() for c in res.checks]}
    code = 200 if res.allowed else 422
    if key:
        st.idempotency[key] = (code, payload)
    return Response(json.dumps(payload, default=str), status_code=code, media_type="application/json")


@router.post("/{merchant_id}/checkout-sessions/{sid}/cancel")
async def cancel(merchant_id: str, sid: str, request: Request):
    st = _state(request)
    s = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        raise HTTPException(404, detail={"error": "session_not_found"})
    await _require_owner(request, st, s)
    try:
        cancel_session(st, s, "ucp cancel")
    except ValueError as e:
        raise HTTPException(409, detail={"error": str(e)}) from e
    return _render(s, str(request.base_url).rstrip("/"))
