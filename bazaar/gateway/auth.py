"""Request-level identity: RFC 9421 signature → registered agent → tier. Unsigned = T0."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from bazaar.gateway.state import BazaarState
from bazaar.schemas.models import AgentTier
from bazaar.trust.http_sig import TAG_PAY, SignatureError, verify_request


@dataclass
class Caller:
    keyid: str = ""
    tier: AgentTier = AgentTier.T0_UNSIGNED
    tag: str = ""
    operator: str = ""


async def identify(request: Request, state: BazaarState, required_tag: str | None = None) -> Caller:
    hdrs = {k: v for k, v in request.headers.items()}
    if "signature-input" not in {k.lower() for k in hdrs}:
        if required_tag == TAG_PAY:
            raise HTTPException(401, detail={"error": "signature_required", "step": "headers", "hint": "sign the request with tag=agent-pay"})
        return Caller()
    body = await request.body()
    authority = request.headers.get("host", "")
    try:
        v = verify_request(hdrs, request.method, authority, request.url.path, body, state.agent_pub, state.nonces, required_tag=required_tag)
    except SignatureError as e:
        raise HTTPException(401, detail={"error": "signature_invalid", "step": e.step, "detail": e.detail}) from e
    ident = state.registry.get(v.keyid)
    assert ident is not None
    return Caller(keyid=v.keyid, tier=ident.tier, tag=v.tag, operator=ident.operator)


def require_admin(request: Request, state: BazaarState) -> None:
    if request.headers.get("x-admin-token", "") != state.settings.bazaar_admin_token:
        raise HTTPException(403, detail={"error": "admin_token_required"})
