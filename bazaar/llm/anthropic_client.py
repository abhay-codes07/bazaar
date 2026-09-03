from __future__ import annotations

import json
from typing import Any

from bazaar.llm.base import LLM, LLMError


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for BAZAAR_LLM=anthropic")
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete_json(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self._call(task, system, user, schema)

    def complete_json_image(self, task: str, system: str, user: str, image_b64: str, mime: str, schema: dict[str, Any]) -> dict[str, Any]:
        content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}}, {"type": "text", "text": user}]
        return self._call(task, system, content, schema)

    def _call(self, task: str, system: str, user: Any, schema: dict[str, Any]) -> dict[str, Any]:
        tool = {
            "name": f"answer_{task}",
            "description": f"Return the structured answer for task '{task}'.",
            "input_schema": schema,
        }
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError(str(e)) from e
        for block in msg.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        raise LLMError(f"no tool_use block in response: {json.dumps(msg.model_dump())[:500]}")
