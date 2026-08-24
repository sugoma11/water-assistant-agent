"""Oracles for family B — the pure reference lookups (``questions.md`` §2 B).

Two shapes, and only one of them has a number.

**T06 answers from the constant, not from the card.** ``rules_constants.py`` is
the single definition and the card's ``values:`` block is test-bound to it
(``agent_architecture.md`` §3.2), so reading the constant is reading what the
card must say. Reading the card instead would make the oracle agree with a
drifted card, which is the one disagreement the binding exists to catch.

**T17a has no numeric oracle at all**, and that is the point of the template. The
answer is an abstention: a wind-shutoff condition would sit in the
``irrigation_rule`` ladder beside the moisture, heat and refill conjuncts, and it
is not there. What the oracle asserts is that it is *still* not there — a card
that grew a wind clause would make the case answerable and the expectation wrong,
and nothing else in the suite would notice.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.knowledge.store import load_card
from water_assistant_agent.assistant.rules_constants import ROOF_RULES

from eval.oracles.base import OracleAnswer, OracleInputError
from eval.oracles.pins import stamp

EXTENSIVE_ROOFS = ("irrigated_extensive", "non_irrigated_extensive")
"""The two segments T06's question says "the extensive roofs"."""

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
