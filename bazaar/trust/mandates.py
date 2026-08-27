"""AP2-shaped mandates: tamper-evident, signed digital objects that record what the buyer allowed.

* **CheckoutMandate** — ``open`` (constraints before a cart is finalised) → ``closed`` (bound to
  one quote). Shared with the merchant.
* **PaymentMandate** — ``open`` (budget / instruments / validity) → ``closed`` (exact amount bound
  to a closed checkout). Shared with the payment handler (Razorpay).

Signing: canonical JSON of the payload, Ed25519 by the buyer's key (demo) — in production the
payment mandate is a UPI Reserve Pay / Autopay mandate issued by the bank, and the same
verification hook accepts that credential instead.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, Field

from bazaar.trust import keys


def _canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Signed(BaseModel):
    signer_keyid: str = ""
    signature_b64u: str = ""

    def _payload(self) -> dict:
        d = self.model_dump(mode="json")
        d.pop("signer_keyid", None)
        d.pop("signature_b64u", None)
        return d

    def digest(self) -> str:
        return hashlib.sha256(_canon(self._payload())).hexdigest()

    def sign(self, priv: Ed25519PrivateKey, keyid: str) -> None:
        self.signer_keyid = keyid
        self.signature_b64u = keys.b64u(keys.sign(priv, _canon(self._payload())))

    def verify(self, pub: Ed25519PublicKey) -> bool:
        if not self.signature_b64u:
            return False
        return keys.verify(pub, keys.b64u_decode(self.signature_b64u), _canon(self._payload()))


class CheckoutMandate(Signed):
    mandate_id: str = Field(default_factory=lambda: "cm_" + secrets.token_hex(6))
    stage: Literal["open", "closed"] = "open"
    buyer_ref: str  # opaque buyer identifier (never PII)
    max_amount_paise: int = Field(ge=0)
    allowed_categories: list[str] = Field(default_factory=list)  # empty = any
    merchant_ids: list[str] = Field(default_factory=list)  # empty = any
    pincode: str = ""
    cod_ok: bool = False
    human_present: bool = True
    expires_at: datetime
    # closed stage
    quote_id: str = ""
    merchant_id: str = ""
    amount_paise: int = 0
    parent_digest: str = ""

    @classmethod
    def open(cls, buyer_ref: str, max_amount_paise: int, ttl_minutes: int = 60, **kw) -> CheckoutMandate:
        return cls(buyer_ref=buyer_ref, max_amount_paise=max_amount_paise, expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes), **kw)

    def close(self, quote_id: str, merchant_id: str, amount_paise: int) -> CheckoutMandate:
        """Derive the closed mandate from this open one (chain via parent_digest). Caller signs it."""
        return CheckoutMandate(
            mandate_id=self.mandate_id,
            stage="closed",
            buyer_ref=self.buyer_ref,
            max_amount_paise=self.max_amount_paise,
            allowed_categories=self.allowed_categories,
            merchant_ids=self.merchant_ids,
            pincode=self.pincode,
            cod_ok=self.cod_ok,
            human_present=self.human_present,
            expires_at=self.expires_at,
            quote_id=quote_id,
            merchant_id=merchant_id,
            amount_paise=amount_paise,
            parent_digest=self.digest(),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) > self.expires_at


class PaymentMandate(Signed):
    mandate_id: str = Field(default_factory=lambda: "pm_" + secrets.token_hex(6))
    stage: Literal["open", "closed"] = "open"
    buyer_ref: str
    instrument: Literal["upi_reserve_pay", "upi_autopay", "scoped_grant", "card_token"] = "scoped_grant"
    budget_paise: int = Field(ge=0)
    expires_at: datetime
    checkout_mandate_digest: str = ""
    amount_paise: int = 0
    parent_digest: str = ""

    @classmethod
    def open(cls, buyer_ref: str, budget_paise: int, ttl_minutes: int = 60, instrument: str = "scoped_grant") -> PaymentMandate:
        return cls(buyer_ref=buyer_ref, budget_paise=budget_paise, expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes), instrument=instrument)  # type: ignore[arg-type]

    def close(self, checkout: CheckoutMandate) -> PaymentMandate:
        if checkout.stage != "closed":
            raise ValueError("payment mandate can only close against a closed checkout mandate")
        return PaymentMandate(
            mandate_id=self.mandate_id,
            stage="closed",
            buyer_ref=self.buyer_ref,
            instrument=self.instrument,
            budget_paise=self.budget_paise,
            expires_at=self.expires_at,
            checkout_mandate_digest=checkout.digest(),
            amount_paise=checkout.amount_paise,
            parent_digest=self.digest(),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) > self.expires_at
