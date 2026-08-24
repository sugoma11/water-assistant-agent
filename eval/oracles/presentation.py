"""Oracles for family H — presentation intent (``questions.md`` §2 H).

The family whose answer is never a number, and the one place that makes an
oracle look unnecessary until the template has more than one shape.

**T24a materializes a status, not an answer.** A plot's deliverable is the spec
and the answer is null on every variant, so the answer metric skips and what
scores the case is ``argument_checks`` over the call's own arguments. That much
was true when the template drew a single measured pair, and it is why T103 left
family H out of the registry. It stopped being the whole story when T24a became
three variants: the measured pair and the model overlay are ``answered`` with a
null answer, the non-modellable overlay is ``not_available``, and §6.1 gives
``status`` no channel but the oracle's. Something has to decide which, per
instance — and the abstention metric is what then scores the third.

**The decision is §3.6's own trigger, reached by calling it.**
:func:`~..tools.plot.prepare_series` is the function the plot tool runs before it
fetches anything: it resolves the closed vocabulary, resolves the roof through
``roofs.py``, and raises :class:`~..tools.plot.PlotScopeError` for a ``model``
series on the gravel roof or the wetland. So the oracle builds the series the
request denotes and runs them through it. Restating the membership test —
``roof in NON_MODELLABLE_ROOFS`` — would have been shorter and would have made
the oracle a second opinion about the tool's scope rather than a reading of it.

**Nothing is fetched, on any variant**, and that is a property of
``prepare_series`` rather than a rule this module keeps: every argument fault and
the one scope limit are settled before the first query, so a declined plot issues
no DuckDB read, no weather fetch and — the reason it matters — no GR2L request.
T24a(iii) is therefore a zero-cache-entry case on the oracle side, exactly as
T18a is.

**T24b is family H's twin and family A's arithmetic.** It asks T03's question
with the presentation verb removed, so it answers through the same
:func:`~.sql.mean_swc_gap`: the pair of shapes is what the twin compares, and two
oracles meaning slightly different things by "the mean difference" would compare
the shapes on a difference neither template intends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.plot import (
    MEASURED_VOCABULARY,
    PlotScopeError,
    PlotVocabularyError,
    prepare_series,
)
from water_assistant_agent.assistant.tools.roofs import resolve_roof
from water_assistant_agent.assistant.tools.schemas import SeriesSpec

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.pins import stamp
from eval.oracles.sql import mean_swc_gap, month_window, within_as_of

MEASURED_PAIR = "measured_pair"
MODEL_OVERLAY = "model_overlay"
NON_MODELLABLE_OVERLAY = "non_modellable_overlay"

VARIANTS: tuple[str, ...] = (MEASURED_PAIR, MODEL_OVERLAY, NON_MODELLABLE_OVERLAY)
"""T24a's three shapes, ``questions.md`` §2 H's (i), (ii) and (iii).

A **shared** axis rather than a sampled one (§1.7): all three are carried by both
splits, because they are distinct probes rather than values of one quantity, and
a disjoint variant axis would move a whole probe into one split.
"""

PAIR_TABLES: tuple[str, ...] = ("swc", "outflow")
"""The two tables the measured pair draws from — the catalog's, not the vocabulary's.

``tsoil`` would resolve here perfectly well and is simply not drawn; ``radiation``
could not, since it carries six per-roof variables and the request names none.
Which is also why the *variable* is derived rather than listed
(:func:`pair_variable`): one table, one quantity, and the roof supplies the
column.
"""

OVERLAY_MEASURED = ("swc", "soil_moisture")
OVERLAY_MODEL = "swc_pct"
"""The overlay's two series, fixed by the catalog on variants (ii) and (iii).

