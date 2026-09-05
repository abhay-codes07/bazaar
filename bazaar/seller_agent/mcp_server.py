"""Per-merchant MCP server exposing the seller tools (read-only + quote/reserve).

Money-moving actions (checkout, refund) are deliberately *not* MCP tools here — they live
behind the gateway's session state machine where mandates and grants are verified. The
side-effect tools (``apply_offer``, ``reserve``) run through the same deterministic
``SellerAgent.verify`` gate as the HTTP path, so the kill switch, agent allowlist,
negotiation-round cap and rule checks hold for MCP clients too — merchant authority is
enforced on every surface, not just HTTP.
"""


from typing import Any

from bazaar.llm import FakeLLM
from bazaar.schemas.models import AgentTier, Merchant
from bazaar.seller_agent.agent import BuyerContext, SellerAgent
from bazaar.seller_agent.propose import Proposal
from bazaar.seller_agent.tools import SellerTools

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]


def build_mcp(merchant: Merchant, tools: SellerTools | None = None, tier: AgentTier = AgentTier.T1_SIGNED, agent_keyid: str = "mcp-stdio-local"):
    t = tools or SellerTools(merchant)
    # verify-only guard: the FakeLLM is never called — verify() is deterministic
    guard = SellerAgent(merchant, llm=FakeLLM())
    ctx = BuyerContext(agent_keyid=agent_keyid, tier=tier)
    state: dict[str, Any] = {"negotiation_rounds": 0}

    def _gate(tool_name: str, args: dict[str, Any], rule_id: str = "") -> dict[str, Any] | None:
        prop = Proposal(tool=tool_name, args=args, rule_id=rule_id, rationale="mcp client request")
        checks = guard.verify(prop, ctx, state)
        if any(not c.passed for c in checks):
            return {"ok": False, "declined": True, "reason": "; ".join(c.name for c in checks if not c.passed), "policy_checks": [c.model_dump() for c in checks]}
        return None

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
        """Apply a merchant pre-approved offer rule to an existing quote (policy-gated)."""
        declined = _gate("apply_offer", {"quote_id": quote_id, "rule_id": rule_id}, rule_id=rule_id)
        if declined:
            return declined
        res = t.apply_offer(quote_id, rule_id)
        if res.ok:
            state["negotiation_rounds"] = int(state.get("negotiation_rounds", 0)) + 1
        return res.model_dump(mode="json")

    @mcp.tool()
    def reserve(quote_id: str) -> dict[str, Any]:
        """Soft-hold stock for a quote (15 minutes; policy-gated)."""
        declined = _gate("reserve", {"quote_id": quote_id})
        if declined:
            return declined
        return t.reserve(quote_id).model_dump(mode="json")

    return mcp


def main(argv: list[str] | None = None) -> int:
    """``python -m bazaar.seller_agent.mcp_server <merchant_id>`` — stdio MCP server for one
    merchant, the standard way a desktop agent (e.g. Claude Desktop) mounts a seller."""
    import argparse
    import sys

    from bazaar.settings import get_settings
    from bazaar.synthetic import load_corpus

    p = argparse.ArgumentParser(prog="bazaar-merchant-mcp")
    p.add_argument("merchant_id")
    p.add_argument("--corpus", default=str(get_settings().data_dir / "synthetic"))
    a = p.parse_args(argv)
    from pathlib import Path

    merchants = {m.merchant_id: m for m in load_corpus(Path(a.corpus))}
    m = merchants.get(a.merchant_id)
    if m is None:
        print(f"unknown merchant {a.merchant_id!r}; have: {', '.join(list(merchants)[:5])}…", file=sys.stderr)
        return 2
    build_mcp(m).run()  # stdio transport
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
