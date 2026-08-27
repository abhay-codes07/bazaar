import json

import pytest

from bazaar.razorpay_client import get_payments_client, verify_webhook_signature
from bazaar.razorpay_client.fake import FakeRazorpay, FakeRazorpayError


def test_order_and_upi_link_and_payment_roundtrip():
    rz = FakeRazorpay.shared()
    events = []
    rz.on_webhook(lambda ev, body, sig: events.append((ev, body, sig)))

    link = rz.create_upi_payment_link(70000, "5 kg basmati", reference_id="sess_1", notes={"merchant": "m1"})
    assert link.id.startswith("plink_") and link.short_url.startswith("https://")
    order = rz.fetch_order(link.order_id)
    assert order.status == "created" and order.amount_paise == 70000

    pay = rz.simulate_payment(order.id)
    assert pay.status == "captured"
    assert rz.fetch_order(order.id).status == "paid"
    assert rz.links[link.id].status == "paid"
    assert rz.fetch_payments_for_order(order.id)[0].id == pay.id

    ev, body, sig = events[-1]
    assert ev.event == "payment.captured"
    assert verify_webhook_signature(body, sig, rz.webhook_secret)
    assert not verify_webhook_signature(body, "deadbeef", rz.webhook_secret)
    assert json.loads(body)["payload"]["order"]["id"] == order.id


def test_failed_payment_and_double_pay_guard():
    rz = FakeRazorpay.shared()
    o = rz.create_order(50000, "r1")
    p = rz.simulate_payment(o.id, succeed=False)
    assert p.status == "failed" and rz.fetch_order(o.id).status == "attempted"
    rz.simulate_payment(o.id)
    with pytest.raises(FakeRazorpayError):
        rz.simulate_payment(o.id)


def test_refund_rules():
    rz = FakeRazorpay.shared()
    o = rz.create_order(10000, "r2")
    with pytest.raises(FakeRazorpayError):
        rz.create_refund("pay_nope", 100)
    p = rz.simulate_payment(o.id)
    with pytest.raises(FakeRazorpayError):
        rz.create_refund(p.id, 10001)
    r1 = rz.create_refund(p.id, 4000)
    assert r1.status == "processed"
    assert rz.fetch_payment(p.id).amount_refunded_paise == 4000
    rz.create_refund(p.id, 6000)
    assert rz.fetch_payment(p.id).status == "refunded"


def test_minimum_amount():
    rz = FakeRazorpay.shared()
    with pytest.raises(FakeRazorpayError):
        rz.create_order(50, "tiny")


def test_factory_returns_fake_by_default():
    assert isinstance(get_payments_client(), FakeRazorpay)