They share ``AXIS_WATER_CONTENT``, which is what makes the overlay a comparison
rather than two charts in one frame: the modelled and the measured quantity are
the same quantity in the same unit against the same scale (``plot.py``).
"""


def pair_variable(table: str) -> str:
    """The one per-roof quantity *table* carries, out of §3.6's closed vocabulary.

    Derived rather than mapped, so a table that grew a second per-roof variable
    would make the request ambiguous *here* — where it can be refused — instead
    of silently drawing whichever entry happened to come first.
    """
    if table not in PAIR_TABLES:
        raise OracleInputError(
            f"T24a's measured pair draws {' or '.join(PAIR_TABLES)}, not {table!r}."
        )
    names = [
        variable
        for (entry_table, variable), entry in MEASURED_VOCABULARY.items()
        if entry_table == table and entry.per_roof
    ]
    if len(names) != 1:
        raise OracleInputError(
            f"the {table!r} table now carries {len(names)} per-roof variables "
            f"({', '.join(sorted(names)) or 'none'}); the request names one quantity "
            "and there is no longer exactly one for it to mean."
        )
    return names[0]


def _prepared(specs: Sequence[SeriesSpec], start: str, end: str) -> tuple[bool, str | None]:
    """Run *specs* through §3.6's own resolver: ``(declined, reason)``.

    A :class:`PlotVocabularyError` is **not** a decline and is re-raised as a
    testbed defect: the tool types it ``invalid_argument``, which is a fumble a
    candidate could correct, and folding it into the abstention would make the
    false-abstention rate uninterpretable (``decisions.md`` § Typed abstention).
    """
    for spec in specs:
        try:
            prepare_series(spec, start, end)
        except PlotScopeError as exc:
            return True, str(exc)
        except PlotVocabularyError as exc:
            raise OracleInputError(
                f"the plot this case asks for cannot be drawn at all: {exc} That is an "
                "argument fault the tool types invalid_argument, not the scope limit "
                "T24a(iii) is about."
            ) from None
    return False, None


def _series_for(variant: str, params: Mapping[str, Any]) -> list[SeriesSpec]:
    """The series T24a's *variant* denotes, in the form the tool takes them."""
    if variant == MEASURED_PAIR:
        table = str(params.get("table") or OVERLAY_MEASURED[0])
        variable = pair_variable(table)
        roofs = [params.get("roof_a"), params.get("roof_b")]
        if any(roof is None for roof in roofs):
            raise OracleInputError(
                "the measured pair needs roof_a and roof_b; the variant is the pair."
            )
        # Compared as segments, not as strings: "gravel" and "Kiesdach" are one
        # roof, and a pair of one roof drawn twice is a single series.
        resolved = [resolve_roof(str(roof)) for roof in roofs]
        if resolved[0] is not None and resolved[0] is resolved[1]:
            raise OracleInputError(
                f"roof_a={roofs[0]!r} and roof_b={roofs[1]!r} are both "
                f"{resolved[0].name}; the request compares two roofs."
            )
        return [
            SeriesSpec(source="measured", table=table, variable=variable, roof=str(roof))
            for roof in roofs
        ]

    # (ii) and (iii) are one request over two roofs: the measured half is drawn
    # for either, and only the `model` half separates them (§3.6).
    key = "roof" if variant == MODEL_OVERLAY else "alias"
    roof = params.get(key) or params.get("roof") or params.get("alias")
    if roof is None:
        raise OracleInputError(f"the {variant} variant needs a {key}.")
    table, variable = OVERLAY_MEASURED
    return [
        SeriesSpec(source="measured", table=table, variable=variable, roof=str(roof)),
        SeriesSpec(source="model", variable=OVERLAY_MODEL, roof=str(roof)),
    ]


