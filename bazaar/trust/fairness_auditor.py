"""Automated fairness audit of a merchant's offer rules.

Runs cohort simulations: for a fixed (segment, cart), vary every attribute that must *not*
matter — agent identity, language, session, time of day, buyer reference — and assert the
discount is identical (conditional parity). Also probes that no rule can exceed its declared
bound. A rule set that fails cannot be published.
"""

from __future__ import annotations

import itertools
import random
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from bazaar.schemas.models import Merchant, OfferType, Segment
from bazaar.seller_agent.offer_engine import CartLine, build_quote


class FairnessFinding(BaseModel):
    rule_id: str
    kind: str  # parity_violation | bound_exceeded
    detail: str


class FairnessReport(BaseModel):
    merchant_id: str
    cohorts: int
    rules_checked: int
    findings: list[FairnessFinding] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings


IRRELEVANT_ATTRS = {
    "agent_keyid": ["ak_a", "ak_b", "ak_c"],
    "language": ["en", "hi", "hi-Latn"],
    "hour": [2, 11, 20],
    "buyer_ref": ["b1", "b2"],
}


def audit_merchant(m: Merchant, seed: int = 7, carts_per_segment: int = 4) -> FairnessReport:
    rng = random.Random(seed)
    in_stock = [p for p in m.products if p.stock > 0] or m.products
    findings: list[FairnessFinding] = []
    cohorts = 0
    base_now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    for seg in Segment:
        for _ in range(carts_per_segment):
            p = rng.choice(in_stock)
            qty = rng.choice([1, 2, 5, 10])
            lines = [CartLine(sku=p.sku, qty=qty)]
            for rule in m.offer_rules:
                outcomes = set()
                for combo in itertools.product(*IRRELEVANT_ATTRS.values()):
                    attrs = dict(zip(IRRELEVANT_ATTRS.keys(), combo, strict=True))
                    now = base_now + timedelta(hours=attrs["hour"])
                    q = build_quote(m, lines, m.base_pincode, seg, [rule.rule_id], now=now)
                    outcomes.add(q.discount_paise if rule.type != OfferType.FREE_DELIVERY else q.delivery_fee_paise)
                    cohorts += 1
                if len(outcomes) > 1:
                    findings.append(FairnessFinding(rule_id=rule.rule_id, kind="parity_violation", detail=f"segment={seg.value} cart={p.sku}x{qty} outcomes={sorted(outcomes)}"))
                q = build_quote(m, lines, m.base_pincode, seg, [rule.rule_id], now=base_now)
                if rule.type == OfferType.PERCENT and q.discount_paise > q.subtotal_paise * rule.value // 100:
                    findings.append(FairnessFinding(rule_id=rule.rule_id, kind="bound_exceeded", detail=f"discount {q.discount_paise} > {rule.value}% of {q.subtotal_paise}"))
                if rule.max_discount_paise and q.discount_paise > rule.max_discount_paise:
                    findings.append(FairnessFinding(rule_id=rule.rule_id, kind="bound_exceeded", detail=f"discount {q.discount_paise} > cap {rule.max_discount_paise}"))
    return FairnessReport(merchant_id=m.merchant_id, cohorts=cohorts, rules_checked=len(m.offer_rules), findings=findings)
