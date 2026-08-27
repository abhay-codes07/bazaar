"""Seller Agent: propose → verify → execute.

* **propose** (model) returns a :class:`Proposal`.
* **verify** (deterministic) checks the proposal against merchant policy and the pre-approved
  offer table. Anything failing is declined with a reason — no side effects.
* **execute** (deterministic tools) performs exactly the verified action.

Every turn returns ``{result, explanation, policy_checks[], audit_id}``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

from bazaar.llm import LLM, get_llm
from bazaar.schemas.models import AgentTier, Merchant, Segment
from bazaar.seller_agent import explain
from bazaar.seller_agent.offer_engine import CartLine, applicable_rules
from bazaar.seller_agent.propose import Proposal, propose, register_for_offline
from bazaar.seller_agent.tools import SellerTools, ToolResult


class PolicyCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class BuyerContext(BaseModel):
    """Demographic-blind by design: only business facts the merchant may segment on."""

    agent_keyid: str = ""
    tier: AgentTier = AgentTier.T0_UNSIGNED
    segment: Segment = Segment.ANY
    session_id: str = ""


class AgentResponse(BaseModel):
    ok: bool
    action: str
    result: dict[str, Any] = Field(default_factory=dict)
    explanation: str
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    audit_id: str
    language: str = "en"
    state: dict[str, Any] = Field(default_factory=dict)


class AuditSink(Protocol):
    def record(self, entry: dict[str, Any]) -> str: ...


class MemoryAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> str:
        aid = "aud_" + secrets.token_hex(6)
        self.entries.append({"audit_id": aid, "at": datetime.now(timezone.utc).isoformat(), **entry})
        return aid


SIDE_EFFECT_TOOLS = {"reserve", "apply_offer"}


class SellerAgent:
    def __init__(self, merchant: Merchant, llm: LLM | None = None, audit: AuditSink | None = None):
        self.m = merchant
        self.llm = llm or get_llm()
        self.tools = SellerTools(merchant)
        self.audit = audit or MemoryAudit()
        register_for_offline(merchant)

    # ------------------------------------------------------------------ verify
    def verify(self, prop: Proposal, ctx: BuyerContext, state: dict[str, Any]) -> list[PolicyCheck]:
        pol = self.m.policy
        checks: list[PolicyCheck] = [
            PolicyCheck(name="kill_switch_off", passed=not pol.kill_switch, detail="merchant disabled the agent" if pol.kill_switch else ""),
            PolicyCheck(name="tool_known", passed=prop.tool in (*SellerTools.TOOLS, "decline", "clarify"), detail=prop.tool),
        ]
        if pol.agent_allowlist and prop.tool in SIDE_EFFECT_TOOLS:
            ok = ctx.agent_keyid in pol.agent_allowlist
            checks.append(PolicyCheck(name="agent_allowlisted", passed=ok, detail=ctx.agent_keyid or "unsigned"))
        if prop.tool in ("reserve", "apply_offer"):
            checks.append(PolicyCheck(name="tier_can_negotiate", passed=ctx.tier >= AgentTier.T1_SIGNED, detail=f"tier {int(ctx.tier)}"))
        if prop.tool == "apply_offer":
            rid = prop.rule_id or prop.args.get("rule_id", "")
            rule = self.m.rule(rid)
            checks.append(PolicyCheck(name="offer_rule_exists", passed=rule is not None, detail=rid))
            rounds = int(state.get("negotiation_rounds", 0))
            checks.append(PolicyCheck(name="negotiation_rounds", passed=rounds < pol.max_negotiation_rounds, detail=f"{rounds}/{pol.max_negotiation_rounds}"))
            checks.append(PolicyCheck(name="rule_not_invented", passed=("value" not in prop.args and "discount" not in prop.args), detail="model may only reference rule_id"))
        if prop.tool == "quote":
            lines = prop.args.get("lines", [])
            checks.append(PolicyCheck(name="cart_non_empty", passed=bool(lines)))
            checks.append(PolicyCheck(name="qty_positive", passed=all(int(ln.get("qty", 0)) > 0 for ln in lines)))
            try:
                est = sum(self.m.product(ln["sku"]).price_paise * int(ln["qty"]) for ln in lines)  # type: ignore[union-attr]
                checks.append(PolicyCheck(name="under_order_cap", passed=est <= pol.max_order_paise, detail=f"₹{est / 100:.0f} ≤ ₹{pol.max_order_paise / 100:.0f}"))
            except Exception:  # noqa: BLE001
                checks.append(PolicyCheck(name="skus_known", passed=False, detail="unknown sku in cart"))
        return checks

    # ------------------------------------------------------------------ execute
    def execute(self, prop: Proposal) -> ToolResult:
        t = self.tools
        a = prop.args
        if prop.tool == "search_products":
            return t.search_products(a.get("query", ""), int(a.get("limit", 5)))
        if prop.tool == "get_availability":
            return t.get_availability(a["sku"], int(a.get("qty", 1)))
        if prop.tool == "check_serviceability":
            return t.check_serviceability(a.get("pincode", ""), a.get("sku", ""))
        if prop.tool == "quote":
            return t.quote(a.get("lines", []), a.get("pincode", ""), a.get("segment", "any"), a.get("rule_ids"))
        if prop.tool == "apply_offer":
            return t.apply_offer(a["quote_id"], prop.rule_id or a.get("rule_id", ""))
        if prop.tool == "reserve":
            return t.reserve(a["quote_id"])
        if prop.tool == "decline":
            return ToolResult(ok=False, tool="decline", reason=a.get("reason", "request declined"))
        if prop.tool == "clarify":
            return ToolResult(ok=True, tool="clarify", result={"question": a.get("question", "")})
        return ToolResult(ok=False, tool=prop.tool, reason="unknown tool")

    # ------------------------------------------------------------------ turn
    def handle(self, message: str, ctx: BuyerContext, state: dict[str, Any] | None = None) -> AgentResponse:
        state = dict(state or {})
        state.setdefault("segment", ctx.segment.value)
        prop = propose(self.llm, self.m, message, state)
        lang = prop.language or "en"
        checks = self.verify(prop, ctx, state)
        failed = [c for c in checks if not c.passed]
        if failed:
            reason = "; ".join(f"{c.name}" + (f" ({c.detail})" if c.detail else "") for c in failed)
            aid = self.audit.record({"session": ctx.session_id, "proposal": prop.model_dump(), "checks": [c.model_dump() for c in checks], "outcome": "declined"})
            return AgentResponse(ok=False, action=prop.tool, explanation=explain.decline_text(reason, lang), policy_checks=checks, audit_id=aid, language=lang, state=state)

        res = self.execute(prop)
        aid = self.audit.record({"session": ctx.session_id, "proposal": prop.model_dump(), "checks": [c.model_dump() for c in checks], "outcome": "ok" if res.ok else "failed", "tool_result": res.model_dump(mode="json")})
        text = self._explain(prop, res, lang)
        # carry forward what the next turn needs
        if res.ok and prop.tool in ("quote", "apply_offer"):
            state["quote_id"] = res.result["quote_id"]
            state["pincode"] = res.result.get("pincode", state.get("pincode", ""))
            lines = [CartLine(sku=ln["sku"], qty=ln["qty"]) for ln in res.result["lines"]]
            applied = {a["rule_id"] for a in res.result.get("applied_offers", [])}
            stackable = all(getattr(self.m.rule(r), "stackable", False) for r in applied)
            state["best_rule_id"] = ""
            if not applied or stackable:
                for d in sorted(applicable_rules(self.m, lines, Segment(state.get("segment", "any")), state.get("pincode", "")), key=lambda d: -d.discount_paise):
                    if d.applicable and d.rule_id not in applied and (not applied or self.m.rule(d.rule_id).stackable):
                        state["best_rule_id"] = d.rule_id
                        break
            if prop.tool == "apply_offer":
                state["negotiation_rounds"] = int(state.get("negotiation_rounds", 0)) + 1
        if res.ok and prop.tool == "reserve":
            state["reservation_id"] = res.result["reservation_id"]
        return AgentResponse(ok=res.ok, action=prop.tool, result=res.result, explanation=text, policy_checks=checks, audit_id=aid, language=lang, state=state)

    def _explain(self, prop: Proposal, res: ToolResult, lang: str) -> str:
        from bazaar.seller_agent.offer_engine import Quote

        if not res.ok:
            return explain.decline_text(res.reason, lang)
        if prop.tool in ("quote", "apply_offer"):
            return explain.quote_text(Quote.model_validate(res.result), lang)
        if prop.tool == "check_serviceability":
            r = res.result
            return explain.serviceability_text(r["serves"], r["pincode"], r.get("eta_hours") or 0, r["delivery_fee_paise"], r["cod_allowed"], lang)
        if prop.tool == "search_products":
            names = ", ".join(f"{p['name']} (₹{p['price_paise'] / 100:.0f}/{p['pack_size']:g} {p['unit']})" for p in res.result["products"]) or "nothing matching"
            return {"hi": f"उपलब्ध: {names}", "hi-Latn": f"Available: {names}"}.get(lang, f"Available: {names}")
        if prop.tool == "reserve":
            return {"hi": "स्टॉक 15 मिनट के लिए रोक दिया गया है; भुगतान लिंक अगला कदम है।", "hi-Latn": "Stock 15 minute ke liye hold hai; payment link agla step hai."}.get(lang, "Stock held for 15 minutes; payment link is the next step.")
        if prop.tool == "clarify":
            return res.result.get("question", "")
        return prop.rationale
