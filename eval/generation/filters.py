"""``questions.md`` §1.6's three input-validity predicates, over one draw's data.

A template declares the ``(table, column, window)`` set its answer is computed
over; this module says whether the pinned record actually carries it. Failing any
predicate **resamples the parameters** — it never drops the template, so §1.7's
``templates × m`` still holds (:mod:`eval.generation.instantiate`).

Three properties shape everything below.

**Every read goes through the case's own as-of view.** The queries run on
``ctx.db``, which is :class:`~..assistant.context.AsOfQueryExecutor` over
``connect_asof``'s bounded views, so a check can never pass on rows the tool
under test cannot see. A filter reading the pinned file directly would accept a
window the rollout then answers from half the data.

**Seed-bearing families anchor at ``seed_at = min(window_start, as_of)``, not at
``as_of``.** That is architecture §3.4's own rule for where GR2L takes its
initial condition, and a retrospective window seeds at its own start rather than
at the case's present (:func:`seed_day`). Checking the seed's freshness at
``as_of`` would clear a T19 or T23 draw whose actual seed day is a hole.

**Applicability is read off ``roofs.py``, never off a hand-list.** Bounds come
from :func:`~..assistant.tools.roofs.column_bounds`, which knows that
``radiation``'s are per instrument while the other tables' are per roof; and the
frozenness test's exclusion of ``QGravel`` is derived from that roof having no
substrate at all rather than from its column name (:func:`frozenness_applies`).

**What is deliberately not a predicate here.** Constant-value rejection: a flux
resting at zero is reporting the truth, and rejecting constant days would delete
217 of 289 ``Kies_Efflux`` days and every "no" T04 needs (``findings.md``
§ Rejecting constant-valued days would delete the balance classes). The
oracle-validity half of §1.6 is not here either — it is each oracle's own
:class:`~eval.oracles.base.OracleInputError`, raised where a draw admits more
than one defensible reading, and the draw loop treats it as this module's
rejections are treated.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from water_assistant_agent.assistant.context import AS_OF_TABLES, ScenarioContext
from water_assistant_agent.assistant.tools.roofs import (
    ROOFS,
    RoofSegment,
    column_bounds,
    resolve_roof,
)
from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE, site_day_expr

ROWS_PER_DAY = 48
"""Half-hourly sampling: 00:00 to 23:30 site time, inclusive, on a whole day."""

POINT_MIN_ROWS = 44
"""§1.6's point-query floor — a day is present at ≥44 of its 48 rows.

Four rows of slack, which is what separates a thin day from a served one: the
band's outage edges land at 8, 25, 27 and 36 rows and are all rejected, while the
spring-forward Sunday's 46 (``findings.md`` § Spring-forward artifact) is not.
"""

PERIOD_MIN_COVERAGE = 0.95
"""§1.6's period-aggregate floor, as a share of the window's expected rows."""

MAX_GAP_HOURS = 24.0
"""§1.6's second period condition — no *single* hole may exceed one day.

Coverage alone would pass a 30-day window missing one contiguous week, which is
the shape the two whole-system lysimeter outages actually make.
"""

FROZEN_RUN = 24
"""§1.6's frozenness threshold — 24 identical consecutive samples, i.e. 12 h.

Measured rather than chosen: the longest identical run on any healthy state
sensor in the band is 17 samples (8.5 h, ``QEx1``), and the test flags zero days
on ``QEx1``, ``QEx2``, ``QIn`` and all five ``tsoil`` columns against 53 on
``QWetland`` (``findings.md`` § Validity-predicate specificity).
"""

STATE_TABLES: tuple[str, ...] = ("swc", "tsoil")
"""The two tables whose columns hold a *state*, which is what can freeze.

``outflow`` is a flux and ``wetter``/``radiation`` are neither the roofs' nor a
store's — ``Sumpf2_Efflux`` legitimately holds 0.000 for 3474 consecutive hours,
so a run test over a flux column measures the weather, not the sensor.
"""

Kind = Literal["point", "period"]
"""How a case reads its window, which is what both coverage and bounds branch on.

