"""The port against the deployed controller, before the unit fix (T064).

Faithfulness before correctness. ``irrigation.py`` claims to reproduce the
site's own controller, and this is where that claim is checked — **in the
controller's original regime**: an %VWC store with millimetres of rain and ET
added to it, a flat 22 %VWC capacity on all three roofs, and hourly rows, the
step at which the horizons in hours and the controller's hard-coded ``[:48]`` /
``[1:168]`` slices are the same window. Landing it before T065 is what makes
every later difference attributable to the unit fix rather than to the port.

The numbers below were produced by the controller itself and committed by
``scripts/capture_irrigation_golden.py``. Nothing here reads
``/home/shpilevo/Downloads/smart_irrigation.py``: it lives outside any
repository (``findings.md`` § External sources on this machine), so a test that
imported it would stop running the day that file moves.

Two standards of evidence, because a port can fail in two ways. Element for
element over the four series ``_simulate_store`` returns, which catches a
slipped constant that a decision would absorb; and the decision itself against
the controller's own German string, which catches a bucket that agrees while the
ladder reads a different rung.
"""

import json
import math
from pathlib import Path
from typing import Any

import pytest

from water_assistant_agent.assistant.irrigation import ReasonCode, Regime, run_roof
from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    HEAT_THRESHOLD_C,
    OUTFLOW_EPSILON_MM,
    REFILL_HORIZON_HOURS,
    RESIDUAL_PCT,
    rules_for,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/irrigation_golden.json").read_text(encoding="utf-8")
)

TOLERANCE = 1e-9
"""How far a stored value may sit from the controller's own.

The two implementations run the same algebra over different constants: the
extraction writes the stress coefficient over fractions
(``(θ/100 − 0.025) / (0.22 − 0.025)``) and the port writes it over %θ, where the
factor of 100 cancels. That is a last-bit difference per step — 2.2e-16 in
``ks`` — and over the committed 168-hour windows it accumulates to at most
1.8e-15 in the store and 3.6e-15 in the outflow: a millionfold inside this bound,
and far below the resolution of any threshold it is compared against.
"""

# The controller's German recommendation, one distinctive phrase per rung. The
# ladder returns a code and the site's script returns prose, so this is where the
# two vocabularies are pinned to each other — a code that quietly came to mean a
# different rung would still fail here.
GERMAN_MARKERS: dict[ReasonCode, str] = {
    ReasonCode.BELOW_WILTING_POINT: "unter Welkpunkt",
    ReasonCode.NO_HEAT_NO_STRESS: "keine Hitzeperiode",
    ReasonCode.SUFFICIENT_MOISTURE: "ausreichend Feuchte im Substrat",
    ReasonCode.REFILL_FORECAST: "Erreichen der Feldkapazitaet",
    ReasonCode.COOLING_REQUESTED: "Kuehleffekt erwuenscht --> Bewaesserung wird empfohlen",
    ReasonCode.NO_FORECAST: "keine Vorhersagedaten",
    ReasonCode.MISSING_VALUES: "Fehlwerte in Modell/Vorhersage",
}

CASES = [(case["name"], roof) for case in FIXTURE["cases"] for roof in case["roofs"]]


def _case(name: str) -> dict[str, Any]:
    return next(case for case in FIXTURE["cases"] if case["name"] == name)


def _run(case: dict[str, Any], site_id: str) -> Any:
    """The port over one committed window, in the controller's own regime."""
    roof = case["roofs"][site_id]
    return run_roof(
        roof["roof_type"],
        precipitation_mm=case["precip_mm"],
        et0_mm=case["et0_mm"],
        temperature_c=case["temperature_c"],
        seed_theta_pct=roof["seed_theta_pct"],
        step_hours=case["step_hours"],
        regime=Regime.PERCENT_THETA,
    )


# --- The constants the capture ran with are still this repository's -----------


def test_the_captured_constants_are_the_ones_the_rule_still_uses() -> None:
    """The fixture is only evidence while both sides run the same numbers.

    Every value here was read off the controller at capture time. A threshold
    that moved in ``rules_constants.py`` afterwards would make the series below
    a comparison against a rule this repository no longer applies, and the
    failure should say *that* rather than surfacing as a drifted decision.
    """
    assert FIXTURE["heat_threshold_c"] == HEAT_THRESHOLD_C
    assert FIXTURE["decision_horizon_h"] == DECISION_HORIZON_HOURS
    assert FIXTURE["outflow_horizon_h"] == REFILL_HORIZON_HOURS
    assert FIXTURE["outflow_eps"] == OUTFLOW_EPSILON_MM
    assert FIXTURE["residual_pct"] == pytest.approx(RESIDUAL_PCT)

    for case in FIXTURE["cases"]:
        for roof in case["roofs"].values():
            rules = rules_for(roof["roof_type"])
            assert (rules.wilting_pct, rules.dry_pct) == (roof["wilting_pct"], roof["dry_pct"])
            assert rules.capacity_pct == FIXTURE["capacity_pct"]


