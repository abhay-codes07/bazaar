"""Payments backend interface — the *only* path through which Bazaar touches money.

Every method maps to a Razorpay REST/MCP capability. Amounts are integer paise.
"""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class Order(BaseModel):
    id: str
    amount_paise: int
    currency: str = "INR"
    receipt: str = ""
    status: str = "created"  # created | attempted | paid
    notes: dict[str, str] = Field(default_factory=dict)
    amount_paid_paise: int = 0


class PaymentLink(BaseModel):
    id: str
    short_url: str
    amount_paise: int
    order_id: str = ""
    status: str = "created"  # created | paid | cancelled | expired
    upi_link: bool = True
    notes: dict[str, str] = Field(default_factory=dict)


class Payment(BaseModel):
    id: str
    order_id: str
    amount_paise: int
    status: str = "captured"  # created | authorized | captured | refunded | failed
    method: str = "upi"
    amount_refunded_paise: int = 0


class Refund(BaseModel):
    id: str
    payment_id: str
    amount_paise: int
    status: str = "processed"
    notes: dict[str, str] = Field(default_factory=dict)


class Customer(BaseModel):
    id: str
    name: str = ""
    contact: str = ""
    email: str = ""
    notes: dict[str, str] = Field(default_factory=dict)


class WebhookEvent(BaseModel):
    event: str  # e.g. payment.captured, refund.processed, payment_link.paid
    payload: dict[str, Any]


def webhook_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    return hmac.compare_digest(webhook_signature(body, secret), signature or "")


class PaymentsClient(ABC):
    """Narrow, auditable interface. Anything not here cannot happen."""

    @abstractmethod
    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> Order: ...

    @abstractmethod
    def fetch_order(self, order_id: str) -> Order: ...

    @abstractmethod
    def create_upi_payment_link(
        self, amount_paise: int, description: str, reference_id: str, notes: dict[str, str] | None = None
    ) -> PaymentLink: ...

    @abstractmethod
    def fetch_payment(self, payment_id: str) -> Payment: ...

    @abstractmethod
    def fetch_payments_for_order(self, order_id: str) -> list[Payment]: ...

    @abstractmethod
    def create_refund(self, payment_id: str, amount_paise: int, notes: dict[str, str] | None = None) -> Refund: ...

    @abstractmethod
    def create_customer(self, name: str, contact: str = "", email: str = "") -> Customer: ...
