"""Held-out compiler evaluation — catalogs the corpus generator did NOT write.

The synthetic corpus is a closed loop (one generator writes both the messy CSV and the truth
labels), so its accuracy is the parsers' ceiling. The files in ``data/heldout/`` are
hand-written in three real-world shapes — a kirana rate card, a Shopify export, an
electronics price list — with hand-labelled truth. Whatever this scores is the honest number.

A truth field of ``null`` means the source genuinely doesn't state it (e.g. GST on a Shopify
export); the right behaviour there is a review-queue entry, not a guess, so those cells are
scored on *was it queued or defaulted honestly*, tracked as ``review_rate`` instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bazaar.compiler.compile import compile_rows
from bazaar.compiler.evaluate import _norm
from bazaar.compiler.ingest import read_csv
from bazaar.llm.base import LLM

FIELDS = ("name", "price", "unit", "pack_size", "stock", "gst")


def run_heldout(llm: LLM, heldout_dir: Path, merchants_by_vertical: dict[str, Any], workers: int = 8) -> dict[str, Any]:
    hits: dict[str, int] = dict.fromkeys(FIELDS, 0)
    totals: dict[str, int] = dict.fromkeys(FIELDS, 0)
    catalogs = []
    reviewed = 0
    rows_total = 0
    for csv_path in sorted(heldout_dir.glob("*.csv")):
        truth = json.loads(csv_path.with_suffix("").with_suffix(".truth.json").read_text(encoding="utf-8"))
        base = merchants_by_vertical.get(truth["vertical"]) or next(iter(merchants_by_vertical.values()), None)
        if base is None:
            continue  # no merchant loaded to use as a template (e.g. a trimmed corpus)
        template = base.model_copy(update={"products": []})
        compiled = compile_rows(read_csv(csv_path), template, llm, workers=workers)
        rows = truth["rows"]
        rows_total += len(rows)
        reviewed += len({r.sku for r in compiled.review_queue})
        per: dict[str, int] = dict.fromkeys(FIELDS, 0)
        for i, p in enumerate(compiled.merchant.products):
            if i >= len(rows):
                break
            t = rows[i]
            checks = {
                "name": _norm(p.name) == _norm(t["name"]),
                "price": p.price_paise == t["price_paise"],
                "unit": p.unit.value == t["unit"],
                "pack_size": abs(p.pack_size - t["pack_size"]) < 1e-6,
                "stock": p.stock == t["stock"],
                "gst": (p.gst_rate_bp == t["gst_rate_bp"]) if t.get("gst_rate_bp") is not None else None,
            }
            for f, ok in checks.items():
                if ok is None:
                    continue
                totals[f] += 1
                hits[f] += int(ok)
                per[f] += int(ok)
        catalogs.append({"catalog": csv_path.stem, "vertical": truth["vertical"], "rows": len(rows), "compiled": len(compiled.merchant.products), "field_hits": per})
    return {
        "catalogs": catalogs,
        "rows": rows_total,
        "accuracy": {f: round(hits[f] / totals[f], 3) if totals[f] else None for f in FIELDS},
        "scored_cells": totals,
        "review_rate": round(reviewed / max(1, rows_total), 3),
    }