``point`` is one day answered from its own rows — T04's did-it-run-off, a model
seed. ``period`` is an aggregate over a window, where the value the case actually
reads is a daily figure rather than a sample.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class Requirement:
    """One ``(table, column, window)`` a draw's answer is computed over.

    The unit §1.6's predicates are evaluated per. A template declares as many as
    its answer reads: T03 compares two ``swc`` columns and declares both, T12
    reads an ``outflow`` column over the event window and the station's rain over
    the same days, and a seed-bearing family adds :func:`seed_requirement`'s
    point at ``min(window_start, as_of)``.
    """

    table: str
    column: str
    start: date
    end: date
    kind: Kind

    def __post_init__(self) -> None:
        if self.table not in AS_OF_TABLES:
            valid = ", ".join(AS_OF_TABLES)
            raise ValueError(f"Unknown table {self.table!r}. Valid: {valid}.")
        if self.end < self.start:
            raise ValueError(
                f"{self.describe()} ends {self.end.isoformat()}, before it starts."
            )
        if self.kind == "point" and self.start != self.end:
            raise ValueError(
                f"{self.describe()} is a point query over more than one day."
            )

    @property
    def days(self) -> int:
        """How many site calendar days the window spans, both ends included."""
        return (self.end - self.start).days + 1

    @property
    def expected_rows(self) -> int:
        """The rows a fully covered window would carry, at 48 per day."""
        return ROWS_PER_DAY * self.days

    def describe(self) -> str:
        """``swc.QWetland over 2026-03-12`` — how a rejection names its window."""
        window = (
            self.start.isoformat()
            if self.start == self.end
            else f"{self.start.isoformat()}..{self.end.isoformat()}"
        )
        return f"{self.table}.{self.column} over {window}"


@dataclasses.dataclass(frozen=True, slots=True)
class Rejection:
    """One predicate a draw failed, and what the record showed.

    Carries the requirement rather than only its text so the draw loop can count
    rejections per predicate and per template — which is how the filters' own
    specificity is measured rather than asserted.
    """

    predicate: Literal["coverage", "plausibility", "frozenness"]
    requirement: Requirement
    detail: str

    def __str__(self) -> str:
        return f"{self.predicate}: {self.requirement.describe()} — {self.detail}"


@dataclasses.dataclass(frozen=True, slots=True)
class Measurement:
    """What one window looks like in the record, before any threshold is applied.

    Separated from the verdict on purpose: the thresholds above were chosen from
    sweeps over these same numbers (``findings.md`` § Validity-predicate
    specificity), and a report that can show the measurement can re-run the sweep
    without re-implementing the query.
    """

    requirement: Requirement
    rows: int
    max_gap_hours: float
    level_min: float | None
    level_max: float | None
    longest_run: int

    @property
    def coverage(self) -> float:
        """Share of the window's expected rows that are present."""
        return self.rows / self.requirement.expected_rows


def seed_day(window_start: date, as_of: date) -> date:
    """``min(window_start, as_of)`` — where a seed-bearing family takes its state.

    Architecture §3.4's rule, and the anchor §1.6 names for these families. A
    forward window starts at the case's present and seeds there; a retrospective
    one (T19's ``last {d} days``, T23's ``{d} days ago``) starts earlier and seeds
    at *its own start*, which is a different day from ``as_of`` and a different
    row to check.
    """
    return min(window_start, as_of)


def seed_requirement(roof: str | RoofSegment, window_start: date, as_of: date) -> Requirement:
    """The ``swc`` point query one GR2L or ``calc_irrigation`` seed reads.

    A point at :func:`seed_day`, so the freshness of the initial condition is
    checked on the day the model is actually seeded from. Raises for a roof with
    no ``swc`` column, which no pool sampled by a seed-bearing family contains.
    """
    segment = roof if isinstance(roof, RoofSegment) else resolve_roof(str(roof))
    if segment is None:
        raise ValueError(f"No roof answers to {roof!r}.")
    column = segment.columns.get("swc")
    if column is None:
        raise ValueError(f"The {segment.name} roof has no swc column to seed from.")
    day = seed_day(window_start, as_of)
    return Requirement(table="swc", column=column, start=day, end=day, kind="point")