async def t24a_plot_request(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T24a — a plot request, and whether this deployment can draw it.

    The answer is ``null`` on every variant and the answer metric skips; what
    this materializes is ``status``, and the three variants split two ways:

    * **measured pair** and **model overlay** — ``answered``. The chart is the
      deliverable, and ``argument_checks`` over the agent-supplied half of the
      spec is the scored surface (``agent_architecture.md`` §7).
    * **non-modellable overlay** — ``not_available``. A ``model`` series for the
      gravel roof or the wetland is §3.6's one typed abstention, which the agent
      relays as the contract status.

    **The declared variant is checked against what the trigger does, not
    trusted.** The oracle builds the series and runs them through
    :func:`prepare_series`; if a ``model_overlay`` draw names a roof GR2L has no
    store for, or a ``non_modellable_overlay`` draw names one it does, the two
    disagree and the draw is refused. That disagreement is a sampling defect and
    it is silent in both directions — a mislabelled (iii) records an answerable
    case as an abstention, which is the error the false-abstention rate cannot
    see, and a mislabelled (ii) does the reverse.

    **The measured half is what keeps (i) and (iii) apart.** A ``measured``
    series for the gravel roof or the wetland is perfectly valid and is drawn
    like any other roof's, so (i) can pin one of those two roofs into its pair
    and charge a candidate that generalized "gravel ⇒ ``not_available``". Inside
    one family and one tool, the two roofs are then separated by the series'
    **source**, which is the distinction §3.6 actually draws.

    Params:
        variant: one of :data:`VARIANTS`.
        month: ``YYYY-MM``, a completed calendar month ending at or before
            ``as_of`` — the measured half has to exist on every variant, and a
            month sits inside §3.3's 31-day cap so the truncation flag stays
            clear.
        roof_a, roof_b: the pair, on the measured-pair variant.
        table: ``swc`` or ``outflow``, on the measured-pair variant.
        roof: the modelled roof, on the model-overlay variant.
        alias: the gravel or wetland spelling, on the non-modellable variant.
    """
    variant, month = required_params(inputs, "variant", "month")
    if variant not in VARIANTS:
        raise OracleInputError(
            f"unknown T24a variant {variant!r}. Valid: {', '.join(VARIANTS)}."
        )

    start, end = month_window(str(month))
    within_as_of(ctx, end, f"the month {month}")
    specs = _series_for(str(variant), inputs.get("params") or {})
    declined, reason = _prepared(specs, start.isoformat(), end.isoformat())

    expected = variant == NON_MODELLABLE_OVERLAY
    if declined != expected:
        raise OracleInputError(
            f"the {variant} draw resolves the other way: §3.6 "
            f"{'declines' if declined else 'draws'} it, so the case's status would be "
            f"{'not_available' if declined else 'answered'} while the variant says "
            f"{'not_available' if expected else 'answered'}."
        )

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available" if declined else "answered",
        # `duckdb_sha256` and nothing else, and here it is the schema's floor
        # rather than a read: this oracle resolves a vocabulary and a roof table
        # and issues no query, no weather fetch and no GR2L call. The *case* will
        # need capture — a model overlay runs GR2L when a rollout draws it — but
        # the answer was not computed against any of that.
        pins=stamp(),
        detail={
            "variant": str(variant),
            "window": f"{start}..{end}",
            # Canonical roof names, not the spelling the question used: the
            # detail is what a reviewer hand-checks the case against, and
            # "Kiesdach" and "gravel" are the same series.
            "series": [
                {
                    "source": spec.source,
                    "variable": spec.variable,
                    "roof": getattr(resolve_roof(str(spec.roof)), "name", spec.roof),
                }
                for spec in specs
            ],
            "declined_because": reason,
        },
    )


async def t24b_extensive_gap(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T24b — T24a's question with the presentation verb removed, as one number.

    The mean soil-moisture gap between the two extensive roofs over a month, in
    pp. Its whole purpose is to be T24a's twin, and the twin compares two
    *shapes* — a two-roof measured comparison against its scalar — rather than
    two identical parameter draws. So the pair is fixed here where T24a's is
    sampled: a mean difference between the gravel roof's 0.06 %θ and the
    wetland's near-saturated fleece is arithmetic without an information need,
    while plotting the same two is merely a dull plot.

    It answers through :func:`~.sql.mean_swc_gap`, which is T03's core. Two
    oracles for one arithmetic would let the twin pair be compared on a
    difference neither template intends.

    Params:
        month: ``YYYY-MM``, complete at ``as_of``.
    """
    (month,) = required_params(inputs, "month")
    start, end = month_window(str(month))
    within_as_of(ctx, end, f"the month {month}")

    gap, detail = await mean_swc_gap(ctx, start, end)
    return OracleAnswer(answer=gap, unit="pp", pins=stamp(), detail=detail)
