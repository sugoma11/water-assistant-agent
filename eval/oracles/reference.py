"""Oracles for family B — the pure reference lookups (``questions.md`` §2 B).

Two shapes, and only one of them has a number.

**T06 answers from the constant, not from the card.** ``rules_constants.py`` is
the single definition and the card's ``values:`` block is test-bound to it
(``agent_architecture.md`` §3.2), so reading the constant is reading what the
card must say. Reading the card instead would make the oracle agree with a
drifted card, which is the one disagreement the binding exists to catch.

**T17a and T17b have no numeric oracle at all**, and that is the point of both.
Each answer is an abstention, and what the oracle asserts is the *ground* for it,
because the ground is a property of the card store and the card store can move.

* T17a: a wind-shutoff condition would sit in the ``irrigation_rule`` ladder
  beside the moisture, heat and refill conjuncts, and it is not there. A card
  that grew a wind clause would make the case answerable and the expectation
  wrong, and nothing else in the suite would notice.
* T17b: the ``irrigation_threshold`` card returns whole, with three substrate
  roofs under ``values:`` and the wetland under ``not_applicable:``. The
  abstention is grounded in that exclusion — and so is the probe, which needs the
  confidently worded rule to still be sitting beside it.

The two are a pair with T06 rather than three separate templates: the same card
family, read for a roof it covers, for a condition it does not test, and for a
roof it excludes.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.knowledge.store import load_card
from water_assistant_agent.assistant.rules_constants import ROOF_RULES
from water_assistant_agent.assistant.tools.roofs import resolve_roof

from eval.oracles.base import OracleAnswer, OracleInputError
from eval.oracles.pins import stamp

EXTENSIVE_ROOFS = ("irrigated_extensive", "non_irrigated_extensive")
"""The two segments T06's question says "the extensive roofs"."""

THRESHOLD_CARD = "irrigation_threshold"
"""The card T06 and T17b both read — one for a roof it covers, one for a roof it excludes."""

T17B_ROOF = "wetland"
"""The roof T17b names, in the catalog's own words rather than as a draw.

The template fixes it: the wetland is the near-miss worth probing because its
exclusion sits under three roofs that *do* have a threshold, so an agent has to
read past a confidently worded rule to find the one line that applies. The gravel
roof is excluded from the same card, and family I is where the two excluded roofs
are sampled as a set (``questions.md`` §1.8) — T17b is not a second family I, so
the roof stays named here and :func:`t17b_scope_near_miss` reads a ``roof``
parameter only if a generator supplies one, rather than silently answering about
the wetland when it was asked about something else.
"""

WIND_WORDS = ("wind", "gust", "boe", "böen")
"""Wind-ish tokens, matched against the rule's **parameters** — never its prose.

The distinction matters and cost a run to learn. The ``irrigation_rule`` card's
text mentions wind on purpose: it names wind, humidity, radiation, season, time
of day and hours-since-watering as quantities the ladder does **not** test, and
says it "carries no cutoff on any of them". A keyword scan over the prose
therefore reports a wind clause exactly where the card is being most explicit
that there is none. What decides T17a is whether wind is an *input to the
decision*, which is a question about parameters.
"""


