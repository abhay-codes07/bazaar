from bazaar.llm.base import LLM, LLMError
from bazaar.llm.fake import FakeLLM


def get_llm(kind: str | None = None) -> LLM:
    from bazaar.settings import get_settings

    s = get_settings()
    kind = kind or s.bazaar_llm
    if kind == "anthropic":
        from bazaar.llm.anthropic_client import AnthropicLLM

        return _maybe_cache(AnthropicLLM(api_key=s.anthropic_api_key, model=s.bazaar_model), s)
    if kind == "openai":
        from bazaar.llm.openai_client import OpenAILLM

        routing = {"normalize_product": s.bazaar_openai_model_compile, "enrich_product": s.bazaar_openai_model_compile}
        return _maybe_cache(OpenAILLM(api_key=s.openai_api_key, model=s.bazaar_openai_model, base_url=s.openai_base_url, task_models=routing), s)
    return FakeLLM()


def _maybe_cache(llm: LLM, s) -> LLM:
    if not s.bazaar_llm_cache:
        return llm
    from bazaar.llm.cache import CachedLLM

    return CachedLLM(llm, s.data_dir / "runtime" / "llm_cache.sqlite")


__all__ = ["LLM", "LLMError", "FakeLLM", "get_llm"]
