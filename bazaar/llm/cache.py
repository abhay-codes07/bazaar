"""Disk cache for model calls.

Every ``complete_json`` is deterministic in (task, system, user, schema), so identical calls are
served from SQLite instead of the API. Re-running the simulator, the compiler or the demo on the
same corpus costs nothing the second time, and a run interrupted halfway resumes for free.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from bazaar.llm.base import LLM


class CachedLLM(LLM):
    def __init__(self, inner: LLM, path: Path):
        self.inner = inner
        self.name = inner.name
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, task TEXT, model TEXT, response TEXT)")
        self._db.commit()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _key(self, task: str, system: str, user: str, schema: dict[str, Any]) -> str:
        model = getattr(self.inner, "_model", "") + json.dumps(getattr(self.inner, "_task_models", {}), sort_keys=True)
        raw = "\x1f".join([self.inner.name, model, task, system, user, json.dumps(schema, sort_keys=True)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = self._key(task, system, user, schema)
        with self._lock:
            row = self._db.execute("SELECT response FROM calls WHERE key = ?", (key,)).fetchone()
        if row:
            self.hits += 1
            return json.loads(row[0])
        out = self.inner.complete_json(task, system, user, schema)
        self.misses += 1
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO calls (key, task, model, response) VALUES (?, ?, ?, ?)", (key, task, getattr(self.inner, "_task_models", {}).get(task, getattr(self.inner, "_model", "")), json.dumps(out, ensure_ascii=False)))
            self._db.commit()
        return out

    def stats(self) -> dict[str, int]:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        return {"hits": self.hits, "misses": self.misses, "stored": n}
