"""The task model's pin and its response cache — the two halves of §5's LLM row.

**Pinned by canary, not by version.** The endpoints in use serve open-weight
models under undated aliases, so there is no version string to require: what is
recorded instead is the served model id, the endpoint, and the decoding
parameters, alongside a committed request/response canary that detects a
provider-side swap after the fact (``decisions.md`` § Model pinning). This module
owns the record; ``eval/pins.json`` commits it and ``just pins`` checks it.

**Cached in the search, uncached on the measurement path.** Three rollouts per
condition at one pinned seed are the replication, and a cache would collapse
them into one sample and three copies of it, so the cache is off by default and
:func:`configure_llm_cache` turns it on for a search
(``decisions.md`` § Replication and the LLM cache).
"""

from typing import Any

import litellm
import structlog
from litellm.caching.caching import Cache

from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

logger = structlog.get_logger(__name__)

_DIRECT_ENDPOINT = "provider-default"
"""Recorded in place of the endpoint when no ``llm_api_base`` is configured."""


def _model_pin(model_id: str, settings: AssistantSettings) -> dict[str, Any]:
    """One model's pin record: served id, endpoint, who serves it, and decoding.

    The shape is shared because the *claim* is shared — every model this package
    builds is a live dependency under an undated alias, and what identifies it is
    the same four things each time. Four copies of this dict would let one of
    them quietly stop recording the endpoint.

    **``served_by`` is the fourth, and an endpoint is not a server** (T134).
    OpenRouter routes one model id across many providers that differ in
    quantization — deepseek-v4-flash-0731 has 29, from fp4 to bf16 — and an
    unpinned run draws a different one call by call, which a canary taken before
    the run cannot see and the endpoint field does not distinguish. ``null``
    means the routing was left to the provider, which is a fact about the run
    worth recording as much as a slug is.

    The ``canary`` slot is deliberately absent: a request/response canary can only
    be captured by talking to the endpoint, which this module never does on its
    own. ``eval/pins.json`` carries the slot and reports it unfilled until a live
    pass writes it.
    """
    provider = (
        (settings.openrouter_provider_kwargs(settings.llm_api_base, model_id) or {})
        .get("extra_body", {})
        .get("provider", {})
        .get("only")
        or []
    )
    return {
        "model_id": model_id,
        "endpoint": settings.llm_api_base or _DIRECT_ENDPOINT,
        # The provider this MODEL resolves to, not the raw setting. The setting
        # is a scoped mapping covering several models, and recording it whole
        # would have every pin claim every server (T147). A resolution that is
        # still not a singleton is recorded as the list it is, so the pin says
        # "unpinned between these" rather than naming one arbitrarily.
        "served_by": (provider[0] if len(provider) == 1 else ",".join(provider)) or None,
        "decoding": {
            "temperature": settings.llm_temperature,
            "seed": settings.llm_seed,
        },
    }


def task_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The task model's pin — the agent under test, and the only optimized one."""
    settings = settings or get_settings()
    return _model_pin(settings.root_agent_model, settings)


def sub_agent_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The frozen sub-agent's model pin — same shape, different served id.

    The sub-agent's prompt is frozen but its model is still a live dependency, and
    a swap under it moves every measured result just as surely as one under the
    root model's.
    """
    settings = settings or get_settings()
    return _model_pin(settings.text_to_sql_agent_model, settings)


def sql_builder_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The model that writes the SQL, one layer below the sub-agent's own.

    ``text_to_sql_agent`` decides *whether* to build a query; this model is what
    actually turns the question into DuckDB (``agents/text_to_sql/builder.py``).
    It is the model doing the work family A's templates measure, so a swap under
    it moves every pure-SQL answer — and until it was pinned, nothing said so.
    """
    settings = settings or get_settings()
    return _model_pin(settings.sql_builder_model, settings)


