"""Fairness ledger: every applied offer is recorded with its rule version, segment predicate and
an inputs hash, so anyone can verify that identical inputs produced identical outputs — the
technical answer to "AI agents doing price discrimination"."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class LedgerEntry(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    merchant_id: str
    rule_id: str
    rule_version: int
    segment_predicate: str
    inputs_hash: str
    discount_paise: int
    session_id: str
    agent_keyid: str = ""


class FairnessLedger:
    def __init__(self) -> None:
        self._rows: list[LedgerEntry] = []
        self._lock = threading.Lock()

    def record(self, e: LedgerEntry) -> None:
        with self._lock:
            self._rows.append(e)

    @property
    def rows(self) -> list[LedgerEntry]:
        return list(self._rows)

    def inconsistencies(self) -> list[dict]:
        """Same (merchant, rule, version, inputs_hash) must always map to the same discount."""
        seen: dict[tuple, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
        for r in self._rows:
            seen[(r.merchant_id, r.rule_id, r.rule_version, r.inputs_hash)][r.discount_paise].append(r.session_id)
        return [
            {"merchant_id": k[0], "rule_id": k[1], "rule_version": k[2], "inputs_hash": k[3], "outcomes": {d: s for d, s in v.items()}}
            for k, v in seen.items()
            if len(v) > 1
        ]

    def summary(self) -> dict:
        by_rule: dict[str, int] = defaultdict(int)
        for r in self._rows:
            by_rule[f"{r.merchant_id}:{r.rule_id}"] += 1
        return {"entries": len(self._rows), "distinct_rules": len(by_rule), "inconsistencies": len(self.inconsistencies())}
