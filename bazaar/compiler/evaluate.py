"""Score a compiled catalog against its labelled truth (field-level accuracy)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from bazaar.compiler.compile import CompiledCatalog
from bazaar.schemas.models import Merchant

FIELDS = ("name", "price", "unit", "pack_size", "category", "gst", "stock")


class EvalReport(BaseModel):
    merchants: int = 0
    products: int = 0
    accuracy: dict[str, float] = Field(default_factory=dict)
    review_rate: float = 0.0
    injections_present: int = 0
    injections_stripped: int = 0

    def summary(self) -> str:
        acc = " ".join(f"{k}={v:.3f}" for k, v in self.accuracy.items())
        return f"{self.merchants} merchants / {self.products} products · {acc} · review_rate={self.review_rate:.3f} · injections {self.injections_stripped}/{self.injections_present}"


def evaluate(pairs: list[tuple[CompiledCatalog, Merchant]]) -> EvalReport:
    hits = dict.fromkeys(FIELDS, 0)
    total = 0
    reviewed = 0
    inj_present = inj_stripped = 0
    for compiled, truth in pairs:
        by_row = {i: p for i, p in enumerate(truth.products)}
        for i, p in enumerate(compiled.merchant.products):
            t = by_row.get(i)
            if t is None:
                continue
            total += 1
            hits["name"] += p.name == t.name
            hits["price"] += p.price_paise == t.price_paise
            hits["unit"] += p.unit == t.unit
            hits["pack_size"] += p.pack_size == t.pack_size
            hits["category"] += p.category == t.category
            hits["gst"] += p.gst_rate_bp == t.gst_rate_bp
            hits["stock"] += p.stock == t.stock
            if "poisoned_source" in t.flags:
                inj_present += 1
                inj_stripped += "instruction_like_text_stripped" in p.flags
        reviewed += len({r.sku for r in compiled.review_queue})
    return EvalReport(
        merchants=len(pairs),
        products=total,
        accuracy={k: v / max(1, total) for k, v in hits.items()},
        review_rate=reviewed / max(1, total),
        injections_present=inj_present,
        injections_stripped=inj_stripped,
    )
