from bazaar.llm.base import LLM, LLMError
from bazaar.llm.fake import FakeLLM


def get_llm(kind: str | None = None) -> LLM:
    from bazaar.settings import get_settings

    s = get_settings()
    kind = kind or s.bazaar_llm
    if kind == "anthropic":
        from bazaar.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(api_key=s.anthropic_api_key, model=s.bazaar_model)
    if kind == "openai":
        from bazaar.llm.openai_client import OpenAILLM

        return OpenAILLM(api_key=s.openai_api_key, model=s.bazaar_openai_model, base_url=s.openai_base_url)
    return FakeLLM()


__all__ = ["LLM", "LLMError", "FakeLLM", "get_llm"]
