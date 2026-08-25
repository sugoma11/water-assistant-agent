"""The reflection model: a second, distinct model, and the seam that pins it (T124).

**Two models, and only one of them is under test.** The task model answers the
cases; the reflection model reads the scorers' rationales and writes the next
candidate. A search records only half of what it depends on if it records only
the first, and the half it leaves out is the one that decides what was
*proposed* — so §5's pin list carries `reflection_model` and
`reflection_model_canary_sha256` beside the task model's own
(``decisions.md`` § Model pinning).

**Pinning it is not the same as configuring it, and this module is the
difference.** ``GepaPromptOptimizer`` takes the reflection model as a *string*,
MLflow forwards it to GEPA as ``reflection_lm``, and GEPA's string path builds

    litellm.completion(model=reflection_lm_name, messages=…)

and nothing else (``gepa/api.py``). No endpoint, no key, no temperature, no
seed. Against an endpoint that is not the provider default that call does not
merely decode differently — it does not arrive. And ``reflection_lm`` cannot be
supplied as a configured callable instead: ``GepaPromptOptimizer`` builds its
arguments as ``self.gepa_kwargs | {…, "reflection_lm": …}``, and the literal on
the right of that union wins, so a caller's value is dropped without a word.

:func:`pinned_reflection_lm` binds the pin at the one seam both facts leave.
For the duration of a search, a ``litellm.completion`` naming the pinned
reflection model is given the endpoint, the key and the decoding parameters the
pin records — **only where the caller supplied none**, and only for that model
id. Everything else passes through untouched, including the task model, which
carries its own parameters explicitly through ADK's ``LiteLlm`` wrapper. The
patch is reverted in a ``finally``, exactly as MLflow's own candidate patch is.

**The canary hashes the message content, not the response envelope.** The
envelope carries an id, a creation timestamp and a token count that move on
every call — the reasoning trace of a thinking model is not stable even at
temperature 0 — while the assistant's ``content`` is what a candidate proposal
is actually built from, and it *is* stable (``findings.md``). So the pinned
value is the sha256 of the canonical JSON of the probe and that content
together: one slot covering both halves, because a silently edited probe would
otherwise redefine what the canary means.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import litellm
import structlog

from water_assistant_agent.assistant.llm import reflection_model_pin
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

logger = structlog.get_logger(__name__)

CANARY_PROMPT = "Reply with exactly the three words: green roof canary"
"""The probe, frozen alongside the pin it produces.

Short, deterministic and instruction-shaped: it asks for exact compliance,
which is the behaviour reflection depends on and the first thing a
provider-side swap disturbs. Its bytes are inside the pinned hash, so editing
it moves the pin rather than quietly redefining it.
"""

BOUND_PARAMETERS: tuple[str, ...] = (
    "api_base",
    "api_key",
    "temperature",
    "seed",
    "num_retries",
)
"""What :func:`pinned_reflection_lm` fills in, and the whole of it.

Each is a field GEPA's bare call omits, and the first four are fields the pin
records. ``num_retries`` is the exception and is deliberately *not* pinned: it
is a litellm-side count that never reaches the provider, so it changes no
request and no result — what it changes is whether a burst of empty HTTP 500s
from the endpoint (``findings.md``) ends a search that was otherwise fine.
Nothing beyond these is added: a parameter this repo invented and the pin did not
carry would be the failure this module exists to prevent, pointing the other way.

The values come from ``AssistantSettings.reflection_extra()``, which is also what
:func:`~water_assistant_agent.assistant.llm.reflection_model_pin` reads — one
function, so a pin cannot describe an endpoint the call did not use.
"""


def reflection_model_uri(settings: AssistantSettings | None = None) -> str:
    """The pinned reflection model as ``<provider>:/<model>`` — GEPA's argument form.

    ``GepaPromptOptimizer`` parses this with MLflow's ``_parse_model_uri`` and
    hands GEPA ``<provider>/<model>``, which is the litellm spelling the settings
    already hold. So the round trip is the identity and the string litellm sees
    is ``settings.reflection_model`` — which is what lets
    :func:`pinned_reflection_lm` match on it.
    """
    settings = settings or get_settings()
    provider, _, model = settings.reflection_model.partition("/")
    if not provider or not model:
        raise ValueError(
            f"The reflection model must be a litellm '<provider>/<model>' id, got "
            f"{settings.reflection_model!r}."
        )
    return f"{provider}:/{model}"


@contextmanager
def pinned_reflection_lm(
    settings: AssistantSettings | None = None,
) -> Iterator[str]:
    """Bind the reflection model's pin to GEPA's call, and yield GEPA's argument.

    Args:
        settings: Where the served id, the endpoint and the decoding parameters
            come from. Defaults to the process settings, which is what
            :func:`~water_assistant_agent.assistant.llm.reflection_model_pin`
            records.

    Yields:
        The ``<provider>:/<model>`` uri to hand ``GepaPromptOptimizer``.

    The patch is narrow in both directions. It matches **one** model id, so no
    other call in the process is touched; and it fills a parameter only where
    the caller left it out, so an explicit argument always wins. Reverted in a
    ``finally``: a search that raised must not leave the process rewriting
    somebody else's completions.
    """
    settings = settings or get_settings()
    model_id = settings.reflection_model
    extra = settings.reflection_extra()
    bound = {name: extra.get(name) for name in BOUND_PARAMETERS}
    original = litellm.completion

    def completion(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("model") == model_id:
            for name in BOUND_PARAMETERS:
                if kwargs.get(name) is None and bound[name] is not None:
                    kwargs[name] = bound[name]
        return original(*args, **kwargs)

    litellm.completion = completion
    logger.info("Reflection model bound", **reflection_model_pin(settings))
    try:
        yield reflection_model_uri(settings)
    finally:
        litellm.completion = original


def probe_canary(settings: AssistantSettings | None = None) -> tuple[str, str]:
    """Send :data:`CANARY_PROMPT` to the live reflection model; return its reply and hash.

    Sent **through the bound seam**, so what is pinned is a fact about the
    request a search actually issues rather than about a request this function
    composed for the occasion.

    Returns:
        ``(content, sha256)`` — the assistant message's text and the pin value.

    Raises:
        RuntimeError: the reply carried no assistant content at all. That is not
            a canary that moved, it is a canary that cannot be taken: GEPA reads
            ``choices[0].message.content`` and would propose ``None``.
    """
    settings = settings or get_settings()
    with pinned_reflection_lm(settings):
        response = litellm.completion(
            model=settings.reflection_model,
            messages=[{"role": "user", "content": CANARY_PROMPT}],
        )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(
            "The reflection model returned no assistant content for the canary "
            "probe. GEPA reads choices[0].message.content and would propose that; "
            "a thinking model whose whole reply lands in reasoning_content cannot "
            "serve as the reflection model."
        )
    return content, canary_sha256(content)


def canary_sha256(content: str) -> str:
    """The pin value: sha256 over the probe **and** the reply, canonically joined.

    Both halves in one hash because ``eval/pins.json`` gives the reflection
    canary one slot. A response-only hash would let an edited
    :data:`CANARY_PROMPT` redefine the canary in place — the pin would still
    match, against a different question.
    """
    payload: Mapping[str, Any] = {"prompt": CANARY_PROMPT, "response": content}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
