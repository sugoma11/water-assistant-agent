"""ADK tool: read one reference card out of the packaged store.

The agent-facing wrapper over :mod:`..knowledge.store`. There is no retrieval
here in the usual sense — no query, no ranking, no index, no network. The model
names a topic out of a closed vocabulary, a Python function reads that card off
disk, and the card comes back (``agent_architecture.md`` §3.2,
``decisions.md`` § Retrieval).

**The vocabulary lives in the signature, and that is the whole point.** ADK
renders ``topic``'s ``Literal[...]`` into the function declaration as a schema
``enum``, so the list of valid topics reaches the model through the *type*
rather than through the prose. Docstrings are candidate-owned
(:func:`..toolset.build_toolset` rewrites ``__doc__``), so a candidate that
rewrote the topic list out of the guidance would otherwise disable the reference
route while still looking optimizable. It can reword every sentence below; it
cannot delete the eleven names. The literal is written out here rather than
built from :data:`~..knowledge.store.CARD_TOPICS` on purpose — that tuple is the
twin a test holds this signature to, and generating one from the other would
leave the comparison asserting a tautology.

**The factory takes no context, and the absence is the guarantee.** Every other
tool's factory closes over a :class:`~..context.ScenarioContext` because it
reads a clock, an executor or a weather client. This one reads packaged files
and nothing else — §5 lists it as *pure, exact, fully offline*, which is what
lets a lookup case replay with zero cache entries and zero live calls. A ``ctx``
parameter it never touched would be an invitation to reach for ``ctx.db`` later;
having none makes the promise structural. It is still a factory, because
:func:`..toolset.build_toolset` applies a candidate's docstring to a freshly
produced callable and must never mutate a module-level singleton.

**Two outcomes and no more.** A known topic returns its card; an unknown
argument returns ``invalid_argument`` echoing what would have been valid. There
is no ``not_available`` — the store either has a topic or does not — and no
``upstream``, because a store baked into the image either loaded or the image is
broken. That last case is caught in :func:`make_lookup_reference_tool`, at build
time, so it can never arrive mid-rollout as a traceback.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

import structlog
from google.adk.tools.tool_context import ToolContext

from water_assistant_agent.assistant.knowledge.store import (
    CARD_TOPICS,
    Card,
    CardStoreError,
    load_card,
    load_cards,
)
from water_assistant_agent.assistant.tools.roofs import ROOFS, resolve_roof
from water_assistant_agent.assistant.tools.schemas import (
    ErrorResult,
    ReferenceCardResult,
)

logger = structlog.get_logger(__name__)

LookupReferenceTool = Callable[..., Awaitable[dict[str, Any]]]
"""What :func:`make_lookup_reference_tool` returns: the ADK-facing card tool."""


def _is_roof_keyed(values: Mapping[str, Any]) -> bool:
    """True when every key of *values* is a canonical roof segment.

    The test for whether ``roof`` has anything to narrow. Four cards are keyed
    this way — the thresholds, the doses, the substrate properties and the
    reference bands — and the rest are keyed by quantity (``heat_threshold_c``)
    or by table (``data_freshness``'s five record ends). Asking whether *all*
    keys are roofs rather than *any* keeps a card that mixed the two from being
    silently half-filtered.
    """
    return bool(values) and all(key in ROOFS for key in values)


def _scoped_values(
    values: Mapping[str, Any], roof: str | None
) -> tuple[dict[str, Any], bool]:
    """*values* narrowed to *roof*, and whether the narrowing happened.

    Narrowing is a courtesy to a single-roof question, not a scope decision, so
    it applies only where there is an entry to narrow *to*. A roof the card
    excludes leaves the block whole — which is the case T17b turns on: asking
    for the wetland's soil-moisture threshold must return the extensive roofs'
    rule and the wetland's exclusion **side by side**, so the agent reads the
    exclusion beside a confidently worded rule that does not apply
    (``agent_architecture.md`` §3.2). Narrowing to nothing would delete half of
    the catalog's strongest hallucination probe.
    """
    if roof is None or not _is_roof_keyed(values) or roof not in values:
        return dict(values), False
    return {roof: values[roof]}, True


def _card_result(card: Card, roof: str | None) -> dict[str, Any]:
    """The card as the agent receives it: whole, under its own field names."""
    values, scoped = _scoped_values(card.values, roof)
    return ReferenceCardResult(
        topic=card.id,
        title=card.title,
        provenance=card.provenance,
        text=card.text,
        values=values,
        # Never narrowed. `applies_to` is the card's scope statement and
        # `not_applicable` is the reason it stops there; both are what a reader
        # needs *most* when the roof asked about is the one outside.
        applies_to=list(card.applies_to),
        not_applicable=dict(card.not_applicable),
        roof=roof,
        values_scoped_to_roof=scoped,
    ).model_dump()


def make_lookup_reference_tool() -> LookupReferenceTool:
    """Build ``lookup_reference``, after checking the store can answer every topic.

    The check is the runtime counterpart of the ``enum == card keys`` test: the
    signature promises the model eleven topics, and a topic with no card behind
    it is a route into a :class:`~..knowledge.store.CardStoreError` in the middle
    of a rollout. Raising here instead means a broken store fails where
    ``store.py`` says it should — at startup, never in a trajectory — and the
    tool body can read a validated topic without a defensive branch that would
    need an error class §3.2 does not have.

    Takes no context: see this module's docstring. The returned callable is
    fresh per call so ``build_toolset`` can give it a candidate's ``__doc__``
    without touching anyone else's.
    """
    cards = load_cards()
    missing = [topic for topic in CARD_TOPICS if topic not in cards]
    if missing:
        raise CardStoreError(
            f"The card store cannot answer {', '.join(missing)}, which "
            f"lookup_reference's topic enum offers the model. Available: "
            f"{', '.join(sorted(cards)) or 'none — the store is empty'}."
        )

    async def lookup_reference(
        topic: Literal[
            "irrigation_rule",
            "irrigation_threshold",
            "substrate_hydraulics",
            "irrigation_dose",
            "heatwave_definition",
            "retention_target",
            "roof_reference_ranges",
            "data_freshness",
            "roof_directory",
            "sensor_reference",
            "et0_method",
        ],
        roof: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Look up what the site's own documentation states about a topic.

        This text is the declaration of ``lookup_reference``, one of the tools the
        assistant may call.

        It reads one of the site's reference cards, named by ``topic`` — a string
        out of the enumerated card names in this declaration's signature. ``roof``
        (str) is optional and scopes the answer to one roof segment. Returns a
        dict with a ``status`` and the card's ``title``, ``text``, ``values``,
        ``applies_to`` and ``not_applicable``.
        """
        if topic not in CARD_TOPICS:
            return ErrorResult(
                error_type="invalid_argument",
                error_details=(
                    f"Unknown topic {topic!r}. Valid topics: {', '.join(CARD_TOPICS)}."
                ),
            ).model_dump()

        # An unresolvable `roof` is a fault in the call, not a scope answer, and
        # is the one thing that must not pass silently: ignoring it would serve
        # every segment's numbers to a question that named one, with nothing in
        # the result to say the filter never ran. `resolve_roof` already accepts
        # the German names, the site ids and the column names, so what reaches
        # here is a spelling nothing on this building answers to.
        canonical: str | None = None
        if roof is not None:
            segment = resolve_roof(roof)
            if segment is None:
                return ErrorResult(
                    error_type="invalid_argument",
                    error_details=(
                        f"Unknown roof {roof!r}. The roof segments are: "
                        f"{', '.join(ROOFS)}. Omit roof to read the card for every "
                        "segment it covers."
                    ),
                ).model_dump()
            canonical = segment.name

        card = load_card(topic)
        logger.info("Reference card read", topic=topic, roof=canonical)
        return _card_result(card, canonical)

    # ADK reads `__name__`; the qualname is reset so a `<locals>`-qualified name
    # never surfaces in logs or reprs (as in ``irrigation.make_irrigation_tool``).
    lookup_reference.__qualname__ = "lookup_reference"
    return lookup_reference