def roof_requirement(
    roof: str | RoofSegment, table: str, start: date, end: date, *, kind: Kind | None = None
) -> Requirement:
    """A requirement over *roof*'s own column in *table*, resolved through ``roofs.py``.

    *kind* defaults to ``point`` for a single day and ``period`` otherwise, which
    is the reading every template wants: nothing in the catalog aggregates over
    one day or asks a point question of a window.
    """
    segment = roof if isinstance(roof, RoofSegment) else resolve_roof(str(roof))
    if segment is None:
        raise ValueError(f"No roof answers to {roof!r}.")
    column = segment.columns.get(table)
    if column is None:
        raise ValueError(f"The {segment.name} roof has no {table} column.")
    return Requirement(
        table=table,
        column=column,
        start=start,
        end=end,
        kind=kind or ("point" if start == end else "period"),
    )


def frozenness_applies(table: str, column: str) -> bool:
    """Whether the run test means anything for this column.

    Two exclusions, and both are physical rather than empirical thresholds.

    *Fluxes and the station.* Only :data:`STATE_TABLES` hold a state. A flux at
    rest is reporting the truth — ``Sumpf2_Efflux`` holds 0.000 for 3474
    consecutive hours — so the test is unusable there by construction.

    *The gravel roof's ``swc``.* Read off ``roofs.py``: a segment with no
    ``substrate_height_cm`` has no substrate store, so its moisture column is not
    the state of anything and honestly reads ~0 %θ (a band median of 0.06). The
    exclusion is derived from that fact rather than written as ``QGravel``, so a
    roof that gains or loses a store moves with it.
    """
    if table not in STATE_TABLES:
        return False
    if table == "swc":
        for roof in ROOFS.values():
            if roof.columns.get("swc") == column:
                return roof.substrate_height_cm is not None
    return True


def _berlin_midnight_utc(day: date) -> datetime:
    """The naive UTC instant a site calendar day begins at.

    The five tables hold naive UTC and a day is a ``SITE_TIMEZONE`` calendar day
    everywhere (``decisions.md`` § The day boundary), so the window's own edges —
    which is where an outage's leading and trailing holes are measured against —
    have to be converted once, here.
    """
    local = datetime.combine(day, datetime.min.time(), ZoneInfo(SITE_TIMEZONE))
    return local.astimezone(UTC).replace(tzinfo=None)


def _measure_query(requirement: Requirement) -> str:
    """One query for all three predicates over one window.

    Coverage needs the row count and the timestamps at the window's ends,
    plausibility the extremes **at the level the case reads** — a daily mean for
    an aggregate, a half-hourly sample for a point query, which is what
    ``roofs.Bounds`` documents its numbers as applying to — and frozenness the
    longest run of identical consecutive samples. Gathering them together keeps a
    draw to one round trip, which matters when balance rejects five draws in six.
    """
    day = site_day_expr()
    level = (
        "SELECT value AS level FROM w"
        if requirement.kind == "point"
        else "SELECT avg(value) AS level FROM w GROUP BY day"
    )
    return (  # noqa: S608 - table and column from roofs.py/AS_OF_TABLES, dates are `date`
        f'WITH w AS (SELECT timestamp AS ts, {day} AS day, "{requirement.column}" AS value '
        f"FROM {requirement.table} "
        f"WHERE {day} BETWEEN DATE '{requirement.start.isoformat()}' "
        f"AND DATE '{requirement.end.isoformat()}'), "
        "gaps AS (SELECT epoch(ts - lag(ts) OVER (ORDER BY ts)) / 3600 AS gap_h FROM w), "
        f"levels AS ({level}), "
        "runs AS (SELECT value, row_number() OVER (ORDER BY ts) "
        "- row_number() OVER (PARTITION BY value ORDER BY ts) AS island FROM w) "
        "SELECT (SELECT count(*) FROM w), "
        "(SELECT max(gap_h) FROM gaps), "
        "(SELECT min(ts) FROM w), (SELECT max(ts) FROM w), "
        "(SELECT min(level) FROM levels), (SELECT max(level) FROM levels), "
        "(SELECT max(length) FROM (SELECT count(*) AS length FROM runs GROUP BY value, island))"
    )


