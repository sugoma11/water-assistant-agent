"""The four invariants a case is held to, each on the window it is meant to catch (T104).

An assertion that never fires is the failure this testbed keeps rejecting
elsewhere: it looks like a guarantee and enforces nothing. So each check is
exercised twice — once on the case that must pass, once on the minimal mutation
that must fail — and the replay one is witnessed on a rollout rather than on a
type.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from harness.assertions import (
    ROOF_POOLS,
    CaseAssertionError,
    Violation,
    assert_case,
    assert_model_replays,
    check_calls,
    check_case,
    check_inputs,
    pool_members,
)
from harness.run_case import ReplayCache, make_case_context, run_case
from tests.harness.conftest import Call, Say, scripted

AS_OF = "2025-08-14T08:00:00+02:00"

CONTRACT = json.dumps({"status": "answered", "answer": True, "unit": None})


def inputs(**overrides: object) -> dict[str, object]:
    return {
        "question": "Did the gravel roof clear the retention target?",
        "as_of": AS_OF,
        "case_id": "T12-0001",
        "template_id": "T12",
        "params": {"roof": "gravel", "event": "2025-07-21..2025-07-22"},
        **overrides,
    }


def case(**overrides: object) -> dict[str, object]:
    return {"inputs": inputs(**overrides), "expectations": {"expected_tool_calls": []}}


# ── Every period parameter is intersected with its own as_of ──────────────────


def test_a_period_inside_the_cut_passes() -> None:
    assert check_inputs(inputs()) == ()


@pytest.mark.parametrize(
    "params",
    [
        {"event": "2025-07-21..2025-08-20"},
        {"event": "2025-08-15"},
        {"window": {"start": "2025-08-01", "end": "2025-09-01"}},
        {"days": ["2025-08-13", "2025-08-15"]},
    ],
)
def test_a_period_reaching_past_the_cut_is_a_violation(params: dict) -> None:
    """Whatever shape the parameter takes, the day it names is what is checked.

    The parameter's *name* is the template author's — `event`, `window`, `days` —
    and the invariant is not, so the days are read off the values.
    """
    (violation,) = check_inputs(inputs(params=params))
    assert violation.check == "period_param_within_as_of"


def test_a_value_that_is_not_a_day_is_passed_over() -> None:
    """A roof name, a number and a template id are not periods and are not checked."""
    assert check_inputs(inputs(params={"roof": "gravel", "n": 12, "id": "T12"})) == ()


def test_the_cut_is_the_sites_own_day() -> None:
    """`as_of` at 00:30 Berlin is the Berlin day it names, whatever offset carries it.

    The same instant written in UTC is the *previous* day there, so reading the
    stamp anywhere but at the site moves the cut for half the year
    (`decisions.md` § The day boundary).
    """
    berlin = inputs(as_of="2025-08-14T00:30:00+02:00", params={"event": "2025-08-14"})
    same_instant_in_utc = inputs(
        as_of="2025-08-13T22:30:00+00:00", params={"event": "2025-08-14"}
    )
    assert check_inputs(berlin) == ()
    assert check_inputs(same_instant_in_utc) == ()


def test_an_as_of_without_an_offset_is_refused() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        check_inputs(inputs(as_of="2025-08-14T08:00:00"))


# ── The roof pool is respected per family ─────────────────────────────────────


def test_the_pools_are_projections_of_the_roof_table() -> None:
    """P1f is the flux-instrumented subset, and it is smaller for a reason.

    The semi-intensive roof has no outflow column, so a template reading one
    cannot sample it — and that follows from `roofs.py` rather than from a second
    hand-written list that could drift from it.
    """
    assert set(ROOF_POOLS["P1f"]) < set(ROOF_POOLS["P1"])
    assert "semi_intensive" not in ROOF_POOLS["P1f"]
    assert set(ROOF_POOLS["P2"]) == {
        "irrigated_extensive",
        "non_irrigated_extensive",
        "semi_intensive",
    }
    assert set(ROOF_POOLS["non_modellable"]) == {"gravel", "wetland"}
    # The two water-balance tools decline exactly what P2 excludes.
    assert set(ROOF_POOLS["P2"]) & set(ROOF_POOLS["non_modellable"]) == set()


def test_a_roof_in_the_pool_passes() -> None:
    assert check_inputs(inputs(), roof_pool="P1f") == ()


def test_a_roof_outside_the_pool_is_a_violation() -> None:
    """The binding case: a modelling family may not sample the gravel roof."""
    (violation,) = check_inputs(inputs(), roof_pool="P2")
    assert violation.check == "roof_pool_respected"
    assert "gravel" in violation.detail
    assert "P2" in violation.detail


def test_the_pool_is_checked_on_every_parameter_that_names_a_roof() -> None:
    """A comparison template names two, and either one can break the pool."""
    params = {"roof_a": "irrigated_extensive", "roof_b": "Kiesdach"}
    (violation,) = check_inputs(inputs(params=params), roof_pool="P2")
    assert "roof_b" in violation.detail
    assert "gravel" in violation.detail, "the German alias must resolve first"


def test_no_pool_means_no_roof_check() -> None:
    """An un-pooled template names no roof, so there is nothing to check."""
    assert check_inputs(inputs()) == ()


def test_an_unknown_pool_is_a_programming_error_not_a_violation() -> None:
    with pytest.raises(ValueError, match="Unknown roof pool"):
        pool_members("P9")


# ── Retrospective comparison windows end at or before as_of ───────────────────


def _comparison(**args: object) -> list[dict]:
    return [
        {
            "name": "predict_green_roof_water_balance_tool",
            "args": {"roof_type": "irrigated_extensive", **args},
        }
    ]


def test_a_comparison_ending_before_the_cut_passes() -> None:
    calls = _comparison(
        evaluate_against_measured=True, start_date="2025-08-01", end_date="2025-08-10"
    )
    assert check_calls(calls, AS_OF) == ()


def test_a_comparison_ending_after_the_cut_is_a_violation() -> None:
    """There is no measured record past the cut to compare a prediction against."""
    calls = _comparison(
        evaluate_against_measured=True, start_date="2025-08-01", end_date="2025-08-20"
    )
    (violation,) = check_calls(calls, AS_OF)
    assert violation.check == "retrospective_window_within_as_of"
    assert "2025-08-20" in violation.detail


def test_a_relative_window_is_resolved_the_way_the_tool_resolves_it() -> None:
    """Through layer 1's own resolver, so the two cannot drift and still pass.

    `past_days=7` ends yesterday and passes; `forecast_days=3` runs from the cut
    forward into days no sensor has recorded, and does not.
    """
    assert check_calls(_comparison(evaluate_against_measured=True, past_days=7), AS_OF) == ()
    (violation,) = check_calls(
        _comparison(evaluate_against_measured=True, past_days=7, forecast_days=3), AS_OF
    )
    assert violation.check == "retrospective_window_within_as_of"


def test_a_forward_window_without_a_comparison_is_not_checked() -> None:
    """The invariant is about the *comparison*, not about the window.

    A forecast run is a legitimate case; it simply has nothing to compare
    against, and it does not ask to.
    """
    assert check_calls(_comparison(forecast_days=7), AS_OF) == ()


def test_a_malformed_comparison_window_is_reported_rather_than_raised() -> None:
    """A half-given window is a defect too, and belongs in the same report."""
    calls = _comparison(evaluate_against_measured=True, start_date="2025-08-01")
    (violation,) = check_calls(calls, AS_OF)
    assert violation.check == "retrospective_window_within_as_of"
    assert "does not resolve" in violation.detail


# ── The whole record, and what raising looks like ─────────────────────────────


def test_check_case_reads_both_halves() -> None:
    record = case()
    record["expectations"] = {
        "expected_tool_calls": _comparison(
            evaluate_against_measured=True, start_date="2025-08-01", end_date="2025-08-20"
        )
    }
    record["inputs"]["params"] = {"roof": "gravel", "event": "2025-09-01"}

    checks = {violation.check for violation in check_case(record, roof_pool="P2")}
    assert checks == {
        "period_param_within_as_of",
        "roof_pool_respected",
        "retrospective_window_within_as_of",
    }


def test_a_clean_case_asserts_silently() -> None:
    assert_case(case(), roof_pool="P1f")


def test_assert_case_names_the_case_and_every_violation() -> None:
    with pytest.raises(CaseAssertionError) as raised:
        assert_case(case(params={"roof": "gravel", "event": "2025-09-01"}), roof_pool="P2")

    assert raised.value.case_id == "T12-0001"
    assert len(raised.value.violations) == 2
    assert "T12-0001" in str(raised.value)


def test_a_violation_carries_what_it_found() -> None:
    (violation,) = check_inputs(inputs(params={"event": "2025-09-01"}))
    assert isinstance(violation, Violation)
    assert "2025-09-01" in violation.detail
    assert "2025-08-14" in violation.detail


# ── The model replays ─────────────────────────────────────────────────────────


def test_a_replay_context_cannot_call_gr2l_out(tmp_path: Path) -> None:
    """Checked at ``ctx.cache``, because that is the seam every model hop goes through."""
    ctx = make_case_context(datetime.fromisoformat(AS_OF), cache_dir=tmp_path)
    assert isinstance(ctx.cache, ReplayCache)
    assert_model_replays(ctx)


def test_a_recording_context_is_refused_as_a_replay_one(tmp_path: Path) -> None:
    """The assertion has to be able to fail, or it is decoration."""
    ctx = make_case_context(
        datetime.fromisoformat(AS_OF), cache_dir=tmp_path, allow_live=True
    )
    with pytest.raises(CaseAssertionError, match="fill a GR2L miss live"):
        assert_model_replays(ctx)


def test_a_replay_context_still_lets_weather_record(tmp_path: Path) -> None:
    """T146's split, asserted where it is made rather than where it is felt.

    The two live dependencies are bound to two different caches by
    :func:`make_case_context`, and the whole change is that they are no longer
    the same object: ``ctx.cache`` refuses a live fill and the weather half's
    does not. Asserted structurally because the alternative — witnessing it on a
    rollout — is a test that reaches Open-Meteo to prove that it can.
    """
    ctx = make_case_context(datetime.fromisoformat(AS_OF), cache_dir=tmp_path)
    archive = ctx.weather._archive  # noqa: SLF001 - the binding is what is asserted

    assert isinstance(ctx.cache, ReplayCache)
    assert archive._allow_live is True  # noqa: SLF001
    assert archive._cache is not ctx.cache  # noqa: SLF001
    assert getattr(archive._cache, "refuses_live", False) is False  # noqa: SLF001


def test_a_replayed_rollout_turns_a_model_miss_into_an_upstream_error(
    tmp_path: Path,
) -> None:
    """The invariant witnessed on a rollout: the miss fails loudly, nothing fetches.

    ``run_gr2l`` passes no ``allow_live`` of its own, so a rule the client had to
    remember would already have a hole in it. Bound at ``ctx.cache`` instead, a
    model run nothing captured comes back as an ``upstream`` error — a harness
    exclusion — rather than as a live call.

    The window is inside the station record on purpose. GR2L fetches its own
    forcing, and since T146 the weather half of a replay context *would* fill a
    day it does not hold; a window the station serves is a pure function of the
    pinned database, so the only thing left that can reach for a socket here is
    the model hop this test is about.
    """
    model = scripted(
        Call(
            "predict_green_roof_water_balance_tool",
            {
                "roof_type": "non_irrigated_extensive",
                "start_date": "2025-08-01",
                "end_date": "2025-08-07",
            },
        ),
        Say(CONTRACT),
    )
    result = run_case(inputs(params={}), model=model, cache_dir=tmp_path / "cache")

    assert result.harness_error is True
    assert result.exclusions[0].source == "upstream"
    assert result.exclusions[0].tool == "predict_green_roof_water_balance_tool"


def test_a_rollout_preflights_its_own_case(tmp_path: Path) -> None:
    """The preflight is on the path, not beside it: a bad case never runs.

    A model that would have raised on being asked for a turn proves the rollout
    stopped before it started.
    """
    model = scripted()
    with pytest.raises(CaseAssertionError):
        run_case(
            inputs(params={"event": "2025-09-01"}),
            model=model,
            cache_dir=tmp_path / "cache",
        )
    assert model.requests == []
