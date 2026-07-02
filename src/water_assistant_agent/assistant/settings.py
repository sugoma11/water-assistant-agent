"""Single-tenant configuration for the water-management assistant.

Replaces core-agent's per-tenant ``settings/tenants.py`` +
``tenant_capabilities/loader.py`` with one flat settings object loaded from the
environment (prefix ``WATER_ASSISTANT_``) and/or the project ``.env``.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MODEL = "openai/qwen3.6-35b-a3b"


class AssistantSettings(BaseSettings):
    """Runtime settings for the conversational assistant service."""

    model_config = SettingsConfigDict(
        env_prefix="WATER_ASSISTANT_",
        env_file=".env",
        extra="ignore",
    )

    # --- Models (all routed through litellm via ADK's LiteLlm wrapper) ---
    root_agent_model: str = _DEFAULT_MODEL
    text_to_sql_agent_model: str = _DEFAULT_MODEL
    sql_builder_model: str = _DEFAULT_MODEL
    sql_fixer_model: str = _DEFAULT_MODEL

    # Optional OpenAI-compatible endpoint (e.g. KISSKI / Blablador) passthrough.
    llm_api_base: str | None = None
    llm_api_key: str | None = None

    # --- Data warehouse (DuckDB) ---
    duckdb_path: str = "data/water.duckdb"

    # --- Text-to-SQL pipeline ---
    max_sql_retries: int = 3

    # --- Conversation session store (any SQLAlchemy URL; Postgres for prod) ---
    session_db_url: str = "postgresql://user:password@localhost:5432/mydb"

    # --- Auth (optional in dev) ---
    jwt_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_JWT_SECRET", "WATER_ASSISTANT_JWT_SECRET_KEY"),
    )

    def litellm_extra(self) -> dict[str, str]:
        """Extra kwargs forwarded to litellm / the LiteLlm wrapper."""
        extra: dict[str, str] = {}
        if self.llm_api_base is not None:
            extra["api_base"] = self.llm_api_base
        if self.llm_api_key is not None:
            extra["api_key"] = self.llm_api_key
        return extra


@lru_cache(maxsize=1)
def get_settings() -> AssistantSettings:
    """Return the process-wide settings singleton."""
    return AssistantSettings()
