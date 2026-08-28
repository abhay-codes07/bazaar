"""Beckn / ONDC BPP adapter.

Every compiled merchant becomes a Beckn Provider Platform so ONDC buyer apps (and ONDC-native
agents) can reach it. Beckn is asynchronous (``search`` → ACK, then ``on_search`` callback to the
BAP); for P0 the ``on_*`` payload is returned inline *and* recorded as the callback we would POST
to ``context.bap_uri``. Flow: ``search → select → init → confirm → status``.

Payment: Beckn buyers are humans on a buyer app, so ``confirm`` issues an **embedded checkout**
(Razorpay payment link paid by the buyer) rather than an agent-paid grant. No agent moves money.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bazaar.compiler.exports import beckn_on_search
from bazaar.gateway.sessions import Session
from bazaar.seller_agent.intent import match_products
from bazaar.seller_agent.offer_engine import Quote

router = APIRouter(prefix="/beckn", tags=["beckn"])


class BecknMessage(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)
    message: dict[str, Any] = Field(default_factory=dict)


def _state(request: Request):
    return request.app.state.bazaar


def _ctx(ctx: dict[str, Any], action: str, merchant_id: str, base: str) -> dict[str, Any]:
    return {**ctx, "action": action, "bpp_id": f"bazaar.{merchant_id}", "bpp_uri": f"{base}/beckn/{merchant_id}", "timestamp": datetime.now(timezone.utc).isoformat(), "message_id": ctx.get("message_id") or secrets.token_hex(8), "transaction_id": ctx.get("transaction_id") or secrets.token_hex(8)}


def _rs(paise: int) -> str:
    return f"{paise / 100:.2f}"


def _quote_breakup(q: Quote) -> dict[str, Any]:
    breakup = [{"title": ln.name, "@ondc/org/item_id": ln.sku, "price": {"currency": "INR", "value": _rs(ln.subtotal_paise)}, "@ondc/org/title_type": "item"} for ln in q.lines]
    if q.discount_paise:
        breakup.append({"title": "Offer " + ",".join(a.rule_id for a in q.applied_offers), "price": {"currency": "INR", "value": _rs(-q.discount_paise)}, "@ondc/org/title_type": "discount"})
    breakup.append({"title": "Delivery", "price": {"currency": "INR", "value": _rs(q.delivery_fee_paise)}, "@ondc/org/title_type": "delivery"})
    breakup.append({"title": "GST", "price": {"currency": "INR", "value": _rs(q.gst_paise)}, "@ondc/org/title_type": "tax"})
    return {"price": {"currency": "INR", "value": _rs(q.total_paise)}, "breakup": breakup, "ttl": "PT30M"}


def _ack(ctx: dict[str, Any], callback_action: str, message: dict[str, Any]) -> dict[str, Any]:
    return {"message": {"ack": {"status": "ACK"}}, "callback": {"context": {**ctx, "action": callback_action}, "message": message}}


def _m(st, merchant_id: str):
    m = st.merchant(merchant_id)
    if m is None:
        raise HTTPException(404, detail={"error": "merchant_not_found"})
    return m


@router.post("/{merchant_id}/search")
def search(merchant_id: str, body: BecknMessage, request: Request):
    st = _state(request)
    m = _m(st, merchant_id)
    base = str(request.base_url).rstrip("/")
    cat = beckn_on_search(m, base)
    intent = body.message.get("intent", {})
    q = (intent.get("item", {}).get("descriptor", {}).get("name") or intent.get("descriptor", {}).get("name") or "").strip()
    pincode = (intent.get("fulfillment", {}).get("end", {}).get("location", {}).get("gps") and "") or intent.get("fulfillment", {}).get("end", {}).get("location", {}).get("address", {}).get("area_code", "")
    if pincode and not m.serviceability.serves(pincode):
        cat["message"]["catalog"]["providers"] = []
    elif q:
        hits = {p.sku for p in match_products(q, m.products, limit=10)}
        for prov in cat["message"]["catalog"]["providers"]:
            prov["items"] = [it for it in prov["items"] if it["id"] in hits]
    st.audit.record({"session": "", "kind": "beckn", "action": "search", "outcome": "ok", "note": f"{merchant_id}: q={q!r} pincode={pincode or '-'}"})
    return _ack(_ctx(body.context, "search", merchant_id, base), "on_search", cat["message"])


def _lines(msg: dict[str, Any]) -> list[dict[str, Any]]:
    items = msg.get("order", {}).get("items", [])
    return [{"sku": it.get("id", ""), "qty": int(it.get("quantity", {}).get("count", it.get("quantity", 1)) if isinstance(it.get("quantity"), dict) else it.get("quantity", 1))} for it in items]


def _pincode(msg: dict[str, Any]) -> str:
    f = msg.get("order", {}).get("fulfillments") or [msg.get("order", {}).get("fulfillment", {})]
    for x in f:
        pin = (x or {}).get("end", {}).get("location", {}).get("address", {}).get("area_code", "")
        if pin:
            return pin
    return msg.get("order", {}).get("billing", {}).get("address", {}).get("area_code", "")


@router.post("/{merchant_id}/select")
def select(merchant_id: str, body: BecknMessage, request: Request):
    st = _state(request)
    m = _m(st, merchant_id)
    base = str(request.base_url).rstrip("/")
    lines, pincode = _lines(body.message), _pincode(body.message)
    r = st.agent(merchant_id).tools.quote(lines, pincode)
    if not r.ok:
        return {"message": {"ack": {"status": "NACK"}}, "error": {"type": "DOMAIN-ERROR", "code": "30009" if "deliver" in r.reason else "40002", "message": r.reason}}
    q = Quote.model_validate(r.result)
    s = st.new_session(merchant_id=merchant_id, source="beckn")
    s.quote, s.status = r.result, "ready_for_payment"
    s.state.update({"quote_id": q.quote_id, "pincode": pincode, "transaction_id": body.context.get("transaction_id", "")})
    st.audit.record({"session": s.session_id, "kind": "beckn", "action": "select", "outcome": "ok", "note": f"{len(lines)} item(s) to {pincode or '?'}"})
    order = {"id": s.session_id, "provider": {"id": merchant_id, "descriptor": {"name": m.name}}, "items": [{"id": ln.sku, "quantity": {"count": ln.qty}} for ln in q.lines], "quote": _quote_breakup(q)}
    return _ack(_ctx(body.context, "select", merchant_id, base), "on_select", {"order": order})


@router.post("/{merchant_id}/init")
def init(merchant_id: str, body: BecknMessage, request: Request):
    st = _state(request)
    base = str(request.base_url).rstrip("/")
    sid = body.message.get("order", {}).get("id", "")
    s = st.session(sid)
    if s is None or s.source != "beckn" or s.quote is None:
        return {"message": {"ack": {"status": "NACK"}}, "error": {"type": "DOMAIN-ERROR", "code": "30004", "message": "unknown order; call select first"}}
    q = Quote.model_validate(s.quote)
    st.audit.record({"session": sid, "kind": "beckn", "action": "init", "outcome": "ok"})
    order = {"id": sid, "items": [{"id": ln.sku, "quantity": {"count": ln.qty}} for ln in q.lines], "quote": _quote_breakup(q), "payment": {"type": "ON-ORDER", "collected_by": "BPP", "status": "NOT-PAID", "@ondc/org/settlement_basis": "delivery"}, "cancellation_terms": [{"fulfillment_state": {"descriptor": {"code": "Pending"}}, "refund_eligible": True}]}
    return _ack(_ctx(body.context, "init", merchant_id, base), "on_init", {"order": order})


@router.post("/{merchant_id}/confirm")
def confirm(merchant_id: str, body: BecknMessage, request: Request):
    st = _state(request)
    m = _m(st, merchant_id)
    base = str(request.base_url).rstrip("/")
    sid = body.message.get("order", {}).get("id", "")
    s = st.session(sid)
    if s is None or s.source != "beckn" or s.quote is None:
        return {"message": {"ack": {"status": "NACK"}}, "error": {"type": "DOMAIN-ERROR", "code": "30004", "message": "unknown order; call select/init first"}}
    if s.status == "in_progress" and s.payment_url:
        pass  # idempotent re-confirm
    elif s.status != "ready_for_payment":
        return {"message": {"ack": {"status": "NACK"}}, "error": {"type": "DOMAIN-ERROR", "code": "30016", "message": f"order is {s.status}"}}
    else:
        q = Quote.model_validate(s.quote)
        problems = []
        if m.policy.kill_switch:
            problems.append("merchant disabled")
        if datetime.now(timezone.utc) > q.valid_until:
            problems.append("quote expired")
        for ln in q.lines:
            p = m.product(ln.sku)
            if p is None or p.stock < ln.qty:
                problems.append(f"insufficient stock for {ln.name}")
        if problems:
            st.audit.record({"session": sid, "kind": "beckn", "action": "confirm", "outcome": "declined", "note": "; ".join(problems)})
            return {"message": {"ack": {"status": "NACK"}}, "error": {"type": "DOMAIN-ERROR", "code": "40002", "message": "; ".join(problems)}}
        r = st.agent(merchant_id).tools.reserve(q.quote_id)
        if r.ok:
            s.reservation_id = r.result["reservation_id"]
        st.audit.record({"session": sid, "kind": "beckn", "action": "confirm", "outcome": "ok", "note": "embedded checkout: buyer pays the Razorpay link (no agent-held funds)"})
        st.issue_payment(s)
    q = Quote.model_validate(s.quote)
    order = {"id": sid, "state": "Created", "items": [{"id": ln.sku, "quantity": {"count": ln.qty}} for ln in q.lines], "quote": _quote_breakup(q), "payment": {"type": "ON-ORDER", "collected_by": "BPP", "status": "PAID" if s.payment_id else "NOT-PAID", "uri": s.payment_url, "params": {"amount": _rs(q.total_paise), "currency": "INR", "transaction_id": s.order_id}}}
    return _ack(_ctx(body.context, "confirm", merchant_id, base), "on_confirm", {"order": order})


@router.post("/{merchant_id}/status")
def status(merchant_id: str, body: BecknMessage, request: Request):
    st = _state(request)
    base = str(request.base_url).rstrip("/")
    sid = body.message.get("order_id", "") or body.message.get("order", {}).get("id", "")
    s: Session | None = st.session(sid)
    if s is None or s.merchant_id != merchant_id:
        return {"message": {"ack": {"status": "NACK"}}, "error": {"type": "DOMAIN-ERROR", "code": "30004", "message": "unknown order"}}
    state = {"completed": "Accepted", "in_progress": "Created", "canceled": "Cancelled"}.get(s.status, "Created")
    return _ack(_ctx(body.context, "status", merchant_id, base), "on_status", {"order": {"id": sid, "state": state, "payment": {"status": "PAID" if s.payment_id else "NOT-PAID", "uri": s.payment_url}}})
