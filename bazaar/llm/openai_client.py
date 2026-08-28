"""OpenAI backend (gpt-4o family) using function calling for structured answers.

Selected with ``BAZAAR_LLM=openai``; needs ``OPENAI_API_KEY`` and optionally ``BAZAAR_OPENAI_MODEL``.
"""

from __future__ import annotations

import json
from typing import Any

from bazaar.llm.base import LLM, LLMError


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = ""):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for BAZAAR_LLM=openai")
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        fn_name = f"answer_{task}"
        tool = {"type": "function", "function": {"name": fn_name, "description": f"Return the structured answer for task '{task}'.", "parameters": schema}}
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": fn_name}},
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(str(e)) from e
        choice = resp.choices[0] if resp.choices else None
        calls = getattr(choice.message, "tool_calls", None) if choice else None
        if not calls:
            raise LLMError("no tool call in OpenAI response")
        try:
            return json.loads(calls[0].function.arguments or "{}")
        except json.JSONDecodeError as e:
            raise LLMError(f"malformed tool arguments: {calls[0].function.arguments[:200]}") from e