async def measure(ctx: ScenarioContext, requirement: Requirement) -> Measurement:
    """Run *requirement*'s window through the case's as-of executor.

    The largest hole is measured **against the window's own edges** as well as
    between consecutive rows: an outage that removes the first week of a month
    leaves no lagged pair to see it, and a window with nothing in it leaves no
    rows at all. Both are the whole window's worth of gap, and reporting them as
    zero is the one way this predicate could pass a window that is simply absent.
    """
    result = await asyncio.to_thread(ctx.db.execute_query, _measure_query(requirement))
    rows, internal_gap, first, last, low, high, longest = result.rows[0]

    window_start = _berlin_midnight_utc(requirement.start)
    window_end = _berlin_midnight_utc(requirement.end + timedelta(days=1))
    if not rows:
        gap_hours = (window_end - window_start).total_seconds() / 3600
    else:
        gap_hours = max(
            float(internal_gap or 0.0),
            (first - window_start).total_seconds() / 3600,
            (window_end - last).total_seconds() / 3600,
        )
    return Measurement(
        requirement=requirement,
        rows=int(rows),
        max_gap_hours=gap_hours,
        level_min=None if low is None else float(low),
        level_max=None if high is None else float(high),
        longest_run=int(longest or 0),
    )


def judge(measurement: Measurement) -> tuple[Rejection, ...]:
    """The three predicates applied to one measured window.

    All three are evaluated rather than short-circuited: a report that says a
    window failed coverage *and* bounds is more use than one that says it failed
    the first thing checked, and nothing here is expensive once the query has run.
    """
    requirement = measurement.requirement
    rejections: list[Rejection] = []

    if requirement.kind == "point":
        if measurement.rows < POINT_MIN_ROWS:
            rejections.append(
                Rejection(
                    predicate="coverage",
                    requirement=requirement,
                    detail=(
                        f"{measurement.rows} of {ROWS_PER_DAY} rows, under the "
                        f"{POINT_MIN_ROWS}-row floor for a point query"
                    ),
                )
            )
    else:
        if measurement.coverage < PERIOD_MIN_COVERAGE:
            rejections.append(
                Rejection(
                    predicate="coverage",
                    requirement=requirement,
                    detail=(
                        f"{measurement.rows} of {requirement.expected_rows} rows "
                        f"({measurement.coverage:.1%}), under {PERIOD_MIN_COVERAGE:.0%}"
                    ),
                )
            )
        if measurement.max_gap_hours > MAX_GAP_HOURS:
            rejections.append(
                Rejection(
                    predicate="coverage",
                    requirement=requirement,
                    detail=(
                        f"a single gap of {measurement.max_gap_hours:.1f} h, over the "
                        f"{MAX_GAP_HOURS:.0f} h ceiling"
                    ),
                )
            )

    bounds = column_bounds(requirement.table, requirement.column)
    if bounds is not None and measurement.level_min is not None:
        read = "daily mean" if requirement.kind == "period" else "sample"
        for value in (measurement.level_min, measurement.level_max):
            if value is not None and not bounds.contains(value):
                rejections.append(
                    Rejection(
                        predicate="plausibility",
                        requirement=requirement,
                        detail=(
                            f"a {read} of {value:.3f} outside the column's bounds "
                            f"[{bounds.low}, {bounds.high}]"
                        ),
                    )
                )
                break

    if (
        frozenness_applies(requirement.table, requirement.column)
        and measurement.longest_run >= FROZEN_RUN
    ):
        rejections.append(
            Rejection(
                predicate="frozenness",
                requirement=requirement,
                detail=(
                    f"{measurement.longest_run} identical consecutive samples "
                    f"({measurement.longest_run / 2:.1f} h), at or over the "
                    f"{FROZEN_RUN}-sample run test"
                ),
            )
        )

    return tuple(rejections)


async def check(
    ctx: ScenarioContext, requirements: Iterable[Requirement]
) -> tuple[Rejection, ...]:
    """Every predicate over every declared window, through the case's as-of view.

    Returns the rejections rather than raising: a failing draw is resampled, not
    an error, and the caller counts what failed
    (:mod:`eval.generation.instantiate`).
    """
    rejections: list[Rejection] = []
    for requirement in requirements:
        rejections.extend(judge(await measure(ctx, requirement)))
    return tuple(rejections)
