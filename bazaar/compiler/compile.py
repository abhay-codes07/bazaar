"""Compile a raw source into a :class:`Merchant` catalog plus a review queue.

Hard rules (enforced here, not by prompt):

1. price / stock / GST come only from the source cell parsers — never from the model.
2. any field below ``review_threshold`` confidence lands in the review queue.
3. instruction-like text is stripped and the product is flagged.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from bazaar.compiler.enrich import enrich_with_llm, normalize_with_llm
from bazaar.compiler.ingest import IMAGE_SUFFIXES, RawRow, read_csv, read_image, read_shopify_json
from bazaar.compiler.normalize import coerce_unit, parse_gst, parse_price, parse_stock, parse_unit
from bazaar.compiler.sanitize import sanitize_text
from bazaar.llm import LLM, get_llm
from bazaar.schemas.models import FieldConfidence, Merchant, Product

REVIEW_THRESHOLD = 0.8


class ReviewItem(BaseModel):
    sku: str
    field: str
    source_value: str
    proposed_value: str
    confidence: float
    reason: str


class CompiledCatalog(BaseModel):
    merchant: Merchant
    review_queue: list[ReviewItem] = Field(default_factory=list)
    stripped_injections: int = 0
    rows_in: int = 0

    @property
    def review_rate(self) -> float:
        return len({r.sku for r in self.review_queue}) / max(1, len(self.merchant.products))


def _row_to_product(row: RawRow, idx: int, merchant_id: str, vertical: str, llm: LLM) -> tuple[Product, list[ReviewItem], bool]:
    src_name = row.get("name", "")
    raw_desc = row.get("description", "")
    desc, modified = sanitize_text(raw_desc)
    clean_name, name_mod = sanitize_text(src_name)
    modified = modified or name_mod

    norm = normalize_with_llm(llm, clean_name or src_name, desc, vertical)
    price_paise, p_conf = parse_price(row.get("price", ""))
    unit, pack, u_conf = parse_unit(row.get("unit", ""), norm["name"])
    hinted = coerce_unit(str(norm.get("unit_hint", "")))
    if u_conf < 0.8 and hinted is not None:
        # blank/ambiguous cell: prefer the dictionary/model hint over keyword guess
        try:
            pack_hint = float(norm.get("pack_hint") or 1.0)
        except (TypeError, ValueError):
            pack_hint = 1.0
        unit, pack, u_conf = hinted, pack_hint if pack_hint > 0 else 1.0, max(u_conf, 0.85)
    stock, s_conf = parse_stock(row.get("stock", ""))
    gst_bp, g_conf = parse_gst(row.get("gst", ""))
    enrich = enrich_with_llm(llm, norm["name"], norm["category"], desc)

    sku = row.get("sku") or f"{merchant_id[:5]}-{idx:02d}"
    conf = FieldConfidence(
        name=float(norm.get("confidence", 0.5)),
        price=p_conf,
        unit=u_conf,
        category=float(norm.get("confidence", 0.5)),
        gst=g_conf,
        stock=s_conf,
    )
    flags = ["instruction_like_text_stripped"] if modified else []
    product = Product(
        sku=sku,
        name=norm["name"],
        source_name=src_name,
        description=desc,
        category=norm["category"],
        unit=unit,
        pack_size=pack,
        price_paise=price_paise,
        stock=stock,
        synonyms=list(norm.get("synonyms", [])),
        use_case_tags=list(enrich.get("use_case_tags", [])),
        buyer_highlights=list(enrich.get("buyer_highlights", [])),
        gst_rate_bp=gst_bp,
        confidence=conf,
        flags=flags,
    )
    reviews: list[ReviewItem] = []
    checks = [
        ("name", src_name, product.name, conf.name, "could not confidently map the product name"),
        ("price", row.get("price", ""), str(product.price_paise / 100), conf.price, "price cell was unusual or empty"),
        ("unit", row.get("unit", ""), f"{product.pack_size:g} {product.unit.value}", conf.unit, "unit inferred from name"),
        ("stock", row.get("stock", ""), str(product.stock), conf.stock, "stock cell not numeric"),
        ("gst", row.get("gst", ""), f"{product.gst_rate_bp / 100:g}%", conf.gst, "GST rate missing or non-standard"),
    ]
    for field, src, proposed, c, reason in checks:
        if c < REVIEW_THRESHOLD:
            reviews.append(ReviewItem(sku=sku, field=field, source_value=src, proposed_value=proposed, confidence=c, reason=reason))
    if modified:
        reviews.append(ReviewItem(sku=sku, field="description", source_value=raw_desc[:120], proposed_value=desc[:120], confidence=0.0, reason="instruction-like text removed"))
    return product, reviews, modified


def compile_rows(rows: list[RawRow], merchant: Merchant, llm: LLM | None = None, workers: int = 8) -> CompiledCatalog:
    llm = llm or get_llm()
    products: list[Product] = []
    queue: list[ReviewItem] = []
    stripped = 0

    def one(i_row):
        i, row = i_row
        return _row_to_product(row, i, merchant.merchant_id, merchant.vertical.value, llm)

    if llm.name == "fake" or workers <= 1:
        outs = [one(x) for x in enumerate(rows)]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            outs = list(ex.map(one, enumerate(rows)))  # order preserved
    for p, reviews, modified in outs:
        products.append(p)
        queue.extend(reviews)
        stripped += int(modified)
    m = merchant.model_copy(update={"products": products})
    return CompiledCatalog(merchant=m, review_queue=queue, stripped_injections=stripped, rows_in=len(rows))


def compile_merchant(source: Path, merchant: Merchant, llm: LLM | None = None, workers: int = 8) -> CompiledCatalog:
    """``merchant`` carries identity/serviceability/policy; products are compiled from ``source``."""
    if source.suffix.lower() == ".csv":
        rows = read_csv(source)
    elif source.suffix.lower() == ".json":
        rows = read_shopify_json(source)
    elif source.suffix.lower() in IMAGE_SUFFIXES:
        rows = read_image(source, llm or get_llm())
    else:
        raise ValueError(f"unsupported source type: {source.suffix}")
    return compile_rows(rows, merchant, llm, workers)
