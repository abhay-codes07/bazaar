"""``bazaar-conformance``: does a gateway behave like the Bazaar protocol says it should?

Runs against any HTTP client (TestClient or httpx to a live server). Each check is independent
and reports a name, pass/fail and detail — the JSON output doubles as a badge payload.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from bazaar.gateway.client import BuyerAgentClient

REQUIRED_MANIFEST_KEYS = {"version", "services", "capabilities", "extensions", "payment_handlers"}
SESSION_STATUSES = {"open", "ready_for_payment", "awaiting_merchant_review", "in_progress", "completed", "canceled", "declined"}


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""


def run_conformance(http, merchant_id: str | None = None) -> list[Check]:
    out: list[Check] = []

    def add(name: str, passed: bool, detail: str = ""):
        out.append(Check(name=name, passed=bool(passed), detail=detail))

    r = http.request("GET", "/.well-known/bazaar")
    add("network_manifest_present", r.status_code == 200 and "bazaar" in r.json())
    add("headers_api_version_and_request_id", r.headers.get("API-Version", "") != "" and r.headers.get("Request-Id", "") != "")
    r = http.request("GET", "/.well-known/ucp")
    add("ucp_manifest_present", r.status_code == 200 and "ucp" in r.json())

    ms = http.request("GET", "/bazaar/v1/merchants").json()
    add("merchants_listed", isinstance(ms, list) and len(ms) > 0)
    mid = merchant_id or (ms[0]["merchant_id"] if ms else "")
    man = http.request("GET", f"/bazaar/v1/merchants/{mid}/manifest").json().get("bazaar", {})
    add("merchant_manifest_shape", REQUIRED_MANIFEST_KEYS <= set(man.keys()), f"missing {REQUIRED_MANIFEST_KEYS - set(man.keys())}")
    add("india_extension_declared", "in.razorpay.bazaar.india" in man.get("extensions", {}))
    add("llms_txt_served", http.request("GET", f"/bazaar/v1/merchants/{mid}/llms.txt").text.startswith("# "))
    feed = http.request("GET", f"/bazaar/v1/merchants/{mid}/acp/feed").json().get("items", [])
    add("acp_feed_items", bool(feed) and {"id", "title", "price", "availability"} <= set(feed[0].keys()))

    r = http.request("POST", "/bazaar/v1/discover", content=json.dumps({"intent": "anything", "pincode": "000000"}), headers={"content-type": "application/json"})
    add("discover_returns_list", r.status_code == 200 and isinstance(r.json().get("candidates"), list))

    # protocol adapters
    r = http.request("GET", f"/ucp/{mid}/.well-known/ucp")
    add("ucp_merchant_profile", r.status_code == 200 and any(c.get("name") == "dev.ucp.shopping.checkout" for c in r.json().get("ucp", {}).get("capabilities", [])))
    r = http.request("POST", f"/beckn/{mid}/search", content=json.dumps({"context": {"domain": "ONDC:RET10", "bap_id": "conf", "bap_uri": "https://conf.example", "transaction_id": "t", "message_id": "m"}, "message": {"intent": {}}}), headers={"content-type": "application/json"})
    add("beckn_search_ack_and_callback", r.status_code == 200 and r.json().get("message", {}).get("ack", {}).get("status") == "ACK" and r.json().get("callback", {}).get("context", {}).get("action") == "on_search")

    # session state machine
    agent = BuyerAgentClient(http, operator="conformance")
    agent.register()
    r = agent.call("POST", "/bazaar/v1/sessions", {"merchant_id": mid})
    add("session_create_201", r.status_code == 201)
    s = r.json().get("session", {})
    add("session_initial_status_open", s.get("status") == "open")
    add("session_status_vocabulary", s.get("status") in SESSION_STATUSES)
    sid = s.get("session_id", "")
    r = http.request("GET", f"/bazaar/v1/sessions/{sid}")
    add("session_get", r.status_code == 200 and r.json().get("session_id") == sid)
    r = http.request("POST", f"/bazaar/v1/sessions/{sid}/complete", content=b"{}", headers={"content-type": "application/json"})
    add("complete_requires_pay_signature", r.status_code == 401)
    r = agent.call("POST", f"/bazaar/v1/sessions/{sid}/complete", {})
    add("complete_rejects_browse_tag", r.status_code == 401)
    r = agent.pay_call("POST", f"/bazaar/v1/sessions/{sid}/complete", {"human_confirmation": True}, idempotency_key="conf-1")
    add("complete_without_quote_declined_422", r.status_code == 422 and r.json().get("allowed") is False)
    r2 = agent.pay_call("POST", f"/bazaar/v1/sessions/{sid}/complete", {"human_confirmation": True}, idempotency_key="conf-1")
    add("idempotency_key_replayed", r2.headers.get("Idempotent-Replayed") == "true")
    r = http.request("POST", f"/bazaar/v1/sessions/{sid}/cancel")
    add("session_cancel", r.status_code == 200 and r.json().get("status") == "canceled")
    r = http.request("GET", f"/bazaar/v1/sessions/{sid}/replay")
    add("replay_available_with_chain_status", r.status_code == 200 and "chain_ok" in r.json())

    hdrs = {"Signature-Input": "sig1=broken", "Signature": "sig1=:AAAA:", "content-type": "application/json"}
    r = http.request("POST", "/bazaar/v1/sessions", content=json.dumps({"merchant_id": mid}), headers=hdrs)
    add("bad_signature_names_step", r.status_code == 401 and "step" in r.json().get("detail", {}))

    r = http.request("GET", "/bazaar/v1/sessions/does-not-exist")
    add("unknown_session_404", r.status_code == 404)
    r = http.request("POST", "/webhooks/razorpay", content=b"{}", headers={"x-razorpay-signature": "nope"})
    add("webhook_signature_enforced", r.status_code == 400)
    return out


def summarize(checks: list[Check]) -> dict[str, Any]:
    return {"checks": len(checks), "passed": sum(c.passed for c in checks), "failed": [c.name for c in checks if not c.passed], "conformant": all(c.passed for c in checks)}
