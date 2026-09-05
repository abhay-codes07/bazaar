"""OpenAI backend (gpt-4o family) using function calling for structured answers.

Selected with ``BAZAAR_LLM=openai``; needs ``OPENAI_API_KEY`` and optionally ``BAZAAR_OPENAI_MODEL``.
"""

from __future__ import annotations

import json
from typing import Any

from bazaar.llm.base import LLM, LLMError

# USD per 1M tokens (input, output). Only the models we actually route to; extend as needed.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-oss-120b": (0.0, 0.0),  # Groq free tier
    "qwen/qwen3.8-27b": (0.0, 0.0),
}
USD_TO_INR = 88.0


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = "", task_models: dict[str, str] | None = None):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for BAZAAR_LLM=openai")
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model
        self._task_models = dict(task_models or {})  # e.g. {"normalize_product": "gpt-4o-mini"}
        # token/cost accounting — only real API calls reach here (cache hits never do), so this
        # is a true measurement of what the run spent, not an estimate
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usd = 0.0

    def usage(self) -> dict[str, float]:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens, "usd": round(self.usd, 4), "inr": round(self.usd * USD_TO_INR, 2)}

    def _account(self, model: str, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        pt, ct = int(getattr(u, "prompt_tokens", 0) or 0), int(getattr(u, "completion_tokens", 0) or 0)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        pin, pout = _PRICE_PER_MTOK.get(model, (0.0, 0.0))
        self.usd += pt / 1e6 * pin + ct / 1e6 * pout

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self._call(task, system, user, schema)

    def complete_json_image(self, task: str, system: str, user: str, image_b64: str, mime: str, schema: dict[str, Any]) -> dict[str, Any]:
        content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}", "detail": "high"}}]
        return self._call(task, system, content, schema)

    def _call(self, task: str, system: str, user: Any, schema: dict[str, Any]) -> dict[str, Any]:
        fn_name = f"answer_{task}"
        model = self._task_models.get(task, self._model)
        tool = {"type": "function", "function": {"name": fn_name, "description": f"Return the structured answer for task '{task}'.", "parameters": schema}}
        try:
            resp = self._client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": fn_name}},
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(str(e)) from e
        self._account(model, resp)
        choice = resp.choices[0] if resp.choices else None
        calls = getattr(choice.message, "tool_calls", None) if choice else None
        if not calls:
            raise LLMError("no tool call in OpenAI response")
        try:
            return json.loads(calls[0].function.arguments or "{}")
        except json.JSONDecodeError as e:
            raise LLMError(f"malformed tool arguments: {calls[0].function.arguments[:200]}") from e
