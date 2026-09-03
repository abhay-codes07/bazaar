"""In-process network state: merchants, seller agents, trust fabric, payments, sessions.

P0 keeps everything in memory (with the audit log on disk). Every collection is behind a
narrow method so swapping in Postgres/Redis in P1 touches only this module.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bazaar.compiler.readiness import readiness_score
from bazaar.gateway.sessions import Session
from bazaar.llm import LLM, get_llm
from bazaar.razorpay_client import PaymentsClient, get_payments_client
from bazaar.razorpay_client.fake import FakeRazorpay
from bazaar.schemas.models import Merchant
from bazaar.seller_agent.agent import SellerAgent
from bazaar.seller_agent.offer_engine import Quote
from bazaar.settings import Settings, get_settings
from bazaar.trust import keys
from bazaar.trust.audit import AuditLog
from bazaar.trust.grants import GrantStore
from bazaar.trust.http_sig import NonceCache
from bazaar.trust.ledger import FairnessLedger, LedgerEntry
from bazaar.trust.policy import PolicyEngine
from bazaar.trust.registry import AgentRegistry


class BazaarState:
    def __init__(self, settings: Settings | None = None, payments: PaymentsClient | None = None, llm: LLM | None = None, audit_path: Path | None = None):
        self.settings = settings or get_settings()
        self.payments = payments or get_payments_client(self.settings.bazaar_razorpay)
        self.llm = llm or get_llm(self.settings.bazaar_llm)
        self.audit = AuditLog(audit_path)
        if hasattr(self.llm, "on_failover"):
            # degraded mode is never silent: every model failover lands on the audit chain
            self.llm.on_failover = lambda info: self.audit.record(
                {"kind": "ops", "action": "llm_failover", "outcome": "degraded", "note": f"model call failed ({info['reason']}); deterministic fallback answered task '{info['task']}'"}
            )
        self.registry = AgentRegistry()
        self.grants = GrantStore()
        self.policy = PolicyEngine(self.registry, self.grants)
        self.ledger = FairnessLedger()
        self.nonces = NonceCache()
        self.merchants: dict[str, Merchant] = {}
        self.agents: dict[str, SellerAgent] = {}
        self.sessions: dict[str, Session] = {}
        self.review_queues: dict[str, list[dict[str, Any]]] = {}
        self.pending_catalogs: dict[str, list[dict[str, Any]]] = {}
        self.buyer_keys: dict[str, Any] = {}  # keyid -> Ed25519PublicKey
        self.delegated_buyer_keys: dict[str, tuple[str, Any]] = {}  # buyer_ref -> (keyid, private key) for ACP delegated auth
        self.idempotency: dict[str, tuple[int, dict[str, Any]]] = {}
        self.readiness_cache: dict[str, int] = {}
        self.grant_events: list[dict[str, Any]] = []
        self.processed_payments: set[str] = set()
        self._lock = threading.RLock()
        self.grants.on_event(lambda ev, d: self.grant_events.append({"event": ev, **d}))
        if isinstance(self.payments, FakeRazorpay):
            self.payments.on_webhook(lambda ev, body, sig: self.handle_webhook_event(ev.event, ev.payload))

    # ------------------------------------------------------------------ merchants
    def add_merchant(self, m: Merchant) -> None:
        with self._lock:
            self.merchants[m.merchant_id] = m
            self.agents[m.merchant_id] = SellerAgent(m, llm=self.llm, audit=self.audit)
            self.readiness_cache[m.merchant_id] = readiness_score(m).score

    def merchant(self, merchant_id: str) -> Merchant | None:
        return self.merchants.get(merchant_id)

    def agent(self, merchant_id: str) -> SellerAgent:
        return self.agents[merchant_id]

    def invalidate_readiness(self, merchant_id: str) -> None:
        m = self.merchants[merchant_id]
        self.readiness_cache[merchant_id] = readiness_score(m).score

    # ------------------------------------------------------------------ buyers
    def register_buyer_key(self, public_key_b64u: str) -> str:
        raw = keys.b64u_decode(public_key_b64u)
        kid = keys.keyid_for(raw)
        self.buyer_keys[kid] = keys.public_from_bytes(raw)
        return kid

    def buyer_pub(self, keyid: str):
        return self.buyer_keys.get(keyid)

    def agent_pub(self, keyid: str):
        a = self.registry.get(keyid)
        return a.public_key() if a and not a.revoked else None

    # ------------------------------------------------------------------ sessions
    def new_session(self, **kw) -> Session:
        s = Session(**kw)
        with self._lock:
            self.sessions[s.session_id] = s
        return s

    def session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def session_by_order(self, order_id: str) -> Session | None:
        return next((s for s in self.sessions.values() if s.order_id == order_id), None)

    # ------------------------------------------------------------------ money
    def issue_payment(self, s: Session) -> Session:
        """Create the Razorpay order + UPI payment link for a policy-approved session."""
        q = Quote.model_validate(s.quote)
        m = self.merchants[s.merchant_id]
        link = self.payments.create_upi_payment_link(q.total_paise, f"{m.name}: {len(q.lines)} item(s)", reference_id=s.session_id, notes={"session_id": s.session_id, "merchant_id": m.merchant_id, "quote_id": q.quote_id})
        s.order_id, s.payment_link_id, s.payment_url, s.status = link.order_id, link.id, link.short_url, "in_progress"
        s.touch()
        self.audit.record({"session": s.session_id, "kind": "money", "action": "payment_link_created", "outcome": "ok", "money": {"order_id": link.order_id, "payment_link_id": link.id, "amount_paise": q.total_paise, "currency": "INR"}, "note": "awaiting buyer payment"})
        return s

    def handle_webhook_event(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if event == "payment.captured":
                pay = payload["payment"]
                if pay["id"] in self.processed_payments:
                    return {"status": "duplicate"}
                s = self.session_by_order(pay["order_id"])
                if s is None:
                    return {"status": "unknown_order"}
                self.processed_payments.add(pay["id"])
                s.payment_id, s.status = pay["id"], "completed"
                s.touch()
                q = Quote.model_validate(s.quote)
                if s.reservation_id:
                    self.agent(s.merchant_id).tools.commit_stock(s.reservation_id)
                else:
                    for ln in q.lines:
                        p = self.merchants[s.merchant_id].product(ln.sku)
                        if p:
                            p.stock = max(0, p.stock - ln.qty)
                if s.grant_id and self.grants.get(s.grant_id):
                    self.grants.use(s.grant_id, q.total_paise, s.session_id, s.order_id)
                for a in q.applied_offers:
                    self.ledger.record(LedgerEntry(merchant_id=s.merchant_id, rule_id=a.rule_id, rule_version=a.rule_version, segment_predicate=a.segment_predicate, inputs_hash=a.inputs_hash, discount_paise=a.discount_paise, session_id=s.session_id, agent_keyid=s.agent_keyid))
                self.audit.record({"session": s.session_id, "kind": "money", "action": "payment_captured", "outcome": "ok", "money": {"order_id": s.order_id, "payment_id": pay["id"], "amount_paise": pay["amount_paise"], "method": pay.get("method", "")}, "note": "order completed; stock committed; grant used"})
                return {"status": "completed", "session_id": s.session_id}
            if event == "payment.failed":
                pay = payload["payment"]
                s = self.session_by_order(pay["order_id"])
                if s is None:
                    return {"status": "unknown_order"}
                self.audit.record({"session": s.session_id, "kind": "money", "action": "payment_failed", "outcome": "failed", "money": {"order_id": s.order_id, "payment_id": pay["id"]}, "note": "buyer may retry the same payment link; no silent retry"})
                return {"status": "failed", "session_id": s.session_id}
            if event == "refund.processed":
                r = payload["refund"]
                self.audit.record({"session": payload.get("session_id", ""), "kind": "money", "action": "refund_processed", "outcome": "ok", "money": {"refund_id": r["id"], "payment_id": r["payment_id"], "amount_paise": r["amount_paise"]}})
                return {"status": "refunded"}
            return {"status": "ignored", "event": event}

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
