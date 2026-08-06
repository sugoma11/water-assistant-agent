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

from typing import Any
from urllib.parse import urlsplit

from openai import OpenAI

from experiments.text2sql.cost_meter import CostMeter
from experiments.text2sql.harness import (
    ENDPOINTS,
    REASONING_EFFORT,
    apply_openrouter_provider,
    read_endpoint_credentials,
)

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
    install_openrouter_provider(client)
    if meter is not None:
        _install_cost_meter(client, meter, role)
    return client


def install_openrouter_provider(client: OpenAI) -> OpenAI:
    """Pin the OpenRouter provider on every completion ``client`` makes (R3, NFR2).

    The raw-openai seam's half of the pin: litellm calls carry it as a completion kwarg
    (``harness.openrouter_provider_kwargs``), but TextGrad's task/backward engines and
    SkillOpt's optimizer reach the endpoint through an ``openai.OpenAI`` client whose
    per-call kwargs are fixed by the library, so the pin has to be installed on the
    client itself.

    Gated on the client's own ``base_url`` rather than on an endpoint name, because this
    also wraps clients built inside SkillOpt (see ``skillopt_optimizer``), whose role is
    not ours to know. A kisski/blablador client is returned untouched, so a
    mixed-endpoint run never sends the field to a non-OpenRouter server.

    Both API surfaces are wrapped: SkillOpt picks between ``chat.completions`` and
    ``responses`` per deployment name (``_needs_responses_api``), and a pin that silently
    stopped applying because a role took the other branch would be worse than no pin.
    """
    host = urlsplit(str(client.base_url)).hostname or ""
    if host != "openrouter.ai" and not host.endswith(".openrouter.ai"):
        return client

    def pin(create: Any) -> Any:
        def pinned_create(*args: Any, **kwargs: Any) -> Any:
            return create(*args, **apply_openrouter_provider(kwargs, "openrouter"))

        return pinned_create

    completions = client.chat.completions
    completions.create = pin(completions.create)  # type: ignore[method-assign]
    responses = client.responses
    responses.create = pin(responses.create)  # type: ignore[method-assign]
    return client


def _install_cost_meter(client: OpenAI, meter: CostMeter, role: str | None) -> None:
    """Wrap ``client.chat.completions.create`` so every completion's usage is recorded to
    ``meter`` under the construction-bound ``role`` (``task`` / ``optimizer``). The role
    is fixed at construction because TextGrad makes these calls with no litellm ``role``
    context around them, so the meter cannot otherwise attribute them. A response missing
    usage data is recorded as an unmetered call with a loud warning (EC2, FR11) rather
    than silently dropped — the missing-usage contract lives in
    :meth:`CostMeter.record_completion` (R-001)."""
    completions = client.chat.completions
    original_create = completions.create

    def metered_create(*args: Any, **kwargs: Any) -> Any:
        # This wrapped create is the only seam that reaches TextGrad's endpoint call:
        # its ``_generate_from_single_prompt`` has a fixed signature and forwards no extra
        # kwargs, so sampling params handed through the engine's ``gen_kwargs`` never
        # arrive as request params here. Inject the frozen thinking budget (REASONING_EFFORT)
        # so TextGrad's task forward and backward/reflection engines run at the same effort
        # as the litellm task/judge calls. ``setdefault`` keeps an explicit call-site value.
        kwargs.setdefault("reasoning_effort", REASONING_EFFORT)
        response = original_create(*args, **kwargs)
        meter.record_completion(role, getattr(response, "usage", None))
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
