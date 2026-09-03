"""Thin wrapper over the official ``razorpay`` SDK (test-mode keys).

Only the methods on :class:`PaymentsClient` are exposed — nothing else can be called.
"""

from __future__ import annotations

import razorpay

from bazaar.razorpay_client.base import Customer, Order, Payment, PaymentLink, PaymentsClient, Refund


class RazorpayClient(PaymentsClient):
    def __init__(self, key_id: str, key_secret: str):
        if not key_id or not key_secret:
            raise ValueError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are required for BAZAAR_RAZORPAY=razorpay")
        self._c = razorpay.Client(auth=(key_id, key_secret))
        self._c.set_app_details({"title": "bazaar", "version": "0.1.0"})

    @staticmethod
    def _order(d: dict) -> Order:
        return Order(
            id=d["id"],
            amount_paise=int(d["amount"]),
            currency=d.get("currency", "INR"),
            receipt=d.get("receipt") or "",
            status=d.get("status", "created"),
            notes=dict(d.get("notes") or {}),
            amount_paid_paise=int(d.get("amount_paid", 0)),
        )

    @staticmethod
    def _payment(d: dict) -> Payment:
        return Payment(
            id=d["id"],
            order_id=d.get("order_id") or "",
            amount_paise=int(d["amount"]),
            status=d.get("status", "created"),
            method=d.get("method", ""),
            amount_refunded_paise=int(d.get("amount_refunded", 0)),
        )

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> Order:
        d = self._c.order.create({"amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes or {}})
        return self._order(d)

    def fetch_order(self, order_id: str) -> Order:
        return self._order(self._c.order.fetch(order_id))

    def create_upi_payment_link(
        self, amount_paise: int, description: str, reference_id: str, notes: dict[str, str] | None = None
    ) -> PaymentLink:
        body = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "reference_id": reference_id,
            "notes": notes or {},
        }
        try:
            d = self._c.payment_link.create({**body, "upi_link": True})
        except razorpay.errors.BadRequestError:
            # fresh test accounts often don't have UPI payment links enabled — fall back to a
            # standard link (the hosted page still offers UPI among its methods)
            d = self._c.payment_link.create(body)
        return PaymentLink(
            id=d["id"],
            short_url=d["short_url"],
            amount_paise=int(d["amount"]),
            order_id=d.get("order_id") or "",
            status=d.get("status", "created"),
            notes=dict(d.get("notes") or {}),
        )

    def fetch_payment(self, payment_id: str) -> Payment:
        return self._payment(self._c.payment.fetch(payment_id))

    def fetch_payments_for_order(self, order_id: str) -> list[Payment]:
        d = self._c.order.payments(order_id)
        return [self._payment(p) for p in d.get("items", [])]

    def create_refund(self, payment_id: str, amount_paise: int, notes: dict[str, str] | None = None) -> Refund:
        d = self._c.payment.refund(payment_id, {"amount": amount_paise, "notes": notes or {}})
        return Refund(id=d["id"], payment_id=payment_id, amount_paise=int(d["amount"]), status=d.get("status", ""))

    def create_customer(self, name: str, contact: str = "", email: str = "") -> Customer:
        d = self._c.customer.create({"name": name, "contact": contact, "email": email, "fail_existing": "0"})
        return Customer(id=d["id"], name=d.get("name", ""), contact=d.get("contact") or "", email=d.get("email") or "")
