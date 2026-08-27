"""Read whatever the merchant has and turn it into loosely-typed rows.

Every source becomes ``list[RawRow]`` with the same canonical keys, so the rest of the
compiler never cares whether the input was a Sheet, a Shopify export or a photo.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import TypedDict

CANONICAL = ("name", "price", "unit", "stock", "gst", "description", "category", "sku")

_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "name": ("item", "product", "naam", "name", "title", "vastu"),
    "price": ("price", "mrp", "rate", "amount", "daam", "cost"),
    "unit": ("unit", "pack", "qty unit", "size", "weight", "measure"),
    "stock": ("stock", "available", "qty", "quantity", "inventory"),
    "gst": ("gst", "tax"),
    "description": ("desc", "detail", "note", "about", "body"),
    "category": ("category", "type", "group", "collection"),
    "sku": ("sku", "code", "id"),
}


class RawRow(TypedDict, total=False):
    name: str
    price: str
    unit: str
    stock: str
    gst: str
    description: str
    category: str
    sku: str
    _row: int


def map_headers(headers: list[str]) -> dict[int, str]:
    """Map column index → canonical key using keyword hints; first match wins per key."""
    mapping: dict[int, str] = {}
    taken: set[str] = set()
    for i, h in enumerate(headers):
        hl = h.strip().lower()
        if not hl:
            continue
        for key, hints in _HEADER_HINTS.items():
            if key in taken:
                continue
            if any(hint in hl for hint in hints):
                # "qty unit" must map to unit, not stock
                if key == "stock" and "unit" in hl:
                    continue
                mapping[i] = key
                taken.add(key)
                break
    return mapping


def read_csv_text(text: str) -> list[RawRow]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    mapping = map_headers(rows[0])
    if "name" not in mapping.values():
        raise ValueError(f"could not find a product-name column in headers {rows[0]!r}")
    out: list[RawRow] = []
    for n, r in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in r):
            continue
        row: RawRow = {"_row": n}
        for i, key in mapping.items():
            if i < len(r):
                row[key] = r[i].strip()  # type: ignore[literal-required]
        out.append(row)
    return out


def read_csv(path: Path) -> list[RawRow]:
    return read_csv_text(path.read_text(encoding="utf-8-sig"))


def read_shopify_json(path: Path) -> list[RawRow]:
    """Shopify/WooCommerce style product export (``{"products": [...]}``)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[RawRow] = []
    for n, p in enumerate(data.get("products", []), start=1):
        v = (p.get("variants") or [{}])[0]
        out.append(
            {
                "_row": n,
                "name": p.get("title", ""),
                "price": str(v.get("price", "")),
                "unit": v.get("title", "") if v.get("title") not in (None, "Default Title") else "",
                "stock": str(v.get("inventory_quantity", "")),
                "description": p.get("body_html", ""),
                "category": p.get("product_type", ""),
                "sku": v.get("sku", ""),
            }
        )
    return out


def read_image(path: Path, llm) -> list[RawRow]:  # pragma: no cover - needs a vision model
    """Photo of a rate card → rows, via the model's ``read_rate_card`` task.

    The fake backend returns nothing here; real deployments use a vision-capable model.
    """
    import base64

    b64 = base64.b64encode(path.read_bytes()).decode()
    out = llm.complete_json(
        "read_rate_card",
        "Transcribe the price list in the image into rows. Do not invent items.",
        f'<image base64="{b64[:64]}..."/>',
        {"type": "object", "properties": {"rows": {"type": "array"}}, "required": ["rows"]},
    )
    return [dict(r, _row=i + 1) for i, r in enumerate(out.get("rows", []))]  # type: ignore[return-value]
