"""Small signing client used by the simulator and tests: wraps an HTTP client and adds RFC 9421
signatures + mandate helpers, so a buyer agent can be written in a few lines."""

from __future__ import annotations

import json
from typing import Any

from bazaar.trust import keys
from bazaar.trust.http_sig import TAG_BROWSE, TAG_PAY, sign_request
from bazaar.trust.mandates import CheckoutMandate, PaymentMandate


class BuyerAgentClient:
    """``http`` must expose ``request(method, url, content=..., headers=...)`` (httpx / TestClient)."""

    def __init__(self, http, authority: str = "testserver", operator: str = "sim-buyer"):
        self.http = http
        self.authority = authority
        self.operator = operator
        self.agent_priv = keys.generate()
        self.agent_pub_raw = keys.public_bytes(self.agent_priv)
        self.keyid = keys.keyid_for(self.agent_pub_raw)
        self.buyer_priv = keys.generate()
        self.buyer_keyid = ""

    # ------------------------------------------------------------------ onboarding
    def register(self) -> dict[str, Any]:
        r = self.http.request("POST", "/bazaar/v1/agents/register", content=json.dumps({"public_key_b64u": keys.b64u(self.agent_pub_raw), "operator": self.operator}), headers={"content-type": "application/json"})
        assert r.status_code == 201, r.text
        b = self.http.request("POST", "/bazaar/v1/buyers/keys", content=json.dumps({"public_key_b64u": keys.b64u(keys.public_bytes(self.buyer_priv))}), headers={"content-type": "application/json"})
        self.buyer_keyid = b.json()["keyid"]
        return r.json()

    # ------------------------------------------------------------------ signed requests
    def call(self, method: str, path: str, body: dict[str, Any] | None = None, tag: str = TAG_BROWSE, extra_headers: dict[str, str] | None = None):
        raw = json.dumps(body).encode() if body is not None else b""
        hdrs = sign_request(self.agent_priv, self.keyid, method, self.authority, path, raw, tag=tag)
        hdrs["content-type"] = "application/json"
        hdrs.update(extra_headers or {})
        return self.http.request(method, path, content=raw, headers=hdrs)

    def pay_call(self, method: str, path: str, body: dict[str, Any] | None = None, idempotency_key: str | None = None):
        extra = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self.call(method, path, body, tag=TAG_PAY, extra_headers=extra)

    # ------------------------------------------------------------------ mandates
    def mandates_for(self, quote: dict[str, Any], merchant_id: str, buyer_ref: str, max_amount_paise: int, human_present: bool = True) -> tuple[dict, dict]:
        cm = CheckoutMandate.open(buyer_ref, max_amount_paise, pincode=quote.get("pincode", ""), human_present=human_present).close(quote["quote_id"], merchant_id, quote["total_paise"])
        cm.sign(self.buyer_priv, self.buyer_keyid)
        pm = PaymentMandate.open(buyer_ref, max_amount_paise).close(cm)
        pm.sign(self.buyer_priv, self.buyer_keyid)
        return cm.model_dump(mode="json"), pm.model_dump(mode="json")
