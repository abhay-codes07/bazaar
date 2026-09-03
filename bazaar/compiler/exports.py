"""One compile → every protocol surface.

* ``/.well-known/bazaar``  — Bazaar manifest (superset; UCP-shaped with the ``bazaar.india`` extension)
* ``/.well-known/ucp``     — UCP-style discovery profile
* ACP product feed          — OpenAI/Stripe Agentic Commerce Protocol feed items
* Beckn ``on_search`` catalog — for the ONDC bridge
* ``llms.txt`` / ``llms-full.txt`` — agent-readable text
* Schema.org JSON-LD
"""

from __future__ import annotations

from typing import Any

from bazaar.schemas.models import Merchant, Product

BAZAAR_VERSION = "2026-08-28"


def _rupees(paise: int) -> str:
    return f"{paise / 100:.2f}"


def _offer(p: Product) -> dict[str, Any]:
    return {
        "@type": "Offer",
        "price": _rupees(p.price_paise),
        "priceCurrency": "INR",
        "availability": "https://schema.org/InStock" if p.stock > 0 else "https://schema.org/OutOfStock",
        "inventoryLevel": {"@type": "QuantitativeValue", "value": p.stock},
    }


def well_known_bazaar(m: Merchant, base_url: str) -> dict[str, Any]:
    return {
        "bazaar": {
            "version": BAZAAR_VERSION,
            "merchant_id": m.merchant_id,
            "name": m.name,
            "vertical": m.vertical.value,
            "languages": m.languages,
            "services": {
                "catalog": f"{base_url}/bazaar/v1/merchants/{m.merchant_id}/catalog",
                "sessions": f"{base_url}/bazaar/v1/sessions",
                "mcp": f"{base_url}/mcp",
            },
            "capabilities": [
                {"name": "dev.bazaar.discover", "version": BAZAAR_VERSION},
                {"name": "dev.bazaar.quote", "version": BAZAAR_VERSION},
                {"name": "dev.bazaar.negotiate", "version": BAZAAR_VERSION, "max_rounds": m.policy.max_negotiation_rounds},
                {"name": "dev.bazaar.checkout", "version": BAZAAR_VERSION, "min_agent_tier": int(m.policy.min_tier_for_checkout)},
            ],
            "extensions": {
                "in.razorpay.bazaar.india": {
                    "serviceability": {
                        "pincode_prefixes": m.serviceability.pincode_prefixes,
                        "pincodes": m.serviceability.pincodes,
                        "eta_hours": m.serviceability.eta_hours,
                        "delivery_fee": _rupees(m.serviceability.delivery_fee_paise),
                        "free_delivery_above": _rupees(m.serviceability.free_delivery_above_paise),
                    },
                    "cod_allowed": m.serviceability.cod_allowed,
                    "gst_registered": bool(m.gstin),
                    "payment_mandates": ["upi_reserve_pay", "upi_autopay", "scoped_grant"],
                }
            },
            "payment_handlers": [
                {"id": "razorpay", "methods": ["upi", "card", "netbanking"], "grant_types": ["scoped_grant"]}
            ],
            "policy": {"review_first": m.policy.review_first, "agent_allowlist": bool(m.policy.agent_allowlist)},
        }
    }


def well_known_ucp(m: Merchant, base_url: str) -> dict[str, Any]:
    return {
        "ucp": {
            "version": "2026-01-11",
            "services": {
                "dev.ucp.shopping": {
                    "version": "2026-01-11",
                    "spec": "https://ucp.dev/specification/overview",
                    "rest_endpoint": f"{base_url}/ucp/{m.merchant_id}",
                    "mcp_endpoint": f"{base_url}/mcp",
                }
            },
            "capabilities": [
                {"name": "dev.ucp.shopping.checkout", "version": "2026-01-11"},
                {"name": "dev.ucp.shopping.discount", "version": "2026-01-11", "extends": "dev.ucp.shopping.checkout"},
                {"name": "dev.ucp.shopping.order", "version": "2026-01-11"},
                {"name": "in.razorpay.bazaar.india", "version": BAZAAR_VERSION, "extends": "dev.ucp.shopping.checkout",
                 "spec": f"{base_url}/spec/india-extension"},
            ],
            "payment_handlers": [{"id": "razorpay", "name": "Razorpay", "version": "1"}],
        }
    }


