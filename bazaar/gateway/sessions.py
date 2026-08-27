"""Session = one buyer-agent ↔ one merchant conversation that may end in an order.

Status machine (ACP-shaped names):

    open ──quote──▶ ready_for_payment ──complete──▶ in_progress ──payment.captured──▶ completed
      │                    │                              │
      │                    ├── review_first ─▶ awaiting_merchant_review ─approve─▶ in_progress
      │                    │
      └────── declined / canceled ◀─────────────────────────┘
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from bazaar.schemas.models import AgentTier, Segment

STATUSES = ("open", "ready_for_payment", "awaiting_merchant_review", "in_progress", "completed", "canceled", "declined")


class Session(BaseModel):
    session_id: str = Field(default_factory=lambda: "sess_" + secrets.token_hex(7))
    merchant_id: str
    agent_keyid: str = ""
    tier: AgentTier = AgentTier.T0_UNSIGNED
    segment: Segment = Segment.ANY
    language: str = "en"
    status: str = "open"
    state: dict[str, Any] = Field(default_factory=dict)  # seller-agent carry-over (quote_id, pincode, ...)
    quote: dict[str, Any] | None = None
    reservation_id: str = ""
    grant_id: str = ""
    order_id: str = ""
    payment_link_id: str = ""
    payment_url: str = ""
    payment_id: str = ""
    last_checks: list[dict[str, Any]] = Field(default_factory=list)
    turns: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "bazaar"  # bazaar | acp | ucp | beckn
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def public(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d.pop("state", None)
        return d
