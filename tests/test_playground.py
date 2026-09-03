from fastapi.testclient import TestClient

from bazaar.gateway import BazaarState, create_app


def _client(merchants, tmp_path):
    st = BazaarState(audit_path=tmp_path / "audit.jsonl")
    for m in merchants:
        mm = m.model_copy(deep=True)
        for p in mm.products:
            p.stock = 30
        mm.policy.review_first = False
        st.add_merchant(mm)
    return st, TestClient(create_app(st), headers={"x-admin-token": st.settings.bazaar_admin_token})


def test_playground_full_flow_and_tamper(merchants, tmp_path):
    st, c = _client(merchants, tmp_path)
    mid = next(m.merchant_id for m in st.merchants.values() if m.vertical.value == "grocery")
    r = c.post("/bazaar/v1/dev/playground/sessions", json={"merchant_id": mid, "message": "5 kg basmati rice to 560034", "segment": "new"})
    assert r.status_code == 201 and r.json()["session"]["tier"] == 2 and r.json()["session"]["quote"]
    sid = r.json()["session"]["session_id"]
    r = c.post(f"/bazaar/v1/dev/playground/sessions/{sid}/messages", json={"message": "koi discount milega?"})
    assert r.json()["turn"]["action"] == "apply_offer" and r.json()["turn"]["ok"]
    # tampered mandate → declined, session retryable
    r = c.post(f"/bazaar/v1/dev/playground/sessions/{sid}/checkout", json={"tamper": True})
    steps = r.json()["steps"]
    gate = next(s for s in steps if s["step"] == "policy_gate")
    assert not gate["ok"] and any(ch["name"] == "checkout_mandate_signed" and not ch["passed"] for ch in gate["checks"])
    assert r.json()["session"]["status"] == "ready_for_payment"
    # clean checkout → paid
    r = c.post(f"/bazaar/v1/dev/playground/sessions/{sid}/checkout", json={})
    names = [s["step"] for s in r.json()["steps"]]
    assert names == ["grant_issued", "mandates_signed", "policy_gate", "payment_link", "webhook_payment_captured"]
    assert r.json()["session"]["status"] == "completed"
    rep = c.get(f"/bazaar/v1/sessions/{sid}/replay").json()
    assert rep["chain_ok"] and any(t["action"] == "payment_captured" for t in rep["timeline"])


def test_playground_review_first_then_pay(merchants, tmp_path):
    st, c = _client(merchants, tmp_path)
    mid = next(m.merchant_id for m in st.merchants.values() if m.vertical.value == "grocery")
    st.merchants[mid].policy.review_first = True
    r = c.post("/bazaar/v1/dev/playground/sessions", json={"merchant_id": mid, "message": "2 kg basmati rice to 560034"})
    sid = r.json()["session"]["session_id"]
    assert c.post(f"/bazaar/v1/dev/playground/sessions/{sid}/pay").status_code == 409
    r = c.post(f"/bazaar/v1/dev/playground/sessions/{sid}/checkout", json={})
    assert r.json()["session"]["status"] == "awaiting_merchant_review"
    assert c.post(f"/bazaar/v1/merchants/{mid}/review-sessions/{sid}/approve").json()["status"] == "in_progress"
    r = c.post(f"/bazaar/v1/dev/playground/sessions/{sid}/pay")
    assert r.status_code == 200 and r.json()["session"]["status"] == "completed"
