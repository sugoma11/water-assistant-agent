"""Oracle for family I — modelling availability (``questions.md`` §2 I).

One template, three variants, and no number on any of them. T27 asks a modelling
question about a roof neither water-balance tool can model — the gravel roof or
the wetland — and the answer is the typed ``not_available`` the tool returns and
the agent relays as the contract status.

**The ground is ``NON_MODELLABLE_ROOFS``, which is the set both tools consult.**
Variants (i) and (iii) reach it through ``predict_green_roof_water_balance_tool``
(architecture §3.4) and variant (ii) through ``calc_irrigation`` (§3.5), and one
table bounds both — so no roof abstains under one tool and answers under the
other, which is why every variant samples both roofs. Restating the membership
here as a list of two names would have made the oracle a second opinion about
the tools' scope rather than a reading of it.

**What makes this family hard is that it is family-dependent.** The gravel roof
is the most-sampled roof in family A, where every question about it is answered
from the record; the same roof in front of a model is a scope limit. So a
candidate cannot learn "gravel ⇒ abstain" or "gravel ⇒ answer" — it has to learn
which route it is on. The ``text_to_sql_agent`` must-not on (i) and (iii) is what
scores the near miss: the database holds no future, so a last measured value is
not a prediction.

**The alias is checked against the table the tools consult, not only against the
alias map**, and the two are not the same set (:func:`declined_roof`). That gap is
this packet's find and it is the reason the pool is stated as spellings rather
than as roofs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    normalize_roof_type,
)
from water_assistant_agent.assistant.tools.roofs import ROOFS, resolve_roof

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.model_chain import forecast_days_for
from eval.oracles.pins import stamp

VARIANT_PREDICTED_MINIMUM = "predicted_minimum"
VARIANT_IRRIGATION = "irrigation"
VARIANT_FORCED_RAIN = "forced_rain"

VARIANTS: tuple[str, ...] = (
    VARIANT_PREDICTED_MINIMUM,
    VARIANT_IRRIGATION,
    VARIANT_FORCED_RAIN,
)
"""T27's three shapes, ``questions.md`` §2 I's (i), (ii) and (iii).

A **shared** axis, like T24a's (§1.7): all three sit in both splits, because they
are distinct routes to one limit rather than values of one quantity. (i) and
(iii) reach the model tool and (ii) reaches the calculator, which is what makes
the limit a property of the roof instead of of one tool.
"""

VARIANT_TOOLS: dict[str, str] = {
    VARIANT_PREDICTED_MINIMUM: "predict_green_roof_water_balance_tool",
    VARIANT_IRRIGATION: "calc_irrigation",
    VARIANT_FORCED_RAIN: "predict_green_roof_water_balance_tool",
}
"""Which tool each variant's gold trajectory names (``questions.md`` §1.4).

