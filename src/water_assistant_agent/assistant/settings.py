"""Single-tenant configuration for the water-management assistant.

Replaces core-agent's per-tenant ``settings/tenants.py`` +
``tenant_capabilities/loader.py`` with one flat settings object loaded from the
environment (prefix ``WATER_ASSISTANT_``) and/or the project ``.env``.
"""

from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MODEL = "openai/qwen3.6-35b-a3b"

_DEFAULT_REFLECTION_MODEL = "openai/qwen3.5-397b-a17b"
"""The optimizer's reflection model — a **second, distinct** model (§5).

Distinct from :data:`_DEFAULT_MODEL` by design and not by accident: the model
proposing candidate text is not the model under test, and a run whose reflection
model is unrecorded is unrepeatable even with the task model fixed
(``decisions.md`` § Model pinning). It is the largest the endpoint serves,
because GEPA's proposal step is the one place in the loop where reasoning
quality turns directly into candidate quality.
"""


class AssistantSettings(BaseSettings):
    """Runtime settings for the conversational assistant service."""

    model_config = SettingsConfigDict(
        env_prefix="WATER_ASSISTANT_",
        env_file=".env",
        extra="ignore",
        # Allow population by field name in addition to the env aliases, so
        # settings_override / tests can pass e.g. jwt_secret_key= directly.
        populate_by_name=True,
    )

    # --- Models (all routed through litellm via ADK's LiteLlm wrapper) ---
    root_agent_model: str = _DEFAULT_MODEL
    text_to_sql_agent_model: str = _DEFAULT_MODEL
    sql_builder_model: str = _DEFAULT_MODEL
    sql_fixer_model: str = _DEFAULT_MODEL
    # The optimizer's reflection model. Not an agent model — nothing in the
    # deployed assistant builds it — but a pinned dependency of every search
    # result all the same (harness/reflection.py).
    reflection_model: str = _DEFAULT_REFLECTION_MODEL

    # Optional OpenAI-compatible endpoint (e.g. KISSKI / Blablador) passthrough.
    llm_api_base: str | None = None
    llm_api_key: str | None = None

    # The reflection model's own endpoint and key, defaulting to the shared pair.
    # It is a *second, distinct* model (§5) and may be served somewhere else or
    # under a different key — these deployments meter per key, and the optimizer's
    # proposals competing with the rollouts for one key's quota is a way for a
    # search to fail that has nothing to do with either model. `None` means "use
    # the shared pair", so nothing changes for a deployment that does not split.
    reflection_api_base: str | None = None
    reflection_api_key: str | None = None

    # Retries litellm performs before an error reaches the caller. A litellm-side
    # parameter: it never reaches the provider and never enters the cache key, so
    # it moves no pin and changes no request. It exists because these endpoints
    # return empty HTTP 500s in bursts (`findings.md`), and a rollout takes up to
    # `MAX_LLM_CALLS` turns — so at a per-call failure rate of p, an unretried
    # rollout completes with probability (1-p)^7, and the exclusions that produces
    # are a fact about the endpoint rather than about the candidate.
    llm_num_retries: int = 5

    # --- Decoding, pinned (agent_architecture.md §5; decisions.md § Model pinning) ---
    # Greedy decoding plus one fixed seed, sent on every call. The seed is **sent
    # but not verifiably honoured** — these endpoints serve open-weight models
    # under no documented seed contract — so what is left over is measured
    # statistically (three repeats per condition), not asserted here.
    llm_temperature: float = 0.0
    llm_seed: int = 42

    # --- LLM response cache (decisions.md § Replication and the LLM cache) ---
    # On inside the search, where it is keyed by the full request; **off** on the
    # measurement path, where the three repeats per condition are the replication
    # and a cache would collapse them to one sample. Off is therefore the default.
    llm_cache_enabled: bool = False
    llm_cache_dir: str = ".cache/llm"

    # --- GR2L green-roof water-balance API (predict_gr2l tool) ---
    # Base URL including the deployment prefix, e.g. "https://host/api-weinbau".
    # Unset ⇒ the GR2L tool returns an error status instead of crashing.
    gr2l_api_base_url: str | None = None
    # Reuses the existing unprefixed ``GR2L_MODEL_API_KEY`` in .env.
    gr2l_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GR2L_MODEL_API_KEY", "WATER_ASSISTANT_GR2L_API_KEY"),
    )

    # --- Data warehouse (DuckDB) ---
    duckdb_path: str = "data/water.duckdb"

    # --- Text-to-SQL pipeline ---
    max_sql_retries: int = 3

    # --- HTTP service (FastAPI + AG-UI + ADK endpoint) ---
    host: str = "0.0.0.0"  # noqa: S104 - bind all interfaces for container/dev use
    port: int = 8080
    app_name: str = "water_assistant"

    # --- Conversation session store ---
    # One SQLAlchemy URL (SQLite file in dev, Postgres in prod) backs ADK sessions
    # *and* the app_users / conversations tables (A1). Required for the assistant
    # service — ``create_bootstrap`` refuses to start when it is unset (D5). Kept
    # nullable here so non-service callers of :func:`get_settings` still construct.
    session_db_url: str | None = None

    # --- Auth ---
    # Required for the assistant service (mandatory JWT auth, D5); enforced at
    # startup by ``create_bootstrap``. Nullable at the settings layer so the
    # text2sql experiments (which never run the service) keep constructing.
    jwt_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_JWT_SECRET", "WATER_ASSISTANT_JWT_SECRET_KEY"),
    )
    # Admin API key (FR2). Absent ⇒ the admin API fails closed with 503 (EC6).
    admin_api_key: str | None = None
    # Lifetime of a minted access token, in days (C8).
    auth_token_ttl_days: int = 7

    def litellm_extra(self) -> dict[str, Any]:
        """Extra kwargs forwarded to litellm / the LiteLlm wrapper.

        Carries the pinned decoding parameters as well as the endpoint, so every
        model this package builds — the root agent, the sub-agent, the builder and
        the fixers — is pinned by construction rather than at four call sites.
        ``caching`` is the search/measurement switch; it only takes effect once
        :func:`..llm.configure_llm_cache` has installed a cache, and it is a
        litellm parameter, so it never reaches the provider and never enters the
        cache key.
        """
        extra: dict[str, Any] = {
            "temperature": self.llm_temperature,
            "seed": self.llm_seed,
            "caching": self.llm_cache_enabled,
            "num_retries": self.llm_num_retries,
        }
        if self.llm_api_base is not None:
            extra["api_base"] = self.llm_api_base
        if self.llm_api_key is not None:
            extra["api_key"] = self.llm_api_key
        return extra

    def reflection_extra(self) -> dict[str, Any]:
        """The same, for the reflection model's own endpoint and key.

        Falls back to the shared pair wherever the reflection-specific one is
        unset, so a deployment serving both models from one endpoint is
        unaffected and the split is opt-in. This is what
        ``harness/reflection.py`` binds onto GEPA's bare ``litellm.completion``,
        and what ``reflection_model_pin`` records — the two read one function, so
        a pin cannot describe an endpoint the call did not use.
        """
        extra = self.litellm_extra()
        base = self.reflection_api_base or self.llm_api_base
        key = self.reflection_api_key or self.llm_api_key
        if base is not None:
            extra["api_base"] = base
        if key is not None:
            extra["api_key"] = key
        return extra


@lru_cache(maxsize=1)
def get_settings() -> AssistantSettings:
    """Return the process-wide settings singleton."""
    return AssistantSettings()
