"""Deterministic, offline stand-in for the model.

It is intentionally *rule-based* rather than random so tests are reproducible and the
"LLM down" fallback path in production has a real implementation behind it. Each task
name has a handler; unknown tasks raise so nothing silently degrades.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from bazaar.llm.base import LLM, LLMError

_HANDLERS: dict[str, Callable[[str, str, dict[str, Any]], dict[str, Any]]] = {}


def handler(task: str):
    def deco(fn):
        _HANDLERS[task] = fn
        return fn

    return deco


def _extract_data_blocks(user: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r'<data label="([^"]+)">\n?(.*?)\n?</data>', user, re.S)}


class FakeLLM(LLM):
    name = "fake"

    def complete_json_image(self, task: str, system: str, user: str, image_b64: str, mime: str, schema: dict[str, Any]) -> dict[str, Any]:
        # the offline engine cannot read pictures; it returns nothing rather than inventing rows
        return {"rows": []}

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        fn = _HANDLERS.get(task)
        if fn is None:
            raise LLMError(f"FakeLLM has no handler for task '{task}'")
        out = fn(system, user, schema)
        # minimal schema check: required keys present
        for key in schema.get("required", []):
            if key not in out:
                raise LLMError(f"FakeLLM handler '{task}' missing required key {key}: {json.dumps(out)[:200]}")
        return out


# ----------------------------------------------------------------------------- handlers
# Handlers for specific tasks are registered by the modules that own them (compiler, seller
# agent, simulator) via ``@handler("task_name")`` so the fake stays close to the real prompt.

__all__ = ["FakeLLM", "handler", "_extract_data_blocks"]
