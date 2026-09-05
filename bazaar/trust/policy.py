"""Pre-execution policy engine for money-moving actions (checkout, refund).

This is the last gate before a Razorpay call. Everything is a named check with a detail string
so the audit trail and the buyer both see exactly why something was declined.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from bazaar.schemas.models import AgentTier, Merchant
from bazaar.seller_agent.offer_engine import Quote
from bazaar.trust.grants import GrantStore
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate
from bazaar.trust.registry import AgentRegistry


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class PolicyResult(BaseModel):
    allowed: bool
    checks: list[Check] = Field(default_factory=list)
    needs_merchant_review: bool = False

    @property
    def reason(self) -> str:
        return "; ".join(f"{c.name}" + (f" ({c.detail})" if c.detail else "") for c in self.checks if not c.passed)


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: int, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] < now - window_s:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True


class PolicyEngine:
    def __init__(self, registry: AgentRegistry, grants: GrantStore):
        self.registry = registry
        self.grants = grants
        self.rate = RateLimiter()

    def check_checkout(self, m: Merchant, quote: Quote, agent_keyid: str, grant_id: str, checkout_mandate: CheckoutMandate | None, payment_mandate: PaymentMandate | None, buyer_pub_lookup, human_confirmation: bool, now: datetime | None = None) -> PolicyResult:
        now = now or datetime.now(timezone.utc)
        cs: list[Check] = []
        pol = m.policy

        cs.append(Check(name="kill_switch_off", passed=not pol.kill_switch, detail="merchant disabled agent" if pol.kill_switch else ""))
        cs.append(Check(name="quote_fresh", passed=now <= quote.valid_until, detail="quote expired" if now > quote.valid_until else ""))
        cs.append(Check(name="quote_merchant_matches", passed=quote.merchant_id == m.merchant_id))
        cs.append(Check(name="quote_amount_valid", passed=quote.total_paise >= 100, detail=f"₹{quote.total_paise / 100:.2f} below the ₹1 minimum a payment link accepts"))

        ident = self.registry.get(agent_keyid)
        cs.append(Check(name="agent_registered", passed=ident is not None and not ident.revoked, detail=agent_keyid or "unsigned"))
        tier = ident.tier if ident else AgentTier.T0_UNSIGNED
        cs.append(Check(name="agent_tier_sufficient", passed=tier >= pol.min_tier_for_checkout, detail=f"tier {int(tier)} < required {int(pol.min_tier_for_checkout)}" if tier < pol.min_tier_for_checkout else f"tier {int(tier)}"))
        if pol.agent_allowlist:
            cs.append(Check(name="agent_allowlisted", passed=agent_keyid in pol.agent_allowlist))
        if ident:
            cs.append(Check(name="agent_order_cap", passed=quote.total_paise <= ident.max_order_paise, detail=f"₹{quote.total_paise / 100:.0f} ≤ ₹{ident.max_order_paise / 100:.0f}"))
            cs.append(Check(name="agent_rate_limit", passed=self.rate.hit(f"chk:{agent_keyid}", ident.rate_limit_per_min, 60), detail=f"{ident.rate_limit_per_min}/min"))
        cs.append(Check(name="merchant_order_cap", passed=quote.total_paise <= pol.max_order_paise, detail=f"₹{quote.total_paise / 100:.0f} ≤ ₹{pol.max_order_paise / 100:.0f}"))
        if quote.total_paise > pol.human_present_above_paise:
            # RBI e-mandate framework (Apr 2026): no AFA-free debit above ₹15,000; CERT-In: human-in-the-loop above a threshold
            cs.append(Check(name="human_present_above_threshold", passed=human_confirmation, detail=f"₹{quote.total_paise / 100:.0f} > ₹{pol.human_present_above_paise / 100:.0f}: a person must confirm this amount"))
        if checkout_mandate is not None and checkout_mandate.cod_ok:
            # COD is granted, never assumed: a fixed rule table (value cap, tier, RTO zones) —
            # the seam where RTO Shield / Vulcan order-risk replaces the scorer in P2
            from bazaar.seller_agent.rto import cod_gate

            v = cod_gate(m, quote.pincode, quote.total_paise, tier)
            cs.append(Check(name="cod_gate", passed=v.allowed, detail=v.reason))
        cs.append(Check(name="pincode_serviceable", passed=bool(quote.pincode) and m.serviceability.serves(quote.pincode), detail=quote.pincode or "missing"))
        cs.append(Check(name="items_in_stock", passed=all((m.product(ln.sku) or None) is not None and m.product(ln.sku).stock >= ln.qty for ln in quote.lines)))  # type: ignore[union-attr]

        # grant
        g = self.grants.get(grant_id) if grant_id else None
        if g is None:
            cs.append(Check(name="grant_present", passed=False, detail="no scoped payment grant"))
        else:
            ok, why = g.usable_for(m.merchant_id, agent_keyid, quote.total_paise, now)
            cs.append(Check(name="grant_usable", passed=ok, detail=why))

        # mandates (AP2-shaped)
        if checkout_mandate is None:
            cs.append(Check(name="checkout_mandate_present", passed=False))
        else:
            cm = checkout_mandate
            pub = buyer_pub_lookup(cm.signer_keyid) if cm.signer_keyid else None
            cs.append(Check(name="checkout_mandate_signed", passed=pub is not None and cm.verify(pub)))
            cs.append(Check(name="checkout_mandate_closed", passed=cm.stage == "closed"))
            cs.append(Check(name="checkout_mandate_fresh", passed=not cm.is_expired(now)))
            cs.append(Check(name="checkout_mandate_binds_quote", passed=cm.quote_id == quote.quote_id and cm.merchant_id == m.merchant_id and cm.amount_paise == quote.total_paise, detail=f"mandate {cm.amount_paise} vs quote {quote.total_paise}"))
            cs.append(Check(name="checkout_within_max", passed=quote.total_paise <= cm.max_amount_paise, detail=f"₹{quote.total_paise / 100:.0f} ≤ ₹{cm.max_amount_paise / 100:.0f}"))
            if g is not None:
                # the mandate must be for the same buyer the grant was issued to — otherwise a
                # signed agent could spend buyer A's grant under a mandate signed for buyer B
                cs.append(Check(name="mandate_binds_grant_buyer", passed=cm.buyer_ref == g.buyer_ref, detail=f"{cm.buyer_ref} vs grant {g.buyer_ref}"))
            if cm.merchant_ids:
                cs.append(Check(name="checkout_merchant_allowed", passed=m.merchant_id in cm.merchant_ids))
            if cm.allowed_categories:
                cats = {m.product(ln.sku).category for ln in quote.lines if m.product(ln.sku)}  # type: ignore[union-attr]
                cs.append(Check(name="checkout_categories_allowed", passed=cats <= set(cm.allowed_categories), detail=", ".join(sorted(cats - set(cm.allowed_categories)))))
            if cm.pincode:
                cs.append(Check(name="checkout_pincode_matches", passed=cm.pincode == quote.pincode))
            if cm.human_present:
                cs.append(Check(name="human_confirmation", passed=human_confirmation, detail="human-present mandate needs explicit confirmation"))
            else:
                cs.append(Check(name="unattended_tier", passed=tier >= AgentTier.T2_VERIFIED, detail="human-not-present requires T2+"))
        if payment_mandate is None:
            cs.append(Check(name="payment_mandate_present", passed=False))
        else:
            pm = payment_mandate
            pub = buyer_pub_lookup(pm.signer_keyid) if pm.signer_keyid else None
            cs.append(Check(name="payment_mandate_signed", passed=pub is not None and pm.verify(pub)))
            cs.append(Check(name="payment_mandate_closed", passed=pm.stage == "closed"))
            cs.append(Check(name="payment_mandate_fresh", passed=not pm.is_expired(now)))
            cs.append(Check(name="payment_binds_checkout", passed=checkout_mandate is not None and pm.checkout_mandate_digest == checkout_mandate.digest() and pm.amount_paise == quote.total_paise))
            cs.append(Check(name="payment_within_budget", passed=quote.total_paise <= pm.budget_paise))

        allowed = all(c.passed for c in cs)
        return PolicyResult(allowed=allowed, checks=cs, needs_merchant_review=allowed and pol.review_first)

    def check_embedded_checkout(self, m: Merchant, quote: Quote, free_stock, now: datetime | None = None) -> PolicyResult:
        """Merchant-authority checks for an embedded, human-paid checkout (Beckn/ONDC, T0):
        the buyer is a person paying the link on their own device, so there is no agent grant
        or AP2 mandate to verify — but the kill switch, serviceability, order cap and
        reservation-aware stock still gate it, through the same engine, not a hand-rolled list.
        ``free_stock(sku)`` returns stock minus active reservations."""
        now = now or datetime.now(timezone.utc)
        pol = m.policy
        cs = [
            Check(name="kill_switch_off", passed=not pol.kill_switch, detail="merchant disabled agent" if pol.kill_switch else ""),
            Check(name="quote_fresh", passed=now <= quote.valid_until, detail="quote expired" if now > quote.valid_until else ""),
            Check(name="quote_merchant_matches", passed=quote.merchant_id == m.merchant_id),
            Check(name="pincode_serviceable", passed=bool(quote.pincode) and m.serviceability.serves(quote.pincode), detail=quote.pincode or "missing"),
            Check(name="items_in_stock", passed=all(free_stock(ln.sku) >= ln.qty for ln in quote.lines)),
            Check(name="merchant_order_cap", passed=quote.total_paise <= pol.max_order_paise, detail=f"₹{quote.total_paise / 100:.0f} ≤ ₹{pol.max_order_paise / 100:.0f}"),
        ]
        allowed = all(c.passed for c in cs)
        return PolicyResult(allowed=allowed, checks=cs, needs_merchant_review=allowed and pol.review_first)

    def check_refund(self, m: Merchant, agent_keyid: str, amount_paise: int, captured_paise: int, double_confirmed: bool) -> PolicyResult:
        cs = [
            Check(name="kill_switch_off", passed=not m.policy.kill_switch),
            Check(name="refund_within_captured", passed=0 < amount_paise <= captured_paise, detail=f"{amount_paise} of {captured_paise}"),
            Check(name="double_confirmed", passed=double_confirmed, detail="irreversible action needs double confirmation"),
        ]
        # only otherwise-valid refunds consume the hourly budget; declined attempts must not starve real ones
        ok_so_far = all(c.passed for c in cs)
        cs.append(Check(name="refund_rate_limit", passed=(not ok_so_far) or self.rate.hit(f"rfnd:{m.merchant_id}", m.policy.refunds_per_hour, 3600), detail=f"{m.policy.refunds_per_hour}/hour"))
        return PolicyResult(allowed=all(c.passed for c in cs), checks=cs)