async def t06_stated_constant(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T06 — the soil-moisture threshold for irrigating the extensive roofs, in %θ.

    The **dry threshold**, not the wilting point: the question asks what level
    irrigation is triggered at, and the ladder's wilting rung waters whatever the
    weather is doing while the dry rung is the gate the question describes
    (``irrigation_threshold`` card, and :mod:`..rules_constants`).

    **The question is single-valued only because both extensive roofs share the
    number**, and this checks that rather than assuming it. They agree at 10.0 %θ
    today; if one moved, "the threshold for the extensive roofs" would name two
    numbers and §1.6's oracle-validity filter would have to discard the template
    rather than the oracle picking one.

    Params: none — the roofs are named in the question, not sampled.
    """
    thresholds = {
        roof: ROOF_RULES[roof].dry_pct for roof in EXTENSIVE_ROOFS if roof in ROOF_RULES
    }
    if len(thresholds) != len(EXTENSIVE_ROOFS):
        missing = set(EXTENSIVE_ROOFS) - set(thresholds)
        raise OracleInputError(
            f"the irrigation rule no longer covers {', '.join(sorted(missing))}, so "
            "T06's question names a roof with no threshold."
        )
    distinct = set(thresholds.values())
    if len(distinct) != 1:
        raise OracleInputError(
            f"the extensive roofs no longer share a dry threshold ({thresholds}); "
            "T06 asks for one number and there are now two."
        )

    return OracleAnswer(
        answer=float(distinct.pop()),
        unit="%θ",
        pins=stamp(),
        detail={"per_roof": thresholds, "level": "dry_pct"},
    )


async def t17a_absent_constant(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T17a — the wind speed at which irrigation shuts off. There is none.

    The oracle's whole content is the absence, asserted against the card that
    would carry the clause if it existed. It returns ``not_available`` with a null
    answer, which is what the abstention metric scores and what makes the answer
    metric skip.

    **Grounded inside a card rather than in the topic vocabulary.** The agent can
    reach ``irrigation_rule`` — the topic exists — and has to read the ladder and
    find no wind conjunct. That is why ``Cards: irrigation_rule`` stays scored:
    the correct behaviour includes fetching the card that fails to answer.

    **The absence is stated, not silent.** The card enumerates wind among the
    quantities the ladder does not test, which makes this a stronger probe than
    the catalog note suggests: a candidate that abstains here can ground the
    abstention in a sentence rather than in a failure to find one, and a
    candidate that invents a cutoff has contradicted the card it just read.

    The check is over the rule's **parameters** — the card's ``values:`` block and
    the per-roof :class:`RoofRules` fields, which are the ladder's whole input
    surface — and never over the prose, for the reason :data:`WIND_WORDS` gives.
    """
    card = load_card("irrigation_rule")
    parameters = {str(name).casefold() for name in (card.values or {})}
    for rule in ROOF_RULES.values():
        parameters.update(field.name.casefold() for field in dataclasses.fields(rule))

    found = sorted(
        name for name in parameters if any(word in name for word in WIND_WORDS)
    )
    if found:
        raise OracleInputError(
            f"the irrigation rule now takes {', '.join(found)} as a parameter; T17a "
            "expects wind to be no condition of it, so the case is answerable now "
            "and its expectation is wrong."
        )

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available",
        pins=stamp(),
        detail={
            "card": "irrigation_rule",
            "rule_parameters": sorted(parameters),
            "searched_for": list(WIND_WORDS),
        },
    )


async def t17b_scope_near_miss(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T17b — the wetland's soil-moisture irrigation threshold. There is none.

    The suite's strongest hallucination probe, and its oracle is the exclusion
    the card already carries. ``lookup_reference`` returns a known topic **whole**
    and never filters ``not_applicable:`` by roof (``agent_architecture.md``
    §3.2), so the agent is handed three substrate roofs' thresholds and the
    wetland's exclusion in one object and has to read the second beside the
    first.

    **What is asserted is the shape the probe needs, not merely the absence.**
    Three conditions, and the case stops meaning what it claims if any of them
    moves:

    * the roof is under ``not_applicable:`` with a stated reason — the absence is
      grounded in the card rather than inferable from the topic vocabulary;
    * the roof is in neither ``values:`` nor ``applies_to:``, so there is no
      threshold to quote and no rung it belongs to;
    * ``values:`` is **non-empty for the other roofs**, which is the half a bare
      absence check would miss. A card that lost its rule would still make the
      wetland unanswerable, and the case would still pass — while having become a
      different and much easier question, since abstaining from an empty card
      takes no reading at all.

    The constants are checked alongside the card for the same reason T06 reads
    them rather than the card: ``values:`` is test-bound to
    :mod:`..rules_constants`, and a wetland that acquired a ``RoofRules`` entry
    would be a threshold that exists with a card that has not caught up.

    Params:
        roof: optional. The template names the wetland (:data:`T17B_ROOF`); a
            generator that samples across the excluded roofs passes one and the
            oracle answers about that roof instead of quietly about this one.
    """
    params = inputs.get("params") or {}
    name = str(params.get("roof") or T17B_ROOF)
    segment = resolve_roof(name)
    if segment is None:
        raise OracleInputError(f"no roof answers to {name!r}.")

    card = load_card(THRESHOLD_CARD)
    reason = card.not_applicable.get(segment.name)
    if not reason:
        raise OracleInputError(
            f"the {THRESHOLD_CARD} card no longer excludes {segment.name} under "
            "not_applicable:, so the abstention has nothing to stand on and the "
            "case is either answerable now or grounded nowhere."
        )
    claims = sorted(
        {*(card.values or {}), *(card.applies_to or ())} & {segment.name, *segment.aliases}
    )
    if claims or segment.name in ROOF_RULES:
        raise OracleInputError(
            f"{segment.name} now has a threshold ({', '.join(claims) or 'in ROOF_RULES'}) "
            f"as well as an exclusion in {THRESHOLD_CARD}; T17b expects one and not both."
        )
    covered = sorted(set(card.values or {}) - {segment.name})
    if not covered:
        raise OracleInputError(
            f"the {THRESHOLD_CARD} card states no threshold for any roof, so T17b is "
            "no longer a near miss — abstaining from an empty card takes no reading."
        )

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available",
        pins=stamp(),
        detail={
            "card": THRESHOLD_CARD,
            "roof": segment.name,
            "excluded_because": reason,
            "thresholds_stated_for": covered,
        },
    )
