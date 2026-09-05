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
    "name": ("item", "product", "naam", "name", "title", "vastu", "saman", "samaan", "cheez", "maal"),
    "price": ("price", "mrp", "rate", "amount", "daam", "cost", "bhav", "keemat", "kimat"),
    "unit": ("unit", "pack", "qty unit", "size", "weight", "measure"),
    "stock": ("stock", "available", "avail", "inventory"),
    "gst": ("gst", "tax"),
    "description": ("desc", "detail", "note", "about", "body"),
    "category": ("category", "type", "group", "collection"),
    "sku": ("sku", "code", "id"),
}
# bare qty/quantity is ambiguous — a kirana sheet's "quantity" is pack text ("10 kg bag"),
# a Shopify export's "Variant Inventory Qty" is stock — so it is resolved in a second pass
_WEAK_QTY = ("qty", "quantity", "qty.")


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
    """Map column index → canonical key. Two passes: unambiguous hints first, then bare
    qty/quantity columns — stock if stock is still free, else the unit/pack column."""
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
                if key == "stock" and "unit" in hl:
                    continue  # "qty unit" must map to unit, not stock
                mapping[i] = key
                taken.add(key)
                break
    for i, h in enumerate(headers):
        if i in mapping:
            continue
        hl = h.strip().lower()
        if any(w in hl for w in _WEAK_QTY):
            key = "stock" if "stock" not in taken else ("unit" if "unit" not in taken else "")
            if key:
                mapping[i] = key
                taken.add(key)
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


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

RATE_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {"type": "object", "properties": {k: {"type": "string"} for k in CANONICAL}, "required": ["name", "price"]},
        }
    },
    "required": ["rows"],
}


def read_image(path: Path, llm) -> list[RawRow]:
    """Photo of a rate card -> rows, via the model's vision entry point.

    The model only *transcribes*: every value it returns is still a string that goes through
    the same normaliser, confidence scoring and review queue as a CSV cell, so the merchant
    sees anything low-confidence before it is published. A backend without vision (the
    offline engine) returns no rows rather than inventing any.
    """
    import base64

    mime = _IMAGE_MIME.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported image type: {path.suffix}")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    out = llm.complete_json_image(
        "read_rate_card",
        "You transcribe a merchant's printed or handwritten price list. Copy each line into a row with the item name, price as written, unit or pack size if shown, stock if shown, and GST if shown. Never invent items, prices or stock that are not visible. Leave a field empty if it is not on the card.",
        "Transcribe every product line in this rate card into rows.",
        b64,
        mime,
        RATE_CARD_SCHEMA,
    )
    rows: list[RawRow] = []
    for i, r in enumerate(out.get("rows", []) or [], 1):
        if not isinstance(r, dict) or not str(r.get("name", "")).strip():
            continue
        row: RawRow = {k: str(r.get(k, "") or "").strip() for k in CANONICAL if r.get(k) not in (None, "")}  # type: ignore[misc]
        row["_row"] = i
        rows.append(row)
    return rows
