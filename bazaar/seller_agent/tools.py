"""Seller tools — the *only* things the executor can call. All deterministic, all audited."""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from bazaar.schemas.models import Merchant, Segment
from bazaar.seller_agent.intent import match_products
from bazaar.seller_agent.offer_engine import CartLine, Quote, applicable_rules, build_quote

RESERVATION_TTL_MIN = 15


class ToolResult(BaseModel):
    ok: bool
    tool: str
    result: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class Reservation(BaseModel):
    reservation_id: str
    quote_id: str
    lines: list[CartLine]
    expires_at: datetime


class SellerTools:
    """Bound to one merchant. Holds soft stock reservations and issued quotes."""

    TOOLS = ("search_products", "get_availability", "check_serviceability", "quote", "apply_offer", "reserve", "release")

    def __init__(self, merchant: Merchant):
        self.m = merchant
        self._lock = threading.RLock()
        self.quotes: dict[str, Quote] = {}
        self.reservations: dict[str, Reservation] = {}

    # ---------------------------------------------------------------- read-only
    def search_products(self, query: str, limit: int = 5) -> ToolResult:
        hits = match_products(query, self.m.products, limit=limit)
        return ToolResult(ok=True, tool="search_products", result={"products": [
            {"sku": p.sku, "name": p.name, "price_paise": p.price_paise, "unit": p.unit.value, "pack_size": p.pack_size, "in_stock": p.stock > 0, "tags": p.use_case_tags}
            for p in hits]})

    def _reserved_qty(self, sku: str) -> int:
        now = datetime.now(timezone.utc)
        return sum(ln.qty for r in self.reservations.values() if r.expires_at > now for ln in r.lines if ln.sku == sku)

    def get_availability(self, sku: str, qty: int) -> ToolResult:
        p = self.m.product(sku)
        if p is None:
            return ToolResult(ok=False, tool="get_availability", reason=f"unknown sku {sku}")
        free = p.stock - self._reserved_qty(sku)
        return ToolResult(ok=True, tool="get_availability", result={"sku": sku, "requested": qty, "available": max(0, free), "sufficient": free >= qty, "lead_time_hours": p.lead_time_hours})

    def check_serviceability(self, pincode: str, sku: str = "") -> ToolResult:
        s = self.m.serviceability
        serves = s.serves(pincode)
        p = self.m.product(sku) if sku else None
        eta = s.eta_hours + (p.lead_time_hours if p else 0)
        cod = s.cod_allowed and (p.cod_allowed if p else True)
        return ToolResult(ok=True, tool="check_serviceability", result={"pincode": pincode, "serves": serves, "eta_hours": eta if serves else None, "delivery_fee_paise": s.delivery_fee_paise, "free_delivery_above_paise": s.free_delivery_above_paise, "cod_allowed": cod if serves else False})

    # ---------------------------------------------------------------- pricing
    def _lines(self, lines: list[dict[str, Any]]) -> tuple[list[CartLine] | None, str]:
        out: list[CartLine] = []
        for ln in lines:
            try:
                cl = CartLine(**ln)
            except Exception as e:  # noqa: BLE001
                return None, f"bad cart line {ln}: {e}"
            p = self.m.product(cl.sku)
            if p is None:
                return None, f"unknown sku {cl.sku}"
            free = p.stock - self._reserved_qty(cl.sku)
            if free < cl.qty:
                return None, f"only {max(0, free)} of {p.name} available, {cl.qty} requested"
            out.append(cl)
        if not out:
            return None, "empty cart"
        return out, ""

    def quote(self, lines: list[dict[str, Any]], pincode: str = "", segment: str = "any", rule_ids: list[str] | None = None) -> ToolResult:
        cart, err = self._lines(lines)
        if cart is None:
            return ToolResult(ok=False, tool="quote", reason=err)
        if pincode and not self.m.serviceability.serves(pincode):
            return ToolResult(ok=False, tool="quote", reason=f"we do not deliver to {pincode}")
        q = build_quote(self.m, cart, pincode=pincode, segment=Segment(segment), rule_ids=rule_ids or [])
        with self._lock:
            self.quotes[q.quote_id] = q
        return ToolResult(ok=True, tool="quote", result=q.model_dump(mode="json"))

    def apply_offer(self, quote_id: str, rule_id: str) -> ToolResult:
        q = self.quotes.get(quote_id)
        if q is None:
            return ToolResult(ok=False, tool="apply_offer", reason=f"unknown quote {quote_id}")
        if datetime.now(timezone.utc) > q.valid_until:
            return ToolResult(ok=False, tool="apply_offer", reason="quote expired; ask for a fresh quote")
        rule = self.m.rule(rule_id)
        if rule is None:
            return ToolResult(ok=False, tool="apply_offer", reason=f"rule {rule_id} does not exist for this merchant")
        lines = [CartLine(sku=ln.sku, qty=ln.qty) for ln in q.lines]
        decisions = {d.rule_id: d for d in applicable_rules(self.m, lines, q.segment, q.pincode)}
        d = decisions[rule_id]
        if not d.applicable:
            return ToolResult(ok=False, tool="apply_offer", reason=f"{rule_id} not applicable: {d.reason}")
        already = [a.rule_id for a in q.applied_offers]
        if rule_id in already:
            return ToolResult(ok=False, tool="apply_offer", reason=f"{rule_id} already applied")
        new_q = build_quote(self.m, lines, pincode=q.pincode, segment=q.segment, rule_ids=[*already, rule_id])
        with self._lock:
            self.quotes[new_q.quote_id] = new_q
        return ToolResult(ok=True, tool="apply_offer", result={"previous_quote_id": quote_id, **new_q.model_dump(mode="json")})

    def list_applicable_rules(self, quote_id: str) -> list[dict[str, Any]]:
        q = self.quotes.get(quote_id)
        if q is None:
            return []
        lines = [CartLine(sku=ln.sku, qty=ln.qty) for ln in q.lines]
        return [d.model_dump() for d in applicable_rules(self.m, lines, q.segment, q.pincode)]

    # ---------------------------------------------------------------- stock holds
    def reserve(self, quote_id: str) -> ToolResult:
        q = self.quotes.get(quote_id)
        if q is None:
            return ToolResult(ok=False, tool="reserve", reason=f"unknown quote {quote_id}")
        if datetime.now(timezone.utc) > q.valid_until:
            return ToolResult(ok=False, tool="reserve", reason="quote expired")
        with self._lock:
            for ln in q.lines:
                p = self.m.product(ln.sku)
                if p is None or p.stock - self._reserved_qty(ln.sku) < ln.qty:
                    return ToolResult(ok=False, tool="reserve", reason=f"stock changed for {ln.name}; re-quote")
            r = Reservation(reservation_id="rsv_" + secrets.token_hex(6), quote_id=quote_id, lines=[CartLine(sku=ln.sku, qty=ln.qty) for ln in q.lines], expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_TTL_MIN))
            self.reservations[r.reservation_id] = r
        return ToolResult(ok=True, tool="reserve", result=r.model_dump(mode="json"))

    def release(self, reservation_id: str) -> ToolResult:
        with self._lock:
            if self.reservations.pop(reservation_id, None) is None:
                return ToolResult(ok=False, tool="release", reason="unknown reservation")
        return ToolResult(ok=True, tool="release", result={"reservation_id": reservation_id})

    def commit_stock(self, reservation_id: str) -> ToolResult:
        """Called by the gateway after payment is captured: decrement stock, drop the hold."""
        with self._lock:
            r = self.reservations.pop(reservation_id, None)
            if r is None:
                return ToolResult(ok=False, tool="commit_stock", reason="unknown reservation")
            for ln in r.lines:
                p = self.m.product(ln.sku)
                if p is not None:
                    p.stock = max(0, p.stock - ln.qty)
        return ToolResult(ok=True, tool="commit_stock", result={"reservation_id": reservation_id})
