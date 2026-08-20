"""The deployed controller's bucket model and decision ladder, in Python.

A port of ``smart_irrigation.py``'s water balance and ``decide_substrate_roof``'s
rule (``findings.md`` § External sources on this machine records where that file
lives; ``irrigation_tool.md`` holds the physics). **Not GR2L** — one store where
GR2L has two, a stress coefficient read off the previous step, ET subtracted
before the cap, and a flat field capacity on all three substrate roofs. The two
models disagree about when a roof overflows and neither is a bug in the other
(``decisions.md`` § The irrigation calculator).

**Four properties look like defects and are preserved deliberately**, because
this tool exists to reproduce the site's decisions rather than to improve them:

1. ``ks`` reads the **previous** step, so the stress coefficient lags the state
   it throttles by one step.
2. There is **no lower floor** — the store may fall below the residual and below
   zero. GR2L floors at ``Ssubmin``; this model does not.
3. The **seed step computes no ET** but *does* compute an outflow from the raw
   initial value before capping. That outflow is a seeding artifact, which is
   why the refill window skips it.
4. **ET is subtracted before the cap**, not after the overflow step.

**The store's unit is a parameter, not a constant** (:class:`Regime`). The
deployed controller holds the store in %VWC and adds millimetres of rain and ET
to it; carrying the balance into millimetres rescales each roof's response to
rain by ``100 / SH_mm``, which changes what the model predicts and with it some
decisions. Millimetres are what this tool runs — and the deployed regime stays
reachable, because the correction is *measured* rather than absorbed: the same
window replays through both with everything else held fixed, and the
decision-diff harness reports where the two disagree
(``agent_architecture.md`` §3.5). The trigger levels themselves are the site's
and are **not** re-derived to absorb the change
(``decisions.md`` § No fitted correction between the instrument and the oracle).
Every threshold below comes from :mod:`.rules_constants`, which authors it in the
unit the site states it in and converts it once; nothing here converts anything
twice.

**What is dropped.** The wetland's branch — an L6 lysimeter level in kg, its own
constants, and the extraction's open-water (``water_limited=False``) bucket run —
is out of scope with the roof itself (``NON_MODELLABLE_ROOFS``), so
:func:`simulate_store` carries no unthrottled arm. So are the valve schedule, the
CSV layer and the German recommendation strings: the ladder returns a
:class:`ReasonCode` and the wording is a rendering table, so prose and logic
cannot drift.

**Returns a decision, never a volume.** The dose is site policy — a per-roof
constant the answer states — not something this module computes
(``agent_architecture.md`` §3.5).

Pure: no I/O, no clock, no settings, no ADK, no LLM. An oracle imports the very
functions the tool calls.
"""

import dataclasses
import enum
import math
from collections.abc import Sequence

from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    HEAT_THRESHOLD_C,
    OUTFLOW_EPSILON_MM,
    REFILL_HORIZON_HOURS,
    RoofRules,
    horizon_rows,
    rules_for,
)
from water_assistant_agent.assistant.tools.swc import mm_to_theta_pct, theta_pct_to_mm


class ReasonCode(enum.StrEnum):
    """Why the ladder stopped where it did — one code per rung.

    The deployed script carries a German recommendation string inline at each
    rung, duplicated once per roof. Here the rung returns a code and the DE/EN
    text is a rendering table shared with the ops manual
    (``irrigation_tool.md`` § The rule), so the wording and the logic cannot
    drift apart. A ``StrEnum`` so a tool payload serializes to the code itself.
    """

    BELOW_WILTING_POINT = "below_wilting_point"
    """Rung 1: the store drops to or below the wilting point inside the horizon."""

    NO_HEAT_NO_STRESS = "no_heat_no_stress"
    """Rung 2: no heat is forecast, so no cooling is wanted."""

    SUFFICIENT_MOISTURE = "sufficient_moisture"
    """Rung 3: cooling is wanted, but the substrate is wetter than the dry threshold."""

    REFILL_FORECAST = "refill_forecast"
    """Rung 4: cooling is wanted and the roof is dry, but rain will refill it."""

    COOLING_REQUESTED = "cooling_requested"
    """Rung 5: heat, a dry roof and no refill in sight — irrigate for the cooling."""

    NO_FORECAST = "no_forecast"
    """Degenerate: the window holds no rows to decide on."""

    MISSING_VALUES = "missing_values"
    """Degenerate: the window holds a gap, and a gap is not a dry roof."""


