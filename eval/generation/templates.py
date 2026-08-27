"""The catalog's 32 entries as data — constants, samplers and declared windows.

``questions.md`` §2 is the human-readable spec; this is the machine-readable
twin the generator reads. Each :class:`Template` carries three separable things,
and the separation is ``agent_architecture.md`` §6.1's:

* **the per-template constants** a case copies in unchanged — the gold
  trajectory, the must-not set, the cards, the tolerance, the answer metric, the
  roof pool. No oracle invents one, and nothing here is computed per draw except
  where the catalog says it varies (family H's ``argument_checks``, which follow
  the sampled variant's series shape).
* **the sampler** — what a draw of this template's parameters looks like, over
  the ``as_of`` band. Discrete wherever §1.7's per-parameter disjointness has to
  cut the values between train and test_seen (T113), because a continuum cannot
  be made disjoint in any checkable way.
* **the declared data** — the ``(table, column, window)`` set §1.6's validity
  predicates are evaluated over, as a function of the draw. A template that reads
  nothing from the record declares nothing, which is the honest answer for the
  weather and the given-values families rather than an omission.

**The registry is keyed by ``template_id``**, exactly like
:data:`eval.oracles.ORACLES`, so a template and its oracle are looked up by one
key and a template with no oracle fails at generation instead of emitting a case
with no answer.

**Two axes are stratified rather than sampled** (§1.7): T24a's and T27's
``variant``. They are distinct probes rather than values of one quantity, so both
splits carry all of them, the mix inside a split is fixed by
:meth:`Template.strata`, and nothing resamples them. T24a's mix is the one that
also fixes an abstention count, since its non-modellable variant is the only
abstention the ledger does not read off a whole template.
"""

from __future__ import annotations

import calendar
import dataclasses
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from random import Random
from typing import Any, Literal
from zoneinfo import ZoneInfo

from water_assistant_agent.assistant.knowledge.store import load_card
from water_assistant_agent.assistant.tools.gr2l_client import ROOF_PRESETS
from water_assistant_agent.assistant.tools.roofs import ROOFS, resolve_roof
from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE
from water_assistant_agent.assistant.toolset import (
    GREEN_ROOF_TOOL,
    IRRIGATION_TOOL,
    LOOKUP_TOOL,
    PLOT_TOOL,
    TEXT_TO_SQL_TOOL,
    WEATHER_TOOL,
)
from harness.assertions import pool_members

from eval.generation.filters import Requirement, roof_requirement, seed_requirement
from eval.oracles.availability import (
    VARIANT_FORCED_RAIN,
    VARIANT_IRRIGATION,
    VARIANT_PREDICTED_MINIMUM,
    declined_spellings,
)
from eval.oracles.counterfactual import (
    VARIANT_ALBEDO_AND_RAIN,
    VARIANT_RAIN_CROSS_ROOF,
    VARIANT_TRAIN_TAUGHT,
)
from eval.oracles.model_chain import past_days_for
from eval.oracles.presentation import (
    MEASURED_PAIR,
    MODEL_OVERLAY,
    NON_MODELLABLE_OVERLAY,
    pair_variable,
)

BAND_START = date(2025, 6, 1)
BAND_END = date(2026, 4, 24)
"""The catalog's ``as_of`` band (``decisions.md`` § Case time), both ends included.

The record's own limits rather than choices: runoff recording starts early enough
that the earliest case has weeks of it behind it, and the ceiling is the last day
of the sensor tables. ``scripts/count_t12_rain_events.py`` carries the same two
dates for the same reason.
"""

AS_OF_HOUR = 23
"""The site-local hour every ``as_of`` is stamped at, fixed rather than sampled.

Two reasons, and the first is load-bearing. A seed-bearing family anchors at
``seed_at = min(window_start, as_of)``, so a forward window's seed day **is** the
``as_of`` day — and §1.6's point rule wants that day at ≥44 of 48 rows. A morning
cut leaves it structurally incomplete (18 rows at 09:00), so every forward draw
would fail a predicate that is asking about a sensor rather than about the cut,
and the only repairs are a second rule for the seed day or a fixed late hour.
This is the fixed late hour: at 23:00 site time the day carries 47 of its 48
rows and the same predicate serves the seed and every other point query.

Second, the day is the axis T113 stripes. An hour drawn alongside it buys no
coverage the band does not already have and makes ``as_of`` disjointness a
statement about two quantities instead of one.
"""

TRAIN_AND_SEEN: frozenset[str] = frozenset({"train", "test_seen"})
HOLDOUT: frozenset[str] = frozenset({"test_unseen"})

M: Mapping[str, int] = {"train": 4, "test_seen": 5, "test_unseen": 8}
"""§1.7's instances per template per split. ``n`` is ``templates × m``, never chosen."""

WET_DAY_MM = 0.2
MIN_EVENT_MM = 10.0
DRAINAGE_TAIL_DAYS = 1
"""T12's event definition (``questions.md`` §2 T12), the three numbers
``scripts/count_t12_rain_events.py`` reports the pool with.

Only the *definition* of an event is shared with that script. Qualification is
not duplicated here: coverage is :mod:`eval.generation.filters`' predicate and
the retention-inside-[0,1] condition is the oracle's own refusal, which is
exactly §1.6's division of labour between an input filter and an oracle that
declines an ambiguous draw.
"""


class Undrawable(Exception):
    """This ``as_of`` admits no draw of this template — resample the day.

    Not an error and not a filter rejection: a September ``as_of`` has no
    completed February behind it, and a two-day window cannot open before the
    band does. The draw loop counts these separately from the predicates', since
    a template that is undrawable everywhere is a sizing problem and one that
    fails a predicate is a data problem.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Pools:
    """What one split may draw — the whole of what differs between splits (T113).

    Two fields, because §1.7 cuts a split along exactly two axes and shares every
    other one:

    * :attr:`days` — the split's ``as_of`` days, a **stripe** of the band rather
      than a contiguous band of its own, and the same stripe the retrospective
      families anchor their window on. A window whose end day is a train day is
      a value no test_seen draw can produce, which is how a ``{period}`` obeys
      per-parameter disjointness without being enumerable.
    * :attr:`parity` — which half of every discrete value pool this split takes.
      ``0`` is train and ``1`` is test_seen, by position in the pool's own order;
      ``None`` takes the pool whole and is the holdout's.

    **Roof, and the stratified ``variant`` axis, are absent on purpose.** They are
    shared (§1.7), so there is nothing per-split to carry: a template reads them
    from :func:`~harness.assertions.pool_members` and from its strata exactly as
    before.
    """

    days: tuple[date, ...]
    parity: int | None = None

    def of(self, values: Sequence[Any]) -> tuple[Any, ...]:
        """This split's stripe of *values* — its every-other member, or all of them.

        **Striped rather than cut at a point**, for the reason ``as_of`` is: a
        pool split at its median gives train the low thresholds and test_seen the
        high ones, which confounds the split with the difficulty of the draw and
        can put one class of a bool template out of reach on one side. Every
        other member leaves both sides spanning the same range.
        """
        if self.parity is None:
            return tuple(values)
        return tuple(
            value for index, value in enumerate(values) if index % 2 == self.parity
        )

    def since(self, earliest: date) -> tuple[date, ...]:
        """The split's days from *earliest* on, for a window that cannot open sooner."""
        return tuple(day for day in self.days if day >= earliest)


Sampler = Callable[[Random, datetime, Mapping[str, Any], Pools], dict[str, Any]]
"""Parameters drawn *from* an ``as_of`` — the forward and ``as_of``-relative families."""

Builder = Callable[[Random, Mapping[str, Any], Pools], tuple[dict[str, Any], date]]
"""Parameters drawn first, returning the earliest ``as_of`` that can see them.

The retrospective families: a ``{month}``, a ``{period}``, T04's ``{date}``,
T12's ``{event}``. Their window is an absolute stretch of the record rather than
something ``as_of`` implies, and drawing ``as_of`` first would weight those
windows by how many band days can reach them — which buries the late band under
the early one and, on T04, thins the wet class from the record's 1-in-6 to
1-in-25. Drawing the window first and the cut afterwards leaves both uniform.
"""

Draw = Callable[[Random, Pools, Mapping[str, Any]], tuple[datetime, dict[str, Any]]]
"""One whole draw: an ``as_of`` and the parameters, out of the split's own pools.

The pools are the split's because §1.7 stripes ``as_of`` rather than cutting it
and cuts every sampled parameter between train and test_seen (T113). Everything
here takes them as given and never reaches for the band or for a module-level
pool directly.
"""

Requires = Callable[[Mapping[str, Any], datetime], tuple[Requirement, ...]]
Renderer = Callable[[Mapping[str, Any]], str]
Checks = Callable[[Mapping[str, Any]], tuple[dict[str, Any], ...]]
Calls = Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], ...]]
"""The gold trajectory as a function of the draw, constant on all but one template.

Only the *names* are scored (``harness.scoring.gold_names``), so a call's ``args``
are there for :func:`~harness.assertions.check_calls`, which resolves the window
of an ``evaluate_against_measured`` call and asserts it ends at or before
``as_of``. T19 is the catalog's only retrospective GR2L window, its window
follows its own ``{d}``, and a gold call carrying no window resolves to a
*forward* one — so a fixed tuple would fail the one invariant this field exists
to let the harness check.
"""


