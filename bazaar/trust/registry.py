"""Agent Registry: who is this agent, and what tier of access has it earned?

Tiers (see :class:`AgentTier`): T0 unsigned browse/quote; T1 signed key → reserve/negotiate;
T2 verified operator → direct checkout with a Scoped Payment Grant; T3 Razorpay-vetted → higher
limits. Tier decisions are logged; abuse reports demote.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from bazaar.schemas.models import AgentTier
from bazaar.trust import keys


class AgentIdentity(BaseModel):
    keyid: str
    public_key_b64u: str
    operator: str  # e.g. "claude.ai", "acme-procurement"
    profile_url: str = ""
    tier: AgentTier = AgentTier.T1_SIGNED
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked: bool = False
    rate_limit_per_min: int = 60
    max_order_paise: int = 20_000_00
    notes: list[str] = Field(default_factory=list)

    def public_key(self):
        return keys.public_from_bytes(keys.b64u_decode(self.public_key_b64u))


TIER_DEFAULTS = {
    AgentTier.T1_SIGNED: (60, 20_000_00),
    AgentTier.T2_VERIFIED: (300, 100_000_00),
    AgentTier.T3_VETTED: (1000, 500_000_00),
}


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentIdentity] = {}
        self._lock = threading.RLock()
        self.events: list[dict] = []

    def register(self, public_key_raw: bytes, operator: str, profile_url: str = "", tier: AgentTier = AgentTier.T1_SIGNED) -> AgentIdentity:
        keyid = keys.keyid_for(public_key_raw)
        rl, cap = TIER_DEFAULTS.get(tier, TIER_DEFAULTS[AgentTier.T1_SIGNED])
        ident = AgentIdentity(keyid=keyid, public_key_b64u=keys.b64u(public_key_raw), operator=operator, profile_url=profile_url, tier=tier, rate_limit_per_min=rl, max_order_paise=cap)
        with self._lock:
            self._agents[keyid] = ident
            self.events.append({"event": "registered", "keyid": keyid, "tier": int(tier), "operator": operator})
        return ident

    def get(self, keyid: str) -> AgentIdentity | None:
        return self._agents.get(keyid)

    def set_tier(self, keyid: str, tier: AgentTier, reason: str) -> AgentIdentity:
        with self._lock:
            a = self._agents[keyid]
            rl, cap = TIER_DEFAULTS.get(tier, TIER_DEFAULTS[AgentTier.T1_SIGNED])
            a.tier, a.rate_limit_per_min, a.max_order_paise = tier, rl, cap
            a.notes.append(f"{datetime.now(timezone.utc).isoformat()} tier→{int(tier)}: {reason}")
            self.events.append({"event": "tier_changed", "keyid": keyid, "tier": int(tier), "reason": reason})
            return a

    def revoke(self, keyid: str, reason: str) -> None:
        with self._lock:
            a = self._agents[keyid]
            a.revoked = True
            a.notes.append(f"revoked: {reason}")
            self.events.append({"event": "revoked", "keyid": keyid, "reason": reason})

    def all(self) -> list[AgentIdentity]:
        return list(self._agents.values())
