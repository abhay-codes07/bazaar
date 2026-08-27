"""In-memory Razorpay sandbox.

Behaves like Razorpay test mode for the subset Bazaar uses, and can *simulate* customer
actions (paying a link / failing a payment) so end-to-end flows and webhooks are testable
offline. Ids mimic Razorpay's prefixes so downstream code never special-cases the fake.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable

from bazaar.razorpay_client.base import (
    Customer,
    Order,
    Payment,
    PaymentLink,
    PaymentsClient,
    Refund,
    WebhookEvent,
    webhook_signature,
)


class FakeRazorpayError(Exception):
    def __init__(self, code: str, description: str):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _rid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(7)}"


class FakeRazorpay(PaymentsClient):
    _shared: FakeRazorpay | None = None

    def __init__(self, webhook_secret: str = "bazaar-dev-webhook-secret"):
        self._lock = threading.RLock()
        self.orders: dict[str, Order] = {}
        self.links: dict[str, PaymentLink] = {}
        self.payments: dict[str, Payment] = {}
        self.refunds: dict[str, Refund] = {}
        self.customers: dict[str, Customer] = {}
        self.webhook_secret = webhook_secret
        self._webhook_sinks: list[Callable[[WebhookEvent, bytes, str], None]] = []
        self.calls: list[tuple[str, dict]] = []  # audit of every call, for tests

    @classmethod
    def shared(cls) -> FakeRazorpay:
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @classmethod
    def reset_shared(cls) -> None:
        cls._shared = None

    # ------------------------------------------------------------------ webhooks
    def on_webhook(self, sink: Callable[[WebhookEvent, bytes, str], None]) -> None:
        self._webhook_sinks.append(sink)

    def _emit(self, event: str, payload: dict) -> None:
        ev = WebhookEvent(event=event, payload=payload)
        body = json.dumps(ev.model_dump(), sort_keys=True).encode()
        sig = webhook_signature(body, self.webhook_secret)
        for sink in list(self._webhook_sinks):
            sink(ev, body, sig)

    # ------------------------------------------------------------------ API
    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str] | None = None) -> Order:
        if amount_paise < 100:
            raise FakeRazorpayError("BAD_REQUEST_ERROR", "amount must be at least ₹1.00")
        with self._lock:
            o = Order(id=_rid("order"), amount_paise=amount_paise, receipt=receipt, notes=notes or {})
            self.orders[o.id] = o
            self.calls.append(("create_order", {"amount_paise": amount_paise, "receipt": receipt}))
            return o.model_copy()

    def fetch_order(self, order_id: str) -> Order:
        try:
            return self.orders[order_id].model_copy()
        except KeyError as e:
            raise FakeRazorpayError("BAD_REQUEST_ERROR", f"order {order_id} not found") from e

    def create_upi_payment_link(
        self, amount_paise: int, description: str, reference_id: str, notes: dict[str, str] | None = None
    ) -> PaymentLink:
        if amount_paise < 100:
            raise FakeRazorpayError("BAD_REQUEST_ERROR", "amount must be at least ₹1.00")
        with self._lock:
            order = self.create_order(amount_paise, receipt=reference_id, notes=notes)
            pl = PaymentLink(
                id=_rid("plink"),
                short_url=f"https://rzp.io/fake/{secrets.token_urlsafe(6)}",
                amount_paise=amount_paise,
                order_id=order.id,
                notes=notes or {},
            )
            self.links[pl.id] = pl
            self.calls.append(("create_upi_payment_link", {"amount_paise": amount_paise, "ref": reference_id}))
            return pl.model_copy()

    def fetch_payment(self, payment_id: str) -> Payment:
        try:
            return self.payments[payment_id].model_copy()
        except KeyError as e:
            raise FakeRazorpayError("BAD_REQUEST_ERROR", f"payment {payment_id} not found") from e

    def fetch_payments_for_order(self, order_id: str) -> list[Payment]:
        return [p.model_copy() for p in self.payments.values() if p.order_id == order_id]

    def create_refund(self, payment_id: str, amount_paise: int, notes: dict[str, str] | None = None) -> Refund:
        with self._lock:
            p = self.payments.get(payment_id)
            if p is None:
                raise FakeRazorpayError("BAD_REQUEST_ERROR", f"payment {payment_id} not found")
            if p.status != "captured":
                raise FakeRazorpayError("BAD_REQUEST_ERROR", "only captured payments can be refunded")
            if amount_paise <= 0 or amount_paise > p.amount_paise - p.amount_refunded_paise:
                raise FakeRazorpayError("BAD_REQUEST_ERROR", "refund exceeds refundable amount")
            r = Refund(id=_rid("rfnd"), payment_id=payment_id, amount_paise=amount_paise, notes=notes or {})
            self.refunds[r.id] = r
            p.amount_refunded_paise += amount_paise
            if p.amount_refunded_paise == p.amount_paise:
                p.status = "refunded"
            self.calls.append(("create_refund", {"payment_id": payment_id, "amount_paise": amount_paise}))
            self._emit("refund.processed", {"refund": r.model_dump(), "payment": p.model_dump()})
            return r.model_copy()

    def create_customer(self, name: str, contact: str = "", email: str = "") -> Customer:
        with self._lock:
            c = Customer(id=_rid("cust"), name=name, contact=contact, email=email)
            self.customers[c.id] = c
            return c.model_copy()

    # ------------------------------------------------------------------ simulation (test-only)
    def simulate_payment(self, order_id: str, succeed: bool = True, method: str = "upi") -> Payment:
        """Pretend the buyer paid (or failed) the order; emits the corresponding webhook."""
        with self._lock:
            o = self.orders.get(order_id)
            if o is None:
                raise FakeRazorpayError("BAD_REQUEST_ERROR", f"order {order_id} not found")
            if o.status == "paid":
                raise FakeRazorpayError("BAD_REQUEST_ERROR", "order already paid")
            p = Payment(
                id=_rid("pay"),
                order_id=order_id,
                amount_paise=o.amount_paise,
                status="captured" if succeed else "failed",
                method=method,
            )
            self.payments[p.id] = p
            if succeed:
                o.status = "paid"
                o.amount_paid_paise = o.amount_paise
                for pl in self.links.values():
                    if pl.order_id == order_id:
                        pl.status = "paid"
                self._emit("payment.captured", {"payment": p.model_dump(), "order": o.model_dump()})
            else:
                o.status = "attempted"
                self._emit("payment.failed", {"payment": p.model_dump(), "order": o.model_dump()})
            return p.model_copy()
