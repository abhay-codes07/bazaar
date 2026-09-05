"""Deterministic COD/RTO gate.

Cash-on-delivery is where agentic orders can hurt a merchant (return-to-origin fraud), so COD
is *granted*, never assumed. The gate is a fixed, explainable rule table — no model — and its
verdict lands in the policy checks as ``cod_gate``. The interface is shaped so RTO Shield or a
Vulcan order-risk score can replace :func:`cod_gate` in P2 without touching any caller.
"""

from __future__ import annotations

from pydantic import BaseModel

from bazaar.schemas.models import AgentTier, Merchant

COD_VALUE_CAP_PAISE = 2_000_00  # above ₹2,000 an agent order must be prepaid
HIGH_RTO_PINCODE_PREFIXES = frozenset({"1100", "7000"})  # published table; illustrative zones


class CodVerdict(BaseModel):
    allowed: bool
    reason: str


def cod_gate(merchant: Merchant, pincode: str, order_value_paise: int, agent_tier: AgentTier) -> CodVerdict:
    if not merchant.serviceability.cod_allowed:
        return CodVerdict(allowed=False, reason="merchant does not offer COD")
    if agent_tier < AgentTier.T1_SIGNED:
        return CodVerdict(allowed=False, reason="COD requires a signed agent (T1+)")
    if order_value_paise > COD_VALUE_CAP_PAISE:
        return CodVerdict(allowed=False, reason=f"COD capped at ₹{COD_VALUE_CAP_PAISE // 100:,} for agent orders; this order is ₹{order_value_paise // 100:,}")
    if pincode[:4] in HIGH_RTO_PINCODE_PREFIXES:
        return CodVerdict(allowed=False, reason=f"pincode zone {pincode[:4]}xx is on the high-RTO table")
    return CodVerdict(allowed=True, reason="within COD limits")
