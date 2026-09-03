"""LLM interface.

Bazaar uses the model for *understanding and explaining*, never for acting. Every call is a
named ``task`` with a JSON schema for the answer, so backends are swappable and outputs are
validated before anything downstream sees them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    pass


class LLM(ABC):
    name: str = "llm"

    @abstractmethod
    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object satisfying ``schema`` for the given task.

        ``user`` may embed untrusted text; callers must wrap it with :func:`bazaar.llm.guard.as_data`.
        """


    def complete_json_image(self, task: str, system: str, user: str, image_b64: str, mime: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Structured answer about one image (rate-card photos). Backends without vision raise
        ``LLMError`` so the compiler can queue the source for the merchant instead of guessing."""
        raise LLMError(f"{self.name} backend has no vision support for task '{task}'")


def wrap_untrusted(label: str, text: str) -> str:
    """Render untrusted text as *data* for a prompt. Instruction-like lines are neutralised upstream
    by the compiler; this is the last line of defence."""
    safe = text.replace("<", "&lt;").replace(">", "&gt;")
    return f'<data label="{label}">\n{safe}\n</data>'
