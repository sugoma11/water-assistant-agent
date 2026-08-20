"""The rule's constants against the documents that state them (T062).

`rules_constants.py` is the single source of truth, which makes it the thing
every *other* statement of a threshold has to agree with. So these tests read the
other statements: the millimetre column of ``irrigation_tool.md`` § Units, the
GR2L preset the flat capacity deliberately contradicts, and — for the two
constants that are this thesis's rather than the site's — the record they were
authored against.
"""

import re
from pathlib import Path

import duckdb
import pytest

from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    HEAT_THRESHOLD_C,
    HEATWAVE_MIN_CONSECUTIVE_DAYS,
    OUTFLOW_EPSILON_MM,
    REFILL_HORIZON_HOURS,
    RETENTION_TARGET,
    ROOF_RULES,
    VERSION,
    horizon_rows,
    rules_for,
)
from water_assistant_agent.assistant.tools.gr2l_client import ROOF_PRESETS
from water_assistant_agent.assistant.tools.roofs import ROOFS
from water_assistant_agent.assistant.tools.swc import theta_pct_to_mm

DB_PATH = "data/water.duckdb"
T12_EVENTS = Path("specs/agent_architecture/t12_rain_events.md")

# `irrigation_tool.md` § Units, transcribed: %θ as the site states it, and the
# millimetres that document says it converts to.
SPEC_UNITS_TABLE = {
    #                          wilting        dry           capacity
    "irrigated_extensive": ((5.0, 3.5), (10.0, 7.0), (22.0, 15.4)),
    "non_irrigated_extensive": ((4.0, 2.8), (10.0, 7.0), (22.0, 15.4)),
    "semi_intensive": ((10.0, 15.0), (16.0, 24.0), (22.0, 33.0)),
}
SPEC_RESIDUAL_MM = {
    "irrigated_extensive": 1.75,
    "non_irrigated_extensive": 1.75,
    "semi_intensive": 3.75,
}

_SITE_DAY = "((timestamp) AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Berlin')::DATE"


# --- Authored in %θ, converted once -------------------------------------------


@pytest.mark.parametrize("roof", sorted(SPEC_UNITS_TABLE))
def test_the_thresholds_are_the_ones_the_tool_spec_states(roof: str) -> None:
    """Both columns of § Units: the site's %θ and the millimetres they become.

    The specification is where a reader looks first and the module is what runs,
    so the two agreeing is not a formality — the whole point of authoring in %θ
    is that the site can check its own numbers without doing arithmetic.
    """
    rules = rules_for(roof)
    (wilting_pct, wilting_mm), (dry_pct, dry_mm), (capacity_pct, capacity_mm) = SPEC_UNITS_TABLE[
        roof
    ]

    assert (rules.wilting_pct, rules.dry_pct, rules.capacity_pct) == (
        wilting_pct,
        dry_pct,
        capacity_pct,
    )
    assert rules.wilting_mm == pytest.approx(wilting_mm)
    assert rules.dry_mm == pytest.approx(dry_mm)
    assert rules.capacity_mm == pytest.approx(capacity_mm)
    assert rules.residual_mm == pytest.approx(SPEC_RESIDUAL_MM[roof])


@pytest.mark.parametrize("roof", sorted(SPEC_UNITS_TABLE))
def test_the_conversion_is_swc_s_against_the_roof_table_s_height(roof: str) -> None:
    """One conversion, one depth, and neither restated here.

    A second implementation of ``θ% × SH / 100`` anywhere downstream is the unit
    bug this module exists to make impossible, so the property under test is that
    the millimetre values are *that function of that height* rather than
    constants that happen to match.
    """
    rules = rules_for(roof)
    sh_cm = ROOFS[roof].substrate_height_cm

    assert rules.substrate_height_cm == sh_cm
    for pct, mm in (
        (rules.wilting_pct, rules.wilting_mm),
        (rules.dry_pct, rules.dry_mm),
        (rules.capacity_pct, rules.capacity_mm),
        (rules.residual_pct, rules.residual_mm),
    ):
        assert mm == theta_pct_to_mm(pct, sh_cm)


def test_capacity_is_flat_across_the_three_roofs_and_disagrees_with_gr2l() -> None:
    """The deviation is carried on purpose, so it is asserted rather than avoided.

    The controller applies one field capacity to three roofs with materially
    different storage. For the semi-intensive roof that is 33.0 mm against GR2L's
    measured `Ssubmax` of 45.6 mm — 12.6 mm the two models disagree about, which
    is exactly the quantity the "will it refill?" branch turns on
    (`irrigation_tool.md` § This is not GR2L).
    """
    assert {rules.capacity_pct for rules in ROOF_RULES.values()} == {22.0}

    gap = float(ROOF_PRESETS["semi_intensive"]["Ssubmax"]) - rules_for("semi_intensive").capacity_mm

    assert gap == pytest.approx(12.6)


def test_one_capacity_replaces_the_extraction_s_two_constants() -> None:
    """`SWC_CAPACITY = 22.0` and `THETA_FIELD_CAPACITY = 0.22` were one quantity twice.

    In millimetres they collapse: the overflow threshold and the stress
    coefficient's upper end are the same attribute, so they cannot drift apart
    (`irrigation_tool.md` § Units).
    """
    rules = rules_for("irrigated_extensive")

    assert rules.capacity_mm == theta_pct_to_mm(22.0, rules.substrate_height_cm)
    assert not hasattr(rules, "theta_field_capacity")


