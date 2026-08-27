from bazaar.llm.base import LLM, LLMError
from bazaar.llm.fake import FakeLLM


def get_llm(kind: str | None = None) -> LLM:
    from bazaar.settings import get_settings

    s = get_settings()
    kind = kind or s.bazaar_llm
    if kind == "anthropic":
        from bazaar.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(api_key=s.anthropic_api_key, model=s.bazaar_model)
    return FakeLLM()


__all__ = ["LLM", "LLMError", "FakeLLM", "get_llm"]
