"""Append-only, hash-chained audit log (JSONL) with replay.

Each entry stores ``prev`` (hash of the previous entry) and ``hash`` (SHA-256 of canonical entry
without ``hash``). ``verify_chain`` detects any edit, deletion or reordering.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def _canon(d: dict[str, Any]) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._last = GENESIS
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    self._entries.append(e)
                    self._last = e["hash"]

    # ------------------------------------------------------------------ write
    def record(self, entry: dict[str, Any]) -> str:
        with self._lock:
            e = {"audit_id": "aud_" + secrets.token_hex(6), "seq": len(self._entries), "at": datetime.now(timezone.utc).isoformat(), "prev": self._last, **entry}
            e["hash"] = hashlib.sha256(_canon({k: v for k, v in e.items() if k != "hash"})).hexdigest()
            self._entries.append(e)
            self._last = e["hash"]
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
            return e["audit_id"]

    # ------------------------------------------------------------------ read
    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [e for e in self._entries if e.get("session") == session_id]

    def verify_chain(self) -> tuple[bool, int]:
        """Return (ok, first_bad_seq). Recomputes every hash and link."""
        prev = GENESIS
        for i, e in enumerate(self._entries):
            if e.get("prev") != prev or e.get("seq") != i:
                return False, i
            h = hashlib.sha256(_canon({k: v for k, v in e.items() if k != "hash"})).hexdigest()
            if h != e.get("hash"):
                return False, i
            prev = h
        return True, -1

    def merkle_root(self) -> str:
        """Daily anchoring hook: root over entry hashes (anchor this to an immutable store)."""
        layer = [e["hash"] for e in self._entries] or [GENESIS]
        while len(layer) > 1:
            if len(layer) % 2:
                layer.append(layer[-1])
            layer = [hashlib.sha256((layer[i] + layer[i + 1]).encode()).hexdigest() for i in range(0, len(layer), 2)]
        return layer[0]

    # ------------------------------------------------------------------ replay
    def replay(self, session_id: str) -> list[dict[str, Any]]:
        """Human-readable timeline for a session — what was proposed, what was checked, what moved."""
        out = []
        for e in self.for_session(session_id):
            kind = e.get("kind", "agent_turn")
            checks = e.get("checks", [])
            failed = [c["name"] for c in checks if not c.get("passed")]
            row = {
                "seq": e["seq"],
                "at": e["at"],
                "audit_id": e["audit_id"],
                "kind": kind,
                "action": e.get("proposal", {}).get("tool") or e.get("action", ""),
                "outcome": e.get("outcome", ""),
                "checks_passed": len(checks) - len(failed),
                "checks_failed": failed,
                "money": e.get("money", {}),
                "note": e.get("note", ""),
                "hash": e["hash"][:12],
            }
            out.append(row)
        return out
