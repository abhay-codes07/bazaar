"""Merchant/product discovery. Ranking is deterministic — no model, no paid placement — so the
"Branded Whisper" class of attacks (instructions hidden in catalog text) has nothing to bias."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from bazaar.compiler.readiness import readiness_score
from bazaar.schemas.models import Merchant
from bazaar.seller_agent.intent import match_products, parse_intent


class DiscoverRequest(BaseModel):
    intent: str = Field(max_length=2000)
    pincode: str = Field(default="", max_length=12)
    budget_paise: int = Field(default=0, ge=0)
    vertical: str = Field(default="", max_length=48)
    limit: int = Field(default=5, ge=1, le=20)


class Candidate(BaseModel):
    merchant_id: str
    merchant_name: str
    city: str
    vertical: str
    serves_pincode: bool | None
    eta_hours: int
    readiness: int
    score: float
    products: list[dict[str, Any]]
    parsed: dict[str, Any]


def discover(req: DiscoverRequest, merchants: list[Merchant], readiness_cache: dict[str, int] | None = None) -> list[Candidate]:
    out: list[Candidate] = []
    readiness_cache = readiness_cache if readiness_cache is not None else {}
    for m in merchants:
        if m.policy.kill_switch:
            continue
        if req.vertical and m.vertical.value != req.vertical:
            continue
        it = parse_intent(req.intent, m)
        hits = match_products(it.product_query or req.intent, m.products, limit=3)
        if not hits:
            continue
        serves = m.serviceability.serves(req.pincode) if req.pincode else None
        if serves is False:
            continue
        if m.merchant_id not in readiness_cache:
            readiness_cache[m.merchant_id] = readiness_score(m).score
        rd = readiness_cache[m.merchant_id]
        best = hits[0]
        qty = it.quantity or 1
        est = int(best.price_paise * max(1, round(qty / best.pack_size))) if best.pack_size else best.price_paise
        in_stock = best.stock > 0
        within_budget = (req.budget_paise == 0) or est <= req.budget_paise
        eta = m.serviceability.eta_hours + best.lead_time_hours
        # deterministic score: relevance (token overlap handled by ordering) + serviceability + stock + readiness + speed
        score = 0.0
        score += 40 if in_stock else 0
        score += 20 if serves else (10 if serves is None else 0)
        score += 15 if within_budget else 0
        score += rd * 0.15
        score += max(0, 10 - eta / 12)
        out.append(
            Candidate(
                merchant_id=m.merchant_id,
                merchant_name=m.name,
                city=m.city,
                vertical=m.vertical.value,
                serves_pincode=serves,
                eta_hours=eta,
                readiness=rd,
                score=round(score, 2),
                products=[{"sku": p.sku, "name": p.name, "price_paise": p.price_paise, "unit": p.unit.value, "pack_size": p.pack_size, "in_stock": p.stock > 0, "estimated_total_paise": int(p.price_paise * max(1, round(qty / p.pack_size)))} for p in hits],
                parsed={"quantity": it.quantity, "unit": it.unit.value if it.unit else None, "pincode": it.pincode or req.pincode, "budget_paise": req.budget_paise or it.budget_paise, "language": it.language},
            )
        )
    out.sort(key=lambda c: (-c.score, c.eta_hours, c.products[0]["price_paise"]))
    return out[: req.limit]
