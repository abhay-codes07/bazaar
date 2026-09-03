"""NPCI Unified Agent Protocol — the adapter surface (spec lands at GFF, 9–11 Sept 2026).

UAP is reported to standardise agent-initiated UPI payments on two existing primitives:
UPI Circle (delegating payment authority to a secondary actor within a pre-set limit) and
Reserve Pay (blocked funds, multiple debits — NPCI OC-228: ₹10,000 / 90 days). Bazaar's money
layer was built on the same shapes, so the binding is a mapping, not a redesign:

    UAP concept (reported)                Bazaar primitive
    ------------------------------------  -----------------------------------------------
    agent registration / verification     AgentRegistry — Ed25519 keyid, trust tiers T0–T3
    delegated authority w/ pre-set limit  ScopedPaymentGrant — one merchant, amount, expiry
    blocked funds, multiple debits        ReservePayMandate (razorpay_client.reserve_pay)
    user-set spending rules               PolicyEngine — 25 named checks before any rupee
    non-repudiable authorisation          AP2-shaped Checkout/Payment mandates, digest-chained

This module is the single place a real UAP binding lands once the spec is public. Until then,
``SandboxUAPBinding`` runs the full lifecycle against the sandbox implementations — used by the
gateway so every grant issued today already follows the UAP shape.
"""

from __future__ import annotations

from typing import Protocol

from bazaar.razorpay_client.reserve_pay import (
    RESERVE_PAY_CAP_PAISE,
    RESERVE_PAY_VALIDITY_DAYS,
    ReservePayMandate,
    SandboxReservePay,
)


class UAPBinding(Protocol):
    """What NPCI's UAP is expected to require of a participant. Implementations must be
    side-effect-complete: every method either fully succeeds or raises, never half-applies."""

    def block_funds(self, buyer_ref: str, merchant_id: str, amount_paise: int) -> ReservePayMandate:
        """Reserve-Pay-style block backing a delegated limit (grant issuance)."""
        ...

    def debit(self, mandate_id: str, amount_paise: int, order_id: str) -> ReservePayMandate:
        """One debit against the block (grant use at checkout)."""
        ...

    def release(self, mandate_id: str) -> ReservePayMandate:
        """Release the unspent remainder (grant revocation/expiry)."""
        ...


class SandboxUAPBinding:
    """The demo binding: deterministic, in-memory, NPCI-default-aware."""

    cap_paise = RESERVE_PAY_CAP_PAISE
    validity_days = RESERVE_PAY_VALIDITY_DAYS

    def __init__(self, reserve_pay: SandboxReservePay | None = None) -> None:
        self.reserve_pay = reserve_pay or SandboxReservePay()

    def block_funds(self, buyer_ref: str, merchant_id: str, amount_paise: int) -> ReservePayMandate:
        return self.reserve_pay.create(buyer_ref, merchant_id, amount_paise)

    def debit(self, mandate_id: str, amount_paise: int, order_id: str = "") -> ReservePayMandate:
        return self.reserve_pay.execute(mandate_id, amount_paise, order_id)

    def release(self, mandate_id: str) -> ReservePayMandate:
        return self.reserve_pay.release(mandate_id)
