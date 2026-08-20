"""The irrigation rule and its tool: ladder, units, scope and error taxonomy (T070).

Four claims the packet's other tasks do not make. The golden series
(``test_irrigation_golden.py``) proves the port reproduces the controller on the
windows it was captured over; this file proves the ladder is *ordered* rather
than merely correct on those windows, that the unit fix is a conversion and not
a second set of numbers, that both roofs the rule declines leave through
``not_available``, and that a stated-value call touches nothing at all.

The last is the property T16b rests on: identical inputs to T16a with symmetric
must-nots, so the calculator has to answer from the values in the question. A
call that quietly read the database or the forecast would still return the right
boolean, and the case would still pass, while the thing being measured — that
the tool can decide without fetching — had stopped being true. So it is asserted
from the far side: the collaborators raise if they are touched.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from zoneinfo import ZoneInfo

from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.irrigation import (
    DecisionFeatures,
    IRRIGATING_REASONS,
    ReasonCode,
    Regime,
    irrigation_decision,
    simulate_store,
)
from water_assistant_agent.assistant.rules_constants import (
    HEAT_THRESHOLD_C,
    OUTFLOW_EPSILON_MM,
    ROOF_RULES,
    rules_for,
)
from water_assistant_agent.assistant.tools import weather_client as weather_client_module
from water_assistant_agent.assistant.tools.gr2l_client import NON_MODELLABLE_ROOFS
from water_assistant_agent.assistant.tools.irrigation import make_irrigation_tool
from water_assistant_agent.assistant.tools.roofs import ROOFS
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow, WeatherResult
from water_assistant_agent.assistant.tools.swc import mm_to_theta_pct, theta_pct_to_mm
from water_assistant_agent.assistant.tools.weather_client import (
    OpenMeteoError,
    make_weather_client,
)

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# Inside every record, and late enough that each roof has a fresh seed: the swc
# record runs to 2026-04-24 and the station's complete days to 2026-04-26.
AS_OF = datetime(2026, 4, 20, 12, 0, tzinfo=BERLIN)

ROOFS_WITH_RULES = sorted(ROOF_RULES)


# --- Doubles ------------------------------------------------------------------


class SpyWeather:
    """The forecast half, recording every window it was asked for."""

    def __init__(self, precip: float = 0.0, tx: float = 30.0, days: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._precip = precip
        self._tx = tx
        self._days = days

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        days = self._days if self._days is not None else (end - start).days + 1
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source="station",
            data=[
                DailyWeatherRow(
                    Date=(start + timedelta(days=offset)).isoformat(),
                    tm=self._tx - 6.0,
                    tx=self._tx,
                    tn=self._tx - 12.0,
                    rf=55.0,
                    precip=self._precip,
                    w=6.0,
                    gs=2400.0,
                )
                for offset in range(days)
            ],
        )


class _Boom:
    """A collaborator that fails the test by being used at all."""

    def __init__(self, what: str) -> None:
        self._what = what

    async def fetch(self, **_: Any) -> WeatherResult:
        raise AssertionError(f"the stated-value path reached {self._what}")

    def execute_query(self, _query: str) -> Any:
        raise AssertionError(f"the stated-value path reached {self._what}")


class _Raises:
    """A collaborator that raises the given error, for the taxonomy tests."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def fetch(self, **_: Any) -> WeatherResult:
        raise self._error

    def execute_query(self, _query: str) -> Any:
        raise self._error


def _context(
    as_of: datetime = AS_OF,
    *,
    weather: Any = None,
    db: Any = None,
    cache: ResponseCache | None = None,
    factory: Any = None,
) -> ScenarioContext:
    if db is not None:
        return ScenarioContext.bound(
            clock=lambda: as_of, db=db, weather=weather, cache=cache
        )
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=factory or (lambda _db, _cache: weather),
        http_cache=cache,
    )