# --- Element for element ------------------------------------------------------


@pytest.mark.parametrize(("name", "site_id"), CASES)
def test_the_bucket_reproduces_the_controllers_series(name: str, site_id: str) -> None:
    """All four series ``_simulate_store`` returns, step by step.

    ``ks`` and ``et_actual`` are asserted beside the store because they are how
    the two preserved defects show themselves: the coefficient lags the state it
    throttles by one step, and the seed step computes no ET at all. A port that
    got the store right by evaporating on the seed step would pass on ``store``
    alone.
    """
    case = _case(name)
    expected = case["roofs"][site_id]
    series = _run(case, site_id).series

    assert len(series.store) == len(expected["store"]) == len(case["precip_mm"])
    for field in ("store", "outflow", "ks"):
        for step, (mine, theirs) in enumerate(
            zip(getattr(series, field), expected[field], strict=True)
        ):
            assert mine == pytest.approx(theirs, abs=TOLERANCE), f"{field} at step {step}"

    assert series.et_actual[0] is None, "the seed step computes no ET"
    assert expected["et_actual"][0] is None
    for step, (mine, theirs) in enumerate(
        zip(series.et_actual[1:], expected["et_actual"][1:], strict=True), start=1
    ):
        assert mine == pytest.approx(theirs, abs=TOLERANCE), f"et_actual at step {step}"


@pytest.mark.parametrize(("name", "site_id"), CASES)
def test_the_ladder_reaches_the_controllers_rung(name: str, site_id: str) -> None:
    """The decision itself, and the rung it came from.

    The boolean alone would be satisfied by the right answer for the wrong
    reason — two rungs say yes and three say no — so the code is checked against
    the phrase the controller printed.
    """
    case = _case(name)
    expected = case["roofs"][site_id]
    decision = _run(case, site_id).decision

    assert decision.irrigate is expected["irrigate"]
    assert GERMAN_MARKERS[decision.reason] in expected["reason_de"]


def test_the_committed_windows_walk_the_whole_ladder() -> None:
    """Every rung fires somewhere in the fixture, degenerate branches included.

    Without this the suite could stay green while three windows exercised one
    comparison: the fixture is evidence about the *ladder*, not about a single
    branch of it, and a window that stops adding a rung is a window to replace.
    """
    reached = {_run(_case(name), site_id).decision.reason for name, site_id in CASES}
    reached |= {
        _degenerate_decision(case).reason for case in FIXTURE["degenerate"]
    }

    assert reached == set(ReasonCode)


# --- The two windows the controller refuses to decide from --------------------


def _degenerate_decision(case: dict[str, Any]) -> Any:
    return run_roof(
        case["roof_type"],
        precipitation_mm=case["precip_mm"],
        et0_mm=case["et0_mm"],
        temperature_c=case["temperature_c"],
        seed_theta_pct=case["seed_theta_pct"],
        step_hours=1.0,
        regime=Regime.PERCENT_THETA,
    ).decision


@pytest.mark.parametrize("case", FIXTURE["degenerate"], ids=lambda case: case["name"])
def test_a_degenerate_window_decides_what_the_controller_decided(
    case: dict[str, Any],
) -> None:
    """An empty window and a gap: no series is pinned, only the rung.

    A gap makes every step after it ``NaN``, which JSON cannot carry and the
    ladder never reads — it stops at the missing value. Neither window
    irrigates, which is the property that matters: a hole in the forecast is not
    a dry roof.
    """
    decision = _degenerate_decision(case)

    assert decision.irrigate is case["irrigate"] is False
    assert GERMAN_MARKERS[decision.reason] in case["reason_de"]


def test_the_gap_case_really_carries_a_gap() -> None:
    """The fixture's own premise, so the case cannot decay into a plain window."""
    gapped = next(case for case in FIXTURE["degenerate"] if case["name"] == "gap_in_the_forcing")

    assert any(value is None or math.isnan(value) for value in gapped["precip_mm"])
