from fastapi.testclient import TestClient

from bazaar.gateway import BazaarState, create_app
from bazaar.gateway.client import BuyerAgentClient


def _env(merchants, tmp_path):
    st = BazaarState(audit_path=tmp_path / "audit.jsonl")
    for m in merchants:
        mm = m.model_copy(deep=True)
        for p in mm.products:
            p.stock = 30
        mm.policy.review_first = False
        st.add_merchant(mm)
    c = TestClient(create_app(st))
    buyer = BuyerAgentClient(c)
    buyer.register()
    c.post(f"/bazaar/v1/agents/{buyer.keyid}/tier", json={"tier": 2, "reason": "t"}, headers={"x-admin-token": st.settings.bazaar_admin_token})
    mid = next(m.merchant_id for m in st.merchants.values() if m.vertical.value == "grocery")
    rice = next(p for p in st.merchants[mid].products if p.name == "Basmati Rice")
    return st, c, buyer, mid, rice


def test_ucp_profile_and_checkout_with_ap2_mandates(merchants, tmp_path):
    st, c, buyer, mid, rice = _env(merchants, tmp_path)
    prof = c.get(f"/ucp/{mid}/.well-known/ucp").json()["ucp"]
    assert any(cap["name"] == "in.razorpay.bazaar.india" for cap in prof["capabilities"])
    r = buyer.call("POST", f"/ucp/{mid}/checkout-sessions", {"line_items": [{"item": {"id": rice.sku}, "quantity": 5}], "fulfillment": {"postal_code": "560034"}})
    assert r.status_code == 201, r.text
    cs = r.json()
    assert cs["status"] == "ready_for_complete" and cs["totals"][-1]["type"] == "total" and cs["payment"]["handlers"][0]["id"] == "razorpay"
    r = buyer.call("PUT", f"/ucp/{mid}/checkout-sessions/{cs['id']}", {"line_items": [{"item": {"id": rice.sku}, "quantity": 2}]})
    assert r.json()["line_items"][0]["quantity"] == 2
    total = r.json()["totals"][-1]["amount"]
    quote = st.session(cs["id"]).quote
    g = buyer.pay_call("POST", "/bazaar/v1/grants", {"buyer_ref": "u1", "merchant_id": mid, "max_amount_paise": total + 100}).json()["grant_id"]
    cm, pm = buyer.mandates_for(quote, mid, "u1", total + 100)
    r = buyer.pay_call("POST", f"/ucp/{mid}/checkout-sessions/{cs['id']}/complete", {"payment": {"handler": "razorpay", "grant_id": g}, "checkout_mandate": cm, "payment_mandate": pm, "human_confirmation": True}, idempotency_key="u-1")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "complete" and out["order"]["payment_url"].startswith("https://") and out["policy"]["allowed"]
    st.payments.simulate_payment(out["order"]["id"])  # type: ignore[attr-defined]
    assert c.get(f"/ucp/{mid}/checkout-sessions/{cs['id']}").json()["payment"]["status"] == "captured"
    # missing mandates → 422 with named checks, session retryable
    r2 = buyer.call("POST", f"/ucp/{mid}/checkout-sessions", {"line_items": [{"item": {"id": rice.sku}, "quantity": 1}], "fulfillment": {"postal_code": "560034"}})
    r = buyer.pay_call("POST", f"/ucp/{mid}/checkout-sessions/{r2.json()['id']}/complete", {"payment": {"handler": "razorpay", "grant_id": g}})
    assert r.status_code == 422 and any(ch["name"] == "checkout_mandate_present" and not ch["passed"] for ch in r.json()["policy"]["checks"])
    assert r.json()["status"] == "ready_for_complete" and r.json()["messages"][0]["code"] == "policy_declined"


def test_beckn_search_select_init_confirm_status(merchants, tmp_path):
    st, c, buyer, mid, rice = _env(merchants, tmp_path)
    ctx = {"domain": "ONDC:RET10", "bap_id": "buyerapp.example", "bap_uri": "https://buyerapp.example/beckn", "transaction_id": "txn-1", "message_id": "m-1"}
    r = c.post(f"/beckn/{mid}/search", json={"context": ctx, "message": {"intent": {"item": {"descriptor": {"name": "basmati chawal"}}, "fulfillment": {"end": {"location": {"address": {"area_code": "560034"}}}}}}})
    out = r.json()
    assert out["message"]["ack"]["status"] == "ACK" and out["callback"]["context"]["action"] == "on_search"
    items = out["callback"]["message"]["catalog"]["providers"][0]["items"]
    assert items and items[0]["id"] == rice.sku and items[0]["price"]["currency"] == "INR"
    # unserviceable pincode → empty providers
    r = c.post(f"/beckn/{mid}/search", json={"context": ctx, "message": {"intent": {"item": {"descriptor": {"name": "rice"}}, "fulfillment": {"end": {"location": {"address": {"area_code": "110001"}}}}}}})
    assert r.json()["callback"]["message"]["catalog"]["providers"] == []
    order_msg = {"order": {"items": [{"id": rice.sku, "quantity": {"count": 3}}], "fulfillments": [{"end": {"location": {"address": {"area_code": "560034"}}}}]}}
    r = c.post(f"/beckn/{mid}/select", json={"context": ctx, "message": order_msg})
    sel = r.json()["callback"]["message"]["order"]
    assert r.json()["callback"]["context"]["action"] == "on_select" and sel["quote"]["price"]["currency"] == "INR"
    assert any(b["@ondc/org/title_type"] == "tax" for b in sel["quote"]["breakup"])
    oid = sel["id"]
    r = c.post(f"/beckn/{mid}/init", json={"context": ctx, "message": {"order": {"id": oid}}})
    assert r.json()["callback"]["message"]["order"]["payment"]["collected_by"] == "BPP"
    r = c.post(f"/beckn/{mid}/confirm", json={"context": ctx, "message": {"order": {"id": oid}}})
    conf = r.json()["callback"]["message"]["order"]
    assert conf["payment"]["status"] == "NOT-PAID" and conf["payment"]["uri"].startswith("https://") and conf["state"] == "Created"
    st.payments.simulate_payment(conf["payment"]["params"]["transaction_id"])  # type: ignore[attr-defined]
    r = c.post(f"/beckn/{mid}/status", json={"context": ctx, "message": {"order_id": oid}})
    assert r.json()["callback"]["message"]["order"]["payment"]["status"] == "PAID"
    assert st.session(oid).status == "completed"
    # confirm without select → NACK; unknown sku → NACK
    assert c.post(f"/beckn/{mid}/confirm", json={"context": ctx, "message": {"order": {"id": "nope"}}}).json()["message"]["ack"]["status"] == "NACK"
    assert c.post(f"/beckn/{mid}/select", json={"context": ctx, "message": {"order": {"items": [{"id": "ghost", "quantity": {"count": 1}}]}}}).json()["message"]["ack"]["status"] == "NACK"
    rep = c.get(f"/bazaar/v1/sessions/{oid}/replay").json()
    assert rep["chain_ok"] and [t["action"] for t in rep["timeline"] if t["kind"] == "money"] == ["payment_link_created", "payment_captured"]