def _calls(*calls: Mapping[str, Any]) -> Calls:
    """A gold trajectory that does not vary with the draw — every template but T19."""
    return lambda params: calls


def from_as_of(sampler: Sampler) -> Draw:
    """A :data:`Draw` that takes ``as_of`` first — the forward families' shape."""

    def draw(
        rng: Random, pools: Pools, fixed: Mapping[str, Any]
    ) -> tuple[datetime, dict[str, Any]]:
        as_of = as_of_at(_pick(rng, pools.days))
        return as_of, sampler(rng, as_of, fixed, pools)

    return draw


def after_window(builder: Builder) -> Draw:
    """A :data:`Draw` that takes the window first and the cut from the pool after it.

    Raises :class:`Undrawable` where the split's pool holds no day late enough to
    see the window drawn — which is a resample of the window, not a defect.
    """

    def draw(
        rng: Random, pools: Pools, fixed: Mapping[str, Any]
    ) -> tuple[datetime, dict[str, Any]]:
        params, earliest = builder(rng, fixed, pools)
        reachable = pools.since(earliest)
        if not reachable:
            raise Undrawable(f"no as_of in this split's pool falls on or after {earliest}")
        return as_of_at(_pick(rng, reachable)), params

    return draw


@dataclasses.dataclass(frozen=True, slots=True)
class Template:
    """One catalog entry: what it fixes, what it draws, and what it reads."""

    template_id: str
    splits: frozenset[str]
    answer_metric: Literal["scored", "skipped"]
    expected_tool_calls: Calls
    render: Renderer
    draw: Draw
    requires: Requires = lambda params, as_of: ()
    roof_pool: str | None = None
    tolerance: Mapping[str, Any] | None = None
    must_not_tools: tuple[str, ...] = ()
    gold_cards: tuple[str, ...] = ()
    argument_checks: Checks = lambda params: ()
    balanced: bool = False
    """A bool template the §1.6 balance rule binds on — ~50/50 inside each split.

    Eight of train's 25 (T04, T07, T09, T11, T12, T13, T16a, T25) and three more
    in the holdout (T16b, T20, T26), which is the reason train's ``m`` is an even
    4. **T26 joined the list at T114**, when its comparison variants stopped
    answering a roof name and started answering over the ordered pair: every
    variant answers a boolean now, so the rule §1.6 states for a bool template
    covers it. Its quota is per template rather than per variant, which is the
    cap the rule describes — the balance is over T26's eight instances, not
    inside each of its three probes.
    """

    abstains: bool = False
    """Every instance of this template is ``not_available`` (T17a/b, T18a/b, T27).

    T24a is **not** marked here even though one of its variants abstains — the
    abstention there is a stratified slice of a ``variant`` axis rather than a
    property of the template, which is why §1.7 counts its two instances by hand
    and leaves the template ledger untouched. :meth:`abstentions_in` is what reads
    both cases.
    """

    strata: Callable[[str], tuple[dict[str, Any], ...]] = lambda split: ()
    """Per-instance parameter fragments fixed by position, not drawn.

    Returns one fragment per instance of *split*, or ``()`` where the template has
    no stratified axis. The ``variant`` axis of T24a and T27 is what this exists
    for (§1.7): a disjoint variant axis would move a whole probe into one split,
    so both splits carry all of them in a stated mix.
    """

    surface_shape: Callable[[Mapping[str, Any]], str] | None = None
    """Which *question shape* a draw takes, on the three templates that have several.

    T24a, T26 and T27 fix shapes rather than wordings of one shape — T27(ii) takes
    no horizon because ``calc_irrigation`` has no date argument, T24a's pair asks
    about runoff or about moisture depending on the table it drew — so a
    paraphrase pool is per shape rather than per template.

    The same function keys the canonical sketch in :func:`_render_by` and the
    paraphrase pool in :mod:`eval.generation.paraphrases`, so a shape cannot exist
    in one and be missing from the other. :meth:`shape` is what both call.
    """

    def shape(self, params: Mapping[str, Any]) -> str:
        """This draw's question shape — the template id, unless the template varies."""
        return self.surface_shape(params) if self.surface_shape else self.template_id

    def instances(self, split: str) -> int:
        """How many instances this template contributes to *split*."""
        return M[split] if split in self.splits else 0

    def abstentions_in(self, split: str) -> int:
        """How many of those instances are ``not_available``.

        A whole-template abstention contributes all of them; T24a contributes the
        stratified count of its non-modellable variant, which is the ledger's one
        per-instance entry.
        """
        count = self.instances(split)
        if not count:
            return 0
        if self.abstains:
            return count
        return sum(
            1
            for fragment in self.strata(split)
            if fragment.get("variant") == NON_MODELLABLE_OVERLAY
        )


# --- Sampling helpers ---------------------------------------------------------


def band_days(start: date = BAND_START, end: date = BAND_END) -> tuple[date, ...]:
    """Every site calendar day in the ``as_of`` band, both ends included."""
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def as_of_at(day: date) -> datetime:
    """*day* stamped at :data:`AS_OF_HOUR`, in the site's own offset.

    Offset-bearing and never ``Z``: the stamp has to name the Berlin day the case
    is about, and the conversion to UTC belongs to ``connect_asof``
    (``decisions.md`` § The as-of cut).
    """
    return datetime(day.year, day.month, day.day, AS_OF_HOUR, tzinfo=ZoneInfo(SITE_TIMEZONE))


def _day(as_of: datetime) -> date:
    return as_of.astimezone(ZoneInfo(SITE_TIMEZONE)).date()


def _label(roof: str) -> str:
    """The roof's English display name, for the canonical question text."""
    segment = resolve_roof(roof)
    return segment.label_en if segment else roof


def _pick(rng: Random, values: Sequence[Any]) -> Any:
    if not values:
        raise Undrawable("nothing to draw from")
    return rng.choice(list(values))


def _striped(pool: Sequence[Any], ladder: Sequence[Any], pools: Pools) -> tuple[Any, ...]:
    """*pool*'s members on this split's side, striped over *ladder* rather than itself.

    The one operation the ladders exist for: parity is read off the ladder, so a
    value's side is the same whichever pool asks for it, and the pool then narrows
    what came back. The **pool's** own object is returned rather than the ladder's,
    which keeps ``8.0`` a float where a pool wrote one — a ladder deduplicates
    ``8`` against ``8.0`` and either could survive that.
    """
    members = {value: value for value in pool}
    return tuple(members[value] for value in pools.of(ladder) if value in members)


def _roof(rng: Random, pool: str) -> str:
    return _pick(rng, pool_members(pool))


def _band_window(rng: Random, length: int, pools: Pools) -> tuple[date, date]:
    """A *length*-day window inside the band, ending on one of the split's own days.

    315 of them at 14 days over the band's 328, which is the pool
    ``findings.md`` § What the record supports as a heatwave definition measures
    T08's acceptance rate against; a third of those ends belong to each split.

    **The end day is where a ``{period}`` becomes disjoint** (T113). A window is
    a continuum and cannot be enumerated into two halves, but its end is a band
    day and the band is striped — so a train period and a test_seen period differ
    in a value the case file carries, and per-parameter disjointness is checkable
    on the parameter itself rather than on a summary of it.
    """
    earliest_end = BAND_START + timedelta(days=length - 1)
    if earliest_end > BAND_END:
        raise Undrawable(f"no {length}-day window fits inside the band")
    end = _pick(rng, pools.since(earliest_end))
    return end - timedelta(days=length - 1), end


def _offset(rng: Random, days: int, pools: Pools) -> int:
    """Which day of a *days*-long window a forcing lands on, striped like any pool.

    ``{offset}`` counts today as 0 (``questions.md`` §2 T21) and its range follows
    the horizon it sits inside, so it is a pool built per draw rather than a
    module constant — striped all the same, because §1.7's disjointness is over
    *every* sampled parameter and (``mm``, ``offset``) is the pair a memorized
    counterfactual would be keyed on.
    """
    return _pick(rng, pools.of(tuple(range(days))))


def _period(window: tuple[date, date]) -> str:
    """``(start, end)`` written the way a ``{period}`` parameter carries it."""
    return f"{window[0].isoformat()}..{window[1].isoformat()}"


def _complete_months(table: str) -> tuple[str, ...]:
    """Every ``YYYY-MM`` *table* covers whole, up to the band's ceiling.

    A month the record enters partway through is dropped: a "July total" answered
    from half of July is an ambiguous oracle rather than a hard case, and
    ``outflow`` opens on 2025-04-15 where the state tables opened the summer
    before.
    """
    earliest = RECORD_START[table]
    year, index = earliest.year, earliest.month
    if earliest.day != 1:
        year, index = (year + 1, 1) if index == 12 else (year, index + 1)
    months: list[str] = []
    while True:
        last = date(year, index, calendar.monthrange(year, index)[1])
        if last > BAND_END:
            break
        months.append(f"{year:04d}-{index:02d}")
        year, index = (year + 1, 1) if index == 12 else (year, index + 1)
    if not months:
        raise Undrawable(f"{table} covers no whole month inside the band")
    return tuple(months)


