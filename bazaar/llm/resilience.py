"""Circuit-breaking failover for model calls.

The model is only ever *advisory* in Bazaar (propose, normalise, explain), so a model outage
must never take money paths down. :class:`ResilientLLM` wraps the primary backend; when a call
fails it answers from the deterministic offline backend instead — quotes, serviceability and
checkout keep working, negotiation degrades to the best pre-approved rule, and nothing 500s.

After ``threshold`` consecutive failures the circuit opens: the primary is skipped entirely for
``cooldown_s`` seconds (no per-request timeout burn while a provider is down), then the next
call is a half-open trial — one success closes the circuit, one failure re-opens it.

``force_down`` exists for demos and tests: the chaos endpoint flips it to show, live, what the
buyer experiences during a model outage. Every failover is reported through ``on_failover`` so
the gateway can put it on the audit chain — degraded mode is visible, never silent.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from bazaar.llm.base import LLM
from bazaar.llm.fake import FakeLLM


class ResilientLLM(LLM):
    def __init__(self, primary: LLM, fallback: LLM | None = None, threshold: int = 3, cooldown_s: float = 60.0):
        self.inner = primary
        self.fallback = fallback or FakeLLM()
        self.name = primary.name  # compile parallelism etc. keys off the primary backend
        self.threshold = max(1, threshold)
        self.cooldown_s = cooldown_s
        self.force_down = False
        self.on_failover: Callable[[dict[str, Any]], Any] | None = None
        self._lock = threading.Lock()
        self._open_until = 0.0
        self.consecutive_failures = 0
        self.total_failures = 0
        self.total_failovers = 0
        self.last_error = ""
        self.last_failover_at = ""

    # ------------------------------------------------------------------ status
    @property
    def degraded(self) -> bool:
        return self.force_down or time.monotonic() < self._open_until

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "fallback": self.fallback.name,
            "degraded": self.degraded,
            "circuit_open": time.monotonic() < self._open_until,
            "forced_down": self.force_down,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "total_failovers": self.total_failovers,
            "last_error": self.last_error,
            "last_failover_at": self.last_failover_at,
        }

    # ------------------------------------------------------------------ calls
    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.force_down:
            return self._failover(task, system, user, schema, "forced down (chaos)")
        if time.monotonic() < self._open_until:
            return self._failover(task, system, user, schema, "circuit open")
        try:
            out = self.inner.complete_json(task, system, user, schema)
        except Exception as e:  # noqa: BLE001 — any backend failure means degrade, not die
            with self._lock:
                self.consecutive_failures += 1
                self.total_failures += 1
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                if self.consecutive_failures >= self.threshold:
                    self._open_until = time.monotonic() + self.cooldown_s
            return self._failover(task, system, user, schema, self.last_error)
        with self._lock:
            self.consecutive_failures = 0
        return out

    def _failover(self, task: str, system: str, user: str, schema: dict[str, Any], reason: str) -> dict[str, Any]:
        with self._lock:
            self.total_failovers += 1
            self.last_failover_at = datetime.now(timezone.utc).isoformat()
        cb = self.on_failover
        if cb is not None:
            try:
                cb({"task": task, "reason": reason})
            except Exception:  # noqa: BLE001 — an audit hiccup must not block the answer
                pass
        return self.fallback.complete_json(task, system, user, schema)

    def __getattr__(self, item: str) -> Any:
        """Delegate what we don't own (e.g. the cache's ``stats``) to the primary."""
        return getattr(self.inner, item)