# --- Scope -------------------------------------------------------------------


def test_the_rule_applies_to_the_three_substrate_roofs_only() -> None:
    """The same scope `NON_MODELLABLE_ROOFS` states, from the other direction."""
    assert set(ROOF_RULES) == {"irrigated_extensive", "non_irrigated_extensive", "semi_intensive"}
    assert all(roof in ROOFS for roof in ROOF_RULES)


@pytest.mark.parametrize("roof", ["gravel", "wetland", "not_a_roof"])
def test_a_roof_the_rule_does_not_cover_raises(roof: str) -> None:
    """Naming the three that are covered, because reaching here is a skipped check."""
    with pytest.raises(ValueError, match="No irrigation rules"):
        rules_for(roof)


def test_the_dose_in_millimetres_is_owed_by_the_site_and_left_empty() -> None:
    """Not guessed: a plausible number here is indistinguishable from the site's own.

    `irrigation_tool.md` § Open questions has the p90-of-historical-ET figures
    outstanding; the deployed valve minutes are what a disclosure can state
    meanwhile.
    """
    assert all(rules.dose_mm is None for rules in ROOF_RULES.values())
    assert [rules_for(roof).valve_minutes for roof in
            ("irrigated_extensive", "non_irrigated_extensive", "semi_intensive")] == [30, 30, 31]


def test_the_module_is_versioned_for_the_pin() -> None:
    """`rules_constants_version` is read out of the source by `just pins`."""
    assert VERSION
    assert isinstance(VERSION, str)


# --- Horizons -----------------------------------------------------------------


def test_the_horizons_are_hours_and_convert_by_the_step() -> None:
    """One implementation serves the site's hourly forcing and this system's daily rows."""
    assert (DECISION_HORIZON_HOURS, REFILL_HORIZON_HOURS) == (48, 168)

    assert horizon_rows(DECISION_HORIZON_HOURS, step_hours=24) == 2
    assert horizon_rows(REFILL_HORIZON_HOURS, step_hours=24) == 7
    assert horizon_rows(DECISION_HORIZON_HOURS, step_hours=1) == 48
    assert horizon_rows(REFILL_HORIZON_HOURS, step_hours=1) == 168


@pytest.mark.parametrize("step_hours", [0, -24])
def test_a_non_positive_step_raises(step_hours: float) -> None:
    with pytest.raises(ValueError, match="step_hours must be positive"):
        horizon_rows(DECISION_HORIZON_HOURS, step_hours=step_hours)


def test_a_step_coarser_than_the_horizon_raises_instead_of_returning_zero() -> None:
    """Zero rows is an empty window, and a decision made on nothing looks like a decision."""
    with pytest.raises(ValueError, match="spans no rows"):
        horizon_rows(DECISION_HORIZON_HOURS, step_hours=72)


def test_the_ladder_s_two_scalar_thresholds() -> None:
    """The heat test's 24 °C (row 2) and the refill guard (row 4)."""
    assert HEAT_THRESHOLD_C == 24.0
    assert OUTFLOW_EPSILON_MM == 0.01


# --- EVAL POLICY --------------------------------------------------------------


def test_the_heatwave_rule_keeps_t08_answerable_over_the_pinned_record() -> None:
    """The duration is this thesis's, so the record is what it has to be checked against.

    Three days at 24 °C marks days in six different months with distinct counts.
    A longer run or a higher threshold empties most of them, and a count question
    whose answer is zero in every sampled month measures nothing
    (`questions.md` § T08).
    """
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        daily_max = connection.execute(
            f"SELECT {_SITE_DAY} AS day, max(Tmax) FROM wetter GROUP BY 1 ORDER BY 1"
        ).fetchall()
    finally:
        connection.close()

    heat_days: list[object] = []
    run: list[object] = []
    for day, tmax in daily_max:
        if tmax >= HEAT_THRESHOLD_C:
            run.append(day)
            continue
        if len(run) >= HEATWAVE_MIN_CONSECUTIVE_DAYS:
            heat_days.extend(run)
        run = []
    if len(run) >= HEATWAVE_MIN_CONSECUTIVE_DAYS:
        heat_days.extend(run)

    months = {f"{day:%Y-%m}" for day in heat_days}  # type: ignore[str-bytes-safe]

    assert HEATWAVE_MIN_CONSECUTIVE_DAYS == 3
    assert len(heat_days) == 53
    assert len(months) == 6


def test_the_retention_target_is_one_the_committed_event_table_supports() -> None:
    """The target is authored eval policy; the record decides whether it is usable.

    `t12_rain_events.md` is the committed evidence — the same list the catalog
    samples from — and it shows the class split each candidate target would
    produce. A target off that table, or one that puts every pair on the same
    side, would leave T12's balance rule nothing to balance.
    """
    table = T12_EVENTS.read_text(encoding="utf-8")
    balance = {
        int(target): (int(above), int(below), int(both))
        for target, above, below, both in re.findall(
            r"^\|\s*(\d+)%\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|$", table, re.MULTILINE
        )
    }
    percent = round(RETENTION_TARGET * 100)

    assert percent in balance, f"{percent}% is not a target the committed table evaluated"
    above, below, both = balance[percent]
    assert above > 0 and below > 0
    # Both classes reachable from 8 of the 11 qualifying events — the headroom
    # `questions.md` §2 (T12) states the balance rule needs.
    assert both >= 8
