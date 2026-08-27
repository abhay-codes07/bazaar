"""Deterministic parsers for messy merchant cells. Each returns ``(value, confidence)``."""

from __future__ import annotations

import re

from bazaar.schemas.models import Unit

_PRICE_RX = re.compile(r"(?i)(?:rs\.?|₹|inr)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?(?:\s*(?:/|per)\s*([a-z]+))?")
_UNIT_RX = re.compile(
    r"(?i)(?:(\d+(?:\.\d+)?)\s*)?(kg|kilo|kilogram|gms?|grams?|g|ltr|litre|liter|l|ml|pcs?|pieces?|piece|nos|dozen|doz|pack|pkt|packet|box|m|mtr|metre)\b"
)
_PACK_OF_RX = re.compile(r"(?i)(?:pack of|pkt/|pack/|set of)\s*(\d+)|(\d+)\s*(?:pcs?|pieces?)\s*(?:pack|pkt|set)")

_UNIT_MAP = {
    "kg": Unit.KG, "kilo": Unit.KG, "kilogram": Unit.KG,
    "g": Unit.GRAM, "gm": Unit.GRAM, "gms": Unit.GRAM, "gram": Unit.GRAM, "grams": Unit.GRAM,
    "l": Unit.LITRE, "ltr": Unit.LITRE, "litre": Unit.LITRE, "liter": Unit.LITRE,
    "ml": Unit.ML,
    "pc": Unit.PIECE, "pcs": Unit.PIECE, "piece": Unit.PIECE, "pieces": Unit.PIECE, "nos": Unit.PIECE,
    "dozen": Unit.DOZEN, "doz": Unit.DOZEN,
    "pack": Unit.PACK, "pkt": Unit.PACK, "packet": Unit.PACK,
    "box": Unit.BOX,
    "m": Unit.METRE, "mtr": Unit.METRE, "metre": Unit.METRE,
}

# name keywords → likely unit when the cell is blank
_NAME_UNIT_HINTS: list[tuple[tuple[str, ...], Unit, float]] = [
    (("rice", "chawal", "dal", "daal", "atta", "sugar", "cheeni", "salt", "namak", "besan", "rajma", "jaggery", "gud", "poha", "paper roll"), Unit.KG, 1.0),
    (("oil", "tel", "ghee", "milk", "doodh", "kettle"), Unit.LITRE, 1.0),
    (("tea", "chai", "coffee", "haldi", "turmeric", "mirch", "chilli", "cashew", "kaju", "almond", "badam", "curd", "dahi"), Unit.GRAM, 200.0),
    (("socks", "towel", "curtain", "jars", "bag", "container", "cup", "tissue", "label", "pillow", "box 10in", "pizza"), Unit.PACK, 1.0),
]


def parse_price(cell: str) -> tuple[int, float]:
    """'Rs 120', '₹120/kg', '120.00', 'Rs. 120/-' → paise. Confidence drops for odd shapes."""
    if not cell or not cell.strip():
        return 0, 0.0
    m = _PRICE_RX.search(cell.replace(",", ""))
    if not m:
        return 0, 0.0
    rupees = float(m.group(1))
    paise = int(round(rupees * 100))
    conf = 1.0 if re.fullmatch(r"(?i)\s*(rs\.?|₹|inr)?\s*[\d.]+\s*(/-)?\s*(/\s*[a-z]+)?\s*", cell.replace(",", "")) else 0.7
    if paise <= 0:
        conf = 0.2
    return paise, conf


def parse_unit(cell: str, name: str = "") -> tuple[Unit, float, float]:
    """→ (unit, pack_size, confidence). Blank cells are inferred from the name at low confidence."""
    cell = (cell or "").strip()
    if cell:
        pm = _PACK_OF_RX.search(cell)
        if pm:
            n = pm.group(1) or pm.group(2)
            return Unit.PACK, float(n), 0.95
        um = _UNIT_RX.search(cell)
        if um:
            qty = float(um.group(1)) if um.group(1) else 1.0
            unit = _UNIT_MAP[um.group(2).lower()]
            if unit == Unit.KG and qty != 1.0:
                return Unit.KG, qty, 0.95
            if unit in (Unit.GRAM, Unit.ML):
                return unit, qty, 0.95 if um.group(1) else 0.6
            return unit, 1.0 if unit != Unit.PACK else qty, 0.9
        return Unit.PIECE, 1.0, 0.4
    nl = name.lower()
    for keys, unit, pack in _NAME_UNIT_HINTS:
        if any(k in nl for k in keys):
            return unit, pack, 0.6
    return Unit.PIECE, 1.0, 0.5


def parse_stock(cell: str) -> tuple[int, float]:
    c = (cell or "").strip().lower()
    if not c:
        return 0, 0.3
    if c.isdigit():
        return int(c), 1.0
    if c in ("yes", "y", "in stock", "available", "haan", "hai"):
        return 10, 0.5
    if c in ("no", "n", "out", "out of stock", "nahi"):
        return 0, 0.9
    m = re.search(r"\d+", c)
    return (int(m.group()), 0.7) if m else (0, 0.3)


def parse_gst(cell: str) -> tuple[int, float]:
    """'5%', '5', '12 %', '0.05' → basis points."""
    c = (cell or "").strip().replace("%", "").strip()
    if not c:
        return 0, 0.4
    try:
        v = float(c)
    except ValueError:
        return 0, 0.3
    if v < 1:  # fraction
        v *= 100
    if v not in (0, 3, 5, 12, 18, 28):
        return int(round(v * 100)), 0.5
    return int(round(v * 100)), 1.0


def strip_parenthetical(name: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
