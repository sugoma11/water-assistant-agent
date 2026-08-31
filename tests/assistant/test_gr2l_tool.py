"""The GR2L tool: scope, seed, counterfactuals, bounds and error taxonomy (T051, T054).

Everything here runs against the real pinned ``data/water.duckdb``, for the same
reason ``test_weather_station.py`` gives: the seed and the measured comparison
*are* that file, and a fixture would pin an aggregation over rows this deployment
never serves. Only the two things outside the repository are replaced — the GR2L
service and Open-Meteo — and each is replaced by something that **records what it
was asked**, so a claim about what reached the model is asserted from the far
side rather than inferred from the answer.

Expected values are hand-computed in Python from the raw rows (``statistics``,
``zoneinfo``), never by running a second copy of the implementation's own query.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import statistics
import threading
import typing
import weakref
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import httpx
import pytest
from google.adk.tools.function_tool import FunctionTool

from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools import gr2l as gr2l_module
from water_assistant_agent.assistant.tools import gr2l_client as gr2l_client_module
from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    ROOF_PRESETS,
    Gr2lConfigError,
)
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    Gr2lResultRow,
    WeatherResult,
)
from water_assistant_agent.assistant.tools.swc import (
    STALE_AFTER_DAYS,
    mm_to_theta_pct,
    theta_pct_to_mm,
)
from water_assistant_agent.assistant.tools.weather_client import (
    OpenMeteoError,
    make_weather_client,
)

DB_PATH = "data/water.duckdb"
BERLIN = ZoneInfo("Europe/Berlin")

# The `swc` record's own edges, restated so a failure names the edge that moved.
FIRST_SWC_DAY = date(2024, 7, 23)
LAST_SWC_DAY = date(2026, 4, 24)

# Past the record's end, so a retrospective window sees all of it.
AFTER_RECORD = datetime(2026, 5, 1, 0, 0, tzinfo=BERLIN)

# A window the station covers whole, well inside every record.
WINDOW = (date(2025, 6, 10), date(2025, 6, 16))


# --- Doubles ------------------------------------------------------------------


class SpyWeather:
    """The weather half, recording every window it was asked for.

    Rows are flat and arbitrary — these tests are about what the wrapper does
    *with* weather, not about the weather itself, which is ``test_weather_station``'s
    subject. ``precip`` is 0.0 so a forced day stands out unambiguously.
    """

    def __init__(self, source: str = "archive") -> None:
        self.calls: list[tuple[str, str]] = []
        self._source = source

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        self.calls.append((start_date, end_date))
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        return WeatherResult(
            latitude=51.353484,
            longitude=12.432152,
            timezone="Europe/Berlin",
            source=self._source,
            data=[
                DailyWeatherRow(
                    Date=(start + timedelta(days=offset)).isoformat(),
                    tm=15.0,
                    tx=20.0,
                    tn=10.0,
                    rf=70.0,
                    precip=0.0,
                    w=5.0,
                    gs=1500.0,
                )
                for offset in range((end - start).days + 1)
            ],
        )


class SpyGr2l:
    """GR2L itself, recording the rows and parameters it was handed.

    Returns a deterministic ramp so every derived number — retention, the driest
    day, the deviation statistics — is hand-computable from the request alone.
    """

    def __init__(self, ssub: float = 10.0, step: float = 0.0) -> None:
        self.rows: list[list[DailyWeatherRow]] = []
        self.parameters: list[Any] = []
        self._ssub = ssub
        self._step = step

    async def __call__(self, rows: list[DailyWeatherRow], parameters: Any, **kwargs: Any) -> Any:
        self.rows.append(list(rows))
        self.parameters.append(parameters)
        return [
            Gr2lResultRow(
                Date=row.Date,
                ET_PM=1.0,
                Ssub=self._ssub + index * self._step,
                Sret=0.0,
                Qdown=None if index == 0 else 0.0,
                Qup=None if index == 0 else 0.0,
                OUT=None if index == 0 else row.precip * 0.5,
                ET=0.8,
            )
            for index, row in enumerate(rows)
        ]


def _context(
    as_of: datetime = AFTER_RECORD,
    *,
    weather: Any = None,
    cache: ResponseCache | None = None,
    factory: Any = None,
) -> ScenarioContext:
    return ScenarioContext(
        clock=lambda: as_of,
        db_path=DB_PATH,
        weather_client_factory=factory or (lambda db, _cache: weather),
        http_cache=cache,
    )


def _tool(ctx: ScenarioContext | None = None) -> Any:
    return gr2l_module.make_green_roof_balance_tool(ctx or _context())


def _run(tool: Any, roof: str = "non_irrigated_extensive", **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("start_date", WINDOW[0].isoformat())
    kwargs.setdefault("end_date", WINDOW[1].isoformat())
    return asyncio.run(tool(roof, **kwargs))


def _gr2l(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> SpyGr2l:
    spy = SpyGr2l(**kwargs)
    monkeypatch.setattr(gr2l_module, "run_gr2l", spy)
    return spy


# --- `roof_type` stays a plain `str` (T051) ----------------------------------


def test_roof_type_is_annotated_as_a_plain_str() -> None:
    """The annotation is pinned, because the abstention family depends on it.

    A ``Literal[...]`` here would be rendered into the function declaration as a
    schema ``enum`` (ADK derives the declaration from the signature), and family
    I asks the model to request a roof the tool declines — unaskable if the
    declaration will not let it name one. Scope is decided in code against
    ``NON_MODELLABLE_ROOFS`` instead (``agent_architecture.md`` §3.4).
    """
    annotations = typing.get_type_hints(_tool())

    assert annotations["roof_type"] is str
    assert typing.get_origin(annotations["roof_type"]) is not typing.Literal


def test_the_declaration_carries_no_enum_for_roof_type() -> None:
    """The same claim from the far side: what ADK actually sends the model.

    Asserting the annotation alone would not catch a declaration built from
    something else (a ``Field`` constraint, a hand-written schema), and the
    declaration is the thing the model reads.
    """
    schema = FunctionTool(_tool())._get_declaration().parameters_json_schema
    roof_type = schema["properties"]["roof_type"]

    assert roof_type["type"] == "string"
    assert "enum" not in roof_type
    # And nothing else smuggled the roof list in either.
    assert "enum" not in repr(schema)


def test_the_wetland_preset_is_retained_and_unchanged() -> None:
    """Out of scope is not the same as deleted — `gr2l_roof_presets_sha256` pins it.

    Layer 2 still accepts the wetland, and the preset's values are part of §5's
    pinned surface, so removing them would move a pin (and with it the GR2L
    canary's comparability) for a branch no layer-1 call takes.
    """
    assert ROOF_PRESETS["wetland"] == {
        "SH": 1.7,
        "Ssubmin": 1.3,
        "Ssubmax": 90,
        "Sret": 0,
        "Sretmax": 0,
        "theta_02": 0,
        "kg": 1,
        "albedo": 0.06,
        "open_water": True,
    }


# --- %θ ↔ mm, per roof (T054) -------------------------------------------------


@pytest.mark.parametrize("roof_type", sorted(ROOF_PRESETS))
@pytest.mark.parametrize("theta_pct", [0.0, 1.22, 7.55, 22.85, 50.0, 86.19, 100.0])
def test_theta_and_mm_round_trip_for_every_roof(roof_type: str, theta_pct: float) -> None:
    """Both directions, against the relation written out rather than reused.

    The wetland is included even though layer 1 declines it: the conversion is a
    property of a substrate height, and its preset is still what layer 2 runs on.
    """
    sh_cm = float(ROOF_PRESETS[roof_type]["SH"])

    storage_mm = theta_pct_to_mm(theta_pct, sh_cm)

    assert storage_mm == pytest.approx(theta_pct / 100.0 * sh_cm * 10.0)
    assert mm_to_theta_pct(storage_mm, sh_cm) == pytest.approx(theta_pct)


@pytest.mark.parametrize(
    ("roof_type", "p1", "p99"),
    [
        # gr2l_tool.md's derivation table: this site's own p1/p99 of θ, which the
        # presets are the depth-scaled conversion of.
        ("irrigated_extensive", 4.70, 32.63),
        ("non_irrigated_extensive", 1.22, 22.85),
        ("semi_intensive", 4.20, 30.38),
    ],
)
def test_the_presets_are_that_conversion_of_the_documented_percentiles(
    roof_type: str, p1: float, p99: float
) -> None:
    """The round trip is not free-floating: it is what produced the pinned bounds.

    A conversion that drifted would leave `Ssubmin`/`Ssubmax` no longer derivable
    from the percentiles the specification records, which is the failure a
    round-trip test on its own cannot see.
    """
    preset = ROOF_PRESETS[roof_type]
    sh_cm = float(preset["SH"])

    assert round(theta_pct_to_mm(p1, sh_cm), 1) == preset["Ssubmin"]
    assert round(theta_pct_to_mm(p99, sh_cm), 1) == preset["Ssubmax"]


# --- Scope: both unmodellable roofs abstain (T054) ----------------------------


@pytest.mark.parametrize(
    "roof_type",
    ["gravel", "Kies", " KD ", "qgravel", "wetland", "Sumpf2", " QWetland ", "sumpfdach"],
)
def test_the_two_unmodellable_roofs_abstain_before_any_io(
    roof_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`not_available` with a reason, and nothing was fetched or simulated.

    Asserted from the far side: the abstention is a scope decision taken from the
    argument alone, so a weather call would mean it had been discovered rather
    than decided.
    """
    weather, gr2l = SpyWeather(), _gr2l(monkeypatch)
    result = _run(_tool(_context(weather=weather)), roof_type)

    assert result["status"] == "not_available"
    assert result["reason"] == NON_MODELLABLE_ROOFS[roof_type.strip().lower()]
    assert weather.calls == []
    assert gr2l.rows == []


def test_the_two_roofs_abstain_for_different_reasons() -> None:
    """One shared reason string would tell a wetland question it has no substrate.

    The gravel roof has none; the wetland's exclusion is its unit contract and a
    dead sensor. Both are true, and neither is the other.
    """
    gravel, wetland = NON_MODELLABLE_ROOFS["gravel"], NON_MODELLABLE_ROOFS["wetland"]

    assert gravel != wetland
    assert "no substrate" in gravel
    assert "ponded" in wetland or "sensor" in wetland
    # Both still point the researcher at what is available.
    assert "queried" in gravel and "queried" in wetland


# --- The seed rule (T047, T054) ----------------------------------------------


def _latest_reading_at_or_before(bound: datetime, column: str = "QEx2") -> tuple[datetime, float]:
    """The reading the seed rule should pick, read straight from the file."""
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        return connection.execute(
            f'SELECT timestamp, "{column}" FROM swc '  # noqa: S608 - literal column
            f"WHERE \"{column}\" IS NOT NULL AND timestamp <= TIMESTAMP '{bound:%Y-%m-%d %H:%M:%S}' "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()


def test_a_window_with_no_trustworthy_seed_is_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window opening before the record starts has nothing to seed from.

    The tool abstains rather than substituting GR2L's generic 20 mm, which sits
    at or above `Ssubmax` for both extensive roofs — a silent start from a
    saturated roof. The model is never called.
    """
    weather, gr2l = SpyWeather(), _gr2l(monkeypatch)
    before_record = FIRST_SWC_DAY - timedelta(days=10)

    result = _run(
        _tool(_context(weather=weather)),
        start_date=before_record.isoformat(),
        end_date=(before_record + timedelta(days=2)).isoformat(),
    )

    assert result["status"] == "not_available"
    assert FIRST_SWC_DAY.isoformat() in result["reason"]
    assert gr2l.rows == [], "a run with no seed reached the model anyway"


def test_a_fresh_seed_is_the_measured_reading_and_is_not_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window opens inside the record, so its own day supplies the seed."""
    _gr2l(monkeypatch)
    result = _run(_tool(_context(weather=SpyWeather())))

    measured_at, theta_pct = _latest_reading_at_or_before(
        datetime.combine(WINDOW[0], datetime.max.time())
    )
    seed = result["seed"]

    assert seed["source"] == "measured"
    assert seed["swc_pct"] == round(theta_pct, 2)
    assert seed["measured_at"].startswith(measured_at.date().isoformat())
    assert seed["age_days"] == 0
    assert seed["is_stale"] is False
    # And the mm actually sent is that %θ through the roof's own depth.
    sh_cm = float(ROOF_PRESETS["non_irrigated_extensive"]["SH"])
    assert seed["substrate_storage_mm"] == round(theta_pct_to_mm(theta_pct, sh_cm), 3)


def test_a_seed_older_than_the_freshness_window_still_runs_and_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staleness discloses, it does not refuse.

    The record ends 2026-04-24, so a window a month later is seeded from a
    reading that describes a roof which has moved on — the run proceeds and the
    answer is obliged to say what it started from.
    """
    gr2l = _gr2l(monkeypatch)
    window_start = LAST_SWC_DAY + timedelta(days=30)

    result = _run(
        _tool(_context(datetime(2026, 6, 1, tzinfo=BERLIN), weather=SpyWeather())),
        start_date=window_start.isoformat(),
        end_date=(window_start + timedelta(days=2)).isoformat(),
    )

    assert result["status"] == "success"
    assert result["seed"]["age_days"] == 30 > STALE_AFTER_DAYS
    assert result["seed"]["is_stale"] is True
    assert gr2l.rows, "a stale seed must still run"


def test_the_seed_never_reads_past_as_of_even_for_a_later_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`seed_at = min(window_start, as_of)`, with `as_of` the binding half.

    The window opens well after the cut, so the rule's other half — the window
    start — would reach readings this case may not see. The reading picked is the
    one at or before `as_of` itself, hand-checked against the raw file.
    """
    _gr2l(monkeypatch)
    as_of = datetime(2026, 3, 10, 12, 0, tzinfo=BERLIN)
    window_start = date(2026, 3, 20)

    result = _run(
        _tool(_context(as_of, weather=SpyWeather())),
        start_date=window_start.isoformat(),
        end_date=(window_start + timedelta(days=2)).isoformat(),
    )

    # `as_of` is 12:00 Berlin = 11:00 UTC, and the columns are naive UTC.
    measured_at, theta_pct = _latest_reading_at_or_before(datetime(2026, 3, 10, 11, 0))
    assert result["seed"]["swc_pct"] == round(theta_pct, 2)
    assert result["seed"]["measured_at"] == measured_at.isoformat(sep=" ")
    assert result["seed"]["age_days"] == (window_start - measured_at.date()).days


def test_the_seed_rule_holds_against_an_unbounded_executor() -> None:
    """The half a case's as-of executor cannot demonstrate — and the reason the rule is here.

    Through ``ctx.db`` the previous test would pass even if ``seed_bound``
    ignored ``as_of`` entirely, because the view has already hidden every later
    row. An oracle imports ``latest_measured_swc`` and may hand it an unbounded
    connection (``agent_architecture.md`` §7), and then nothing but this function
    enforces the cut. Asserted mutation-first: with the ``as_of`` half removed,
    this is the test that fails.
    """
    from water_assistant_agent.assistant.agents.text_to_sql.executor import (
        DuckDbQueryExecutor,
        create_duckdb_connection,
    )
    from water_assistant_agent.assistant.tools.swc import latest_measured_swc

    unbounded = DuckDbQueryExecutor(
        connection_factory=lambda: create_duckdb_connection(DB_PATH)
    )
    as_of = datetime(2026, 3, 10, 12, 0, tzinfo=BERLIN)

    measured = latest_measured_swc(
        unbounded, "non_irrigated_extensive", date(2026, 3, 20), as_of=as_of
    )

    # 12:00 Berlin is 11:00 in the column's own naive UTC.
    assert measured.measured_at == datetime(2026, 3, 10, 11, 0)
    assert measured.seed_at == datetime(2026, 3, 10, 11, 0)
    # The record itself runs a month and a half further, so the bound did the work.
    assert LAST_SWC_DAY > measured.measured_at.date()


def test_a_caller_supplied_seed_replaces_the_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counterfactual seed is echoed as the caller's, not dressed as measured."""
    _gr2l(monkeypatch)
    result = _run(_tool(_context(weather=SpyWeather())), initial_soil_moisture_pct=5.0)

    assert result["seed"]["source"] == "caller"
    assert result["seed"]["swc_pct"] == 5.0
    assert result["seed"]["measured_at"] is None
    assert result["seed"]["is_stale"] is False


# --- The seed-day retention flag (T054) --------------------------------------


def test_seed_day_rain_sets_the_retention_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Day 1's `OUT` is null, so its rain counts as retained; the flag discloses it."""
    _gr2l(monkeypatch)
    result = _run(
        _tool(_context(weather=SpyWeather())),
        forcings={"precip": {WINDOW[0].isoformat(): 12.0}},
    )

    assert result["summary"]["retention_excludes_seed_day_runoff"] is True


def test_a_dry_seed_day_carries_no_caveat(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction, so the flag is not simply always on.

    Rain on a *later* day of the same window leaves the summary clean: the
    caveat is about day 1 specifically, not about the window being wet.
    """
    _gr2l(monkeypatch)
    result = _run(
        _tool(_context(weather=SpyWeather())),
        forcings={"precip": {(WINDOW[0] + timedelta(days=3)).isoformat(): 12.0}},
    )

    assert result["summary"]["retention_excludes_seed_day_runoff"] is False


# --- Forcings: applied, sparse, echoed (T048, T054) --------------------------


def test_forcings_reach_the_model_and_leave_every_other_value_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asserted on the rows GR2L received, not on the answer that came back."""
    gr2l = _gr2l(monkeypatch)
    forced_day = (WINDOW[0] + timedelta(days=2)).isoformat()

    result = _run(
        _tool(_context(weather=SpyWeather())),
        forcings={"precip": {forced_day: 50.0}, "tm": {forced_day: 30.0}},
    )

    sent = {row.Date: row for row in gr2l.rows[0]}
    assert sent[forced_day].precip == 50.0
    assert sent[forced_day].tm == 30.0
    # Unnamed fields on the forced day, and every field on every other day.
    assert sent[forced_day].tx == 20.0
    untouched = [row for day, row in sent.items() if day != forced_day]
    assert {row.precip for row in untouched} == {0.0}
    assert {row.tm for row in untouched} == {15.0}
    # Echoed exactly as applied, for argument checking.
    assert result["forcings"] == {"precip": {forced_day: 50.0}, "tm": {forced_day: 30.0}}


def test_an_unforced_run_echoes_no_forcings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Null rather than an empty mapping: the run was driven by the weather as fetched."""
    _gr2l(monkeypatch)

    assert _run(_tool(_context(weather=SpyWeather())))["forcings"] is None


def test_the_summary_is_computed_over_the_forced_rain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A counterfactual's retention is against its own rain, not the rain it replaced.

    The spy returns ``OUT = precip / 2`` from day 2, so both totals are known
    exactly: 50 mm in, 25 mm out.
    """
    _gr2l(monkeypatch)
    result = _run(
        _tool(_context(weather=SpyWeather())),
        forcings={"precip": {(WINDOW[0] + timedelta(days=2)).isoformat(): 50.0}},
    )

    assert result["summary"]["total_precip_mm"] == 50.0
    assert result["summary"]["total_outflow_mm"] == 25.0
    assert result["summary"]["retention_mm"] == 25.0
    assert result["summary"]["retention_pct"] == 50.0


# --- `evaluate_against_measured` (T049, T054) --------------------------------


def _measured_daily_means(start: date, end: date, column: str = "QEx2") -> dict[str, float]:
    """Daily mean %θ per Berlin day, bucketed in Python from the raw rows.

    No ``GROUP BY`` and no ``AT TIME ZONE`` — the day boundary the implementation
    claims is checked against an independent implementation of the same rule,
    the convention ``test_weather_station.py`` set.
    """
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = connection.execute(
            f'SELECT timestamp, "{column}" FROM swc WHERE "{column}" IS NOT NULL'  # noqa: S608
        ).fetchall()
    finally:
        connection.close()

    buckets: dict[date, list[float]] = defaultdict(list)
    for timestamp, value in rows:
        day = timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(BERLIN).date()
        if start <= day <= end:
            buckets[day].append(float(value))
    return {day.isoformat(): statistics.fmean(values) for day, values in buckets.items()}


def test_the_deviation_statistics_match_a_hand_computed_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mean and max |predicted − measured| in %θ, over the days both series hold."""
    _gr2l(monkeypatch, ssub=5.0, step=1.0)
    result = _run(_tool(_context(weather=SpyWeather())), evaluate_against_measured=True)

    measured = _measured_daily_means(*WINDOW)
    sh_cm = float(ROOF_PRESETS["non_irrigated_extensive"]["SH"])
    deviations = [
        abs(round(mm_to_theta_pct(5.0 + index, sh_cm), 2) - measured[day.isoformat()])
        for index, day in enumerate(
            WINDOW[0] + timedelta(days=offset) for offset in range((WINDOW[1] - WINDOW[0]).days + 1)
        )
    ]

    evaluation = result["evaluation"]
    assert evaluation["days"] == len(deviations)
    assert evaluation["overlap_start"] == WINDOW[0].isoformat()
    assert evaluation["overlap_end"] == WINDOW[1].isoformat()
    assert evaluation["mean_abs_deviation_pct"] == round(statistics.fmean(deviations), 2)
    assert evaluation["max_abs_deviation_pct"] == round(max(deviations), 2)


def test_a_window_past_the_record_has_no_overlap_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero overlap is a success carrying a reason, never a deviation of 0.0.

    A forecast window has no measured counterpart yet; reporting perfect
    agreement would be the one answer that is actively wrong.
    """
    _gr2l(monkeypatch)
    window_start = LAST_SWC_DAY + timedelta(days=3)

    result = _run(
        _tool(_context(datetime(2026, 5, 1, tzinfo=BERLIN), weather=SpyWeather())),
        start_date=window_start.isoformat(),
        end_date=(window_start + timedelta(days=2)).isoformat(),
        evaluate_against_measured=True,
    )

    assert result["status"] == "success"
    assert result["evaluation"]["days"] == 0
    assert result["evaluation"]["mean_abs_deviation_pct"] is None
    assert result["evaluation"]["reason"]


def test_the_comparison_is_absent_unless_it_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gr2l(monkeypatch)

    assert _run(_tool(_context(weather=SpyWeather())))["evaluation"] is None


# --- The forecast horizon (T041's deferral, T054) ----------------------------


def test_a_window_past_the_horizon_is_not_available_for_the_model_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model needs a forcing for every day, and none exists past the horizon.

    Nothing upstream refuses it — Archive answers day 400 as the real past — so
    the check is load-bearing here exactly as it is in the weather tool, and it
    is measured against `ctx.as_of` rather than a wall clock.
    """
    weather, gr2l = SpyWeather(), _gr2l(monkeypatch)
    as_of = datetime(2026, 3, 15, 11, 0, tzinfo=BERLIN)
    beyond = as_of.date() + timedelta(days=17)

    result = _run(
        _tool(_context(as_of, weather=weather)),
        start_date=beyond.isoformat(),
        end_date=beyond.isoformat(),
    )

    assert result["status"] == "not_available"
    assert "16 days ahead" in result["reason"]
    assert weather.calls == [], "an abstention must not be a disguised fetch failure"
    assert gr2l.rows == []


def test_the_last_day_inside_the_horizon_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boundary is exact, so the abstention above is about the horizon."""
    _gr2l(monkeypatch)
    as_of = datetime(2026, 3, 15, 11, 0, tzinfo=BERLIN)
    edge = as_of.date() + timedelta(days=16)

    result = _run(
        _tool(_context(as_of, weather=SpyWeather())),
        start_date=edge.isoformat(),
        end_date=edge.isoformat(),
    )

    assert result["status"] == "success"


# --- The daily-series cap (T050, T054) ---------------------------------------


def test_a_window_at_the_cap_keeps_its_daily_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _gr2l(monkeypatch)
    start = WINDOW[0]

    result = _run(
        _tool(_context(weather=SpyWeather())),
        start_date=start.isoformat(),
        end_date=(start + timedelta(days=30)).isoformat(),
    )

    assert len(result["data"]) == 31
    assert result["truncated"] is False
    assert result["weekly"] is None


def test_one_day_past_the_cap_returns_weeks_and_the_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model still ran on every day — only the response is bounded."""
    gr2l = _gr2l(monkeypatch)
    start = WINDOW[0]

    result = _run(
        _tool(_context(weather=SpyWeather())),
        start_date=start.isoformat(),
        end_date=(start + timedelta(days=31)).isoformat(),
    )

    assert result["data"] == []
    assert result["truncated"] is True
    assert result["summary"]["days"] == 32
    assert len(gr2l.rows[0]) == 32, "the cap must not shorten the simulation"
    weeks = result["weekly"]
    assert [week["days"] for week in weeks] == [7, 7, 7, 7, 4]
    assert weeks[0]["start"] == start.isoformat()
    assert weeks[-1]["end"] == (start + timedelta(days=31)).isoformat()


def test_each_field_is_aggregated_the_way_that_field_is_defined() -> None:
    """A weekly `tx` is the week's hottest day, not a mean of maxima.

    The bucketing tests above pass under any per-field rule, so the rule itself
    is asserted here on values that differ: fluxes accumulate, states average,
    `tx` is a max and `tn` a min.
    """
    from water_assistant_agent.assistant.tools.series import roof_periods, weather_periods

    rows = [
        DailyWeatherRow(
            Date=f"2025-06-{10 + offset:02d}",
            tm=10.0 + offset,
            tx=20.0 + offset,
            tn=5.0 - offset,
            rf=60.0 + offset,
            precip=2.0,
            w=4.0,
            gs=1000.0,
        )
        for offset in range(3)
    ]

    (week,) = weather_periods(rows, 7)

    assert week.tx == 22.0, "a max, not a mean"
    assert week.tn == 3.0, "a min, not a mean"
    assert week.tm == 11.0
    assert week.precip == 6.0, "a total, not a mean"
    assert week.gs == 3000.0
    assert week.w == 4.0

    days = gr2l_module._to_days(
        [
            Gr2lResultRow(
                Date=row.Date,
                ET_PM=1.0,
                Ssub=7.0 + index,
                Sret=0.0,
                Qdown=None if index == 0 else 1.0,
                Qup=None if index == 0 else 0.0,
                OUT=None if index == 0 else 2.0,
                ET=0.5,
            )
            for index, row in enumerate(rows)
        ],
        gr2l_client_module.resolve_roof_parameters(
            "non_irrigated_extensive", hoehe_nn=142.0, lat=51.0, long=12.0, theta_01=8.0
        ),
    )
    (period,) = roof_periods(days, 7)

    assert period.ET == 1.5, "actual ET accumulates over the span"
    assert period.OUT == 4.0, "day 1's null is skipped, not read as a zero"
    assert period.Ssub == 8.0, "a state is a mean"
    assert period.min_swc_pct == min(day.swc_pct for day in days)


# --- Every error site's `error_type` (T052, T054) -----------------------------


class _Boom:
    """A weather half that fails the way its constructor was told to."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        raise self._error


class _Empty:
    async def fetch(self, *, start_date: str, end_date: str) -> WeatherResult:
        return WeatherResult(
            latitude=51.0, longitude=12.0, timezone="Europe/Berlin", source="archive", data=[]
        )


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("unknown roof type", {"roof": "terrace"}),
        ("half a window", {"end_date": None}),
        ("reversed window", {"start_date": "2025-06-16", "end_date": "2025-06-10"}),
        ("albedo out of range", {"albedo": 3.0}),
        ("seed percentage out of range", {"initial_soil_moisture_pct": 250.0}),
        ("forcing names no weather field", {"forcings": {"rain": {"2025-06-11": 5.0}}}),
        ("forcing outside the window", {"forcings": {"precip": {"2025-07-01": 5.0}}}),
        ("forcing day is not a date", {"forcings": {"precip": {"tuesday": 5.0}}}),
        ("forcing value is not a number", {"forcings": {"precip": {"2025-06-11": "wet"}}}),
    ],
)
def test_argument_faults_are_invalid_argument(
    label: str, kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every pre-I/O validation, tagged as scored rather than excluded.

    ``invalid_argument`` is what §7 keeps in the denominator; the fetch is
    asserted never to have happened, which is what "before any I/O" means.
    """
    weather, gr2l = SpyWeather(), _gr2l(monkeypatch)
    roof = kwargs.pop("roof", "non_irrigated_extensive")

    result = _run(_tool(_context(weather=weather)), roof, **kwargs)

    assert result["status"] == "error", label
    assert result["error_type"] == "invalid_argument", label
    assert weather.calls == [], label
    assert gr2l.rows == [], label


def test_a_failing_weather_source_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _gr2l(monkeypatch)
    context = _context(weather=_Boom(OpenMeteoError("service unavailable")))

    result = _run(_tool(context))

    assert result["error_type"] == "upstream"
    assert "service unavailable" in result["error_details"]


def test_an_unexpected_weather_failure_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catch-all too: an exception nothing anticipated is still not the call's fault."""
    _gr2l(monkeypatch)

    result = _run(_tool(_context(weather=_Boom(RuntimeError("socket closed")))))

    assert result["error_type"] == "upstream"


def test_an_empty_weather_window_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _gr2l(monkeypatch)

    result = _run(_tool(_context(weather=_Empty())))

    assert result["error_type"] == "upstream"


def test_an_unconfigured_model_service_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unconfigured(*args: Any, **kwargs: Any) -> Any:
        raise Gr2lConfigError("GR2L is not configured")

    monkeypatch.setattr(gr2l_module, "run_gr2l", unconfigured)

    result = _run(_tool(_context(weather=SpyWeather())))

    assert result["error_type"] == "upstream"


def test_a_failing_model_service_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("502 from the gateway")

    monkeypatch.setattr(gr2l_module, "run_gr2l", broken)

    result = _run(_tool(_context(weather=SpyWeather())))

    assert result["error_type"] == "upstream"
    # The upstream's own words stay out of the agent-facing message.
    assert "502" not in result["error_details"]


def test_a_failing_seed_read_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database that raises is upstream; a database with no reading is `not_available`.

    The two share a `try` and must not share an outcome — one is a system fault,
    the other a scope limit the answer is supposed to report.
    """
    _gr2l(monkeypatch)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise duckdb.IOException("the file went away")

    monkeypatch.setattr(gr2l_module, "latest_measured_swc", broken)

    result = _run(_tool(_context(weather=SpyWeather())))

    assert result["status"] == "error"
    assert result["error_type"] == "upstream"


def test_a_failing_comparison_read_is_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _gr2l(monkeypatch)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise duckdb.IOException("the file went away")

    monkeypatch.setattr(gr2l_module, "daily_mean_swc", broken)

    result = _run(_tool(_context(weather=SpyWeather())), evaluate_against_measured=True)

    assert result["status"] == "error"
    assert result["error_type"] == "upstream"


@pytest.mark.parametrize("kwargs", [{"start_date": "2025-06-10"}, {"past_days": -1}])
def test_the_weather_tool_tags_its_own_argument_faults(kwargs: dict[str, Any]) -> None:
    """The other wrapper's sites, since the taxonomy is one taxonomy."""
    from water_assistant_agent.assistant.tools import weather as weather_module

    tool = weather_module.make_weather_forecast_tool(_context(weather=SpyWeather()))
    result = asyncio.run(tool(**kwargs))

    assert result["error_type"] == "invalid_argument"


def test_the_weather_tool_tags_a_failed_fetch_upstream() -> None:
    from water_assistant_agent.assistant.tools import weather as weather_module

    tool = weather_module.make_weather_forecast_tool(
        _context(weather=_Boom(OpenMeteoError("down")))
    )
    result = asyncio.run(tool(start_date="2025-06-10", end_date="2025-06-11"))

    assert result["error_type"] == "upstream"


# --- The client's connection pool is per event loop, not per process (T148) ---


class _KeepAliveHandler(BaseHTTPRequestHandler):
    """A GR2L-shaped endpoint that keeps its connection open.

    Keep-alive is the whole point: it is what leaves a pooled connection behind
    for the *next* loop to pick up, and a server answering `Connection: close`
    hides the defect under test by forcing every call to dial afresh.
    """

    protocol_version = "HTTP/1.1"
    _BODY = b'{"data": []}'

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self._BODY)))
        self.end_headers()
        self.wfile.write(self._BODY)

    def log_message(self, *args: Any) -> None:
        """Silence the per-request line this would otherwise write to stderr."""


