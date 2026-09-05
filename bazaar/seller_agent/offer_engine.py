"""Deterministic pricing & bounded offers.

The model never computes money. It may *propose* a ``rule_id``; this module decides whether
the rule is applicable to the cart/segment and what it is worth, and it records the full
provenance so the fairness ledger can prove that identical inputs → identical outputs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from bazaar.schemas.models import Merchant, OfferRule, OfferType, Product, Segment

QUOTE_TTL_MINUTES = 30


class CartLine(BaseModel):
    sku: str
    qty: int = Field(ge=1)


class QuoteLine(BaseModel):
    sku: str
    name: str
    qty: int
    unit: str
    pack_size: float
    unit_price_paise: int
    subtotal_paise: int
    gst_rate_bp: int
    gst_paise: int


class AppliedOffer(BaseModel):
    rule_id: str
    rule_version: int
    type: OfferType
    discount_paise: int
    segment_predicate: str
    inputs_hash: str  # hash of (rule, cart, segment) → fairness ledger key


class Quote(BaseModel):
    quote_id: str
    merchant_id: str
    lines: list[QuoteLine]
    subtotal_paise: int
    discount_paise: int
    delivery_fee_paise: int
    gst_paise: int
    total_paise: int
    applied_offers: list[AppliedOffer] = Field(default_factory=list)
    pincode: str = ""
    eta_hours: int = 0
    cod_allowed: bool = False
    segment: Segment = Segment.ANY
    valid_until: datetime
    explanation: str = ""


class RuleDecision(BaseModel):
    rule_id: str
    applicable: bool
    reason: str
    discount_paise: int = 0


def _segment_ok(rule: OfferRule, segment: Segment) -> bool:
    return rule.segment == Segment.ANY or rule.segment == segment


def evaluate_rule(rule: OfferRule, m: Merchant, lines: list[CartLine], segment: Segment, subtotal_paise: int, delivery_fee_paise: int, now: datetime | None = None) -> RuleDecision:
    if not rule.is_active(now):
        return RuleDecision(rule_id=rule.rule_id, applicable=False, reason="rule expired")
    if not _segment_ok(rule, segment):
        return RuleDecision(rule_id=rule.rule_id, applicable=False, reason=f"rule is for segment '{rule.segment.value}', buyer is '{segment.value}'")
    if subtotal_paise < rule.min_cart_paise:
        return RuleDecision(rule_id=rule.rule_id, applicable=False, reason=f"cart ₹{subtotal_paise / 100:.0f} below minimum ₹{rule.min_cart_paise / 100:.0f}")
    if rule.min_qty and max((ln.qty for ln in lines), default=0) < rule.min_qty:
        return RuleDecision(rule_id=rule.rule_id, applicable=False, reason=f"needs {rule.min_qty}+ units of one item")
    if rule.type == OfferType.PERCENT:
        d = subtotal_paise * rule.value // 100
    elif rule.type == OfferType.FLAT:
        d = min(rule.value, subtotal_paise)
    else:  # FREE_DELIVERY
        d = delivery_fee_paise
    if rule.max_discount_paise:
        d = min(d, rule.max_discount_paise)
    if d <= 0:
        return RuleDecision(rule_id=rule.rule_id, applicable=False, reason="rule yields no discount for this cart")
    return RuleDecision(rule_id=rule.rule_id, applicable=True, reason="ok", discount_paise=d)


def applicable_rules(m: Merchant, lines: list[CartLine], segment: Segment, pincode: str = "", now: datetime | None = None) -> list[RuleDecision]:
    subtotal, fee = _subtotal_and_fee(m, lines, pincode)
    return [evaluate_rule(r, m, lines, segment, subtotal, fee, now) for r in m.offer_rules]


def best_rule(m: Merchant, lines: list[CartLine], segment: Segment, pincode: str = "") -> RuleDecision | None:
    ok = [d for d in applicable_rules(m, lines, segment, pincode) if d.applicable]
    return max(ok, key=lambda d: d.discount_paise) if ok else None


def _subtotal_and_fee(m: Merchant, lines: list[CartLine], pincode: str) -> tuple[int, int]:
    subtotal = 0
    for ln in lines:
        p = m.product(ln.sku)
        if p is None:
            raise KeyError(ln.sku)
        subtotal += p.price_paise * ln.qty
    fee = m.serviceability.fee_for(subtotal) if pincode else m.serviceability.delivery_fee_paise
    return subtotal, fee


def _inputs_hash(rule: OfferRule, lines: list[CartLine], segment: Segment, subtotal: int) -> str:
    payload = {"rule": rule.rule_id, "v": rule.version, "seg": segment.value, "sub": subtotal, "lines": [(ln.sku, ln.qty) for ln in lines]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def build_quote(m: Merchant, lines: list[CartLine], pincode: str = "", segment: Segment = Segment.ANY, rule_ids: list[str] | None = None, now: datetime | None = None) -> Quote:
    """Itemised quote. ``rule_ids`` are *requested* rules; each is re-validated here."""
    now = now or datetime.now(timezone.utc)
    qlines: list[QuoteLine] = []
    subtotal = 0
    for ln in lines:
        p: Product | None = m.product(ln.sku)
        if p is None:
            raise KeyError(ln.sku)
        sub = p.price_paise * ln.qty
        qlines.append(QuoteLine(sku=p.sku, name=p.name, qty=ln.qty, unit=p.unit.value, pack_size=p.pack_size, unit_price_paise=p.price_paise, subtotal_paise=sub, gst_rate_bp=p.gst_rate_bp, gst_paise=0))
        subtotal += sub
    fee = m.serviceability.fee_for(subtotal) if pincode else m.serviceability.delivery_fee_paise

    applied: list[AppliedOffer] = []
    discount = 0
    fee_after = fee
    for rid in rule_ids or []:
        rule = m.rule(rid)
        if rule is None:
            continue
        if applied and not (rule.stackable and all(m.rule(a.rule_id).stackable for a in applied)):  # type: ignore[union-attr]
            continue
        dec = evaluate_rule(rule, m, lines, segment, subtotal, fee, now)
        if not dec.applicable:
            continue
        if rule.type == OfferType.FREE_DELIVERY:
            fee_after = 0
        else:
            discount += dec.discount_paise
        applied.append(AppliedOffer(rule_id=rule.rule_id, rule_version=rule.version, type=rule.type, discount_paise=dec.discount_paise, segment_predicate=f"segment in ({rule.segment.value})", inputs_hash=_inputs_hash(rule, lines, segment, subtotal)))

    # discount can never exceed the subtotal — stacked flat rules must not drive the quote
    # negative (that would sail past every ≤ cap and then blow up at the payment link)
    discount = min(discount, subtotal)
    # GST on the discounted line values (pro-rata), rounded per line at paise level
    taxable = subtotal - discount
    gst_total = 0
    for ql in qlines:
        share = ql.subtotal_paise * taxable // subtotal if subtotal else 0
        ql.gst_paise = (share * ql.gst_rate_bp + 5000) // 10000
        gst_total += ql.gst_paise
    total = taxable + gst_total + fee_after
    qid = "q_" + hashlib.sha256(f"{m.merchant_id}|{[(ln.sku, ln.qty) for ln in lines]}|{pincode}|{segment.value}|{sorted(a.rule_id for a in applied)}|{now.isoformat()}".encode()).hexdigest()[:14]
    return Quote(
        quote_id=qid,
        merchant_id=m.merchant_id,
        lines=qlines,
        subtotal_paise=subtotal,
        discount_paise=discount,
        delivery_fee_paise=fee_after,
        gst_paise=gst_total,
        total_paise=total,
        applied_offers=applied,
        pincode=pincode,
        eta_hours=m.serviceability.eta_hours + max((m.product(ln.sku).lead_time_hours for ln in lines), default=0),  # type: ignore[union-attr]
        cod_allowed=m.serviceability.cod_allowed and all(m.product(ln.sku).cod_allowed for ln in lines),  # type: ignore[union-attr]
        segment=segment,
        valid_until=now + timedelta(minutes=QUOTE_TTL_MINUTES),
    )