Carried so the detail a reviewer hand-checks says which route was asserted; the
trajectory itself is a per-template constant copied in at generation, not
something an oracle owns.
"""


def declined_spellings(roof: str) -> tuple[str, ...]:
    """Every spelling of *roof* that reaches a typed ``not_available``.

    The **intersection** of two vocabularies that ought to be one and are not:
    ``roofs.py``'s alias map, which is what §1.6 means by "natural language the
    semantic layer's alias map covers", and ``NON_MODELLABLE_ROOFS``, which is
    what the two tools actually match an argument against. Both are read here and
    neither is copied, so the pool is whatever the code currently agrees on.

    The intersection is not the whole of either side, and the difference is a
    real trap rather than a tidiness point — see :func:`declined_roof`.
    """
    segment = ROOFS[roof]
    return tuple(
        sorted(
            alias
            for alias in segment.aliases
            if normalize_roof_type(alias) in NON_MODELLABLE_ROOFS
        )
    )


def declined_roof(alias: Any) -> str:
    """The roof *alias* names, asserted to be one both water balances decline.

    Three conditions, and the last one is the packet's find:

    * the alias **resolves** — a question naming a roof nothing in the repository
      answers to is unanswerable for the wrong reason, and would record a
      hallucination probe as an abstention about a roof that does not exist;
    * the resolved roof is **outside both water balances**, which is the
      abstention's actual ground and the thing a modellable roof fails;
    * the alias itself, normalized the way a tool normalizes its argument, is
      **also a key of ``NON_MODELLABLE_ROOFS``**. ``roofs.py`` and the scope
      table are not the same vocabulary: ``sd`` resolves to the wetland in the
      alias map and is absent from the scope table, so a candidate passing the
      question's own spelling gets ``invalid_argument`` — an argument fault it
      could correct — where the gold expectation is a scope limit.
      ``decisions.md`` § Typed abstention is what that would break, and it would
      break it silently, since both outcomes look like "did not answer".

    The tool docstring's "name the roof the user actually asked about" is what
    makes the third condition binding rather than pedantic: the agent is
    instructed to pass the spelling it was given.
    """
    segment = resolve_roof(str(alias))
    if segment is None:
        raise OracleInputError(
            f"no roof answers to {alias!r}, so the question names nothing the "
            "semantic layer's alias map covers (questions.md §1.6)."
        )
    if segment.name not in NON_MODELLABLE_ROOFS:
        declined = ", ".join(sorted(ROOFS[name].name for name in ("gravel", "wetland")))
        raise OracleInputError(
            f"{alias!r} is the {segment.name} roof, which the water balances model; "
            f"T27's pool is the two they decline ({declined})."
        )
    if normalize_roof_type(str(alias)) not in NON_MODELLABLE_ROOFS:
        pool = ", ".join(declined_spellings(segment.name))
        raise OracleInputError(
            f"{alias!r} resolves to the {segment.name} roof in roofs.py but is not a "
            "key of NON_MODELLABLE_ROOFS, so a candidate passing that spelling gets "
            "an invalid_argument rather than the typed not_available this case "
            f"expects. The spellings that reach the scope limit are: {pool}."
        )
    return segment.name


async def t27_non_modellable_roof(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T27 — a modelling question about a roof no water balance covers.

    ``not_available`` with a null answer, on every variant and for either roof.
    There is no numeric oracle and there is nothing to compute: what is asserted
    is the outcome, and the outcome's ground is the scope table both tools
    consult (:func:`declined_roof`).

    **Nothing is fetched, and nothing may be.** Both tools check scope at entry,
    before the window resolves and before any client is reached, so a T27 case
    costs no cache entry — no weather fetch, and in particular no GR2L request.
    That is a property of the tools rather than a rule this oracle keeps, and it
    is what puts T27 in the same zero-capture class as T18a and T24a(iii).

    **The pins are ``duckdb_sha256`` alone**, which here is the schema's floor
    rather than a read: the answer depends on a Python table, not on the record.
    Stamping the GR2L canary would claim a dependency on a service the case
    deliberately never reaches.

    **The horizon is a day count on (i)**, repaired from "the next {h} hours" in
    this packet — the last template carrying the phrasing T09, T13 and T15b were
    repaired out of. It changes no answer here, because the answer is an
    abstention whatever the window is, and that is exactly why it would have
    survived: this is the one family where the defect is invisible to its own
    oracle, and it would have reached a *candidate* as an hours-to-days
    conversion nobody specified (``decisions.md`` § Forward horizons are counted
    in days).

    Params:
        alias: the spelling the question names the roof by. Drawn from
            :func:`declined_spellings` for the gravel roof and the wetland — the
            two the pools of §1.8 deliberately exclude.
        variant: one of :data:`VARIANTS`.
        d: the horizon in whole days, on (i) and (iii).
    """
    alias, variant = required_params(inputs, "alias", "variant")
    variant = str(variant)
    if variant not in VARIANTS:
        raise OracleInputError(
            f"unknown T27 variant {variant!r}. Valid: {', '.join(VARIANTS)}."
        )

    roof = declined_roof(alias)
    # The scope *table*'s sentence, which is GR2L's. `calc_irrigation` declines
    # the same two roofs in its own words — one membership, two wordings, stated
    # as deliberate in `tools/irrigation.py` — so this is recorded as the table's
    # entry rather than as "what the tool will say". The oracle asserts the
    # status; the prose is for a reviewer.
    reason = NON_MODELLABLE_ROOFS[normalize_roof_type(str(alias))]

    # (ii) asks about tomorrow and takes no horizon: `calc_irrigation` has no date
    # arguments at all, so a `d` on that variant would be a parameter naming
    # something the gold call cannot carry.
    horizon = None
    if variant != VARIANT_IRRIGATION:
        (days,) = required_params(inputs, "d")
        horizon = forecast_days_for(days)

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available",
        pins=stamp(),
        detail={
            "roof": roof,
            "asked_as": str(alias),
            "variant": variant,
            "tool": VARIANT_TOOLS[variant],
            "horizon_days": horizon,
            "scope_table_reason": reason,
        },
    )
