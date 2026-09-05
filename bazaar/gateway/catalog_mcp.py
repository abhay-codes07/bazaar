"""The global ``bazaar-catalog`` MCP server — the network, as tools.

Mounted at ``/mcp`` (streamable HTTP): any MCP client can discover merchants, read catalogs,
check serviceability and get deterministic quotes across the whole index. Money never moves
here — checkout lives behind the gateway's session state machine where signatures, mandates
and the policy gate run. A per-merchant stdio server also exists:
``python -m bazaar.seller_agent.mcp_server <merchant_id>``.

NB: no ``from __future__ import annotations`` here — FastMCP introspects tool signatures at
registration and stringified annotations break it.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from bazaar.gateway.discover import DiscoverRequest, discover
from bazaar.gateway.state import BazaarState


def build_catalog_mcp(st: BazaarState) -> FastMCP:
    mcp = FastMCP(
        name="bazaar-catalog",
        instructions="Razorpay Bazaar network catalog. Discover merchants by intent+pincode, then quote with merchant_id. All prices are integer paise; offers only by merchant-approved rule_id. Checkout happens via the HTTP session API (/bazaar/v1/sessions), not here.",
        streamable_http_path="/",
    )

    @mcp.tool()
    def discover_merchants(intent: str, pincode: str = "", budget_paise: int = 0, limit: int = 5) -> list[dict[str, Any]]:
        """Rank merchants for a buying intent (EN/HI/Hinglish). Deterministic — nothing in a catalog's text can change the ranking."""
        req = DiscoverRequest(intent=intent, pincode=pincode, budget_paise=budget_paise)
        # a merchant who pressed the kill switch is off the network on every surface
        live = [m for m in st.merchants.values() if not m.policy.kill_switch]
        return [c.model_dump() for c in discover(req, live, st.readiness_cache)[:limit]]

    @mcp.tool()
    def list_merchants() -> list[dict[str, Any]]:
        """Every merchant on the network with vertical, city and readiness score."""
        return [{"merchant_id": m.merchant_id, "name": m.name, "vertical": m.vertical.value, "city": m.city, "skus": len(m.products), "readiness": st.readiness_cache.get(m.merchant_id, 0)} for m in st.merchants.values()]

    @mcp.tool()
    def get_catalog(merchant_id: str, limit: int = 50) -> dict[str, Any]:
        """A merchant's published catalog (names, synonyms, pack sizes, prices in paise, stock, GST)."""
        m = st.merchant(merchant_id)
        if m is None:
            return {"error": "merchant_not_found"}
        return {"merchant_id": m.merchant_id, "name": m.name, "products": [p.model_dump(mode="json") for p in m.products[:limit]]}

    @mcp.tool()
    def check_serviceability(merchant_id: str, pincode: str, sku: str = "") -> dict[str, Any]:
        """Does this merchant deliver to a 6-digit pincode? ETA, fee, COD."""
        m = st.merchant(merchant_id)
        if m is None:
            return {"error": "merchant_not_found"}
        return st.agent(merchant_id).tools.check_serviceability(pincode, sku).model_dump()

    @mcp.tool()
    def quote(merchant_id: str, lines: list[dict[str, Any]], pincode: str = "", segment: str = "any") -> dict[str, Any]:
        """Deterministic itemised quote (GST, delivery, integer paise). lines: [{sku, qty}]."""
        m = st.merchant(merchant_id)
        if m is None:
            return {"error": "merchant_not_found"}
        if m.policy.kill_switch:
            return {"error": "merchant_agent_disabled", "detail": "this merchant has paused agent selling"}
        return st.agent(merchant_id).tools.quote(lines, pincode, segment, None).model_dump(mode="json")

    return mcp