WIDEST_MONTH_TABLE = "swc"
"""The table whose complete months contain every other table's, for :func:`_months`.

``swc`` and ``wetter`` open on the same day and ``outflow`` eleven months later,
so ``swc``'s month list is the superset every month draw in the catalog is a
subset of. Named rather than assumed: a table that ever opened earlier would move
this, and :func:`_months` asserts nothing else.
"""


def _months(table: str, pools: Pools) -> tuple[str, ...]:
    """The split's months for *table* — striped once, over the record's widest list.

    **A stripe taken over two different lists is not a stripe over the value**, and
    that is a defect T113 found by reading the emitted parameters rather than the
    sampler. T24a draws its ``{month}`` from ``outflow``'s eleven complete months
    on the flux variant and from ``swc``'s twenty on the state one; striping each
    list by position put ``2025-07`` at index 2 of the first (train) and index 11
    of the second (test_seen), so one template's own parameter overlapped between
    the splits and §1.7's memorized-constant detector was open on it.

    Striping the widest list and narrowing afterwards makes a month's side a
    property of the month, whichever template asks and through whichever table.
    ``swc``'s complete months *are* ``{month}``'s ladder, which is why this one is
    computed rather than listed beside the others.
    """
    return _striped(_complete_months(table), _complete_months(WIDEST_MONTH_TABLE), pools)


def _month_window(month: str) -> tuple[date, date]:
    year, index = (int(part) for part in month.split("-"))
    return date(year, index, 1), date(year, index, calendar.monthrange(year, index)[1])


RECORD_START: Mapping[str, date] = {
    "swc": date(2024, 7, 23),
    "tsoil": date(2024, 7, 23),
    "outflow": date(2025, 4, 15),
    "wetter": date(2024, 7, 23),
}
"""When each table's record opens (``findings.md`` § Data record).

Only used to keep a ``{month}`` draw off a month the table enters partway
through; every other coverage question is the filter's, not the sampler's.
"""


def rain_events(daily_rain: Mapping[date, float]) -> tuple[tuple[date, date], ...]:
    """T12's candidate event windows over the band, deepest definition first.

    A maximal run of consecutive site days at or above :data:`WET_DAY_MM`,
    extended by :data:`DRAINAGE_TAIL_DAYS`, kept where the window's rain reaches
    :data:`MIN_EVENT_MM`. **Candidates, not the qualifying pool**: coverage is the
    filter's predicate and a retention outside [0, 1] is the oracle's refusal, so
    what comes back here is every window the definition admits and the draw loop
    finds out which ones survive. That is the same 15 the committed
    ``t12_rain_events.md`` table lists, 11 of which qualify.
    """
    days = sorted(day for day in daily_rain if BAND_START <= day <= BAND_END)
    runs: list[list[date]] = []
    for day in days:
        if daily_rain[day] < WET_DAY_MM:
            continue
        if runs and (day - runs[-1][-1]).days == 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    windows = [(run[0], run[-1] + timedelta(days=DRAINAGE_TAIL_DAYS)) for run in runs]
    return tuple(
        (start, end)
        for start, end in windows
        if sum(
            daily_rain.get(start + timedelta(days=offset), 0.0)
            for offset in range((end - start).days + 1)
        )
        >= MIN_EVENT_MM
    )


# --- Value pools --------------------------------------------------------------
#
# Discrete on purpose, wherever T113 has to cut a parameter's values between
# train and test_seen (§1.7's per-param disjointness): a continuum admits no
# checkable disjointness, and the memorized-constant detector fails open without
# one. Ranges are read off the record where the record fixes them — the heat
# thresholds straddle the 24 °C the controller carries, the soil-moisture
# thresholds straddle the three roofs' dry thresholds — so both classes of a
# bool template are reachable rather than the balance rule doing all the work.

HOT_DAY_THRESHOLDS: tuple[int, ...] = (20, 22, 24, 25, 26, 28, 30)
MOISTURE_THRESHOLDS: tuple[float, ...] = (5, 8, 10, 12, 14, 15, 16, 18, 20, 22, 25)
RAIN_THRESHOLDS: tuple[float, ...] = (0.5, 1, 2, 3, 5, 8, 10)
FORWARD_HORIZONS: tuple[int, ...] = (2, 3, 4, 5, 6, 7)
PAST_HORIZONS: tuple[int, ...] = (3, 4, 5, 6, 7, 8, 10, 14)
RAIN_HORIZONS: tuple[int, ...] = (3, 4, 5, 6, 7, 8, 10)
HEATWAVE_HORIZONS: tuple[int, ...] = (3, 4, 5, 6, 7)
OVERRIDE_HORIZONS: tuple[int, ...] = (2, 3, 4, 5, 7, 10)
FORCED_RAIN_MM: tuple[float, ...] = (10, 20, 30, 40, 50)
ALBEDOS: tuple[float, ...] = (0.05, 0.1, 0.3, 0.45, 0.6, 0.75, 0.9)
STATED_MOISTURE: tuple[float, ...] = (4.5, 6.0, 8.0, 9.5, 11.0, 12.0, 13.5, 15.0, 16.0)
STATED_TMAX: tuple[float, ...] = (16, 19, 22, 25, 28, 31, 34)
STATED_RAIN: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0, 7.0, 10.0, 14.0)
BEYOND_HORIZON_DAYS: tuple[int, ...] = (21, 25, 28, 35, 42, 56)

# --- The ladders a parameter's stripe is taken over ----------------------------
#
# `decisions.md § Value pools are striped, and the holdout takes them whole` sets
# the validity condition these exist for: **the stripe must be a property of the
# value, not of its index in one particular list.** A parameter drawn from more
# than one pool breaks it the moment two of those pools share a value — striped
# separately, the shared value lands on train's side in one and on test_seen's in
# the other, and §1.7's per-parameter disjointness is gone while every per-template
# check still passes. Measured on `{month}` first (T113, `_months` below) and on
# `{d}` after it (T138), which reached all three of train, test_seen and the
# holdout through four different horizon lists.
#
# The repair is one ladder per *parameter*: stripe the union once, narrow to the
# pool afterwards. Derived rather than written out, so a pool that gains a member
# cannot quietly fall outside the ladder that is supposed to cover it.


def _ladder(*pools: Sequence[Any]) -> tuple[Any, ...]:
    """The sorted union of *pools* — the list a parameter's parity is read off."""
    return tuple(sorted({value for pool in pools for value in pool}))


HORIZONS: tuple[int, ...] = _ladder(
    FORWARD_HORIZONS, PAST_HORIZONS, RAIN_HORIZONS, HEATWAVE_HORIZONS, OVERRIDE_HORIZONS
)
"""``{d}``'s ladder: every horizon any template offers, forward or backward.

Five pools feed one parameter name. ``PAST_HORIZONS`` carries even members it did
not need on its own (4, 6, 8) because the ladder decides its parity: without them
T19's train side would be the single value 14, and a template drawing one horizon
four times is a pool this cut has emptied rather than striped.
"""

THRESHOLDS: tuple[float, ...] = _ladder(
    HOT_DAY_THRESHOLDS, MOISTURE_THRESHOLDS, RAIN_THRESHOLDS
)
"""``{thr}``'s ladder, across the three quantities the catalog spells ``thr``.

Degrees on T02, %θ on T09, millimetres on T13 — one parameter *name*, which is
the unit §1.7's rule and the detector both work in. Only 25 was ever placed
inconsistently by the per-pool stripe (train through T09's list, test_seen
through T02's); the ladder moves that one value and leaves the other eighteen
where they were.
"""

MOISTURE_LEVELS: tuple[float, ...] = _ladder(STATED_MOISTURE, MOISTURE_THRESHOLDS)
"""``{x}``'s ladder: the stated readings T16a/b give and the seeds T23 overrides.

Inert today — T23 is holdout and the holdout takes every pool whole, so nothing
is currently placed twice — and here anyway, because that safety is a property of
the split table rather than of the stripe. Moving T23 into train+seen would
otherwise reopen the defect silently.
"""

UNSERVED_VARIABLES: tuple[str, ...] = (
    "soil temperature",
    "soil moisture",
    "air pressure",
    "snow depth",
    "evapotranspiration",
    "dew point",
)
"""T18b's ``{variable}`` pool — quantities the daily row does not carry.

Authored rather than sampled over any noun, for the reason
:func:`~eval.oracles.weather.served_field` gives: the guard is generous about
what counts as served, and what it catches is this pool drifting onto the row's
own vocabulary. A member that ever starts matching a served field is refused by
the oracle as a template fault, which is the failure mode that would otherwise
write a false abstention into the gold set.
"""


