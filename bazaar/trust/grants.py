"""Scoped Payment Grants — the Bazaar analogue of Stripe's Shared Payment Token.

A grant lets an agent pay *one merchant*, *up to an amount*, *until a time*, and can be revoked
at any moment. Every use is recorded and emitted as an event. In production the grant maps to a
UPI Reserve Pay / Autopay mandate — the blocked-funds + delegated-limit primitives that NPCI's
Unified Agent Protocol (UAP) is built on — so a UAP binding is an adapter, not a redesign.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field


class GrantUse(BaseModel):
    at: datetime
    amount_paise: int
    session_id: str
    order_id: str


class ScopedPaymentGrant(BaseModel):
    grant_id: str = Field(default_factory=lambda: "spg_" + secrets.token_hex(6))
    buyer_ref: str
    agent_keyid: str
    merchant_id: str
    max_amount_paise: int = Field(ge=100)
    expires_at: datetime
    single_use: bool = True
    revoked: bool = False
    razorpay_customer_id: str = ""
    payment_mandate_id: str = ""
    uses: list[GrantUse] = Field(default_factory=list)

    @property
    def spent_paise(self) -> int:
        return sum(u.amount_paise for u in self.uses)

    @property
    def remaining_paise(self) -> int:
        return max(0, self.max_amount_paise - self.spent_paise)

    def usable_for(self, merchant_id: str, agent_keyid: str, amount_paise: int, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now(timezone.utc)
        if self.revoked:
            return False, "grant revoked"
        if now > self.expires_at:
            return False, "grant expired"
        if merchant_id != self.merchant_id:
            return False, "grant is scoped to a different merchant"
        if agent_keyid != self.agent_keyid:
            return False, "grant was issued to a different agent"
        if self.single_use and self.uses:
            return False, "single-use grant already used"
        if amount_paise > self.remaining_paise:
            return False, f"amount ₹{amount_paise / 100:.0f} exceeds remaining ₹{self.remaining_paise / 100:.0f}"
        return True, "ok"


class GrantStore:
    def __init__(self) -> None:
        self._g: dict[str, ScopedPaymentGrant] = {}
        self._lock = threading.RLock()
        self._sinks: list[Callable[[str, dict], None]] = []

    def on_event(self, sink: Callable[[str, dict], None]) -> None:
        self._sinks.append(sink)

    def _emit(self, event: str, g: ScopedPaymentGrant, extra: dict | None = None) -> None:
        for s in self._sinks:
            s(event, {"grant_id": g.grant_id, "merchant_id": g.merchant_id, "agent_keyid": g.agent_keyid, **(extra or {})})

    def issue(self, buyer_ref: str, agent_keyid: str, merchant_id: str, max_amount_paise: int, ttl_minutes: int = 30, single_use: bool = True, razorpay_customer_id: str = "", payment_mandate_id: str = "") -> ScopedPaymentGrant:
        g = ScopedPaymentGrant(buyer_ref=buyer_ref, agent_keyid=agent_keyid, merchant_id=merchant_id, max_amount_paise=max_amount_paise, expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes), single_use=single_use, razorpay_customer_id=razorpay_customer_id, payment_mandate_id=payment_mandate_id)
        with self._lock:
            self._g[g.grant_id] = g
        self._emit("grant.issued", g, {"max_amount_paise": max_amount_paise})
        return g

    def get(self, grant_id: str) -> ScopedPaymentGrant | None:
        return self._g.get(grant_id)

    def use(self, grant_id: str, amount_paise: int, session_id: str, order_id: str) -> ScopedPaymentGrant:
        with self._lock:
            g = self._g[grant_id]
            g.uses.append(GrantUse(at=datetime.now(timezone.utc), amount_paise=amount_paise, session_id=session_id, order_id=order_id))
        self._emit("grant.used", g, {"amount_paise": amount_paise, "order_id": order_id})
        return g

    def revoke(self, grant_id: str, reason: str = "") -> None:
        with self._lock:
            g = self._g[grant_id]
            g.revoked = True
        self._emit("grant.revoked", g, {"reason": reason})