def sql_fixer_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The model that repairs SQL the transpiler or the validator rejected.

    **It moves results by changing what fails, not by changing what is answered.**
    A query the fixer rescues is a case that scores; one it cannot is a
    ``RuntimeError`` out of the pipeline, which reaches the root agent as the
    sub-agent's ``{"status": "error"}`` payload and excludes the rollout as
    ``upstream``. So a weaker fixer does not produce worse answers — it produces
    *fewer* answers and a higher exclusion rate, which §7 reports and the
    aggregates drop. That is the least visible way a model swap can move a number,
    which is the argument for pinning it rather than against.
    """
    settings = settings or get_settings()
    return _model_pin(settings.sql_fixer_model, settings)


def reflection_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The optimizer's reflection model — the **second** model a search depends on.

    Same shape and the same claim: a live dependency under an undated alias,
    identified by its served id, its endpoint and its decoding parameters. What
    makes it worth a pin of its own is that it never appears in a rollout at
    all — it writes the candidates the rollouts are *scored on*, so a swap under
    it changes which prompts the search proposes while leaving every measured
    surface of the agent untouched. A run recording only the task model is
    unrepeatable for that reason (``decisions.md`` § Model pinning).

    **Its endpoint is its own.** These deployments meter per key, so the second
    model may be served somewhere else or under a different key — and a pin
    reading the *shared* endpoint would then name a host the reflection call
    never reached. The record comes from ``reflection_extra()``, which is the
    same function ``harness/reflection.py`` binds onto GEPA's call, so the two
    cannot disagree.

    The binding that makes these numbers true of the request actually sent is
    ``harness/reflection.py``: GEPA builds its reflection call as a bare
    ``litellm.completion(model=…, messages=…)`` and would otherwise carry none
    of them.
    """
    settings = settings or get_settings()
    extra = settings.reflection_extra()
    provider = ((extra.get("extra_body") or {}).get("provider") or {}).get("only") or []
    return {
        "model_id": settings.reflection_model,
        "endpoint": extra.get("api_base") or _DIRECT_ENDPOINT,
        # Read back off the bound kwargs rather than off the settings field, so
        # this cannot claim a provider the reflection call did not pin — the
        # reflection endpoint is its own and may not be OpenRouter at all.
        # Singleton or nothing: `provider[0]` silently named the first of a
        # list, so a model the whitelist did not pin read as pinned (T147).
        "served_by": (provider[0] if len(provider) == 1 else ",".join(provider)) or None,
        "decoding": {
            "temperature": extra["temperature"],
            "seed": extra["seed"],
        },
    }


def configure_llm_cache(
    enabled: bool | None = None,
    settings: AssistantSettings | None = None,
) -> bool:
    """Install or remove litellm's response cache; returns whether it is on.

    Args:
        enabled: ``True`` inside a search, ``False`` on the measurement path.
            Defaults to ``settings.llm_cache_enabled``.
        settings: Settings to read the cache directory and default from.

    The key litellm builds covers the model, the messages, **the tool
    declarations**, ``tool_choice``, the temperature and the seed
    (``ModelParamHelper._get_all_llm_api_params``), which is what
    ``decisions.md`` § Replication and the LLM cache requires: two candidates
    differing only in a docstring must not share an entry, or one is scored on
    the other's responses. The endpoint is *not* in that key, so the cache is
    namespaced by it here — otherwise moving the same model id to a second
    provider would replay the first provider's answers.
    """
    settings = settings or get_settings()
    is_enabled = settings.llm_cache_enabled if enabled is None else enabled
    if is_enabled:
        litellm.cache = Cache(
            type="disk",
            disk_cache_dir=settings.llm_cache_dir,
            namespace=settings.llm_api_base or _DIRECT_ENDPOINT,
        )
        logger.info(
            "LLM response cache on (search path)",
            cache_dir=settings.llm_cache_dir,
            **task_model_pin(settings),
        )
    else:
        litellm.disable_cache()
        logger.info("LLM response cache off (measurement path)", **task_model_pin(settings))
    return is_enabled