IRRIGATING_REASONS: frozenset[ReasonCode] = frozenset(
    {ReasonCode.BELOW_WILTING_POINT, ReasonCode.COOLING_REQUESTED}
)
"""The two rungs that say yes.

Stated once so a caller can check a code without re-deriving the ladder, and so
a rung added later cannot be silently omitted from a rendering table.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class Thresholds:
    """The four levels one run turns on, in whatever unit the store is held in.

    Two of them bound the stress coefficient (:attr:`residual`,
    :attr:`capacity`) and two are the ladder's triggers. :attr:`capacity` does
    both jobs: it is the overflow level *and* the coefficient's upper end, which
    is the pair of constants the extraction carries twice in two units
    (``SWC_CAPACITY`` and ``THETA_FIELD_CAPACITY``) collapsed into one
    (``irrigation_tool.md`` § Units).

    Built by :meth:`Regime.thresholds` from a :class:`~.rules_constants.RoofRules`
    rather than by hand: the unit regime is the only thing that varies between
    two runs of the same roof, and it varies in one place.
    """

    wilting: float
    dry: float
    capacity: float
    residual: float


class Regime(enum.Enum):
    """Which unit the store is held in — the one variable the unit fix moves.

    The deployed controller holds the store in %VWC and adds millimetres of rain
    and ET to it, so a millimetre of rain raises every roof by one point of %θ
    whatever its depth. That is the bug ``irrigation_tool.md`` § Units names, and
    correcting it rescales each roof's **response to rain** by ``100 / SH_mm``:
    about 0.7× on the 7 cm extensive roofs and 1.5× on the 15 cm semi-intensive.
    Deep roofs become harder to move and shallow ones easier, which is a change
    in what the model predicts and therefore in some decisions.

    **The trigger levels are not re-derived to absorb it.** They are the site's,
    carried verbatim from the deployed controller, and a set rescaled here would
    be reproducible only from this repository where the deployed ones are
    reproducible from the site's own documentation
    (``decisions.md`` § No fitted correction between the instrument and the
    oracle). Whether to re-tune is the site's call, made against the
    decision-diff evidence.

    Which is why the regime is an argument rather than a repaired constant: the
    correction is **measured, not absorbed**. The decision-diff harness replays
    one historical window through both members with the same forcing, the same
    ET0 and the same trigger levels, so a decision that flips is attributable to
    unit handling and to nothing else (``agent_architecture.md`` §3.5).
    """

    PERCENT_THETA = "percent_theta"
    """The deployed controller's: a store in %VWC with millimetres added to it."""

    MILLIMETRES = "millimetres"
    """The corrected balance: one unit for the store, the rain and the ET alike."""

    @property
    def unit(self) -> str:
        """How a value in this regime is spelled in a disclosure."""
        return "%θ" if self is Regime.PERCENT_THETA else "mm"

    def thresholds(self, rules: RoofRules) -> Thresholds:
        """This roof's four levels in this regime's unit.

        Both sets are the same four site constants: :mod:`.rules_constants`
        authors them in %θ, as the site states them, and converts once through
        :func:`~.tools.swc.theta_pct_to_mm`. Nothing is converted twice and
        nothing is re-derived.
        """
        if self is Regime.PERCENT_THETA:
            return Thresholds(
                wilting=rules.wilting_pct,
                dry=rules.dry_pct,
                capacity=rules.capacity_pct,
                residual=rules.residual_pct,
            )
        return Thresholds(
            wilting=rules.wilting_mm,
            dry=rules.dry_mm,
            capacity=rules.capacity_mm,
            residual=rules.residual_mm,
        )

    def store_from_theta_pct(self, theta_pct: float, rules: RoofRules) -> float:
        """A measured %θ reading as this regime's store value.

        The seed is read from the sensor in %θ whichever regime runs, so this is
        the second half of the unit fix: the deployed regime holds the store in
        the sensor's own unit and has nothing to convert, and the millimetre
        regime converts it once, against the roof's own substrate height.
        """
        if self is Regime.PERCENT_THETA:
            return theta_pct
        return theta_pct_to_mm(theta_pct, rules.substrate_height_cm)

    def theta_pct_from_store(self, store: float, rules: RoofRules) -> float:
        """The inverse: this regime's store value restated as %θ.

        Millimetres stay internal and the site's own unit is what a surface
        speaks (``agent_architecture.md`` §3.5), so every value the tool reports
        comes back through here rather than through a conversion of its own.
        """
        if self is Regime.PERCENT_THETA:
            return store
        return mm_to_theta_pct(store, rules.substrate_height_cm)


