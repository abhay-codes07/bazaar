"""Agent-Readiness Score (0–100) with a fix-it list.

Weights reflect what actually blocks an agent from transacting: a price it can trust, a unit it
can quote in, a pincode answer, and a policy that lets checkout happen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from bazaar.schemas.models import Merchant


class ReadinessReport(BaseModel):
    score: int
    components: dict[str, int] = Field(default_factory=dict)
    fixes: list[str] = Field(default_factory=list)


def readiness_score(m: Merchant) -> ReadinessReport:
    n = max(1, len(m.products))
    comps: dict[str, int] = {}
    fixes: list[str] = []

    priced = sum(1 for p in m.products if p.price_paise > 0 and p.confidence.price >= 0.8) / n
    comps["prices_trusted"] = round(25 * priced)
    if priced < 0.95:
        fixes.append(f"Confirm prices on {n - round(priced * n)} item(s) flagged for review")

    units = sum(1 for p in m.products if p.confidence.unit >= 0.8) / n
    comps["units_clear"] = round(15 * units)
    if units < 0.9:
        fixes.append("Add a unit/pack size column (kg, g, pc, pack of N) for ambiguous items")

    stock = sum(1 for p in m.products if p.confidence.stock >= 0.8) / n
    comps["stock_known"] = round(10 * stock)
    if stock < 0.9:
        fixes.append("Use numbers for stock instead of 'yes'/'in stock'")

    gst = sum(1 for p in m.products if p.confidence.gst >= 0.8) / n
    comps["gst_known"] = round(10 * gst)
    if gst < 0.9:
        fixes.append("Fill GST % per item so quotes can itemise tax")

    serv = bool(m.serviceability.pincode_prefixes or m.serviceability.pincodes)
    comps["serviceability"] = 15 if serv else 0
    if not serv:
        fixes.append("Set delivery pincodes/prefixes and ETA so agents can answer 'do you deliver to…'")

    comps["offer_rules"] = 10 if m.offer_rules else 0
    if not m.offer_rules:
        fixes.append("Add at least one pre-approved offer rule to allow bounded negotiation")

    described = sum(1 for p in m.products if p.description or p.buyer_highlights) / n
    comps["descriptions"] = round(10 * described)
    if described < 0.8:
        fixes.append("Add short descriptions so agents can match buyer intent")

    flagged = sum(1 for p in m.products if "instruction_like_text_stripped" in p.flags)
    comps["clean_text"] = 5 if flagged == 0 else 0
    if flagged:
        fixes.append(f"Review {flagged} item(s) where instruction-like text was removed")

    if m.policy.kill_switch:
        fixes.append("Agent is disabled (kill switch on)")
    score = min(100, sum(comps.values()))
    return ReadinessReport(score=score, components=comps, fixes=fixes)
