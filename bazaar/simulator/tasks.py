"""Deterministic buyer-task generator (200 tasks by default, ~30 % impossible by construction).

Each task states the *expected* outcome so the run can report precision/recall of declines,
not just "orders happened".
"""

from __future__ import annotations

import random

from pydantic import BaseModel

from bazaar.schemas.models import Merchant, Segment, Unit
from bazaar.seller_agent.offer_engine import CartLine, best_rule, build_quote

OTHER_CITY_PINCODES = {"5600": "110001", "3020": "560034", "4520": "700001", "4110": "302017", "6410": "411045"}

_EN = ["I need {q} {u} {name}, deliver to {pin}, budget ₹{b}", "{q} {u} {name} to {pin} under ₹{b}", "Can you send {q} {u} of {name} to {pin}? max ₹{b}"]
_HI = ["मुझे {q} {uh} {hname} चाहिए, {pin}, बजट {b}", "{pin} पर {q} {uh} {hname} भेज दो, बजट {b} रुपये"]
_HL = ["mujhe {q} {u} {name} chahiye {pin} tak, {b} ke andar", "{q} {u} {hname} bhejo {pin}, budget {b}"]
_UNIT_HI = {"kg": "किलो", "g": "ग्राम", "l": "लीटर", "ml": "मिली", "pc": "पीस", "pack": "पैकेट", "dozen": "दर्जन", "box": "बॉक्स", "m": "मीटर"}


class Task(BaseModel):
    task_id: str
    merchant_id: str
    sku: str
    product_name: str
    quantity: int
    unit: str
    pincode: str
    budget_paise: int
    language: str
    negotiate: bool
    segment: str
    message: str
    expected: str  # order | decline_unserviceable | decline_budget | decline_stock | decline_unknown_item


def _qty_for(p, rng: random.Random) -> tuple[int, str]:
    if p.unit in (Unit.KG, Unit.LITRE):
        q = rng.choice([1, 2, 5, 10])
        return q, p.unit.value
    if p.unit in (Unit.GRAM, Unit.ML):
        n = rng.choice([1, 2, 4])
        return n, "pack"
    n = rng.choice([1, 2, 3, 5, 10])
    return n, p.unit.value if p.unit != Unit.PACK else "pack"


def generate_tasks(merchants: list[Merchant], n: int = 200, seed: int = 20260828) -> list[Task]:
    rng = random.Random(seed)
    tasks: list[Task] = []
    i = 0
    while len(tasks) < n:
        m = merchants[i % len(merchants)]
        i += 1
        sellable = [p for p in m.products if p.stock > 0]
        if not sellable:
            continue
        p = rng.choice(sellable)
        q, unit = _qty_for(p, rng)
        lines_qty = q if unit != "pack" else q
        est = p.price_paise * lines_qty
        roll = rng.random()
        pin = m.base_pincode
        expected = "order"
        if roll < 0.12:
            pin = OTHER_CITY_PINCODES[m.serviceability.pincode_prefixes[0]]
            expected = "decline_unserviceable"
            budget = est * 3 + 10000  # generous, so a re-route to a serviceable merchant can succeed
        elif roll < 0.22:
            budget = max(100, est // 3)
            expected = "decline_budget"
        elif roll < 0.28:
            lines_qty = p.stock + 20  # units, i.e. packs
            q = int(lines_qty * p.pack_size) if unit in ("kg", "l") and p.pack_size > 1 else lines_qty
            budget = p.price_paise * lines_qty * 2
            expected = "decline_stock"
        elif roll < 0.31:
            expected = "decline_unknown_item"
            budget = 100000
        else:
            # keep "possible" tasks genuinely possible: under the merchant's and a T2 agent's order caps
            cap = min(m.policy.max_order_paise, 100_000_00)
            while p.price_paise * lines_qty * 1.3 > cap and lines_qty > 1:
                lines_qty = max(1, lines_qty // 2)
                q = lines_qty
            est = p.price_paise * lines_qty
            with_tax = est + est * p.gst_rate_bp // 10000
            # budgets straddle the list price: some deals only close because a pre-approved offer applies
            budget = int(with_tax * rng.uniform(0.85, 1.4)) + m.serviceability.delivery_fee_paise
        lang = rng.choices(["en", "hi", "hi-Latn"], weights=[0.5, 0.3, 0.2])[0]
        negotiate = rng.random() < 0.5
        segment = rng.choices(["new", "returning", "any", "b2b"], weights=[0.4, 0.3, 0.2, 0.1])[0]
        if expected == "order":
            # exact label: what would the deterministic engine charge, with the best offer if the buyer negotiates?
            lines = [CartLine(sku=p.sku, qty=lines_qty)]
            rule_ids: list[str] = []
            if negotiate:
                br = best_rule(m, lines, Segment(segment), pin)
                if br:
                    rule_ids = [br.rule_id]
            total = build_quote(m, lines, pin, Segment(segment), rule_ids).total_paise
            if total > budget:
                expected = "decline_budget"
        name = p.name if expected != "decline_unknown_item" else "plutonium rods"
        hname = (p.synonyms[1] if len(p.synonyms) > 1 else (p.synonyms[0] if p.synonyms else p.name)) if expected != "decline_unknown_item" else "प्लूटोनियम"
        fmt = dict(q=q, u=unit, uh=_UNIT_HI.get(unit, unit), name=name, hname=hname, pin=pin, b=budget // 100)
        msg = rng.choice({"en": _EN, "hi": _HI, "hi-Latn": _HL}[lang]).format(**fmt)
        tasks.append(Task(task_id=f"t{len(tasks):03d}", merchant_id=m.merchant_id, sku=p.sku, product_name=p.name, quantity=q, unit=unit, pincode=pin, budget_paise=budget, language=lang, negotiate=negotiate, segment=segment, message=msg, expected=expected))
    return tasks
