"""Bazaar Gateway — FastAPI application.

Surfaces
--------
* ``/.well-known/bazaar`` · ``/.well-known/ucp`` — network manifests
* ``/bazaar/v1/*`` — discovery, sessions, checkout, agents, buyers, grants, merchant console
* ``/acp/*`` — Agentic Commerce Protocol adapter (checkout sessions + delegated payment)
* ``/webhooks/razorpay`` — payment events
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from bazaar import __version__
from bazaar.compiler.compile import compile_rows
from bazaar.compiler.exports import (
    acp_product_feed,
    export_all,
    llms_full_txt,
    llms_txt,
    well_known_bazaar,
    well_known_ucp,
)
from bazaar.compiler.ingest import read_csv_text
from bazaar.compiler.readiness import readiness_score
from bazaar.gateway.adapters.acp import router as acp_router
from bazaar.gateway.adapters.beckn import router as beckn_router
from bazaar.gateway.adapters.ucp import router as ucp_router
from bazaar.gateway.auth import identify, require_admin
from bazaar.gateway.checkout import (
    approve_review,
    cancel_session,
    complete_session,
    run_turn,
    session_summary,
)
from bazaar.gateway.discover import DiscoverRequest, discover
from bazaar.gateway.playground import router as playground_router
from bazaar.gateway.state import BazaarState
from bazaar.razorpay_client import verify_webhook_signature
from bazaar.schemas.models import AgentTier, Merchant, MerchantPolicy, OfferRule, Product, Segment
from bazaar.trust.fairness_auditor import audit_merchant
from bazaar.trust.http_sig import TAG_PAY
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate

API_VERSION = "2026-08-28"


# ----------------------------------------------------------------------------- request models
class SessionCreate(BaseModel):
    merchant_id: str
    message: str = ""
    segment: Segment = Segment.ANY
    language: str = ""


class MessageIn(BaseModel):
    message: str


class CompleteIn(BaseModel):
    grant_id: str = ""
    checkout_mandate: dict[str, Any] | None = None
    payment_mandate: dict[str, Any] | None = None
    human_confirmation: bool = False


class AgentRegister(BaseModel):
    public_key_b64u: str
    operator: str
    profile_url: str = ""


class BuyerKey(BaseModel):
    public_key_b64u: str


class GrantIn(BaseModel):
    buyer_ref: str
    merchant_id: str
    max_amount_paise: int = Field(ge=100)
    ttl_minutes: int = Field(default=30, ge=1, le=24 * 60)
    single_use: bool = True
    payment_mandate_id: str = ""


class TierIn(BaseModel):
    tier: AgentTier
    reason: str


class CompileIn(BaseModel):
    csv: str


class ReviewApply(BaseModel):
    sku: str
    field: str
    value: str


class RulesIn(BaseModel):
    rules: list[OfferRule]


def _idem_key(request: Request, body: bytes) -> str | None:
    k = request.headers.get("idempotency-key")
    if not k:
        return None
    return f"{request.method}:{request.url.path}:{k}:{hashlib.sha256(body).hexdigest()[:16]}"


def create_app(state: BazaarState | None = None, load_corpus: bool = False) -> FastAPI:
    st = state or BazaarState()
    if load_corpus and not st.merchants:
        from bazaar.synthetic import load_corpus as _load

        for m in _load(st.settings.data_dir / "synthetic"):
            st.add_merchant(m)

    app = FastAPI(title="Bazaar Gateway", version=__version__, docs_url="/docs")
    app.state.bazaar = st
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["Request-Id", "API-Version"])

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        rid = request.headers.get("request-id") or "req_" + secrets.token_hex(6)
        resp: Response = await call_next(request)
        resp.headers["Request-Id"] = rid
        resp.headers["API-Version"] = API_VERSION
        return resp

    base = lambda r: str(r.base_url).rstrip("/")  # noqa: E731

    # ------------------------------------------------------------------ manifests
    @app.get("/.well-known/bazaar")
    def network_manifest(request: Request):
        return {"bazaar": {"version": API_VERSION, "network": "razorpay-bazaar", "services": {"discover": f"{base(request)}/bazaar/v1/discover", "sessions": f"{base(request)}/bazaar/v1/sessions", "agents": f"{base(request)}/bazaar/v1/agents/register", "acp": f"{base(request)}/acp", "ucp": f"{base(request)}/ucp", "beckn": f"{base(request)}/beckn"}, "merchants": len(st.merchants), "extensions": ["in.razorpay.bazaar.india"], "signature": {"alg": "ed25519", "spec": "RFC 9421", "tags": ["agent-browse", "agent-pay"]}}}

    @app.get("/.well-known/ucp")
    def network_ucp(request: Request):
        return {"ucp": {"version": "2026-01-11", "services": {"dev.ucp.shopping": {"rest_endpoint": f"{base(request)}/ucp"}}, "capabilities": [{"name": "dev.ucp.shopping.checkout"}, {"name": "in.razorpay.bazaar.india", "version": API_VERSION}]}}

    @app.get("/bazaar/v1/merchants")
    def list_merchants():
        return [{"merchant_id": m.merchant_id, "name": m.name, "vertical": m.vertical.value, "city": m.city, "skus": len(m.products), "readiness": st.readiness_cache.get(m.merchant_id, 0), "kill_switch": m.policy.kill_switch, "review_first": m.policy.review_first} for m in st.merchants.values()]

    def _m(mid: str) -> Merchant:
        m = st.merchant(mid)
        if m is None:
            raise HTTPException(404, detail={"error": "merchant_not_found"})
        return m

    @app.get("/bazaar/v1/merchants/{mid}/manifest")
    def merchant_manifest(mid: str, request: Request):
        return well_known_bazaar(_m(mid), base(request))

    @app.get("/bazaar/v1/merchants/{mid}/ucp")
    def merchant_ucp(mid: str, request: Request):
        return well_known_ucp(_m(mid), base(request))

    @app.get("/bazaar/v1/merchants/{mid}/catalog")
    def merchant_catalog(mid: str):
        m = _m(mid)
        return {"merchant_id": m.merchant_id, "name": m.name, "products": [p.model_dump(mode="json") for p in m.products]}

    @app.get("/bazaar/v1/merchants/{mid}/llms.txt")
    def merchant_llms(mid: str, request: Request, full: bool = False):
        m = _m(mid)
        return Response((llms_full_txt if full else llms_txt)(m, base(request)), media_type="text/plain; charset=utf-8")

    @app.get("/bazaar/v1/merchants/{mid}/exports")
    def merchant_exports(mid: str, request: Request):
        return export_all(_m(mid), base(request))

    # ------------------------------------------------------------------ discovery
    @app.post("/bazaar/v1/discover")
    def discover_ep(req: DiscoverRequest):
        return {"candidates": [c.model_dump() for c in discover(req, list(st.merchants.values()), st.readiness_cache)]}

    # ------------------------------------------------------------------ agents / buyers / grants
    @app.post("/bazaar/v1/agents/register", status_code=201)
    def register_agent(body: AgentRegister):
        from bazaar.trust import keys

        ident = st.registry.register(keys.b64u_decode(body.public_key_b64u), body.operator, body.profile_url)
        return ident.model_dump(mode="json")

    @app.get("/bazaar/v1/agents/{keyid}")
    def get_agent(keyid: str):
        a = st.registry.get(keyid)
        if a is None:
            raise HTTPException(404, detail={"error": "agent_not_found"})
        return a.model_dump(mode="json")

    @app.post("/bazaar/v1/agents/{keyid}/tier")
    def set_tier(keyid: str, body: TierIn, request: Request):
        require_admin(request, st)
        return st.registry.set_tier(keyid, body.tier, body.reason).model_dump(mode="json")

    @app.post("/bazaar/v1/buyers/keys", status_code=201)
    def buyer_key(body: BuyerKey):
        return {"keyid": st.register_buyer_key(body.public_key_b64u)}

    @app.post("/bazaar/v1/grants", status_code=201)
    async def issue_grant(body: GrantIn, request: Request):
        caller = await identify(request, st, required_tag=TAG_PAY)
        _m(body.merchant_id)
        g = st.grants.issue(body.buyer_ref, caller.keyid, body.merchant_id, body.max_amount_paise, body.ttl_minutes, body.single_use, payment_mandate_id=body.payment_mandate_id)
        st.audit.record({"session": "", "kind": "grant", "action": "issue", "outcome": "ok", "money": {"grant_id": g.grant_id, "max_amount_paise": g.max_amount_paise}, "note": f"agent {caller.keyid} for merchant {body.merchant_id}"})
        return g.model_dump(mode="json")

    @app.post("/bazaar/v1/grants/{grant_id}/revoke")
    def revoke_grant(grant_id: str):
        if st.grants.get(grant_id) is None:
            raise HTTPException(404, detail={"error": "grant_not_found"})
        st.grants.revoke(grant_id, "revoked via API")
        return {"grant_id": grant_id, "revoked": True}

    # ------------------------------------------------------------------ sessions
    @app.post("/bazaar/v1/sessions", status_code=201)
    async def create_session(body: SessionCreate, request: Request):
        caller = await identify(request, st)
        m = _m(body.merchant_id)
        if m.policy.kill_switch:
            raise HTTPException(409, detail={"error": "merchant_agent_disabled"})
        s = st.new_session(merchant_id=m.merchant_id, agent_keyid=caller.keyid, tier=caller.tier, segment=body.segment, language=body.language or "en")
        st.audit.record({"session": s.session_id, "kind": "session", "action": "create", "outcome": "ok", "note": f"agent={caller.keyid or 'unsigned'} tier={int(caller.tier)}"})
        if body.message:
            return run_turn(st, s, body.message, caller.keyid, caller.tier)
        return {"session": session_summary(s), "turn": None}

    @app.get("/bazaar/v1/sessions/{sid}")
    def get_session(sid: str):
        s = st.session(sid)
        if s is None:
            raise HTTPException(404, detail={"error": "session_not_found"})
        return session_summary(s)

    @app.post("/bazaar/v1/sessions/{sid}/messages")
    async def message(sid: str, body: MessageIn, request: Request):
        caller = await identify(request, st)
        s = st.session(sid)
        if s is None:
            raise HTTPException(404, detail={"error": "session_not_found"})
        if s.status in ("completed", "canceled", "declined"):
            raise HTTPException(409, detail={"error": f"session_{s.status}"})
        if s.agent_keyid and caller.keyid and caller.keyid != s.agent_keyid:
            raise HTTPException(403, detail={"error": "session_belongs_to_another_agent"})
        return run_turn(st, s, body.message, caller.keyid, caller.tier if caller.keyid else s.tier)

    @app.post("/bazaar/v1/sessions/{sid}/complete")
    async def complete(sid: str, request: Request, body: CompleteIn):
        raw = await request.body()
        ik = _idem_key(request, raw)
        if ik and ik in st.idempotency:
            code, payload = st.idempotency[ik]
            return Response(json.dumps(payload), status_code=code, media_type="application/json", headers={"Idempotent-Replayed": "true"})
        caller = await identify(request, st, required_tag=TAG_PAY)
        s = st.session(sid)
        if s is None:
            raise HTTPException(404, detail={"error": "session_not_found"})
        if s.status == "in_progress" and s.payment_url:
            payload = {"session": session_summary(s), "allowed": True, "checks": s.last_checks, "payment": {"order_id": s.order_id, "payment_url": s.payment_url}}
            return payload
        if s.status not in ("ready_for_payment", "open"):
            raise HTTPException(409, detail={"error": f"session_{s.status}"})
        cm = CheckoutMandate.model_validate(body.checkout_mandate) if body.checkout_mandate else None
        pm = PaymentMandate.model_validate(body.payment_mandate) if body.payment_mandate else None
        res, s = complete_session(st, s, caller.keyid, body.grant_id, cm, pm, body.human_confirmation)
        payload = {"session": session_summary(s), "allowed": res.allowed, "needs_merchant_review": res.needs_merchant_review, "checks": [c.model_dump() if hasattr(c, "model_dump") else c for c in res.checks], "reason": res.reason if not res.allowed else "", "payment": {"order_id": s.order_id, "payment_url": s.payment_url} if s.payment_url else None}
        code = 200 if res.allowed else 422
        if ik:
            st.idempotency[ik] = (code, payload)
        return Response(json.dumps(payload, default=str), status_code=code, media_type="application/json")

    @app.post("/bazaar/v1/sessions/{sid}/cancel")
    async def cancel(sid: str, request: Request, reason: str = "buyer canceled"):
        s = st.session(sid)
        if s is None:
            raise HTTPException(404, detail={"error": "session_not_found"})
        try:
            return session_summary(cancel_session(st, s, reason))
        except ValueError as e:
            raise HTTPException(409, detail={"error": str(e)}) from e

    @app.get("/bazaar/v1/sessions/{sid}/replay")
    def replay(sid: str):
        if st.session(sid) is None:
            raise HTTPException(404, detail={"error": "session_not_found"})
        ok, bad = st.audit.verify_chain()
        return {"session_id": sid, "chain_ok": ok, "first_bad_seq": bad, "timeline": st.audit.replay(sid)}

    # ------------------------------------------------------------------ webhooks
    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(request: Request):
        raw = await request.body()
        sig = request.headers.get("x-razorpay-signature", "")
        if not verify_webhook_signature(raw, sig, st.settings.razorpay_webhook_secret):
            raise HTTPException(400, detail={"error": "bad_webhook_signature"})
        ev = json.loads(raw)
        return st.handle_webhook_event(ev["event"], ev["payload"])

    # ------------------------------------------------------------------ merchant console
    @app.get("/bazaar/v1/merchants/{mid}")
    def merchant_detail(mid: str):
        m = _m(mid)
        rd = readiness_score(m)
        return {"merchant": m.model_dump(mode="json", exclude={"products"}), "products": len(m.products), "readiness": rd.model_dump(), "review_queue": st.review_queues.get(mid, []), "pending_products": len(st.pending_catalogs.get(mid, [])), "sessions": [session_summary(s) for s in st.sessions.values() if s.merchant_id == mid][-50:]}

    @app.put("/bazaar/v1/merchants/{mid}/policy")
    def put_policy(mid: str, body: MerchantPolicy):
        m = _m(mid)
        m.policy = body
        st.invalidate_readiness(mid)
        st.audit.record({"session": "", "kind": "merchant", "action": "policy_updated", "outcome": "ok", "note": mid})
        return m.policy.model_dump(mode="json")

    @app.post("/bazaar/v1/merchants/{mid}/kill-switch")
    def kill_switch(mid: str, on: bool = True):
        m = _m(mid)
        m.policy.kill_switch = on
        st.audit.record({"session": "", "kind": "merchant", "action": "kill_switch", "outcome": "on" if on else "off", "note": mid})
        return {"merchant_id": mid, "kill_switch": on}

    @app.put("/bazaar/v1/merchants/{mid}/rules")
    def put_rules(mid: str, body: RulesIn):
        m = _m(mid)
        candidate = m.model_copy(update={"offer_rules": body.rules})
        rep = audit_merchant(candidate)
        if not rep.passed:
            raise HTTPException(422, detail={"error": "fairness_audit_failed", "findings": [f.model_dump() for f in rep.findings]})
        m.offer_rules = body.rules
        st.invalidate_readiness(mid)
        st.audit.record({"session": "", "kind": "merchant", "action": "rules_published", "outcome": "ok", "note": f"{mid}: {[r.rule_id for r in body.rules]} (fairness cohorts={rep.cohorts})"})
        return {"rules": [r.model_dump(mode="json") for r in m.offer_rules], "fairness": rep.model_dump()}

    @app.get("/bazaar/v1/merchants/{mid}/fairness")
    def fairness(mid: str):
        rep = audit_merchant(_m(mid))
        return {**rep.model_dump(), "passed": rep.passed, "ledger": st.ledger.summary()}

    @app.post("/bazaar/v1/merchants/{mid}/compile")
    def compile_ep(mid: str, body: CompileIn):
        m = _m(mid)
        try:
            rows = read_csv_text(body.csv)
        except ValueError as e:
            raise HTTPException(422, detail={"error": str(e)}) from e
        compiled = compile_rows(rows, m.model_copy(update={"products": []}), st.llm)
        st.pending_catalogs[mid] = [p.model_dump(mode="json") for p in compiled.merchant.products]
        st.review_queues[mid] = [r.model_dump() for r in compiled.review_queue]
        rd = readiness_score(compiled.merchant)
        st.audit.record({"session": "", "kind": "merchant", "action": "compiled", "outcome": "ok", "note": f"{mid}: {compiled.rows_in} rows, {len(compiled.review_queue)} review items, {compiled.stripped_injections} injections stripped"})
        return {"products": len(compiled.merchant.products), "review_queue": st.review_queues[mid], "stripped_injections": compiled.stripped_injections, "readiness": rd.model_dump(), "preview": st.pending_catalogs[mid][:20]}

    @app.post("/bazaar/v1/merchants/{mid}/review/apply")
    def review_apply(mid: str, body: ReviewApply):
        _m(mid)
        pend = st.pending_catalogs.get(mid)
        if not pend:
            raise HTTPException(409, detail={"error": "nothing_to_review"})
        for p in pend:
            if p["sku"] == body.sku:
                if body.field == "price":
                    p["price_paise"] = int(round(float(body.value) * 100))
                    p["confidence"]["price"] = 1.0
                elif body.field == "stock":
                    p["stock"] = int(body.value)
                    p["confidence"]["stock"] = 1.0
                elif body.field == "gst":
                    p["gst_rate_bp"] = int(round(float(body.value.rstrip("%")) * 100))
                    p["confidence"]["gst"] = 1.0
                elif body.field == "unit":
                    qty, unit = body.value.split()
                    p["pack_size"], p["unit"] = float(qty), unit
                    p["confidence"]["unit"] = 1.0
                elif body.field == "name":
                    p["name"] = body.value
                    p["confidence"]["name"] = 1.0
                elif body.field == "description":
                    p["description"] = body.value
                    p["flags"] = [f for f in p["flags"] if f != "instruction_like_text_stripped"]
                else:
                    raise HTTPException(422, detail={"error": f"unknown field {body.field}"})
                st.review_queues[mid] = [r for r in st.review_queues.get(mid, []) if not (r["sku"] == body.sku and r["field"] == body.field)]
                return {"sku": body.sku, "field": body.field, "remaining": len(st.review_queues[mid])}
        raise HTTPException(404, detail={"error": "sku_not_pending"})

    @app.post("/bazaar/v1/merchants/{mid}/publish")
    def publish(mid: str):
        m = _m(mid)
        pend = st.pending_catalogs.pop(mid, None)
        if pend is None:
            raise HTTPException(409, detail={"error": "nothing_to_publish"})
        m.products = [Product.model_validate(p) for p in pend]
        st.review_queues.pop(mid, None)
        st.add_merchant(m)  # rebuilds the seller agent on the new catalog
        rd = readiness_score(m)
        st.audit.record({"session": "", "kind": "merchant", "action": "published", "outcome": "ok", "note": f"{mid}: {len(m.products)} products, readiness {rd.score}"})
        return {"merchant_id": mid, "products": len(m.products), "readiness": rd.model_dump(), "endpoints": {"manifest": f"/bazaar/v1/merchants/{mid}/manifest", "mcp": f"/mcp/{mid}", "llms": f"/bazaar/v1/merchants/{mid}/llms.txt"}}

    @app.post("/bazaar/v1/merchants/{mid}/review-sessions/{sid}/approve")
    def approve(mid: str, sid: str):
        s = st.session(sid)
        if s is None or s.merchant_id != mid:
            raise HTTPException(404, detail={"error": "session_not_found"})
        try:
            return session_summary(approve_review(st, s))
        except ValueError as e:
            raise HTTPException(409, detail={"error": str(e)}) from e

    @app.get("/bazaar/v1/merchants/{mid}/audit")
    def merchant_audit(mid: str, limit: int = 100):
        sess = {s.session_id for s in st.sessions.values() if s.merchant_id == mid}
        rows = [e for e in st.audit.entries if e.get("session") in sess or (e.get("kind") == "merchant" and mid in str(e.get("note", "")))]
        ok, bad = st.audit.verify_chain()
        def _row(e: dict[str, Any]) -> dict[str, Any]:
            r = {k: e.get(k) for k in ("seq", "at", "audit_id", "session", "outcome", "note", "money", "hash")}
            r["kind"] = e.get("kind") or "agent_turn"
            r["action"] = e.get("action") or (e.get("proposal") or {}).get("tool") or ""
            r["note"] = r["note"] or (", ".join(c["name"] for c in e.get("checks", []) if not c.get("passed")) and "failed: " + ", ".join(c["name"] for c in e.get("checks", []) if not c.get("passed")))
            return r

        return {"chain_ok": ok, "first_bad_seq": bad, "merkle_root": st.audit.merkle_root(), "entries": [_row(e) for e in rows[-limit:]]}

    @app.get("/bazaar/v1/merchants/{mid}/acp/feed")
    def acp_feed(mid: str, request: Request):
        return {"items": acp_product_feed(_m(mid), base(request))}

    @app.get("/bazaar/v1/stats")
    def stats():
        ss = list(st.sessions.values())
        return {"merchants": len(st.merchants), "agents": len(st.registry.all()), "sessions": len(ss), "completed": sum(s.status == "completed" for s in ss), "gmv_paise": sum((s.quote or {}).get("total_paise", 0) for s in ss if s.status == "completed"), "audit_entries": len(st.audit.entries), "chain_ok": st.audit.verify_chain()[0], "ledger": st.ledger.summary()}

    app.include_router(acp_router)
    app.include_router(ucp_router)
    app.include_router(beckn_router)
    app.include_router(playground_router)
    _mount_console(app)
    return app


def _mount_console(app: FastAPI) -> None:
    """Serve the built merchant console (console/dist) from the gateway so one process = one URL.
    API routes are registered first, so they take precedence; anything else falls back to the SPA."""
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    dist = Path(__file__).resolve().parents[2] / "console" / "dist"
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="console-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def console_spa(path: str):
        target = dist / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(dist / "index.html")


def default_app() -> FastAPI:
    st = BazaarState(audit_path=Path(get_settings_data_dir()) / "runtime" / "audit.jsonl")
    return create_app(st, load_corpus=True)


def get_settings_data_dir() -> str:
    from bazaar.settings import get_settings

    return str(get_settings().data_dir)
