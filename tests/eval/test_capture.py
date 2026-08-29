"""What the capture pass warms, asserted on the committed suite rather than on a fixture.

T140's subject is a coverage gap, and a coverage gap is only visible against the
cases that fell into it. So these tests read ``eval/cases/`` and check the three
properties the repair rests on: a template naming no day count is warmed anyway,
a template whose oracle fetches nothing is warmed anyway, and a case's own
override is warmed at every window a rollout could resolve rather than at gold's
alone.

T146 split the surface in two and the tests follow it: :func:`forward_windows` is
now GR2L's alone, and weather is warmed over the ``as_of ± WARM_BAND_DAYS`` band
of days. Two properties are asserted of the band rather than of a request — that
it **contains** every window the sweep still enumerates, and that it reaches the
backward axis no forward sweep can produce.

**Nothing here calls out.** :func:`forward_windows`, :func:`model_overrides` and
:func:`named_roofs` are pure functions of a case and its clock, and the band is
arithmetic over ``as_of``; the fetching halves of ``warm_rollout_windows`` are
exercised by the capture pass itself, which is a live pass by definition and
cannot be a test.

``scripts/`` is loaded by path because an installed distribution of that name
shadows the repository's directory on ``sys.path`` — ``import
scripts.capture_cache`` resolves into ``site-packages`` and raises. The loader is
three lines and it is the only way to reach the module under test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from harness.run_case import _as_instant, make_case_context
from water_assistant_agent.assistant.tools.weather_client import days_in_window

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_capture_cache() -> Any:
    spec = importlib.util.spec_from_file_location(
        "capture_cache_under_test", REPO_ROOT / "scripts" / "capture_cache.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


capture = _load_capture_cache()


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for split in capture.SPLITS:
        cases.extend(
            json.loads(
                (REPO_ROOT / "eval" / "cases" / f"{split}.json").read_text(encoding="utf-8")
            )
        )
    return cases


def _by_template(template_id: str) -> list[dict[str, Any]]:
    return [case for case in _cases() if case["inputs"]["template_id"] == template_id]


def _windows(inputs: Mapping[str, Any]) -> list[tuple[str, str]]:
    return capture.forward_windows(inputs, make_case_context(_as_instant(inputs["as_of"])))


# ── The gap the neighbourhood cannot reach ───────────────────────────────────


def test_a_template_naming_no_day_count_is_warmed_all_the_same() -> None:
    """T22 is the case in point, and :func:`neighbours` returns nothing for it.

    Its window is a literal ``forward_window(2, ctx)`` inside the oracle, so
    moving a parameter warms nothing and coverage was a spike at gold's own span.
    What is asserted is the repair's shape rather than a number: no neighbours,
    and a window list that nonetheless contains gold's two-day span along with the
    spans either side of it.
    """
    for case in _by_template("T22"):
        inputs = case["inputs"]
        assert capture.neighbours(inputs) == []

        windows = _windows(inputs)
        as_of = _as_instant(inputs["as_of"]).date()
        gold = (as_of.isoformat(), (as_of + timedelta(days=1)).isoformat())

        assert len(windows) == capture.FORWARD_SPAN_CEILING
        assert gold in windows
        assert windows != [gold]
        assert len({window[1] for window in windows}) == len(windows)


def test_a_case_whose_oracle_fetches_nothing_still_gets_its_horizon_warmed() -> None:
    """T18b's gold trajectory is empty, and that is what left it uncovered.

    The oracle grounds the abstention in ``DailyWeatherRow`` and makes no call, so
    warming *through* the oracle recorded nothing for any of its eight cases —
    while the rollout that consults the tool before abstaining issues an Archive
    request. The window list is what closed it, and it must span the case's own
    horizon on both sides.

    Since T146 the weather half of that is the day band's job rather than this
    list's, and T18b carries no model pin — so what this asserts of the list now
    is that it still *reaches* the family's horizon, which is what makes the band
    below wide enough to contain it.
    """
    for case in _by_template("T18b"):
        inputs = case["inputs"]
        horizon = int(inputs["params"]["d"])
        ends = [window[1] for window in _windows(inputs)]

        assert len(ends) >= horizon + max(capture.NEIGHBOURHOOD)
        assert capture.MODEL_PIN not in case["expectations"]["pins"]


# ── The band the weather warm reaches, which the window list does not ────────


def test_the_day_band_contains_every_window_the_forward_sweep_warms() -> None:
    """The band has to subsume the list, or T146 traded coverage for a smaller key.

    Weather is warmed once over ``as_of ± WARM_BAND_DAYS`` and assembled per day,
    so every window the GR2L sweep still enumerates must be assemblable from days
    the band holds. Checked over the whole committed suite rather than one case,
    because the sweep's ceiling depends on the case's own day count.
    """
    for case in _cases():
        inputs = case["inputs"]
        as_of = _as_instant(inputs["as_of"]).date()
        band = {
            (as_of + timedelta(days=offset)).isoformat()
            for offset in range(-capture.WARM_BAND_DAYS, capture.WARM_BAND_DAYS + 1)
        }
        for start, end in _windows(inputs):
            assert set(days_in_window(start, end)) <= band, (
                f"{inputs['case_id']}: {start}..{end} reaches outside the warmed band"
            )


def test_the_band_reaches_the_backward_axis_the_sweep_never_did() -> None:
    """T25's own gold route opens the day *before* ``as_of`` — the gap T146 closed.

    ``resolve_window(past_days=1, forecast_days=2)`` is a window no forward sweep
    can produce, and its neighbours missed on three of five cases while the cache
    was keyed on windows. It is inside the band by construction now, and that is
    the assertion: not that a request was warmed, but that the day it needs is one
    the band holds.
    """
    for case in _by_template("T25"):
        as_of = _as_instant(case["inputs"]["as_of"]).date()
        opened = (as_of - timedelta(days=1)).isoformat()

        assert opened not in {window[0] for window in _windows(case["inputs"])}
        assert 1 <= capture.WARM_BAND_DAYS, "the band must reach at least one day back"
        assert opened >= (as_of - timedelta(days=capture.WARM_BAND_DAYS)).isoformat()


def test_every_forward_window_opens_on_the_cases_own_day_and_stays_inside_the_horizon() -> None:
    """Two invariants that hold for every case in the suite, not one family's.

    A rollout's relative window is ``resolve_window(forecast_days=F, today=as_of)``
    and that always opens on ``as_of``; a window past the tool's 16-day limit is a
    ``not_available`` the tool types before it fetches, so warming one would
    record a request nothing ever makes.
    """
    for case in _cases():
        inputs = case["inputs"]
        as_of = _as_instant(inputs["as_of"]).date()
        windows = _windows(inputs)

        assert windows, inputs["case_id"]
        for start, end in windows:
            assert start == as_of.isoformat()
            assert not capture.beyond_horizon(end, as_of)


# ── The override half ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("template_id", "argument"),
    [
        ("T22", "albedo"),
        ("T23", "initial_soil_moisture_pct"),
        ("T21", "forcings"),
        ("T26", "forcings"),
    ],
)
def test_a_counterfactual_cases_own_override_is_read_off_its_params(
    template_id: str, argument: str
) -> None:
    """The override reaches the warm in the tool's vocabulary, never a second one.

    ``forcings`` in particular is built by the oracles' own ``rain_forcing`` — a
    second spelling of ``{"precip": {day: mm}}`` here is the translation table
    ``decisions.md`` § GR2L argument surface rejects, in the one place nothing
    would notice it.
    """
    for case in _by_template(template_id):
        overrides = capture.model_overrides(case["inputs"])

        params = case["inputs"]["params"]
        assert argument in overrides, case["inputs"]["case_id"]
        if argument != "forcings":
            continue
        forced_day = _as_instant(case["inputs"]["as_of"]).date() + timedelta(
            days=int(params["offset"])
        )
        assert overrides["forcings"] == {
            "precip": {forced_day.isoformat(): float(params["mm"])}
        }


def test_family_d_names_no_override_so_the_baseline_is_the_whole_warm() -> None:
    """T09 and T10 call the model without an argument to override anything.

    An empty override map is what makes the warm one run per window rather than
    two, and it is also the assertion that ``model_overrides`` reads params rather
    than guessing from the family.
    """
    for template_id in ("T09", "T10"):
        for case in _by_template(template_id):
            assert capture.model_overrides(case["inputs"]) == {}
            assert capture.named_roofs(case["inputs"])


def test_the_model_warm_is_gated_on_the_pin_the_oracle_stamped() -> None:
    """A case is warmed against GR2L exactly where its answer came from GR2L.

    The gate is ``expectations.pins``, not a list of template ids: the emitted
    cases already answer the question, and a list here would go stale the first
    time a family gained or lost a model call. Asserted both ways — the model
    families carry the pin, and a family that never reaches the service does not.
    """
    modelled = {"T09", "T10", "T19", "T21", "T22", "T23", "T26"}
    for case in _cases():
        stamped = capture.MODEL_PIN in case["expectations"]["pins"]
        assert stamped is (case["inputs"]["template_id"] in modelled), case["inputs"][
            "case_id"
        ]


def test_a_roof_the_model_declines_never_reaches_the_warm() -> None:
    """T27 names the gravel roof and the wetland, and both are out of GR2L's scope.

    ``named_roofs`` filters through the oracles' own ``modellable_roof``, which is
    the tool's scope table read the way the tool reads it, so a warm can never
    record a request the tool would have refused before fetching.
    """
    for case in _by_template("T27"):
        assert capture.named_roofs(case["inputs"]) == []