def acp_product_feed(m: Merchant, base_url: str) -> list[dict[str, Any]]:
    """ACP/OpenAI product feed items (subset of required fields)."""
    return [
        {
            "id": p.sku,
            "title": p.name,
            "description": p.description or p.name,
            "link": f"{base_url}/m/{m.merchant_id}/p/{p.sku}",
            "price": f"{_rupees(p.price_paise)} INR",
            "availability": "in_stock" if p.stock > 0 else "out_of_stock",
            "inventory_quantity": p.stock,
            "seller_name": m.name,
            "product_category": p.category,
            "enable_search": True,
            "enable_checkout": True,
            "custom_attributes": {"unit": p.unit.value, "pack_size": p.pack_size, "gst_rate": p.gst_rate_bp / 100, "hsn": p.hsn},
        }
        for p in m.products
    ]


def beckn_on_search(m: Merchant, base_url: str) -> dict[str, Any]:
    return {
        "context": {"domain": "ONDC:RET10", "action": "on_search", "version": "2.0.0", "bpp_id": f"bazaar.{m.merchant_id}", "bpp_uri": f"{base_url}/beckn/{m.merchant_id}"},
        "message": {
            "catalog": {
                "descriptor": {"name": "Bazaar"},
                "providers": [
                    {
                        "id": m.merchant_id,
                        "descriptor": {"name": m.name},
                        "locations": [{"id": "L1", "address": {"city": m.city, "area_code": m.base_pincode}}],
                        "items": [
                            {
                                "id": p.sku,
                                "descriptor": {"name": p.name, "short_desc": p.description},
                                "price": {"currency": "INR", "value": _rupees(p.price_paise)},
                                "quantity": {"available": {"count": p.stock}, "unitized": {"measure": {"unit": p.unit.value, "value": str(p.pack_size)}}},
                                "category_id": p.category,
                            }
                            for p in m.products
                        ],
                    }
                ],
            }
        },
    }


def jsonld(m: Merchant, base_url: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "Store",
        "name": m.name,
        "address": {"@type": "PostalAddress", "addressLocality": m.city, "postalCode": m.base_pincode, "addressCountry": "IN"},
        "makesOffer": [
            {"@type": "Product", "sku": p.sku, "name": p.name, "description": p.description, "category": p.category,
             "alternateName": p.synonyms, "offers": _offer(p)}
            for p in m.products
        ],
    }


def llms_txt(m: Merchant, base_url: str) -> str:
    s = m.serviceability
    lines = [
        f"# {m.name}",
        f"> {m.vertical.value.replace('_', ' ').title()} merchant in {m.city}, India. Transactable by AI agents via Bazaar.",
        "",
        "## Agent endpoints",
        f"- Manifest: {base_url}/.well-known/bazaar (merchant {m.merchant_id})",
        f"- MCP: {base_url}/mcp (bazaar-catalog server; pass merchant_id={m.merchant_id})",
        f"- Sessions: {base_url}/bazaar/v1/sessions",
        "",
        "## Delivery",
        f"- Serves pincodes starting with: {', '.join(s.pincode_prefixes) or '—'}; ETA ~{s.eta_hours} h; fee ₹{_rupees(s.delivery_fee_paise)}"
        + (f", free above ₹{_rupees(s.free_delivery_above_paise)}" if s.free_delivery_above_paise else ""),
        f"- Cash on delivery: {'yes' if s.cod_allowed else 'no'}",
        "",
        "## Products",
    ]
    for p in m.products:
        tags = f" [{', '.join(p.use_case_tags)}]" if p.use_case_tags else ""
        lines.append(f"- {p.name} ({p.pack_size:g} {p.unit.value}) — ₹{_rupees(p.price_paise)}{tags}")
    return "\n".join(lines) + "\n"


def llms_full_txt(m: Merchant, base_url: str) -> str:
    out = [llms_txt(m, base_url), "## Product details", ""]
    for p in m.products:
        out.append(f"### {p.name}")
        out.append(f"SKU {p.sku} · {p.category} · ₹{_rupees(p.price_paise)} per {p.pack_size:g} {p.unit.value} · stock {p.stock} · GST {p.gst_rate_bp / 100:g}%")
        if p.synonyms:
            out.append(f"Also known as: {', '.join(p.synonyms)}")
        if p.buyer_highlights:
            out.append("Highlights: " + "; ".join(p.buyer_highlights))
        if p.description:
            out.append(p.description)
        out.append("")
    return "\n".join(out)


def export_all(m: Merchant, base_url: str) -> dict[str, Any]:
    return {
        "well_known_bazaar": well_known_bazaar(m, base_url),
        "well_known_ucp": well_known_ucp(m, base_url),
        "acp_feed": acp_product_feed(m, base_url),
        "beckn_on_search": beckn_on_search(m, base_url),
        "jsonld": jsonld(m, base_url),
        "llms_txt": llms_txt(m, base_url),
        "llms_full_txt": llms_full_txt(m, base_url),
    }