def _declined_aliases() -> tuple[str, ...]:
    """Every spelling of the gravel roof or the wetland that reaches the scope limit.

    The oracle's own :func:`~eval.oracles.availability.declined_spellings`, read
    rather than copied, so the pool is the intersection ``roofs.py``'s alias map
    and ``NON_MODELLABLE_ROOFS`` currently agree on (``questions.md`` §2 T27).
    """
    return declined_spellings("gravel") + declined_spellings("wetland")


# --- Family A: pure SQL -------------------------------------------------------


def _build_month_outflow(
    rng: Random, fixed: Mapping[str, Any], pools: Pools
) -> tuple[dict[str, Any], date]:
    """T01 and T05's draw: a roof with a lysimeter and a month that roof shed into."""
    month = _pick(rng, _months("outflow", pools))
    return {"roof": _roof(rng, "P1f"), "month": month}, _month_window(month)[1]


def _requires_month_outflow(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
    start, end = _month_window(str(params["month"]))
    return (roof_requirement(str(params["roof"]), "outflow", start, end),)


def _build_t02(
    rng: Random, fixed: Mapping[str, Any], pools: Pools
) -> tuple[dict[str, Any], date]:
    window = _band_window(rng, 14, pools)
    return (
        {"period": _period(window), "thr": _pick(rng, _striped(HOT_DAY_THRESHOLDS, THRESHOLDS, pools))},
        window[1],
    )


def _requires_station(
    column: str,
) -> Callable[[Mapping[str, Any], datetime], tuple[Requirement, ...]]:
    """A ``{period}`` window over one ``wetter`` column.

    The station has no roof, so the column is named rather than resolved — the
    same exception ``eval/oracles/sql.py`` makes, and the same two names.
    """

    def requires(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
        key = "period" if "period" in params else "past_period"
        start, end = (date.fromisoformat(part) for part in str(params[key]).split(".."))
        return (Requirement("wetter", column, start, end, "period"),)

    return requires


def _build_period(length: int) -> Builder:
    def build(
        rng: Random, fixed: Mapping[str, Any], pools: Pools
    ) -> tuple[dict[str, Any], date]:
        window = _band_window(rng, length, pools)
        return {"period": _period(window)}, window[1]

    return build


def _requires_extensive_pair(
    params: Mapping[str, Any], as_of: datetime
) -> tuple[Requirement, ...]:
    """Both extensive roofs' ``swc`` over the window T03 and T24b average.

    A mean difference is only as present as its scarcer side, so both columns are
    declared rather than one standing in for the pair.
    """
    if "month" in params:
        start, end = _month_window(str(params["month"]))
    else:
        start, end = (date.fromisoformat(part) for part in str(params["period"]).split(".."))
    return tuple(
        roof_requirement(roof, "swc", start, end, kind="period")
        for roof in ("irrigated_extensive", "non_irrigated_extensive")
    )


def _build_t04(
    rng: Random, fixed: Mapping[str, Any], pools: Pools
) -> tuple[dict[str, Any], date]:
    """A roof and one band day — the suite's tightest date pool.

    Daily outflow is exactly zero on 75–94 % of band days depending on roof, so
    the balance rule's rejection sampling is what makes the "yes" class reachable
    and the roof is redrawn on every attempt. Nothing is filtered *for* wetness
    here: a dry day is the "no" class, and rejecting constant days would delete it
    outright (``findings.md``).

    The day is uniform over the band and the cut follows it, which is what keeps
    the class prior the record's own 1-in-6 rather than the 1-in-25 an
    ``as_of``-first draw produces by burying the wet winter under the dry summer.
    """
    drawn = _pick(rng, pools.since(max(BAND_START, RECORD_START["outflow"])))
    return {"roof": _roof(rng, "P1f"), "date": drawn.isoformat()}, drawn


def _requires_day_outflow(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
    day = date.fromisoformat(str(params["date"]))
    return (roof_requirement(str(params["roof"]), "outflow", day, day, kind="point"),)


def _build_t15a(
    rng: Random, fixed: Mapping[str, Any], pools: Pools
) -> tuple[dict[str, Any], date]:
    window = _band_window(rng, 7, pools)
    return {"past_period": _period(window)}, window[1]


# --- Families B and C: lookup and weather -------------------------------------


def _sample_nothing(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    """Templates whose whole question is fixed — T06, T17a, T17b, T25.

    ``as_of`` still varies, which is the only axis these have, and for T25 it is
    the axis the answer turns on.
    """
    return {}


def _sample_horizon(pool: Sequence[int] = FORWARD_HORIZONS) -> Sampler:
    def sample(
        rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
    ) -> dict[str, Any]:
        return {"d": _pick(rng, _striped(pool, HORIZONS, pools))}

    return sample


def _sample_t13(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    return {
        "thr": _pick(rng, _striped(RAIN_THRESHOLDS, THRESHOLDS, pools)),
        "d": _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools)),
    }


def _sample_t18a(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    return {"ahead_days": _pick(rng, pools.of(BEYOND_HORIZON_DAYS))}


def _sample_t18b(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    return {
        "variable": _pick(rng, pools.of(UNSERVED_VARIABLES)),
        "d": _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools)),
    }


def _requires_yesterday_station(
    params: Mapping[str, Any], as_of: datetime
) -> tuple[Requirement, ...]:
    """T25's measured half — yesterday's station day, as a point query.

    Tomorrow is a forecast and has no row to check; yesterday is the side the
    database can answer, and the whole template is the comparison between them.
    """
    yesterday = _day(as_of) - timedelta(days=1)
    return (Requirement("wetter", "Tmax", yesterday, yesterday, "point"),)


# --- Families D, E, G: the model chains and the calculator ---------------------


def _sample_roof_only(pool: str) -> Sampler:
    def sample(
        rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
    ) -> dict[str, Any]:
        return {"roof": _roof(rng, pool)}

    return sample


def _sample_t09(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    return {
        "roof": _roof(rng, "P2"),
        "thr": _pick(rng, _striped(MOISTURE_THRESHOLDS, THRESHOLDS, pools)),
        "d": _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools)),
    }


def _sample_t10(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    return {"roof": _roof(rng, "P2"), "d": _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools))}


def _sample_t19(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    days = _pick(rng, _striped(PAST_HORIZONS, HORIZONS, pools))
    if _day(as_of) - timedelta(days=days) < BAND_START - timedelta(days=30):
        raise Undrawable("the comparison window opens before the record is usable")
    return {"roof": _roof(rng, "P2"), "d": days}


def _forward_seed(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
    """The seed a forward-window model chain reads: a point at ``as_of``'s own day.

    ``seed_at = min(window_start, as_of)`` and a forward window starts today, so
    the seed day is the ``as_of`` day — which :data:`AS_OF_HOUR` keeps checkable
    under the same 44-of-48 rule as any other point query.
    """
    day = _day(as_of)
    return (seed_requirement(str(params["roof"]), day, day),)


def _requires_t19(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
    """T19's seed **and** the measured half it is compared against.

    The catalog's only retrospective GR2L window, and the one place the seed
    anchor is visibly not ``as_of``: the window opens ``d`` days back, so
    ``min(window_start, as_of)`` is that earlier day and the seed is checked
    there. The measured ``swc`` over the window is declared as well, because a
    deviation computed over the days that happen to be present is exactly the
    silent failure §1.6's coverage rule exists to stop.
    """
    today = _day(as_of)
    start = today - timedelta(days=int(params["d"]))
    end = today - timedelta(days=1)
    return (
        seed_requirement(str(params["roof"]), start, today),
        roof_requirement(str(params["roof"]), "swc", start, end, kind="period"),
    )


def _sample_t21(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    days = _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools))
    return {
        "roof": _roof(rng, "P2"),
        "d": days,
        "mm": _pick(rng, pools.of(FORCED_RAIN_MM)),
        "offset": _offset(rng, days, pools),
    }


def _albedo(rng: Random, pools: Pools, *roofs: str) -> float:
    """An albedo that is not the default of any roof it will be applied to.

    A counterfactual equal to the baseline probes nothing: it scores a candidate
    that never passed the argument full marks on the answer, which is the guard
    the oracles raise on and the reason T22 refused every draw while the served
    model ignored ``albedo`` (``findings.md``). The defaults are read off
    ``ROOF_PRESETS`` per draw rather than assumed flat, and a comparison variant
    passes both of its roofs because one overlay covers both runs.
    """
    defaults = {float(ROOF_PRESETS[roof]["albedo"]) for roof in roofs}
    return _pick(rng, [value for value in pools.of(ALBEDOS) if value not in defaults])


def _sample_t22(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    """An albedo that is not the roof's own default, which would probe nothing.

    The default is read off ``ROOF_PRESETS`` per draw rather than assumed flat, on
    the same ground the oracle refuses such a draw: a counterfactual equal to the
    baseline scores a candidate that never passed the argument full marks.
    """
    roof = _roof(rng, "P2")
    return {"roof": roof, "a": _albedo(rng, pools, roof)}


def _sample_t23(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    return {
        "roof": _roof(rng, "P2"),
        "x": _pick(rng, _striped(MOISTURE_THRESHOLDS, MOISTURE_LEVELS, pools)),
        "d": _pick(rng, _striped(OVERRIDE_HORIZONS, HORIZONS, pools)),
    }


def _sample_stated(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    """T16a and T16b's four stated values — the same draw, by construction.

    The pair supplies identical inputs and differs in one thing, which is the
    phrasing §1.6 calls the discriminator, so one sampler serves both.
    """
    return {
        "roof": _roof(rng, "P2"),
        "x": _pick(rng, _striped(STATED_MOISTURE, MOISTURE_LEVELS, pools)),
        "tmax": _pick(rng, pools.of(STATED_TMAX)),
        "y": _pick(rng, pools.of(STATED_RAIN)),
    }


def _sample_t26(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    days = _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools))
    params: dict[str, Any] = {
        "variant": fixed["variant"],
        "d": days,
        "mm": _pick(rng, pools.of(FORCED_RAIN_MM)),
        "offset": _offset(rng, days, pools),
    }
    if fixed["variant"] == VARIANT_ALBEDO_AND_RAIN:
        roof = _roof(rng, "P2")
        params["roof"] = roof
        params["a"] = _albedo(rng, pools, roof)
    else:
        pair = rng.sample(list(pool_members("P2")), 2)
        params["roof_a"], params["roof_b"] = pair
        # (ii) composes the albedo override across the pair and (iii) composes
        # only train-taught axes, which is the whole difference between them —
        # and until T114 neither carried one, so they were three labels over two
        # probes (`questions.md` §2 T26).
        if fixed["variant"] == VARIANT_RAIN_CROSS_ROOF:
            params["a"] = _albedo(rng, pools, *pair)
    return params


def _requires_t26(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
    day = _day(as_of)
    roofs = [params[key] for key in ("roof", "roof_a", "roof_b") if params.get(key)]
    return tuple(seed_requirement(str(roof), day, day) for roof in roofs)


# --- Family H: presentation ---------------------------------------------------


def _build_t24a(
    rng: Random, fixed: Mapping[str, Any], pools: Pools
) -> tuple[dict[str, Any], date]:
    """One month plus the shape the stratified variant fixes.

    The month is complete at ``as_of`` on every variant — the measured half has
    to exist — and sits inside §3.3's 31-day cap so the truncation flag stays
    clear. On the measured pair the table is stratified too, and the ``swc`` pair
    is required to include the gravel roof or the wetland: that pair is (iii)'s
    counter-probe, and it only charges a candidate that generalized
    "gravel ⇒ not_available" if one of those two roofs is actually in it.
    """
    variant = fixed["variant"]
    table = fixed.get("table", "swc")
    month = _pick(rng, _months(table if table == "outflow" else "swc", pools))
    params: dict[str, Any] = {"variant": variant, "month": month}
    if variant == MEASURED_PAIR:
        pool = list(pool_members("P1f" if table == "outflow" else "P1"))
        params["table"] = table
        if table == "swc":
            anchor = _pick(rng, [roof for roof in pool if roof in ("gravel", "wetland")])
            other = _pick(rng, [roof for roof in pool if roof != anchor])
            pair = [anchor, other]
            rng.shuffle(pair)
        else:
            pair = rng.sample(pool, 2)
        params["roof_a"], params["roof_b"] = pair
    elif variant == MODEL_OVERLAY:
        params["roof"] = _roof(rng, "P2")
    else:
        params["alias"] = _pick(rng, pools.of(_declined_aliases()))
    return params, _month_window(month)[1]


def _requires_t24a(params: Mapping[str, Any], as_of: datetime) -> tuple[Requirement, ...]:
    """The measured series the plot draws, plus the model overlay's seed.

    A plot is only as answerable as its measured half: the tool fetches its own
    data, so a month with a hole in it renders a chart of an outage. (iii)
    declares its measured half too — the request is refused for its ``model``
    series, not for the roof, and its measured series is valid (§3.6).
    """
    start, end = _month_window(str(params["month"]))
    variant = str(params["variant"])
    if variant == MEASURED_PAIR:
        table = str(params.get("table") or "swc")
        return tuple(
            roof_requirement(str(params[key]), table, start, end, kind="period")
            for key in ("roof_a", "roof_b")
        )
    roof = str(params.get("roof") or params.get("alias"))
    measured = roof_requirement(roof, "swc", start, end, kind="period")
    if variant == MODEL_OVERLAY:
        return (measured, seed_requirement(roof, start, _day(as_of)))
    return (measured,)


def _t24a_checks(params: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """§2 H's scored surface, per variant: source, variable, roof, and the window.

    Aggregation, unit, axis and ``kind`` are deliberately absent — §3.6 derives
    the first three in code from the variable and §7 fixes the scored surface to
    the rest, so checking them would discriminate no candidate.

    **The roof is a set match on (i) and (ii) and a presence check on (iii)**, and
    that asymmetry is the parameter's. (i) and (ii) draw a canonical roof name,
    which the candidate passes through; (iii)'s parameter is a *spelling* drawn
    from the alias map, and ``set_eq`` compares strings with no roof resolution
    behind it, so demanding the spelling back would score vocabulary rather than
    the routing this variant probes. Presence is §7's own rule for a
    candidate-chosen argument and is what the model overlay already uses for its
    modelling argument.
    """
    start, end = _month_window(str(params["month"]))
    variant = str(params["variant"])
    window = (
        {"tool": PLOT_TOOL, "path": "start_date", "op": "eq",
         "value": start.isoformat(), "resolve": "window"},
        {"tool": PLOT_TOOL, "path": "end_date", "op": "eq",
         "value": end.isoformat(), "resolve": "window"},
    )
    if variant == MEASURED_PAIR:
        table = str(params.get("table") or "swc")
        return (
            {"tool": PLOT_TOOL, "path": "series.*.source", "op": "set_eq",
             "value": ["measured"]},
            {"tool": PLOT_TOOL, "path": "series.*.variable", "op": "set_eq",
             "value": [pair_variable(table)]},
            {"tool": PLOT_TOOL, "path": "series.*.roof", "op": "set_eq",
             "value": sorted({str(params["roof_a"]), str(params["roof_b"])})},
            *window,
        )
    shared = (
        {"tool": PLOT_TOOL, "path": "series.*.source", "op": "set_eq",
         "value": ["measured", "model"]},
        {"tool": PLOT_TOOL, "path": "series.*.variable", "op": "set_eq",
         "value": ["soil_moisture", "swc_pct"]},
    )
    if variant == MODEL_OVERLAY:
        return (
            *shared,
            {"tool": PLOT_TOOL, "path": "series.*.roof", "op": "set_eq",
             "value": [str(params["roof"])]},
            {"tool": PLOT_TOOL, "path": "series.*.roof", "op": "present"},
            *window,
        )
    return (
        *shared,
        {"tool": PLOT_TOOL, "path": "series.*.roof", "op": "present"},
        *window,
    )


def _t24a_strata(split: str) -> tuple[dict[str, Any], ...]:
    """§2 H's stated variant mix: train 2/1/1, test_seen 2/2/1 over (i)/(ii)/(iii).

    Positional, never resampled, and the pair's two instances take one ``swc``
    table and one ``outflow`` table so §1.8's P1f row is real for family H. These
    counts are also where two of the suite's 45 abstentions come from — the only
    two the ledger does not read off a whole template.
    """
    pair = (
        {"variant": MEASURED_PAIR, "table": "swc"},
        {"variant": MEASURED_PAIR, "table": "outflow"},
    )
    if split == "train":
        return (*pair, {"variant": MODEL_OVERLAY}, {"variant": NON_MODELLABLE_OVERLAY})
    if split == "test_seen":
        return (
            *pair,
            {"variant": MODEL_OVERLAY},
            {"variant": MODEL_OVERLAY},
            {"variant": NON_MODELLABLE_OVERLAY},
        )
    return ()


def _build_t24b(
    rng: Random, fixed: Mapping[str, Any], pools: Pools
) -> tuple[dict[str, Any], date]:
    month = _pick(rng, _months("swc", pools))
    return {"month": month}, _month_window(month)[1]


# --- Family I: modelling availability -----------------------------------------


def _sample_t27(
    rng: Random, as_of: datetime, fixed: Mapping[str, Any], pools: Pools
) -> dict[str, Any]:
    """An alias that reaches the scope limit, plus the horizon the variant needs.

    ``calc_irrigation`` takes no date arguments, so (ii) carries no horizon; (i)
    and (iii) carry ``{d}`` days, repaired from hours for the reason
    ``decisions.md`` § Forward horizons are counted in days gives.
    """
    variant = fixed["variant"]
    params: dict[str, Any] = {
        "alias": _pick(rng, pools.of(_declined_aliases())),
        "variant": variant,
    }
    if variant != VARIANT_IRRIGATION:
        params["d"] = _pick(rng, _striped(FORWARD_HORIZONS, HORIZONS, pools))
    if variant == VARIANT_FORCED_RAIN:
        params["mm"] = _pick(rng, pools.of(FORCED_RAIN_MM))
    return params


def _t27_strata(split: str) -> tuple[dict[str, Any], ...]:
    """T27's three variants, round-robin inside a split and never resampled.

    Shared like T24a's for §1.7's reason — three routes to one limit rather than
    values of one quantity — and unlike T24a's the mix moves no count, since every
    instance of this template abstains whichever variant it draws.
    """
    order = (VARIANT_PREDICTED_MINIMUM, VARIANT_IRRIGATION, VARIANT_FORCED_RAIN)
    count = M.get(split, 0) if split in TRAIN_AND_SEEN else 0
    return tuple({"variant": order[index % len(order)]} for index in range(count))


def _t26_strata(split: str) -> tuple[dict[str, Any], ...]:
    """T26's three variants over the holdout's eight instances, 3/3/2."""
    order = (VARIANT_ALBEDO_AND_RAIN, VARIANT_RAIN_CROSS_ROOF, VARIANT_TRAIN_TAUGHT)
    count = M["test_unseen"] if split == "test_unseen" else 0
    return tuple({"variant": order[index % len(order)]} for index in range(count))


# --- Question rendering -------------------------------------------------------
#
# The canonical English form of each catalog sketch. T112 owns the paraphrases,
# the EN/DE 50-50 split and the style pools; what belongs here is the one
# unambiguous rendering a paraphrase is a paraphrase *of*, and the roof named in
# the agent's own vocabulary rather than by a raw column name (§1.6).


def raw_column_names() -> frozenset[str]:
    """Every spelling §1.6 forbids a question to name a roof by, lowercased.

    Read off ``roofs.py`` rather than written out, so a column that is renamed
    moves the rule with it. Three forms, and the catalog's own three examples —
    ``Extensiv1``, ``Sumpf2``, ``QGravel`` — are one of each:

    * the column itself (``QGravel``, ``Kies_Efflux``, ``QEx1``);
    * a lysimeter column's **stem**, the part before ``_Efflux``. ``Sumpf2`` and
      ``Extensiv1`` are named by §1.6 and are neither whole columns nor anything
      else the site says out loud;
    * the ``radiation`` entry, which is a mast prefix rather than a column
      (``KD``, ``ED1``) and reads exactly as raw.

    :func:`mentions_raw_column` is the check.
    """
    names: set[str] = set()
    for roof in ROOFS.values():
        for column in roof.columns.values():
            names.add(column.lower())
            stem, separator, _ = column.partition("_")
            if separator:
                names.add(stem.lower())
    return frozenset(names)


def mentions_raw_column(question: str) -> tuple[str, ...]:
    """Raw column names appearing as words in *question* — empty is the rule kept."""
    words = {word.strip(".,:;'\"!?()").lower() for word in question.split()}
    return tuple(sorted(words & raw_column_names()))


def _spoken(alias: str) -> str:
    """The roof an ``{alias}`` parameter names, in the question's own words.

    The alias pool of T27 and T24a(iii) constrains the **parameter** — it is what
    the oracle matches against the two tools' scope table, and half of it is
    database columns (``QGravel``, ``Sumpf2``, ``KD``, …) that §1.6 forbids a
    question to say. The catalog is explicit that the text is a separate matter
    ("the question *text* may still say 'das Kiesdach'", §2 T27), so the canonical
    rendering names the roof and T112 varies the surface from there.
    """
    segment = resolve_roof(alias)
    return segment.label_en if segment else alias


def _unbound(reason: str) -> Draw:
    """A placeholder draw for a template whose pool is read off the record at run time."""

    def draw(
        rng: Random, pools: Pools, fixed: Mapping[str, Any]
    ) -> tuple[datetime, dict[str, Any]]:
        raise Undrawable(reason)

    return draw


def _render(sketch: str) -> Renderer:
    def render(params: Mapping[str, Any]) -> str:
        values = dict(params)
        for key in ("roof", "roof_a", "roof_b"):
            if key in values:
                values[key] = _label(str(values[key]))
        if "alias" in values:
            values["alias"] = _spoken(str(values["alias"]))
        return sketch.format(**values)

    return render


def _render_by(key: Callable[[Mapping[str, Any]], str], sketches: Mapping[str, str]) -> Renderer:
    """One sketch per variant, chosen by *key* — families H, G(T26) and I.

    Their variants fix different *shapes* rather than different wordings of one
    shape: T27(ii) takes no horizon at all because ``calc_irrigation`` has no date
    argument, and T24a's pair asks about runoff or about moisture depending on the
    table it drew. One sketch could not carry either.
    """

    def render(params: Mapping[str, Any]) -> str:
        return _render(sketches[key(params)])(params)

    return render


def _t24a_shape(params: Mapping[str, Any]) -> str:
    """H's four shapes: the pair asks a different question of each of its two tables."""
    variant = str(params["variant"])
    if variant == MEASURED_PAIR:
        return f"T24a:{variant}:{params.get('table') or 'swc'}"
    return f"T24a:{variant}"


def _t26_shape(params: Mapping[str, Any]) -> str:
    """T26's three shapes, one per variant.

    The two comparison variants ask a differently-shaped question now that (ii)
    carries the albedo override it is named for: (ii) states two overrides and
    (iii) one, so a single sketch could not carry both. Until T114 they shared a
    shape because they shared everything (``questions.md`` §2 T26).
    """
    return f"T26:{params['variant']}"


def _t27_shape(params: Mapping[str, Any]) -> str:
    """I's three routes to one scope limit, each a shape of its own."""
    return f"T27:{params['variant']}"


# --- The registry -------------------------------------------------------------

EXACT: Mapping[str, Any] = {"kind": "exact"}


def _abs(value: float) -> dict[str, Any]:
    return {"kind": "abs", "value": value}


def _rel(value: float) -> dict[str, Any]:
    return {"kind": "rel", "value": value}


TEMPLATES: dict[str, Template] = {
    # --- A. Pure SQL ---------------------------------------------------------
    "T01": Template(
        template_id="T01",
        splits=TRAIN_AND_SEEN,
        roof_pool="P1f",
        answer_metric="scored",
        tolerance=_rel(0.02),
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        must_not_tools=(LOOKUP_TOOL,),
        render=_render("What was the total outflow of the {roof} in {month}?"),
        draw=after_window(_build_month_outflow),
        requires=_requires_month_outflow,
    ),
    "T02": Template(
        template_id="T02",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        render=_render(
            "On how many days in {period} did the maximum air temperature exceed {thr} °C?"
        ),
        draw=after_window(_build_t02),
        requires=_requires_station("Tmax"),
    ),
    "T03": Template(
        template_id="T03",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=_abs(0.2),
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        render=_render(
            "What was the mean soil-moisture difference between the irrigated and the "
            "non-irrigated extensive roof over {period}?"
        ),
        draw=after_window(_build_period(14)),
        requires=_requires_extensive_pair,
    ),
    "T04": Template(
        template_id="T04",
        splits=TRAIN_AND_SEEN,
        roof_pool="P1f",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        must_not_tools=(GREEN_ROOF_TOOL,),
        render=_render("Did the {roof} produce any outflow on {date}?"),
        draw=after_window(_build_t04),
        requires=_requires_day_outflow,
        balanced=True,
    ),
    "T05": Template(
        template_id="T05",
        splits=TRAIN_AND_SEEN,
        roof_pool="P1f",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        render=_render("On which day in {month} did the {roof} have its highest outflow?"),
        draw=after_window(_build_month_outflow),
        requires=_requires_month_outflow,
    ),
    "T15a": Template(
        template_id="T15a",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=_rel(0.02),
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        render=_render("How much rain fell in {past_period}?"),
        draw=after_window(_build_t15a),
        requires=_requires_station("Rain"),
    ),
    # --- B. Pure reference lookup --------------------------------------------
    "T06": Template(
        template_id="T06",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls(
            {"name": LOOKUP_TOOL, "args": {"topic": "irrigation_threshold"}},
        ),
        gold_cards=("irrigation_threshold",),
        render=_render(
            "What is the soil-moisture threshold for irrigating the extensive roofs?"
        ),
        draw=from_as_of(_sample_nothing),
    ),
    "T17a": Template(
        template_id="T17a",
        splits=TRAIN_AND_SEEN,
        answer_metric="skipped",
        expected_tool_calls=_calls({"name": LOOKUP_TOOL, "args": {"topic": "irrigation_rule"}},),
        gold_cards=("irrigation_rule",),
        render=_render(
            "What is the maximum wind speed at which irrigation must be shut off?"
        ),
        draw=from_as_of(_sample_nothing),
        abstains=True,
    ),
    "T17b": Template(
        template_id="T17b",
        splits=HOLDOUT,
        answer_metric="skipped",
        expected_tool_calls=_calls(
            {"name": LOOKUP_TOOL, "args": {"topic": "irrigation_threshold"}},
        ),
        gold_cards=("irrigation_threshold",),
        render=_render(
            "What is the soil-moisture irrigation threshold for the wetland roof?"
        ),
        draw=from_as_of(_sample_nothing),
        abstains=True,
    ),
    # --- C. Pure weather ------------------------------------------------------
    "T13": Template(
        template_id="T13",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": WEATHER_TOOL},),
        render=_render("Is more than {thr} mm of rain expected in the next {d} days?"),
        draw=from_as_of(_sample_t13),
        balanced=True,
    ),
    "T14": Template(
        template_id="T14",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=_abs(0.1),
        expected_tool_calls=_calls({"name": WEATHER_TOOL},),
        render=_render("What is the highest temperature forecast for the next {d} days?"),
        draw=from_as_of(_sample_horizon()),
    ),
    "T15b": Template(
        template_id="T15b",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=_rel(0.02),
        expected_tool_calls=_calls({"name": WEATHER_TOOL},),
        must_not_tools=(TEXT_TO_SQL_TOOL,),
        render=_render("How much rain will fall in the next {d} days?"),
        draw=from_as_of(_sample_horizon(RAIN_HORIZONS)),
    ),
    "T18a": Template(
        template_id="T18a",
        splits=TRAIN_AND_SEEN,
        answer_metric="skipped",
        expected_tool_calls=_calls({"name": WEATHER_TOOL},),
        render=_render("What will the temperature be in {ahead_days} days?"),
        draw=from_as_of(_sample_t18a),
        abstains=True,
    ),
    "T18b": Template(
        template_id="T18b",
        splits=HOLDOUT,
        answer_metric="skipped",
        expected_tool_calls=_calls(),
        render=_render("What is the forecast {variable} for the next {d} days?"),
        draw=from_as_of(_sample_t18b),
        abstains=True,
    ),
    # --- D. Model chains ------------------------------------------------------
    "T09": Template(
        template_id="T09",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        render=_render(
            "Will the soil moisture of the {roof} fall below {thr} %θ over the next "
            "{d} days?"
        ),
        draw=from_as_of(_sample_t09),
        requires=_forward_seed,
        balanced=True,
    ),
    "T10": Template(
        template_id="T10",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=_abs(0.1),
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        render=_render(
            "What is the minimum soil moisture predicted for the {roof} over the next "
            "{d} days?"
        ),
        draw=from_as_of(_sample_t10),
        requires=_forward_seed,
    ),
    "T19": Template(
        template_id="T19",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=_abs(0.1),
        # The window is carried so `check_calls` can resolve it and assert it ends
        # at or before `as_of` — the one invariant an `evaluate_against_measured`
        # call has to keep (T104) — and it is `past_days_for(d)`, the oracle's own
        # conversion, so the gold call and the answer name one window by
        # construction rather than by two matching literals.
        expected_tool_calls=lambda params: (
            {
                "name": GREEN_ROOF_TOOL,
                "args": {
                    "evaluate_against_measured": True,
                    "past_days": past_days_for(params["d"]),
                },
            },
        ),
        must_not_tools=(WEATHER_TOOL,),
        render=_render(
            "How far off was the soil-moisture model for the {roof} over the last {d} "
            "days, on average?"
        ),
        draw=from_as_of(_sample_t19),
        requires=_requires_t19,
    ),
    # --- E. Hybrid / full chain ----------------------------------------------
    "T07": Template(
        template_id="T07",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": IRRIGATION_TOOL},),
        render=_render("Does the {roof} need irrigation right now?"),
        draw=from_as_of(_sample_roof_only("P2")),
        requires=_forward_seed,
        balanced=True,
    ),
    "T08": Template(
        template_id="T08",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls(
            {"name": LOOKUP_TOOL, "args": {"topic": "heatwave_definition"}},
            {"name": TEXT_TO_SQL_TOOL},
        ),
        gold_cards=("heatwave_definition",),
        render=_render(
            "How many heatwave days, as defined in the operations manual, occurred in "
            "{period}?"
        ),
        draw=after_window(_build_period(14)),
        requires=_requires_station("Tmax"),
    ),
    "T11": Template(
        template_id="T11",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": IRRIGATION_TOOL},),
        render=_render("Does the {roof} need irrigation tomorrow?"),
        draw=from_as_of(_sample_roof_only("P2")),
        requires=_forward_seed,
        balanced=True,
    ),
    "T12": Template(
        template_id="T12",
        splits=TRAIN_AND_SEEN,
        roof_pool="P1f",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls(
            {"name": LOOKUP_TOOL, "args": {"topic": "retention_target"}},
            {"name": TEXT_TO_SQL_TOOL},
        ),
        gold_cards=("retention_target",),
        render=_render(
            "Was the retention of the {roof} during {event} above the manual's target?"
        ),
        # Drawn against the record rather than from a value pool: the event set is
        # the pinned rain record's, so the draw is installed by the loop once the
        # daily rain has been read (`instantiate.event_draw`). Unbound, it raises
        # rather than silently drawing nothing.
        draw=_unbound("T12's {event} pool is the record's; see instantiate.event_draw"),
        requires=lambda params, as_of: (
            roof_requirement(
                str(params["roof"]),
                "outflow",
                *(date.fromisoformat(part) for part in str(params["event"]).split("..")),
                kind="period",
            ),
            Requirement(
                "wetter",
                "Rain",
                *(date.fromisoformat(part) for part in str(params["event"]).split("..")),
                "period",
            ),
        ),
        balanced=True,
    ),
    "T20": Template(
        template_id="T20",
        splits=HOLDOUT,
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls(
            {"name": LOOKUP_TOOL, "args": {"topic": "heatwave_definition"}},
            {"name": WEATHER_TOOL},
        ),
        must_not_tools=(TEXT_TO_SQL_TOOL,),
        gold_cards=("heatwave_definition",),
        render=_render(
            "Does the forecast for the next {d} days qualify as a heatwave under the "
            "manual's definition?"
        ),
        draw=from_as_of(_sample_horizon(HEATWAVE_HORIZONS)),
        balanced=True,
    ),
    "T25": Template(
        template_id="T25",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=EXACT,
        # One of the two gold routes, and the one the docstring recommends. The
        # trajectory metric reads names only, so listing the combined call does
        # not forbid the two-call route the catalog also calls gold.
        expected_tool_calls=_calls({"name": WEATHER_TOOL},),
        render=_render("Will tomorrow be warmer than yesterday?"),
        draw=from_as_of(_sample_nothing),
        requires=_requires_yesterday_station,
        balanced=True,
    ),
    # --- F. Given-values controls --------------------------------------------
    "T16a": Template(
        template_id="T16a",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": LOOKUP_TOOL, "args": {"topic": "irrigation_rule"}},),
        must_not_tools=(TEXT_TO_SQL_TOOL, WEATHER_TOOL, IRRIGATION_TOOL),
        gold_cards=("irrigation_rule", "irrigation_threshold"),
        render=_render(
            "The {roof} is at {x} %θ, {tmax} °C is expected over the next 48 h and {y} mm "
            "of rain over the coming week — what does the operations manual say, should "
            "we irrigate?"
        ),
        draw=from_as_of(_sample_stated),
        balanced=True,
    ),
    "T16b": Template(
        template_id="T16b",
        splits=HOLDOUT,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": IRRIGATION_TOOL},),
        must_not_tools=(LOOKUP_TOOL,),
        render=_render(
            "The {roof} is at {x} %θ, {tmax} °C is expected over the next 48 h and {y} mm "
            "of rain over the coming week — should we irrigate?"
        ),
        draw=from_as_of(_sample_stated),
        balanced=True,
    ),
    # --- G. Counterfactuals ---------------------------------------------------
    "T21": Template(
        template_id="T21",
        splits=TRAIN_AND_SEEN,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=_abs(0.1),
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        argument_checks=lambda params: (
            {"tool": GREEN_ROOF_TOOL, "path": "forcings.precip", "op": "present"},
        ),
        render=_render(
            "If {mm} mm of rain falls on day {offset} of the next {d} days, what is the "
            "minimum soil moisture of the {roof} over that window?"
        ),
        draw=from_as_of(_sample_t21),
        requires=_forward_seed,
    ),
    "T22": Template(
        template_id="T22",
        splits=HOLDOUT,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=_abs(0.1),
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        argument_checks=lambda params: (
            {"tool": GREEN_ROOF_TOOL, "path": "albedo", "op": "eq", "value": params["a"]},
        ),
        render=_render(
            "Under the current forecast but with albedo {a}, what soil moisture is "
            "predicted for the {roof} tomorrow?"
        ),
        draw=from_as_of(_sample_t22),
        requires=_forward_seed,
    ),
    "T23": Template(
        template_id="T23",
        splits=HOLDOUT,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=_abs(0.1),
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        must_not_tools=(WEATHER_TOOL,),
        argument_checks=lambda params: (
            {"tool": GREEN_ROOF_TOOL, "path": "initial_soil_moisture_pct", "op": "eq",
             "value": params["x"]},
        ),
        render=_render(
            "If the {roof}'s soil moisture had been {x} %θ {d} days ago, where would it "
            "be now?"
        ),
        draw=from_as_of(_sample_t23),
        # No seed requirement, and the absence is the template: the initial
        # condition is *stated*, so the run reads no `swc` row to check. What the
        # window's own store does to that seed is the oracle's saturation guard.
    ),
    "T26": Template(
        template_id="T26",
        splits=HOLDOUT,
        roof_pool="P2",
        answer_metric="scored",
        tolerance=EXACT,
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        argument_checks=lambda params: (
            {"tool": GREEN_ROOF_TOOL, "path": "forcings.precip", "op": "present"},
            *(
                ({"tool": GREEN_ROOF_TOOL, "path": "albedo", "op": "eq",
                  "value": params["a"]},)
                if params.get("a") is not None
                else ()
            ),
        ),
        render=_render_by(
            _t26_shape,
            {
                f"T26:{VARIANT_ALBEDO_AND_RAIN}": (
                    "If albedo were {a} and {mm} mm fell on day {offset} of the next "
                    "{d} days, would the {roof} stay above the irrigation threshold?"
                ),
                f"T26:{VARIANT_RAIN_CROSS_ROOF}": (
                    "If albedo were {a} and {mm} mm of rain fell on day {offset} of "
                    "the next {d} days, would the {roof_a} end wetter than the "
                    "{roof_b}?"
                ),
                f"T26:{VARIANT_TRAIN_TAUGHT}": (
                    "If {mm} mm of rain falls on day {offset} of the next {d} days, "
                    "does the {roof_a} end wetter than the {roof_b}?"
                ),
            },
        ),
        draw=from_as_of(_sample_t26),
        requires=_requires_t26,
        strata=_t26_strata,
        surface_shape=_t26_shape,
        # **Every variant answers a boolean, which is why this is `balanced`.**
        # The comparison variants answered the winning roof's canonical name until
        # T114 — a fourth answer shape that neither `case.schema.json` nor the
        # agent's own contract admits, so no draw of them could be emitted at all.
        # They now answer over the pair in the order the question names it, and
        # the pair order is drawn rather than sorted, so both classes are reachable
        # by construction (`decisions.md § A comparison is answered as a boolean
        # over an ordered pair`).
        balanced=True,
    ),
    # --- H. Presentation ------------------------------------------------------
    "T24a": Template(
        template_id="T24a",
        splits=TRAIN_AND_SEEN,
        # Read per draw rather than fixed: the pair variant samples P1 on `swc`
        # and P1f on `outflow`, the overlay P2, and the non-modellable variant
        # deliberately outside every pool (§1.8). The draw loop asks
        # `t24a_roof_pool` for the one that binds on the variant it drew.
        roof_pool=None,
        answer_metric="skipped",
        expected_tool_calls=_calls({"name": PLOT_TOOL},),
        must_not_tools=(TEXT_TO_SQL_TOOL,),
        argument_checks=_t24a_checks,
        render=_render_by(
            _t24a_shape,
            {
                f"T24a:{MEASURED_PAIR}:swc": (
                    "Show me how the soil moisture of the {roof_a} and the {roof_b} "
                    "developed in {month}."
                ),
                f"T24a:{MEASURED_PAIR}:outflow": (
                    "Show me how much water ran off the {roof_a} and the {roof_b} in "
                    "{month}."
                ),
                f"T24a:{MODEL_OVERLAY}": (
                    "Plot the measured soil moisture of the {roof} against the model's "
                    "prediction for {month}."
                ),
                f"T24a:{NON_MODELLABLE_OVERLAY}": (
                    "Plot the {alias}'s measured soil moisture against the model's "
                    "prediction for {month}."
                ),
            },
        ),
        draw=after_window(_build_t24a),
        requires=_requires_t24a,
        strata=_t24a_strata,
        surface_shape=_t24a_shape,
    ),
    "T24b": Template(
        template_id="T24b",
        splits=TRAIN_AND_SEEN,
        answer_metric="scored",
        tolerance=_abs(0.2),
        expected_tool_calls=_calls({"name": TEXT_TO_SQL_TOOL},),
        must_not_tools=(PLOT_TOOL,),
        render=_render(
            "What was the mean soil-moisture difference between the two extensive roofs "
            "in {month}?"
        ),
        draw=after_window(_build_t24b),
        requires=_requires_extensive_pair,
    ),
    # --- I. Modelling availability -------------------------------------------
    "T27": Template(
        template_id="T27",
        splits=TRAIN_AND_SEEN,
        roof_pool="non_modellable",
        answer_metric="skipped",
        expected_tool_calls=_calls({"name": GREEN_ROOF_TOOL},),
        must_not_tools=(TEXT_TO_SQL_TOOL,),
        render=_render_by(
            _t27_shape,
            {
                f"T27:{VARIANT_PREDICTED_MINIMUM}": (
                    "What is the minimum soil moisture predicted for the {alias} over "
                    "the next {d} days?"
                ),
                f"T27:{VARIANT_IRRIGATION}": "Does the {alias} need irrigation tomorrow?",
                f"T27:{VARIANT_FORCED_RAIN}": (
                    "If {mm} mm fell tomorrow, what would the {alias}'s minimum soil "
                    "moisture be over the next {d} days?"
                ),
            },
        ),
        draw=from_as_of(_sample_t27),
        strata=_t27_strata,
        surface_shape=_t27_shape,
        abstains=True,
    ),
}
"""Template id → its catalog entry, keyed exactly like :data:`eval.oracles.ORACLES`."""


T24A_POOLS: Mapping[str, str | None] = {
    MEASURED_PAIR: "P1",
    MODEL_OVERLAY: "P2",
    NON_MODELLABLE_OVERLAY: None,
}
"""Which §1.8 pool binds on each T24a variant, per §1.8's own H rows.

``None`` on the non-modellable overlay because that variant samples the two roofs
the pools deliberately exclude — the pools govern answerable cases, and the point
of this one is the roofs it excludes.
"""


def t24a_roof_pool(params: Mapping[str, Any]) -> str | None:
    """The pool a T24a draw is held to, which is the variant's rather than the family's.

    A plot is only as modellable as its most demanding series and only as widely
    instrumented as its narrowest one, so the pair variant narrows to P1f the
    moment it draws the ``outflow`` table.
    """
    variant = str(params.get("variant", ""))
    if variant == MEASURED_PAIR and str(params.get("table")) == "outflow":
        return "P1f"
    return T24A_POOLS.get(variant)


def roof_pool_for(template: Template, params: Mapping[str, Any]) -> str | None:
    """The pool this *draw* is checked against — the family's, or H's per variant."""
    if template.template_id == "T24a":
        return t24a_roof_pool(params)
    return template.roof_pool


def shapes(template: Template) -> tuple[str, ...]:
    """Every question shape *template* can draw, in the order its strata name them.

    Read off the stratified ``variant`` axis rather than listed, because that axis
    is exactly what fixes a shape: a template with no strata has one shape and it
    is the template itself. This is the completeness key
    :mod:`eval.generation.paraphrases` is checked against — a shape with no
    paraphrase pool is a case that would fall back to nothing.
    """
    fragments = [
        fragment
        for split in ("train", "test_seen", "test_unseen")
        for fragment in template.strata(split)
    ]
    if not fragments:
        return (template.template_id,)
    return tuple(dict.fromkeys(template.shape(fragment) for fragment in fragments))


def gold_card_topics() -> frozenset[str]:
    """Every card the registry names, so a renamed card fails loudly at import."""
    return frozenset(
        topic for template in TEMPLATES.values() for topic in template.gold_cards
    )


def assert_cards_exist() -> None:
    """Every ``gold_cards`` entry resolves in the card store.

    A card recall metric scored against a topic the store does not carry is
    unscoreable rather than failed, and it would look like every candidate missing
    the same card.
    """
    for topic in sorted(gold_card_topics()):
        load_card(topic)


def ledger() -> dict[str, dict[str, int]]:
    """§1.7's ``templates × m`` table, recomputed from the registry.

    Composition is verified against the product rather than steered towards a
    target: the shares in §1.7 are outputs of this table, and a share moves only
    when a template is authored or retired.
    """
    report: dict[str, dict[str, int]] = {}
    for split in ("train", "test_seen", "test_unseen"):
        carried = [t for t in TEMPLATES.values() if split in t.splits]
        report[split] = {
            "templates": len(carried),
            "m": M[split],
            "n": sum(t.instances(split) for t in carried),
            "abstentions": sum(t.abstentions_in(split) for t in carried),
        }
    return report


def abstention_share() -> dict[str, float]:
    """The abstention share per split and overall — **stated, never steered**.

    §1.6 states 13.0 % of train, 12.8 % of test_seen, 28.6 % of test_unseen and
    16.0 % overall, and those numbers are a *consequence* of the five-template
    abstention set plus T24a's non-modellable variant at §1.7's ``m``. Hitting a
    band instead would take a per-template ``m``, which makes ``n`` unverifiable
    against ``templates × m`` — which is the derivation rule §1.7 exists to keep.
    So this reports what the registry produces and a test compares it against the
    stated figures; nothing anywhere adjusts a draw to move it.
    """
    table = ledger()
    shares = {
        split: row["abstentions"] / row["n"] for split, row in table.items() if row["n"]
    }
    total_n = sum(row["n"] for row in table.values())
    total_abstentions = sum(row["abstentions"] for row in table.values())
    shares["overall"] = total_abstentions / total_n
    return shares