@dataclasses.dataclass(frozen=True, slots=True)
class StoreSeries:
    """One roof's simulated state, step by step.

    Every field is as long as the forcing. :attr:`et_actual` is ``None`` on the
    seed step, where the extraction has ``NaN``: that step initialises the store
    and computes no ET at all (property 3 above).
    """

    store: tuple[float, ...]
    outflow: tuple[float, ...]
    et_actual: tuple[float | None, ...]
    ks: tuple[float, ...]


def stress_coefficient(store: float, thresholds: Thresholds) -> float:
    """FAO-56 style water-stress coefficient, clipped to [0, 1].

    ``clip((S − S_r) / (S_fc − S_r), 0, 1)``, and *store* is the **previous**
    step's — the lag is the deployed controller's and is preserved (property 1).
    The extraction writes the same expression over fractions
    (``(θ/100 − 0.025) / (0.22 − 0.025)``); over %θ it is the identical algebra
    with the factor of 100 cancelled.
    """
    span = thresholds.capacity - thresholds.residual
    return min(1.0, max(0.0, (store - thresholds.residual) / span))


def simulate_store(
    precipitation: Sequence[float],
    et0: Sequence[float],
    *,
    initial: float,
    thresholds: Thresholds,
) -> StoreSeries:
    """Run the one-store bucket over *precipitation* and *et0*, step by step.

    ``_simulate_store`` in the extraction, line for line. The step is the
    forcing's own — the site runs this hourly over ICON rows and this system runs
    it daily — so nothing here reads a horizon; :func:`summarize` is where hours
    become rows.

    *initial*, *thresholds* and the store are one unit (:class:`Regime`);
    *precipitation* and *et0* are always millimetres. In the deployed regime that
    mismatch is the whole of the bug the unit fix corrects, and it is reproduced
    faithfully rather than quietly repaired.

    The order of the three operations is load-bearing and is the extraction's:
    the stress coefficient off the previous step, ET subtracted from the level,
    and only then the cap and the overflow (properties 1 and 4). The seed step
    takes *initial* as its level unchanged, adding no rain and subtracting no ET
    (property 3), and the store has no lower bound at any step (property 2).

    Raises:
        ValueError: *precipitation* and *et0* differ in length — a forcing whose
            two halves disagree about how many steps there are would run to the
            shorter one and silently drop the tail.
    """
    if len(precipitation) != len(et0):
        raise ValueError(
            f"precipitation has {len(precipitation)} steps and et0 has {len(et0)}; "
            "the two forcings must cover the same steps."
        )

    store: list[float] = []
    outflow: list[float] = []
    et_actual: list[float | None] = []
    ks: list[float] = []

    for index in range(len(precipitation)):
        previous = initial if index == 0 else store[index - 1]
        step_ks = stress_coefficient(previous, thresholds)
        ks.append(step_ks)

        if index == 0:
            # The seed step only initialises the store from the measurement.
            et_actual.append(None)
            level = initial
        else:
            step_et = et0[index] * step_ks
            et_actual.append(step_et)
            level = previous + precipitation[index] - step_et

        outflow.append(max(level - thresholds.capacity, 0.0))
        store.append(min(level, thresholds.capacity))

    return StoreSeries(
        store=tuple(store),
        outflow=tuple(outflow),
        et_actual=tuple(et_actual),
        ks=tuple(ks),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class DecisionFeatures:
    """Everything the ladder reads, and nothing else.

    One shape for both entry points: a simulated series reduced by
    :func:`summarize`, or the three values a caller stated
    (:func:`features_from_stated_values`). The comparison chain itself therefore
    exists exactly once (``irrigation_tool.md`` § The rule).
    """

    min_store: float | None
    """Driest step inside the decision horizon, in the store's own unit."""

    max_temperature_c: float | None
    """Warmest step inside the decision horizon."""

    will_reach_capacity: bool
    """The roof refills on its own inside the refill horizon."""

    refill_step: int | None = None
    """Index of the first refilling step, for disclosure — never for the decision.

    Computed over the refill window, so the seed step's artificial outflow cannot
    be reported as the day the roof fills. The extraction's own
    ``first_outflow_date`` scans from index 0 against ``> 0``, which can name the
    seed step; that is a message, never a decision, and it is not carried here.
    """

    has_gaps: bool = False
    """The window holds a missing value, so no threshold can be read off it."""

    is_empty: bool = False
    """The window holds no steps at all."""


def summarize(
    series: StoreSeries,
    temperature_c: Sequence[float],
    *,
    step_hours: float,
) -> DecisionFeatures:
    """Reduce a simulated run to the features the ladder reads.

    **The two window conventions are the point of this function**
    (``irrigation_tool.md`` § The rule). Store and temperature are read over
    ``[0 : decision_horizon]``, *including* the seed step; outflow over
    ``[1 : refill_horizon]``, *excluding* it, because the seed step's outflow is
    an initialisation artifact rather than a forecast that the roof will fill.

    The horizons are authored in hours (:mod:`.rules_constants`) and become row
    counts here at the series' own *step_hours*, so 48 h is 2 daily rows here and
    48 hourly rows at the site.

    Raises:
        ValueError: *series* and *temperature_c* differ in length, or
            *step_hours* is coarser than a horizon (:func:`horizon_rows`).
    """
    if len(series.store) != len(temperature_c):
        raise ValueError(
            f"the simulated series has {len(series.store)} steps and the temperature "
            f"forcing has {len(temperature_c)}; both must cover the same steps."
        )

    decision_rows = horizon_rows(DECISION_HORIZON_HOURS, step_hours=step_hours)
    refill_rows = horizon_rows(REFILL_HORIZON_HOURS, step_hours=step_hours)

    stores = series.store[:decision_rows]
    temperatures = tuple(temperature_c[:decision_rows])
    outflows = series.outflow[1:refill_rows]

    if not stores or not temperatures:
        return DecisionFeatures(
            min_store=None,
            max_temperature_c=None,
            will_reach_capacity=False,
            is_empty=True,
        )

    values = stores + temperatures
    if any(value is None or math.isnan(value) for value in values):
        return DecisionFeatures(
            min_store=None,
            max_temperature_c=None,
            will_reach_capacity=False,
            has_gaps=True,
        )

    refill_step = next(
        (
            index
            for index, value in enumerate(outflows, start=1)
            if value > OUTFLOW_EPSILON_MM
        ),
        None,
    )
    return DecisionFeatures(
        min_store=min(stores),
        max_temperature_c=max(temperatures),
        will_reach_capacity=refill_step is not None,
        refill_step=refill_step,
    )


def features_from_stated_values(
    rules: RoofRules,
    *,
    soil_moisture_pct: float,
    max_temperature_c: float,
    forecast_precip_mm: float,
    regime: Regime = Regime.MILLIMETRES,
) -> DecisionFeatures:
    """The ladder's features from values a caller stated, with no simulation.

    The second entry point. Soil moisture arrives in %θ as everywhere else and
    becomes the store's own unit once; the refill conjunct is the stated rain
    against the roof's deficit to capacity rather than a modelled outflow series,
    compared through the same :data:`~.rules_constants.OUTFLOW_EPSILON_MM` the
    modelled path uses so one convention decides "will it refill?" on both paths.

    A stated call carries no ET0, so the rain is taken at face value: the refill
    it implies is an upper bound, where the modelled path spends part of that
    rain on evaporation before the roof fills.
    """
    thresholds = regime.thresholds(rules)
    store = regime.store_from_theta_pct(soil_moisture_pct, rules)
    deficit = thresholds.capacity - store
    return DecisionFeatures(
        min_store=store,
        max_temperature_c=max_temperature_c,
        will_reach_capacity=forecast_precip_mm - deficit > OUTFLOW_EPSILON_MM,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Decision:
    """Whether to irrigate, why, and the dose the site would apply.

    The dose is stated **alongside** the decision for disclosure, never computed
    from it: the deployed algorithm has no volume calculation, so the fixed
    per-roof constant is the only honest figure to quote
    (``decisions.md`` § The irrigation calculator).
    """

    roof_type: str
    irrigate: bool
    reason: ReasonCode

    dose_mm: float | None
    """The roof's dose as a depth — ``None`` until the site supplies it."""

    valve_minutes: int
    """How long the deployed controller opens this roof's valve."""


def irrigation_decision(
    features: DecisionFeatures,
    rules: RoofRules,
    *,
    regime: Regime = Regime.MILLIMETRES,
) -> Decision:
    """Walk the priority ladder over *features* and return the decision.

    A fixed order, no weights and no model (``irrigation_tool.md`` § The rule):
    never below the wilting point, then no cooling without heat, then not while
    the substrate is wet enough, then not if rain will refill the roof anyway,
    and otherwise irrigate for the cooling. A degenerate window — empty, or with
    a gap in it — never irrigates: a gap is not a dry roof.

    *features* and *rules* must be read in the same *regime*, which is what
    building both through :class:`Regime` guarantees.
    """
    thresholds = regime.thresholds(rules)

    def decide(irrigate: bool, reason: ReasonCode) -> Decision:
        return Decision(
            roof_type=rules.roof,
            irrigate=irrigate,
            reason=reason,
            dose_mm=rules.dose_mm,
            valve_minutes=rules.valve_minutes,
        )

    if features.is_empty:
        return decide(False, ReasonCode.NO_FORECAST)
    if features.has_gaps or features.min_store is None or features.max_temperature_c is None:
        return decide(False, ReasonCode.MISSING_VALUES)
    if features.min_store <= thresholds.wilting:
        return decide(True, ReasonCode.BELOW_WILTING_POINT)
    if features.max_temperature_c < HEAT_THRESHOLD_C:
        return decide(False, ReasonCode.NO_HEAT_NO_STRESS)
    if features.min_store > thresholds.dry:
        return decide(False, ReasonCode.SUFFICIENT_MOISTURE)
    if features.will_reach_capacity:
        return decide(False, ReasonCode.REFILL_FORECAST)
    return decide(True, ReasonCode.COOLING_REQUESTED)


@dataclasses.dataclass(frozen=True, slots=True)
class RoofRun:
    """One roof simulated over one window, and what was decided from it."""

    roof_type: str
    regime: Regime
    seed: float
    """The day-1 store, in :attr:`regime`'s unit."""

    thresholds: Thresholds
    series: StoreSeries
    features: DecisionFeatures
    decision: Decision


def run_roof(
    roof_type: str,
    *,
    precipitation_mm: Sequence[float],
    et0_mm: Sequence[float],
    temperature_c: Sequence[float],
    seed_theta_pct: float,
    step_hours: float,
    regime: Regime = Regime.MILLIMETRES,
) -> RoofRun:
    """Simulate *roof_type* over one forcing window and decide from the result.

    The three steps in the one order that is correct — bucket, windows, ladder —
    so the tool, the decision-diff harness and an oracle all reach a decision
    through the same call rather than each composing the parts itself.

    The seed arrives in %θ, the unit the sensors and the ops manual speak, and is
    converted (or not) exactly once by *regime*.

    Raises:
        ValueError: an unknown *roof_type* (:func:`~.rules_constants.rules_for`
            names the three the rule applies to), forcings of unequal length, or
            a *step_hours* coarser than a horizon.
    """
    rules = rules_for(roof_type)
    seed = regime.store_from_theta_pct(seed_theta_pct, rules)
    thresholds = regime.thresholds(rules)
    series = simulate_store(
        precipitation_mm, et0_mm, initial=seed, thresholds=thresholds
    )
    features = summarize(series, temperature_c, step_hours=step_hours)
    return RoofRun(
        roof_type=rules.roof,
        regime=regime,
        seed=seed,
        thresholds=thresholds,
        series=series,
        features=features,
        decision=irrigation_decision(features, rules, regime=regime),
    )
