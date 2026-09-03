"""UPI Reserve Pay mandates — blocked funds, multiple debits (NPCI circular OC-228 shape).

Reserve Pay lets a payer block funds once and debit against the block multiple times; NPCI's
defaults are a ₹10,000 block and 90-day validity. This module is the mandate ledger Bazaar's
Scoped Payment Grants bind to: issuing a grant blocks funds, using it debits the block,
revoking it releases the remainder — every transition auditable.

``SandboxReservePay`` is the demo/test implementation (deterministic, in-memory). Razorpay
test mode does not yet expose a mandate primitive; when it does (NPCI's UAP lands at GFF,
9–11 Sept 2026), a ``RazorpayReservePay`` with the same four methods replaces it and nothing
upstream changes — see ``bazaar.trust.uap``.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

RESERVE_PAY_CAP_PAISE = 10_000_00  # ₹10,000 default block per NPCI OC-228
RESERVE_PAY_VALIDITY_DAYS = 90  # default mandate validity per NPCI OC-228


class ReservePayError(Exception):
    pass


class ReservePayDebit(BaseModel):
    at: datetime
    amount_paise: int
    order_id: str = ""


class ReservePayMandate(BaseModel):
    mandate_id: str = Field(default_factory=lambda: "rpm_" + secrets.token_hex(6))
    buyer_ref: str
    merchant_id: str
    blocked_paise: int = Field(ge=100)
    expires_at: datetime
    status: str = "active"  # active | released | expired
    within_npci_defaults: bool = True  # blocked ≤ ₹10,000 and validity ≤ 90 days
    debits: list[ReservePayDebit] = Field(default_factory=list)

    @property
    def debited_paise(self) -> int:
        return sum(d.amount_paise for d in self.debits)

    @property
    def remaining_paise(self) -> int:
        return max(0, self.blocked_paise - self.debited_paise)


class SandboxReservePay:
    def __init__(self) -> None:
        self._m: dict[str, ReservePayMandate] = {}
        self._lock = threading.RLock()

    def create(self, buyer_ref: str, merchant_id: str, block_paise: int, validity_days: int = RESERVE_PAY_VALIDITY_DAYS) -> ReservePayMandate:
        if block_paise < 100:
            raise ReservePayError("block amount below minimum")
        m = ReservePayMandate(
            buyer_ref=buyer_ref,
            merchant_id=merchant_id,
            blocked_paise=block_paise,
            expires_at=datetime.now(timezone.utc) + timedelta(days=validity_days),
            within_npci_defaults=block_paise <= RESERVE_PAY_CAP_PAISE and validity_days <= RESERVE_PAY_VALIDITY_DAYS,
        )
        with self._lock:
            self._m[m.mandate_id] = m
        return m

    def get(self, mandate_id: str) -> ReservePayMandate | None:
        return self._m.get(mandate_id)

    def execute(self, mandate_id: str, amount_paise: int, order_id: str = "") -> ReservePayMandate:
        with self._lock:
            m = self._m.get(mandate_id)
            if m is None:
                raise ReservePayError("unknown mandate")
            if m.status != "active":
                raise ReservePayError(f"mandate is {m.status}")
            if datetime.now(timezone.utc) > m.expires_at:
                m.status = "expired"
                raise ReservePayError("mandate expired")
            if amount_paise > m.remaining_paise:
                raise ReservePayError(f"debit ₹{amount_paise / 100:.0f} exceeds blocked remainder ₹{m.remaining_paise / 100:.0f}")
            m.debits.append(ReservePayDebit(at=datetime.now(timezone.utc), amount_paise=amount_paise, order_id=order_id))
        return m

    def release(self, mandate_id: str) -> ReservePayMandate:
        with self._lock:
            m = self._m.get(mandate_id)
            if m is None:
                raise ReservePayError("unknown mandate")
            m.status = "released"
        return m