def _call(ctx: ScenarioContext, roof: str = "irrigated_extensive", **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(make_irrigation_tool(ctx)(roof, **kwargs))


# --- The ladder is ordered, not just correct ----------------------------------

# One features row per rung, each satisfying *every* condition below it as well,
# so what is asserted is the priority and not the branch. Read down the column:
# the store is at or under the wilting point in the first row and above the dry
# threshold in the third, the heat is present from the second row on, and a
# refill is forecast wherever the rung above it does not already decide.
_WILTING, _DRY = 5.0, 10.0
LADDER: list[tuple[ReasonCode, DecisionFeatures]] = [
    (
        ReasonCode.NO_FORECAST,
        DecisionFeatures(
            min_store=None, max_temperature_c=None, will_reach_capacity=True, is_empty=True
        ),
    ),
    (
        ReasonCode.MISSING_VALUES,
        DecisionFeatures(
            min_store=None, max_temperature_c=None, will_reach_capacity=True, has_gaps=True
        ),
    ),
    (
        # Below wilting *and* cold *and* refilling: rung 1 outranks both.
        ReasonCode.BELOW_WILTING_POINT,
        DecisionFeatures(min_store=_WILTING, max_temperature_c=-5.0, will_reach_capacity=True),
    ),
    (
        # Dry enough to act on and refilling, but no heat: rung 2.
        ReasonCode.NO_HEAT_NO_STRESS,
        DecisionFeatures(
            min_store=_WILTING + 0.1,
            max_temperature_c=HEAT_THRESHOLD_C - 0.1,
            will_reach_capacity=True,
        ),
    ),
    (
        # Heat, and refilling, but wetter than the dry threshold: rung 3.
        ReasonCode.SUFFICIENT_MOISTURE,
        DecisionFeatures(
            min_store=_DRY + 0.1, max_temperature_c=HEAT_THRESHOLD_C, will_reach_capacity=True
        ),
    ),
    (
        ReasonCode.REFILL_FORECAST,
        DecisionFeatures(
            min_store=_DRY, max_temperature_c=HEAT_THRESHOLD_C, will_reach_capacity=True
        ),
    ),
    (
        ReasonCode.COOLING_REQUESTED,
        DecisionFeatures(
            min_store=_DRY, max_temperature_c=HEAT_THRESHOLD_C, will_reach_capacity=False
        ),
    ),
]


@pytest.mark.parametrize(("expected", "features"), LADDER, ids=[code for code, _ in LADDER])
def test_the_ladder_stops_at_the_highest_rung_that_applies(
    expected: ReasonCode, features: DecisionFeatures
) -> None:
    """Each rung against a window that would also satisfy every rung below it.

    A ladder whose rungs were merely correct in isolation would pass a test that
    fed each one only its own condition; what the deployed rule states is an
    *order* — never below the wilting point first, cooling last — so each row
    here satisfies everything under it.
    """
    rules = rules_for("irrigated_extensive")
    assert (rules.wilting_pct, rules.dry_pct) == (_WILTING, _DRY), "the fixture roof moved"

    decision = irrigation_decision(features, rules, regime=Regime.PERCENT_THETA)

    assert decision.reason is expected
    assert decision.irrigate is (expected in IRRIGATING_REASONS)


def test_the_wilting_and_dry_comparisons_are_the_controllers_way_round() -> None:
    """``<=`` on the wilting point and ``>`` on the dry threshold, not the reverse.

    Both boundaries are reachable — a store sits exactly on a threshold whenever
    the seed does — and the deployed script fires rung 1 *at* the wilting point
    and declines rung 3 *at* the dry threshold.
    """
    rules = rules_for("irrigated_extensive")
    at_wilting = DecisionFeatures(
        min_store=rules.wilting_pct, max_temperature_c=30.0, will_reach_capacity=False
    )
    at_dry = DecisionFeatures(
        min_store=rules.dry_pct, max_temperature_c=30.0, will_reach_capacity=False
    )

    assert irrigation_decision(at_wilting, rules, regime=Regime.PERCENT_THETA).reason is (
        ReasonCode.BELOW_WILTING_POINT
    )
    assert irrigation_decision(at_dry, rules, regime=Regime.PERCENT_THETA).reason is (
        ReasonCode.COOLING_REQUESTED
    )


def test_every_reason_code_is_classified_as_irrigating_or_not() -> None:
    """``IRRIGATING_REASONS`` and the ladder cannot drift apart."""
    reached = {reason for reason, _ in LADDER}

    assert reached == set(ReasonCode)
    assert IRRIGATING_REASONS <= set(ReasonCode)


# --- Units: one set of numbers, converted once --------------------------------


@pytest.mark.parametrize("roof", ROOFS_WITH_RULES)
@pytest.mark.parametrize("theta_pct", [0.0, 4.0, 12.5, 22.0, 47.3])
def test_the_seed_conversion_round_trips_in_both_regimes(roof: str, theta_pct: float) -> None:
    """%θ → store → %θ, for the unit the tool speaks at its surface."""
    rules = rules_for(roof)

    for regime in Regime:
        store = regime.store_from_theta_pct(theta_pct, rules)
        assert regime.theta_pct_from_store(store, rules) == pytest.approx(theta_pct)

    assert Regime.PERCENT_THETA.store_from_theta_pct(theta_pct, rules) == theta_pct
    assert Regime.MILLIMETRES.store_from_theta_pct(theta_pct, rules) == pytest.approx(
        theta_pct_to_mm(theta_pct, rules.substrate_height_cm)
    )


@pytest.mark.parametrize("roof", ROOFS_WITH_RULES)
def test_the_millimetre_thresholds_are_the_site_values_converted(roof: str) -> None:
    """Not a second set of numbers: the same four, through one conversion.

    This is what makes the decision-diff table readable in %θ — both regimes are
    compared against identical levels, so a flip is a trajectory and never a
    threshold that moved (``specs/agent_architecture/irrigation_decision_diff.md``).
    """
    rules = rules_for(roof)
    deployed = Regime.PERCENT_THETA.thresholds(rules)
    corrected = Regime.MILLIMETRES.thresholds(rules)

    for level in ("wilting", "dry", "capacity", "residual"):
        assert getattr(corrected, level) == pytest.approx(
            theta_pct_to_mm(getattr(deployed, level), rules.substrate_height_cm)
        )
        assert mm_to_theta_pct(
            getattr(corrected, level), rules.substrate_height_cm
        ) == pytest.approx(getattr(deployed, level))


@pytest.mark.parametrize("roof", ROOFS_WITH_RULES)
def test_a_millimetre_of_rain_moves_each_roof_by_its_own_share(roof: str) -> None:
    """The whole content of the unit fix: ``100 / SH_mm`` instead of 1 %θ per mm.

    Same roof, same rain, same (zero) ET — 1.43 %θ per mm on a 7 cm roof and
    0.67 on a 15 cm one, where the deployed balance moves every roof by exactly
    one point whatever its depth.
    """
    rules = rules_for(roof)
    seed_pct = 8.0
    rain = [0.0, 1.0]
    no_et = [0.0, 0.0]

    moved = {}
    for regime in Regime:
        series = simulate_store(
            rain,
            no_et,
            initial=regime.store_from_theta_pct(seed_pct, rules),
            thresholds=regime.thresholds(rules),
        )
        moved[regime] = (
            regime.theta_pct_from_store(series.store[1], rules)
            - regime.theta_pct_from_store(series.store[0], rules)
        )

    assert moved[Regime.PERCENT_THETA] == pytest.approx(1.0)
    assert moved[Regime.MILLIMETRES] == pytest.approx(
        100.0 / (rules.substrate_height_cm * 10.0)
    )


def test_the_tool_reports_soil_moisture_in_the_sites_own_unit() -> None:
    """Millimetres stay internal (``agent_architecture.md`` §3.5).

    The semi-intensive roof at 8 %θ holds 12 mm, so a payload that leaked the
    store would read 12.0 here — caught, rather than rounded into agreement as it
    would be on a roof whose two numbers sit close together.
    """
    result = _call(
        _context(weather=SpyWeather(precip=0.0, tx=30.0)),
        "semi_intensive",
        soil_moisture_pct=8.0,
        max_temperature_c=30.0,
        forecast_precip_mm=0.0,
    )

    assert result["features"]["min_swc_pct"] == pytest.approx(8.0)


# --- The stated-value path touches nothing ------------------------------------


def test_the_stated_path_reaches_neither_the_database_nor_the_forecast() -> None:
    """T16b's premise, asserted from the far side.

    Both collaborators raise on use, so a success here is proof the call issued
    no I/O rather than an inference from the answer being right.
    """
    ctx = _context(weather=_Boom("the weather client"), db=_Boom("the database"))

    result = _call(
        ctx, "irrigated_extensive", soil_moisture_pct=4.0, max_temperature_c=30.0,
        forecast_precip_mm=0.0,
    )

    assert result["status"] == "success"
    assert result["inputs"] == "stated"
    assert result["irrigate"] is True
    assert result["reason"] == ReasonCode.BELOW_WILTING_POINT
    # Nothing was measured, so nothing is disclosed as measured.
    assert result["seed"] is None
    assert result["weather_source"] is None
    assert result["window_start"] is None


def test_the_stated_refill_conjunct_is_the_rain_against_the_deficit() -> None:
    """The second entry point's own arithmetic, at the boundary either side.

    A 15 cm roof at 12 %θ holds 18 mm against a 33 mm capacity, so 15 mm of rain
    is exactly the deficit: the roof reaches capacity and does not exceed it,
    which is not a refill. Two hundredths of a millimetre more is. The roof sits
    between its wilting point and its dry threshold so the ladder reaches rung 4
    at all.
    """
    rules = rules_for("semi_intensive")
    deficit = rules.capacity_mm - theta_pct_to_mm(12.0, rules.substrate_height_cm)
    assert deficit == pytest.approx(15.0)
    assert rules.wilting_pct < 12.0 <= rules.dry_pct

    ctx = _context(weather=_Boom("the weather client"), db=_Boom("the database"))
    stated = {"soil_moisture_pct": 12.0, "max_temperature_c": 30.0}

    exact = _call(ctx, "semi_intensive", forecast_precip_mm=deficit, **stated)
    over = _call(
        ctx, "semi_intensive", forecast_precip_mm=deficit + 2 * OUTFLOW_EPSILON_MM, **stated
    )

    assert exact["features"]["will_reach_capacity"] is False
    assert exact["reason"] == ReasonCode.COOLING_REQUESTED
    assert over["features"]["will_reach_capacity"] is True
    assert over["reason"] == ReasonCode.REFILL_FORECAST


@pytest.mark.parametrize(
    "kwargs",
    [
        {"soil_moisture_pct": 12.0},
        {"max_temperature_c": 30.0},
        {"soil_moisture_pct": 12.0, "forecast_precip_mm": 3.0},
    ],
)
def test_a_partly_stated_call_is_refused_before_any_fetch(kwargs: dict[str, float]) -> None:
    """Half measured and half assumed is an answer no disclosure could describe."""
    ctx = _context(weather=_Boom("the weather client"), db=_Boom("the database"))

    result = _call(ctx, "irrigated_extensive", **kwargs)

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_argument"
    assert "forecast_precip_mm" in result["error_details"]


# --- Scope: both roofs the rule declines --------------------------------------


@pytest.mark.parametrize("alias", sorted(NON_MODELLABLE_ROOFS))
def test_a_non_modellable_roof_is_a_scope_limit_not_a_failure(alias: str) -> None:
    """Every spelling of both roofs, through the set GR2L shares.

    The set is shared and the wording is not: an answer about irrigating the
    wetland has to say the rule reads a soil store, not that a *simulation*
    cannot start — which is GR2L's reason for declining the same roof.
    """
    result = _call(_context(weather=_Boom("the weather client")), alias)

    assert result["status"] == "not_available"
    assert "irrigation rule" in result["reason"]
    # Nothing failed, so nothing is classified as a failure.
    assert "error_type" not in result


def test_the_two_declined_roofs_are_the_two_the_rule_has_no_thresholds_for() -> None:
    """The scope set and ``ROOF_RULES`` are two statements of one fact."""
    declined = {ROOFS[alias].name for alias in NON_MODELLABLE_ROOFS if alias in ROOFS}

    assert declined == {"gravel", "wetland"}
    assert set(ROOF_RULES).isdisjoint(declined)


def test_a_roof_nobody_has_heard_of_is_an_argument_fault() -> None:
    result = _call(_context(weather=_Boom("the weather client")), "roof garden")

    assert result["error_type"] == "invalid_argument"
    assert "irrigated_extensive" in result["error_details"]


# --- The modelled path, and the three outcomes --------------------------------


def test_the_modelled_path_fetches_the_refill_horizon_from_today() -> None:
    """One window, sized by the rule's own constant and opening at ``ctx.as_of``."""
    weather = SpyWeather(precip=0.0, tx=30.0)

    result = _call(_context(weather=weather), "irrigated_extensive")

    assert weather.calls == [("2026-04-20", "2026-04-26")]
    assert result["inputs"] == "modelled"
    assert result["seed"]["source"] == "measured"
    assert result["weather_source"] == "station"
    assert result["features"]["decision_horizon_hours"] == 48
    assert result["features"]["refill_horizon_hours"] == 168


def test_a_window_with_no_seed_is_not_available() -> None:
    """The record's own edge: before the first reading there is nothing to start from."""
    before_the_record = datetime(2024, 1, 1, 12, 0, tzinfo=BERLIN)

    result = _call(
        _context(before_the_record, weather=SpyWeather()), "irrigated_extensive"
    )

    assert result["status"] == "not_available"
    assert "soil-moisture" in result["reason"]


def test_a_failed_forecast_is_upstream() -> None:
    result = _call(_context(weather=_Raises(OpenMeteoError("down"))), "irrigated_extensive")

    assert result["status"] == "error"
    assert result["error_type"] == "upstream"


def test_a_failed_database_read_is_upstream() -> None:
    ctx = _context(weather=SpyWeather(), db=_Raises(RuntimeError("no database")))

    result = _call(ctx, "irrigated_extensive")

    assert result["error_type"] == "upstream"
    assert "soil moisture" in result["error_details"]


@pytest.mark.parametrize("soil_moisture_pct", [-1.0, 101.0])
def test_an_impossible_soil_moisture_is_an_argument_fault(soil_moisture_pct: float) -> None:
    result = _call(
        _context(weather=_Boom("the weather client")),
        "irrigated_extensive",
        soil_moisture_pct=soil_moisture_pct,
        max_temperature_c=30.0,
        forecast_precip_mm=0.0,
    )

    assert result["error_type"] == "invalid_argument"


def test_an_empty_forecast_is_upstream_and_never_a_decision() -> None:
    """A window that came back with no days is a fault, not a `no_forecast` answer.

    The ladder's degenerate rung exists for a series that reaches it empty; a
    *fetch* that returned nothing is the tool's own dependency failing, and
    reporting it as "no forecast, so no irrigation" would read as a decision.
    """
    result = _call(_context(weather=SpyWeather(days=0)), "irrigated_extensive")

    assert result["status"] == "error"
    assert result["error_type"] == "upstream"


# --- Replay: an irrigation case leaves nothing behind -------------------------


def test_an_irrigation_case_replays_with_no_cache_entry_and_no_live_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The packet's own exit clause, over the real collaborators.

    Everything the rule needs runs local: the calculator is Python, the seed is
    the pinned database, and a stated-value case fetches nothing at all. So the
    case is run through a real :class:`ResponseCache` and the real weather
    composite in replay mode, and the cache directory is *counted* afterwards —
    zero files, not "no cache hit observed". The live seam is replaced by
    something that fails the test if it is reached, so "zero live calls" is not
    an inference either.
    """

    async def no_live_call(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an irrigation case reached the network")

    monkeypatch.setattr(weather_client_module, "fetch_daily_weather", no_live_call)

    cache = ResponseCache(tmp_path / "cache")
    ctx = _context(
        cache=cache,
        factory=lambda db, http_cache: make_weather_client(db, http_cache, allow_live=False),
    )

    result = _call(
        ctx,
        "semi_intensive",
        soil_moisture_pct=12.0,
        max_temperature_c=31.0,
        forecast_precip_mm=1.0,
    )

    assert result["status"] == "success"
    assert result["irrigate"] is True
    assert result["reason"] == ReasonCode.COOLING_REQUESTED
    assert list((tmp_path / "cache").iterdir()) == []
