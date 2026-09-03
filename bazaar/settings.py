from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    bazaar_llm: str = "fake"
    anthropic_api_key: str = ""
    bazaar_model: str = "claude-sonnet-4-5"
    openai_api_key: str = ""
    openai_base_url: str = ""
    bazaar_openai_model: str = "gpt-4o"
    bazaar_openai_model_compile: str = "gpt-4o-mini"  # catalog normalisation/enrichment: high volume, low stakes
    bazaar_llm_cache: bool = True
    bazaar_llm_workers: int = 8
    bazaar_llm_fail_threshold: int = 3  # consecutive failures before the circuit opens
    bazaar_llm_cooldown_s: float = 60.0  # how long the primary is skipped once open

    bazaar_razorpay: str = "fake"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = "bazaar-dev-webhook-secret"

    bazaar_base_url: str = "http://localhost:8000"
    bazaar_db_url: str = f"sqlite:///{(ROOT / 'data' / 'runtime' / 'bazaar.db').as_posix()}"
    bazaar_api_version: str = "2026-08-28"
    bazaar_admin_token: str = "dev-admin-token"
    bazaar_env: str = "dev"  # "prod" refuses to boot with the dev admin token / webhook secret
    bazaar_cors_origins: str = "http://localhost:5173"  # dev console only; the served console is same-origin

    @property
    def data_dir(self) -> Path:
        return ROOT / "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