@contextlib.contextmanager
def _local_gr2l_service() -> Iterator[str]:
    """A real socket serving `_post_gr2l`'s shape, yielding its URL.

    A real server rather than a patched `httpx.AsyncClient.send`, because the
    binding under test is the *transport's*: a fake that never opens a socket
    passes on the loop-bound client too, and would have asserted nothing.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/predict_gr2l"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_loop_per_call_does_not_close_the_pool_out_from_under_the_next_one() -> None:
    """T115's failure, reproduced at the transport and then required not to happen.

    `run_case` runs one `asyncio.run` per rollout, so this is the harness's own
    shape. Against a process-global client the second call raises `RuntimeError:
    Event loop is closed` — the first loop's keep-alive connection, offered to a
    loop that no longer exists. Three calls rather than two because the failure
    is not every-time: the dead connection is evicted when it fails, so call
    three succeeds on its own fresh socket and a two-call test would pass half
    the time for the wrong reason.
    """
    with _local_gr2l_service() as url:
        replies = [
            asyncio.run(gr2l_client_module._post_gr2l(url, {}, {"day": index}))
            for index in range(3)
        ]

    assert replies == [{"data": []}] * 3


def test_each_loop_gets_its_own_client_and_reuses_it_within_the_loop() -> None:
    """One pool per loop — not one per process, and not one per call."""

    async def twice() -> tuple[httpx.AsyncClient, bool]:
        client = gr2l_client_module._get_client()
        return client, gr2l_client_module._get_client() is client

    first, first_reused = asyncio.run(twice())
    second, second_reused = asyncio.run(twice())

    assert first_reused and second_reused
    assert first is not second


def test_concurrent_loops_in_threads_never_share_a_pool() -> None:
    """MLflow evaluates records in worker threads, each running its own loop.

    Distinctness has to hold while those loops are *alive* at once, which is a
    stronger claim than the sequential case: the holder is one mapping reached
    from several threads.
    """

    async def _client() -> httpx.AsyncClient:
        client = gr2l_client_module._get_client()
        await asyncio.sleep(0.05)  # hold the loop open while its peers enter
        assert gr2l_client_module._get_client() is client
        return client

    with ThreadPoolExecutor(max_workers=8) as pool:
        # The *objects*, not their ids: once a loop closes its entry is dropped,
        # and an id compared after the client has been freed can collide with a
        # later allocation at the same address.
        clients = list(pool.map(lambda _: asyncio.run(_client()), range(8)))

    assert len({id(client) for client in clients}) == len(clients)


def test_a_closed_client_is_replaced_rather_than_handed_out_again() -> None:
    """`aclose_client` drops its entry, so the next call on that loop builds one."""

    async def close_then_ask() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        closed = gr2l_client_module._get_client()
        await gr2l_client_module.aclose_client()
        return closed, gr2l_client_module._get_client()

    closed, fresh = asyncio.run(close_then_ask())

    assert closed.is_closed
    assert fresh is not closed
    assert not fresh.is_closed


def test_a_finished_loop_does_not_keep_its_pool_alive() -> None:
    """A search's loops must not accumulate — and the weak key alone does not do it.

    **The request is the test.** A client that has issued one holds an asyncio
    transport, which holds the loop, so the mapping's *value* strongly
    references its own weak *key* and the entry cannot be collected: twenty
    loops and twenty entries stayed alive before `_drop_closed_loops` existed.
    A version of this test that only touched the holder passed against that leak
    and asserted nothing.

    One survivor is the bound rather than a tolerance: the last loop's entry is
    dropped by whichever call comes next, so what is ruled out is accumulation,
    not a single stale entry at rest.
    """

    finished: list[weakref.ref[asyncio.AbstractEventLoop]] = []

    async def call(url: str, index: int) -> None:
        await gr2l_client_module._post_gr2l(url, {}, {"day": index})
        finished.append(weakref.ref(asyncio.get_running_loop()))

    with _local_gr2l_service() as url:
        for index in range(8):
            asyncio.run(call(url, index))
    gc.collect()

    # Asserted on the loops rather than on the holder's size, which every other
    # test in this file also writes to: a live weakref here means the mapping is
    # what is still holding a finished rollout's loop.
    assert sum(reference() is not None for reference in finished) <= 1


# --- Replay: a model case issues no live call (the packet's exit) -------------


class _FakeGr2lService:
    """The GR2L endpoint, at the one seam `run_gr2l` calls out through.

    Records every POST so "zero live calls" is a count rather than an assumption,
    and answers the canary probe and a real request alike — the cache treats both
    as ordinary entries.
    """

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.enabled = True

    async def __call__(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        if not self.enabled:
            raise AssertionError("a replayed model case reached the GR2L service")
        self.posts.append(body)
        return {
            "data": [
                {
                    "Date": row["Date"],
                    "ET_PM": 1.0,
                    "Ssub": 9.0,
                    "Sret": 0.0,
                    "Qdown": None if index == 0 else 0.0,
                    "Qup": None if index == 0 else 0.0,
                    "OUT": None if index == 0 else 0.0,
                    "ET": 0.7,
                }
                for index, row in enumerate(body["data"])
            ]
        }


def test_a_model_case_replays_with_no_live_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Record once, then replay: the second pass issues nothing at all.

    The weather half is the real composite over the pinned database and the
    window is one the station covers whole, so that side needs no cache (T044);
    the model half is served from the entry the first pass committed, keyed on
    the request `run_gr2l` actually builds. Proved by a service that raises: the
    replayed run cannot have called it and still succeeded.

    The canary is part of the claim. It is fetched on the recording pass —
    before any entry is written — and **not** on the replay, because a hit
    returns before the canary is ever consulted: there is no service to have
    moved when nothing is asked of it.
    """
    service = _FakeGr2lService()
    monkeypatch.setattr(gr2l_client_module, "_post_gr2l", service)
    monkeypatch.setattr(
        gr2l_client_module,
        "get_settings",
        lambda: type("S", (), {"gr2l_api_base_url": "http://gr2l.test", "gr2l_api_key": "k"})(),
    )

    cache = ResponseCache(tmp_path / "cache")
    context = _context(
        cache=cache,
        factory=lambda db, http_cache: make_weather_client(db, http_cache, allow_live=False),
    )

    recorded = _run(_tool(context))
    assert recorded["status"] == "success"
    assert recorded["weather_source"] == "station", "the window must be a station window"
    # The data request and the canary probe, and nothing else.
    assert len(service.posts) == 2
    assert gr2l_client_module.CANARY_REQUEST in service.posts

    service.enabled = False
    replayed = _run(_tool(context))

    assert replayed == recorded
    assert len(service.posts) == 2, "the replay issued a live call"


def test_the_replayed_run_is_the_cache_and_not_a_second_computation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of the claim: an uncached window in the same setup fails loudly.

    Without this, the pass above could be evidence about a service that is never
    called rather than about a cache that is hit.
    """
    service = _FakeGr2lService()
    service.enabled = False
    monkeypatch.setattr(gr2l_client_module, "_post_gr2l", service)
    monkeypatch.setattr(
        gr2l_client_module,
        "get_settings",
        lambda: type("S", (), {"gr2l_api_base_url": "http://gr2l.test", "gr2l_api_key": "k"})(),
    )

    context = _context(
        cache=ResponseCache(tmp_path / "cache"),
        factory=lambda db, http_cache: make_weather_client(db, http_cache, allow_live=False),
    )

    result = _run(_tool(context))

    assert result["status"] == "error"
    assert result["error_type"] == "upstream"
