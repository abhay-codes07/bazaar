"""Deterministic buyer-intent parser (EN / Hindi / Hinglish).

Used by the offline backend and as the production fallback when the model is down. It is
deliberately conservative: anything it cannot parse is left empty, never guessed.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from bazaar.schemas.models import Merchant, Product, Unit

_HI_NUM = {
    "ek": 1, "एक": 1, "do": 2, "दो": 2, "teen": 3, "तीन": 3, "char": 4, "chaar": 4, "चार": 4,
    "paanch": 5, "panch": 5, "पांच": 5, "पाँच": 5, "chhe": 6, "छह": 6, "saat": 7, "सात": 7,
    "aath": 8, "आठ": 8, "nau": 9, "नौ": 9, "das": 10, "दस": 10, "bees": 20, "बीस": 20, "pachas": 50, "पचास": 50, "sau": 100, "सौ": 100,
}
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_UNIT_WORDS = {
    "kg": Unit.KG, "kilo": Unit.KG, "kilos": Unit.KG, "किलो": Unit.KG, "kgs": Unit.KG,
    "g": Unit.GRAM, "gm": Unit.GRAM, "gram": Unit.GRAM, "grams": Unit.GRAM, "ग्राम": Unit.GRAM,
    "l": Unit.LITRE, "ltr": Unit.LITRE, "litre": Unit.LITRE, "liter": Unit.LITRE, "लीटर": Unit.LITRE,
    "pc": Unit.PIECE, "pcs": Unit.PIECE, "piece": Unit.PIECE, "pieces": Unit.PIECE, "nos": Unit.PIECE, "पीस": Unit.PIECE,
    "pack": Unit.PACK, "packs": Unit.PACK, "packet": Unit.PACK, "पैकेट": Unit.PACK, "dozen": Unit.DOZEN, "दर्जन": Unit.DOZEN, "box": Unit.BOX, "boxes": Unit.BOX,
}
_UNIT_RX = "|".join(sorted(map(re.escape, _UNIT_WORDS), key=len, reverse=True))
_QTY_RX = re.compile(rf"(?i)(\d+(?:\.\d+)?|{'|'.join(map(re.escape, _HI_NUM))})\s*(?:x\s*)?({_UNIT_RX})?\b")
_PIN_RX = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_BUDGET_RX = re.compile(r"(?i)(?:budget|bajat|बजट|under|below|max|within|upto|up to|₹|rs\.?|inr)\s*(\d{2,7})|(\d{2,7})\s*(?:rupees|rupaye|rs|₹|ke andar|ke under|se kam|tak ka|तक|के अंदर|रुपये)")
_DEADLINE_RX = re.compile(r"(?i)\b(today|aaj|आज|tonight)\b|\b(tomorrow|kal|कल)\b|\b(\d{1,2})\s*(?:hours?|hrs|ghante|घंटे)\b|\b(\d)\s*(?:days?|din|दिन)\b")
_NEGOTIATE_RX = re.compile(r"(?i)\b(discount|offer|deal|cheaper|best price|kam|sasta|sasti|chhoot|छूट|कम|सस्ता|coupon|any offer)\b")
_SERVICE_RX = re.compile(r"(?i)\b(deliver|delivery|pincode|pin code|serve|shipping|ship|pahunch|पहुंच|डिलीवरी)\b")
_STATUS_RX = re.compile(r"(?i)\b(status|track|where is my order|order id|kahan hai)\b")
_CONFIRM_RX = re.compile(r"(?i)^\s*(yes|confirm|ok|okay|book it|place order|haan|ha|हाँ|हां|theek hai|done|go ahead|proceed)\b")
_COD_RX = re.compile(r"(?i)\b(cod|cash on delivery|cash)\b")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINGLISH_HINTS = ("chahiye", "hai", "kya", "tak", "andar", "karo", "kilo", "wala", "wali", "mujhe", "bhejo", "milega", "kitna", "kitne")


class ParsedIntent(BaseModel):
    kind: str = "search"  # search | quote | serviceability | negotiate | confirm | status | other
    product_query: str = ""
    matched_skus: list[str] = Field(default_factory=list)
    quantity: float = 0.0
    unit: Unit | None = None
    pincode: str = ""
    budget_paise: int = 0
    deadline_hours: int = 0
    wants_cod: bool = False
    language: str = "en"  # en | hi | hi-Latn


def detect_language(text: str) -> str:
    if _DEVANAGARI.search(text):
        return "hi"
    tl = f" {text.lower()} "
    if sum(f" {w} " in tl for w in _HINGLISH_HINTS) >= 1:
        return "hi-Latn"
    return "en"


def _num(tok: str) -> float:
    tok = tok.lower().translate(_DEV_DIGITS)
    return float(tok) if re.fullmatch(r"\d+(?:\.\d+)?", tok) else float(_HI_NUM.get(tok, 0))


def match_products(text: str, products: list[Product], limit: int = 5) -> list[Product]:
    """Rank products by token overlap across name/synonyms/category/tags; longest alias first."""
    t = f" {text.lower()} "
    scored: list[tuple[int, int, Product]] = []
    for p in products:
        best = 0
        for alias in [p.name, *p.synonyms, p.category, *p.use_case_tags]:
            a = alias.lower().strip()
            if not a:
                continue
            if f" {a} " in t or (len(a) >= 4 and a in t):
                best = max(best, len(a))
            else:
                # partial: distinctive tokens of the alias present (all tokens, or one token ≥ 4 chars)
                toks = [x for x in re.split(r"[^a-z0-9ऀ-ॿ]+", a) if len(x) >= 3]
                hit = [x for x in toks if f" {x}" in t]
                if toks and (len(hit) == len(toks) or any(len(x) >= 4 for x in hit)):
                    best = max(best, sum(len(x) for x in hit) // (1 if len(hit) == len(toks) else 2))
        if best:
            scored.append((best, p.stock > 0, p))
    scored.sort(key=lambda x: (-x[0], -int(x[1]), x[2].price_paise))
    return [p for _, _, p in scored[:limit]]


def parse_intent(text: str, merchant: Merchant | None = None) -> ParsedIntent:
    text_n = text.translate(_DEV_DIGITS)
    it = ParsedIntent(language=detect_language(text))
    pm = _PIN_RX.search(text_n)
    if pm:
        it.pincode = pm.group(1)
    bm = _BUDGET_RX.search(text_n)
    if bm:
        it.budget_paise = int(bm.group(1) or bm.group(2)) * 100
    dm = _DEADLINE_RX.search(text_n)
    if dm:
        if dm.group(1):
            it.deadline_hours = 12
        elif dm.group(2):
            it.deadline_hours = 24
        elif dm.group(3):
            it.deadline_hours = int(dm.group(3))
        elif dm.group(4):
            it.deadline_hours = int(dm.group(4)) * 24
    it.wants_cod = bool(_COD_RX.search(text_n))

    # quantity: skip numbers that are the pincode or budget
    for qm in _QTY_RX.finditer(text_n):
        tok = qm.group(1)
        if tok == it.pincode or (it.budget_paise and tok == str(it.budget_paise // 100)):
            continue
        if not qm.group(2) and tok.isdigit() and len(tok) >= 4:
            continue
        if not qm.group(2) and not re.fullmatch(r"\d+(?:\.\d+)?", tok):
            continue  # a bare word-numeral ("do", "char") is ambiguous without a unit
        it.quantity = _num(tok)
        if qm.group(2):
            it.unit = _UNIT_WORDS[qm.group(2).lower()]
        break

    stripped = _PIN_RX.sub(" ", text_n)
    stripped = _BUDGET_RX.sub(" ", stripped)
    it.product_query = re.sub(r"\s+", " ", stripped).strip()
    if merchant is not None:
        it.matched_skus = [p.sku for p in match_products(it.product_query, merchant.products)]

    if _CONFIRM_RX.search(text_n):
        it.kind = "confirm"
    elif _STATUS_RX.search(text_n):
        it.kind = "status"
    elif _NEGOTIATE_RX.search(text_n):
        it.kind = "negotiate"
    elif _SERVICE_RX.search(text_n) and not it.quantity:
        it.kind = "serviceability"
    elif it.matched_skus and (it.quantity or it.pincode):
        it.kind = "quote"
    else:
        it.kind = "search"
    return it
