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


def task_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The task model's pin record: served id, endpoint and decoding parameters.

    The ``canary`` slot is deliberately absent here — a request/response canary
    can only be captured by talking to the endpoint, which this module never does
    on its own. ``eval/pins.json`` carries the slot and reports it as unfilled
    until a live pass writes it.
    """
    settings = settings or get_settings()
    return {
        "model_id": settings.root_agent_model,
        "endpoint": settings.llm_api_base or _DIRECT_ENDPOINT,
        "decoding": {
            "temperature": settings.llm_temperature,
            "seed": settings.llm_seed,
        },
    }


def sub_agent_model_pin(settings: AssistantSettings | None = None) -> dict[str, Any]:
    """The frozen sub-agent's model pin — same shape, different served id.

    The sub-agent's prompt is frozen but its model is still a live dependency, and
    a swap under it moves every measured result just as surely as one under the
    root model's.
    """
    settings = settings or get_settings()
    return {
        "model_id": settings.text_to_sql_agent_model,
        "endpoint": settings.llm_api_base or _DIRECT_ENDPOINT,
        "decoding": {
            "temperature": settings.llm_temperature,
            "seed": settings.llm_seed,
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
