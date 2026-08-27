"""Per-merchant MCP server exposing the seller tools (read-only + quote/reserve).

Money-moving actions (checkout, refund) are deliberately *not* MCP tools here — they live
behind the gateway's session state machine where mandates and grants are verified.
"""


from typing import Any

from mcp.server.fastmcp import FastMCP

from bazaar.schemas.models import Merchant
from bazaar.seller_agent.tools import SellerTools


def build_mcp(merchant: Merchant, tools: SellerTools | None = None) -> FastMCP:
    t = tools or SellerTools(merchant)
    mcp = FastMCP(name=f"bazaar-{merchant.merchant_id}", instructions=f"Seller tools for {merchant.name} ({merchant.city}). All prices in paise; offers only by rule_id.")

    @mcp.tool()
    def search_products(query: str, limit: int = 5) -> dict[str, Any]:
        """Find products by name/synonym/category (EN/HI/Hinglish)."""
        return t.search_products(query, limit).model_dump()

    @mcp.tool()
    def get_availability(sku: str, qty: int) -> dict[str, Any]:
        """Free stock for a SKU after active reservations."""
        return t.get_availability(sku, qty).model_dump()

    @mcp.tool()
    def check_serviceability(pincode: str, sku: str = "") -> dict[str, Any]:
        """Does the merchant deliver to this 6-digit pincode? ETA, fee, COD."""
        return t.check_serviceability(pincode, sku).model_dump()

    @mcp.tool()
    def quote(lines: list[dict[str, Any]], pincode: str = "", segment: str = "any", rule_ids: list[str] | None = None) -> dict[str, Any]:
        """Itemised quote (GST, delivery, offers by rule_id). Valid 30 minutes."""
        return t.quote(lines, pincode, segment, rule_ids).model_dump(mode="json")

    @mcp.tool()
    def apply_offer(quote_id: str, rule_id: str) -> dict[str, Any]:
        """Apply a merchant pre-approved offer rule to an existing quote."""
        return t.apply_offer(quote_id, rule_id).model_dump(mode="json")

    @mcp.tool()
    def reserve(quote_id: str) -> dict[str, Any]:
        """Soft-hold stock for a quote (15 minutes)."""
        return t.reserve(quote_id).model_dump(mode="json")

    return mcp
