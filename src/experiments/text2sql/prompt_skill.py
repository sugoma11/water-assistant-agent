"""Shared text-2-SQL prompt helpers for the pluggable ``mlflow.genai`` optimizers.

These were first written for ``textgrad_optimizer.py`` and are lifted here so the
SkillOpt optimizer can reuse the *same* prompt-template seam, per-role endpoint
wiring and split guard (FR8) -- both techniques split the system prompt the same
way, recombine into the same reusable template shape, and resolve project endpoints
the same way, so their registered artifacts stay comparable.

The system prompt is split at :data:`SCHEMA_MARKER` into an **optimizable instruction
block** (everything before the schema section) and the **fixed DB schema** context;
:func:`instruction_block` extracts the former and :func:`recombine` stitches an
optimized instruction block back into the full ``SYSTEM_PROMPT_TEMPLATE`` shape so
the registered artifact stays a complete, reusable template (FR6).
"""

import logging
from typing import Any

from openai import OpenAI

from experiments.text2sql.cost_meter import CostMeter
from experiments.text2sql.harness import ENDPOINTS, read_endpoint_credentials

logger = logging.getLogger(__name__)

# Section marker that separates the optimizable instruction block from the fixed
# schema context in SYSTEM_PROMPT_TEMPLATE.
SCHEMA_MARKER = "Schema:"

# Smallest train/val split an optimizer will run on (EC3): a gradient/edit step needs
# at least one train record and per-epoch keep-best needs at least one val record.
# Refused up front, before any LLM call, so a degenerate split fails fast instead of
# producing an unreliable result.
MIN_SPLIT_SIZE = 1


def make_client(
    endpoint: str,
    meter: CostMeter | None = None,
    role: str | None = None,
) -> OpenAI:
    """Build an OpenAI-compatible client for an ``ENDPOINTS`` entry (R3).

    Mirrors how the notebook wires each engine: resolve the endpoint's
    ``(api_base, api_key)`` env vars and point an ``openai.OpenAI`` client at them, so
    per-role endpoints route correctly without touching global litellm/env config.

    Both the unknown-endpoint case and an unset ``*_API_BASE``/``*_API_KEY`` env var
    raise an actionable ``ValueError`` (rather than a bare ``KeyError``; EC1, TextGrad
    retrospective finding #4) so a misconfigured optimizer/task role fails clearly.

    When ``meter`` is supplied, the client's ``chat.completions.create`` is wrapped to
    record each completion's usage to that meter under the construction-bound ``role``
    (D1-2). TextGrad's task + backward engines reach the endpoint straight through this
    raw ``openai`` client -- litellm never sees them -- so this is the only seam that can
    meter the gradient-step forward and the reflection rewrites. Passing no ``meter``
    (the default, used by evaluation-only callers) leaves the client untouched.
    """
    if endpoint not in ENDPOINTS:
        raise ValueError(
            f"Unknown endpoint {endpoint!r}; expected one of {sorted(ENDPOINTS)}"
        )
    base_url, api_key = read_endpoint_credentials(endpoint)
    client = OpenAI(base_url=base_url, api_key=api_key)
    if meter is not None:
        _install_cost_meter(client, meter, role)
    return client


def _install_cost_meter(client: OpenAI, meter: CostMeter, role: str | None) -> None:
    """Wrap ``client.chat.completions.create`` so every completion's usage is recorded to
    ``meter`` under the construction-bound ``role`` (``task`` / ``optimizer``). The role
    is fixed at construction because TextGrad makes these calls with no litellm ``role``
    context around them, so the meter cannot otherwise attribute them. A response missing
    usage data is recorded as an unmetered call with a loud warning (EC2, FR11) rather
    than silently dropped."""
    completions = client.chat.completions
    original_create = completions.create

    def metered_create(*args: Any, **kwargs: Any) -> Any:
        response = original_create(*args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is None:
            logger.warning(
                "COST METER: TextGrad %r completion returned no usage data; recording "
                "it as unmetered (its spend is not charged against the budget).",
                role,
            )
            meter.record_unmetered(role)
        else:
            meter.record(role, usage.prompt_tokens, usage.completion_tokens)
        return response

    completions.create = metered_create  # type: ignore[method-assign]


def instruction_block(template: str) -> str:
    """The optimizable instruction block: everything before the schema section."""
    return template.split(SCHEMA_MARKER)[0].rstrip()


def recombine(instruction: str) -> str:
    """Recombine an optimized instruction block into the full template shape so the
    registered artifact is a complete, reusable template (FR6). The ``{schema}``
    placeholder is filled by ``render_system_prompt`` at call time."""
    return f"{instruction}\n\n{SCHEMA_MARKER}\n\n{{schema}}\n"
