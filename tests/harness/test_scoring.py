"""The four metrics of §7, each on the edge case that is the point of it.

A metric that only ever sees the case it was written for proves nothing, so
every one of them is exercised on the boundary it exists to draw:

* **answer** — compared after unit normalization, and only ``L``↔``mm`` and
  ``%θ``↔``%`` normalize;
* **trajectory** — binary, so two thirds of a gold set is 0 and three extra
  calls are free;
* **card recall** — graded, over the union of every lookup call;
* **abstention** — accuracy and false abstention reported apart, never blended.

Plus the skip both skippable metrics go through, the exclusion the aggregates
apply, and one end-to-end pass that scores a :class:`CaseResult` a real rollout
produced rather than one this file wrote.
"""

from __future__ import annotations

import json

import pytest

from harness.run_case import CaseResult, Exclusion, ToolCall, run_case
from harness.scoring import (
    ABSTENTION,
    ANSWER,
    CARD_RECALL,
    SELECTION_WEIGHTS,
    SKIPPED,
    TRAJECTORY,
    aggregate,
    aggregate_arms,
    aggregate_scores,
    extra_calls,
    score_abstention,
    score_answer,
    score_card_recall,
    score_case,
    score_trajectory,
)
from tests.harness.conftest import Call, Say, scripted

AS_OF = "2025-08-14T08:00:00+02:00"


def result(**overrides: object) -> CaseResult:
    """One recorded run, defaulting to an unexcluded one that answered nothing.

    ``parse_failure`` is derived from the contract rather than defaulted, so a
    fixture cannot claim a diagnostic a real rollout would never have recorded.
    """
    fields: dict[str, object] = {
        "case_id": "T12-0001",
        "template_id": "T12",
        "contract": None,
        "final_text": "",
        "trajectory": (),
        "harness_error": False,
        "exclusions": (),
        "diagnostics": {
            "steps": 0,
            "tokens": {"total": 0},
            "latency_s": 0.0,
            "parse_failure": overrides.get("contract") is None,
            "fixer_iterations": None,
        },
    }
    fields.update(overrides)
    return CaseResult(**fields)  # type: ignore[arg-type]


def answered(
    answer: object = True, unit: str | None = None, status: str = "answered"
) -> CaseResult:
    """A run whose final message carried the given contract, parsed for real."""
    from harness.contract import parse_contract

    text = json.dumps(
        {"status": status, "answer": answer, "unit": unit, "explanation": "…"}
    )
    return result(contract=parse_contract(text), final_text=text)


def expectations(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "status": "answered",
        "answer": True,
        "unit": None,
        "answer_metric": "scored",
        "tolerance": {"kind": "exact"},
        "expected_tool_calls": [],
        "must_not_tools": [],
        "gold_cards": [],
        "argument_checks": [],
        "pins": {},
    }
    fields.update(overrides)
    return fields


# ── Answer: compared after unit normalization ────────────────────────────────


@pytest.mark.parametrize(
    ("oracle_unit", "given_unit"),
    [("mm", "L"), ("L", "mm"), ("%", "%θ"), ("%θ", "%"), ("mm", "mm")],
)
def test_the_normalization_table_makes_two_spellings_one_unit(
    oracle_unit: str, given_unit: str
) -> None:
    score = score_answer(
        answered(12.5, given_unit),
        expectations(answer=12.5, unit=oracle_unit, tolerance={"kind": "exact"}),
    )

    assert score.value == 1.0


@pytest.mark.parametrize(
    ("oracle_unit", "given_unit"),
    [("pp", "mm"), ("mm", "pp"), ("%", "pp"), ("mm", "°C"), ("mm", None)],
)
def test_nothing_else_converts(oracle_unit: str, given_unit: str | None) -> None:
    """A millimetre answer to a percentage-point oracle is wrong, not rescalable."""
    score = score_answer(
        answered(12.5, given_unit),
        expectations(answer=12.5, unit=oracle_unit, tolerance={"kind": "exact"}),
    )

    assert score.value == 0.0
    assert "rescales" in score.rationale


def test_the_unit_is_compared_before_the_number_is() -> None:
    """The right number in the wrong quantity is wrong, however close it is."""
    score = score_answer(
        answered(30.0, "%"),
        expectations(answer=30.0, unit="mm", tolerance={"kind": "abs", "value": 0.5}),
    )

    assert score.value == 0.0


@pytest.mark.parametrize(
    ("given", "tolerance", "expected_value"),
    [
        (40.05, {"kind": "abs", "value": 0.1}, 1.0),
        (40.2, {"kind": "abs", "value": 0.1}, 0.0),
        (40.5, {"kind": "rel", "value": 0.02}, 1.0),
        (42.0, {"kind": "rel", "value": 0.02}, 0.0),
        (40.0, {"kind": "exact"}, 1.0),
        (40.000001, {"kind": "exact"}, 0.0),
    ],
)
def test_a_number_is_compared_under_its_own_tolerance(
    given: float, tolerance: dict[str, object], expected_value: float
) -> None:
    score = score_answer(
        answered(given, "mm"),
        expectations(answer=40.0, unit="mm", tolerance=tolerance),
    )

    assert score.value == expected_value


def test_a_yes_no_answer_is_never_a_number() -> None:
    """``0`` is not ``False`` here, or every wrong number would be a right no."""
    assert score_answer(answered(0, None), expectations(answer=False)).value == 0.0
    assert score_answer(answered(False, None), expectations(answer=False)).value == 1.0


def test_a_date_answer_is_compared_as_written() -> None:
    exp = expectations(answer="2025-07-21", unit=None)

    assert score_answer(answered("2025-07-21"), exp).value == 1.0
    assert score_answer(answered("2025-07-22"), exp).value == 0.0


def test_a_parse_failure_is_a_wrong_answer_and_says_so() -> None:
    """Scored 0 — but named, so the format channel stays legible beside it."""
    score = score_answer(result(contract=None), expectations(answer=True))

    assert score.value == 0.0
    assert "parse_failure" in score.rationale


# ── Answer: skipped, with coverage, where the oracle answer is null ──────────


def test_a_null_oracle_answer_skips_rather_than_scoring_zero() -> None:
    """The plot family's deliverable is the chart; there is no answer to compare."""
    score = score_answer(
        answered(None, None),
        expectations(answer=None, answer_metric=SKIPPED, tolerance=None),
    )

    assert score.value == SKIPPED
    assert score.skipped and score.score is None


def test_the_skip_is_reported_as_coverage_not_folded_into_the_mean() -> None:
    plot = score_case(
        answered(None, None),
        expectations(answer=None, answer_metric=SKIPPED, tolerance=None),
    )
    wrong = score_case(
        answered(1.0, "mm"),
        expectations(answer=2.0, unit="mm", tolerance={"kind": "exact"}),
    )

    report = aggregate([plot, wrong])

    assert report.answer.scored == 1
    assert report.answer.skipped == 1
    assert report.answer.coverage == 0.5
    # The mean is over the one case that had an answer, not over both.
    assert report.answer.mean == 0.0


def test_a_skipped_metric_does_not_drag_the_selection_scalar_down() -> None:
    """Dropped and renormalized — a plot case is not penalized for having no answer."""
    perfect_with_answer = aggregate_scores(
        {ANSWER: 1.0, TRAJECTORY: 1.0, CARD_RECALL: 1.0, ABSTENTION: 1.0}
    )
    perfect_plot = aggregate_scores(
        {ANSWER: SKIPPED, TRAJECTORY: 1.0, CARD_RECALL: SKIPPED, ABSTENTION: 1.0}
    )

    assert perfect_with_answer == pytest.approx(1.0)
    assert perfect_plot == pytest.approx(1.0)


def test_the_remaining_weights_keep_their_ratio_when_one_skips() -> None:
    blended = aggregate_scores({ANSWER: SKIPPED, TRAJECTORY: 1.0, ABSTENTION: 0.0})
    share = SELECTION_WEIGHTS[TRAJECTORY] / (
        SELECTION_WEIGHTS[TRAJECTORY] + SELECTION_WEIGHTS[ABSTENTION]
    )

    assert blended == pytest.approx(share)


def test_an_unweighted_scorer_name_is_a_wiring_fault_not_a_silent_zero() -> None:
    with pytest.raises(ValueError, match="No selection weight"):
        aggregate_scores({ANSWER: 1.0, "novel_metric": 1.0})

    # Including on the record where it happened to skip: a metric nobody
    # weighted is a wiring fault whatever it returned.
    with pytest.raises(ValueError, match="No selection weight"):
        aggregate_scores({ANSWER: 1.0, "novel_metric": SKIPPED})


def test_a_non_numeric_value_that_is_not_a_skip_raises() -> None:
    with pytest.raises(ValueError, match="neither a number"):
        aggregate_scores({ANSWER: "excellent"})


def test_a_record_on_which_everything_skipped_has_nothing_to_select_on() -> None:
    with pytest.raises(ValueError, match="Every metric skipped"):
        aggregate_scores({ANSWER: SKIPPED, CARD_RECALL: SKIPPED})


def test_the_objective_reads_a_feedback_wrapper_as_its_value() -> None:
    """T122 hands over ``Feedback`` objects; this module never imports MLflow."""

    class Feedback:
        def __init__(self, value: float) -> None:
            self.value = value

    assert aggregate_scores(
        {TRAJECTORY: Feedback(1.0), ABSTENTION: Feedback(0.0)}
    ) == pytest.approx(
        SELECTION_WEIGHTS[TRAJECTORY]
        / (SELECTION_WEIGHTS[TRAJECTORY] + SELECTION_WEIGHTS[ABSTENTION])
    )


# ── Trajectory: binary, no partial credit, no extra-call penalty ─────────────


def test_extra_calls_are_free() -> None:
    run = result(
        trajectory=(
            ToolCall("lookup_reference", {"topic": "retention_target"}),
            ToolCall("get_weather_forecast_tool", {"past_days": 7}),
            ToolCall("text_to_sql_agent", {"request": "…"}),
            ToolCall("calc_irrigation", {"roof": "extensive"}),
        )
    )
    score = score_trajectory(
        run,
        expectations(expected_tool_calls=[{"name": "lookup_reference"}]),
    )

    assert score.value == 1.0


def test_two_thirds_of_a_gold_set_is_zero_not_two_thirds() -> None:
    run = result(
        trajectory=(
            ToolCall("lookup_reference", {"topic": "retention_target"}),
            ToolCall("text_to_sql_agent", {"request": "…"}),
        )
    )
    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[
                {"name": "lookup_reference"},
                {"name": "text_to_sql_agent"},
                {"name": "calc_irrigation"},
            ]
        ),
    )

    assert score.value == 0.0
    assert "calc_irrigation" in score.rationale


def test_a_must_not_tool_is_the_only_route_error_that_scores() -> None:
    run = result(trajectory=(ToolCall("get_weather_forecast_tool", {"past_days": 3}),))

    unlisted = score_trajectory(run, expectations(expected_tool_calls=[]))
    listed = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[], must_not_tools=["get_weather_forecast_tool"]
        ),
    )

    assert unlisted.value == 1.0
    assert listed.value == 0.0


def test_an_empty_gold_set_with_no_must_not_is_satisfied_by_anything() -> None:
    assert score_trajectory(result(), expectations()).value == 1.0


def test_the_diagnostic_counts_the_extra_calls_the_metric_forgave() -> None:
    trajectory = (
        ToolCall("lookup_reference", {"topic": "retention_target"}),
        ToolCall("lookup_reference", {"topic": "irrigation_rule"}),
        ToolCall("calc_irrigation", {}),
    )

    assert extra_calls(trajectory, ["lookup_reference"]) == 2
    assert extra_calls(trajectory, ["lookup_reference", "calc_irrigation"]) == 1
    # A gold tool that never ran does not earn the run a discount.
    assert extra_calls(trajectory, ["text_to_sql_agent"]) == 3


# ── Trajectory: the declarative argument checks ──────────────────────────────


def test_a_wildcard_collects_one_field_across_every_series() -> None:
    """``series.*.roof`` is the roofs of the call, in no particular order.

    The catalog scores a plot's roofs "as a set match" (``questions.md`` §2 H),
    and neither of the other two addresses can say that: an index imposes an
    order the request does not have, and ``set_eq`` on ``series`` itself compares
    whole declarations, so a candidate that added an optional key it was entitled
    to add would fail a check about roofs.
    """
    run = result(
        trajectory=(
            ToolCall(
                "plot_timeseries",
                {
                    "series": [
                        {"source": "measured", "roof": "gravel", "kind": "line"},
                        {"source": "measured", "roof": "irrigated_extensive"},
                    ]
                },
            ),
        )
    )
    check = {"tool": "plot_timeseries", "path": "series.*.roof", "op": "set_eq"}

    # The declared order is the reverse of the case's, and it still matches.
    passing = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {**check, "value": ["irrigated_extensive", "gravel"]}
            ],
        ),
    )
    wrong_roof = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[{**check, "value": ["gravel", "semi_intensive"]}],
        ),
    )

    assert passing.value == 1.0
    assert wrong_roof.value == 0.0


def test_a_wildcard_skips_a_member_that_has_no_such_field() -> None:
    """A weather series carries no roof, and that is not a missing argument.

    Collecting it as ``None`` would make every mixed chart fail a roof check;
    skipping it makes ``series.*.roof`` a statement about the series that have
    roofs, which is what the check is about.
    """
    run = result(
        trajectory=(
            ToolCall(
                "plot_timeseries",
                {
                    "series": [
                        {"source": "weather", "variable": "precip"},
                        {"source": "measured", "roof": "gravel"},
                    ]
                },
            ),
        )
    )

    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {
                    "tool": "plot_timeseries",
                    "path": "series.*.roof",
                    "op": "set_eq",
                    "value": ["gravel"],
                }
            ],
        ),
    )

    assert score.value == 1.0


def test_an_eq_check_reads_a_dotted_path_into_the_call() -> None:
    run = result(
        trajectory=(
            ToolCall("plot_timeseries", {"series": [{"source": "measured"}]}),
        )
    )
    check = {"tool": "plot_timeseries", "path": "series.0.source", "op": "eq"}

    passing = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[{**check, "value": "measured"}],
        ),
    )
    failing = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[{**check, "value": "model"}],
        ),
    )

    assert passing.value == 1.0
    assert failing.value == 0.0


def test_a_set_eq_check_ignores_the_order_a_selection_was_written_in() -> None:
    run = result(
        trajectory=(
            ToolCall(
                "plot_timeseries",
                {"series": [{"variable": "runoff"}, {"variable": "precip"}]},
            ),
        )
    )
    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {
                    "tool": "plot_timeseries",
                    "path": "series",
                    "op": "set_eq",
                    "value": [{"variable": "precip"}, {"variable": "runoff"}],
                }
            ],
        ),
    )

    assert score.value == 1.0


def test_a_present_check_wants_the_value_to_exist_and_be_plausible() -> None:
    check = {
        "tool": "predict_green_roof_water_balance_tool",
        "path": "initial_soil_moisture_pct",
        "op": "present",
        "plausible": {"min": 0, "max": 60},
    }
    exp = expectations(
        expected_tool_calls=[{"name": "predict_green_roof_water_balance_tool"}],
        argument_checks=[check],
    )

    def run(args: dict[str, object]) -> CaseResult:
        return result(
            trajectory=(
                ToolCall("predict_green_roof_water_balance_tool", args),
            )
        )

    assert score_trajectory(run({"initial_soil_moisture_pct": 22.0}), exp).value == 1.0
    assert score_trajectory(run({"initial_soil_moisture_pct": 900.0}), exp).value == 0.0
    assert score_trajectory(run({"roof_type": "extensive"}), exp).value == 0.0


def test_a_present_check_on_a_wildcard_that_collects_nothing_fails() -> None:
    """A wildcard returns ``[]``, not ``_MISSING``, when no member carries it.

    Left as an absence only for ``_MISSING``/``None``, ``present`` over a wildcard
    would be a check no call could fail: it would pass for any call that merely
    passed a list, which the tool already requires.
    """
    check = {"tool": "plot_timeseries", "path": "series.*.roof", "op": "present"}
    exp = expectations(
        expected_tool_calls=[{"name": "plot_timeseries"}], argument_checks=[check]
    )

    def run(series: list[dict[str, object]]) -> CaseResult:
        return result(trajectory=(ToolCall("plot_timeseries", {"series": series}),))

    roofless = run([{"source": "weather", "variable": "precip"}])
    carried = run([{"source": "measured", "roof": "gravel"}])

    assert score_trajectory(roofless, exp).value == 0.0
    assert "no member of the list carries it" in score_trajectory(roofless, exp).rationale
    assert score_trajectory(carried, exp).value == 1.0


def test_a_check_passes_where_any_call_satisfies_it() -> None:
    """An extra call with other arguments must not fail a check the run met."""
    run = result(
        trajectory=(
            ToolCall("plot_timeseries", {"series": [{"source": "weather"}]}),
            ToolCall("plot_timeseries", {"series": [{"source": "measured"}]}),
        )
    )
    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {
                    "tool": "plot_timeseries",
                    "path": "series.0.source",
                    "op": "eq",
                    "value": "measured",
                }
            ],
        ),
    )

    assert score.value == 1.0


def test_a_fumble_repaired_on_a_later_call_still_passes() -> None:
    """The reason the quantifier is existential over calls at all.

    The pilot's own plotting rollouts grope: a wrong variable name, an
    ``invalid_argument`` rejection, then the right call. That is a self-repair and
    ``decisions.md`` § Trajectory scoring and routing probes makes it free.
    """
    run = result(
        trajectory=(
            ToolCall(
                "plot_timeseries",
                {"series": [{"variable": "soil_moisture_pct", "roof": "gravel"}]},
            ),
            ToolCall(
                "plot_timeseries",
                {"series": [{"variable": "soil_moisture", "roof": "gravel"}]},
            ),
        )
    )
    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {
                    "tool": "plot_timeseries",
                    "path": "series.*.variable",
                    "op": "set_eq",
                    "value": ["soil_moisture"],
                },
                {
                    "tool": "plot_timeseries",
                    "path": "series.*.roof",
                    "op": "set_eq",
                    "value": ["gravel"],
                },
            ],
        ),
    )

    assert score.value == 1.0


def test_a_pass_cannot_be_assembled_from_fragments_of_two_calls() -> None:
    """One call must satisfy every check of its group — no correct call, no pass.

    Per-check search over the trajectory would score this run 1: the roof check
    finds its call, the variable check finds the other, and the candidate never
    made the call the case is about. On the plotting family the argument checks
    are the whole scored surface (§7), and on T26(i) the same hole would pass the
    compositional holdout on ``albedo`` set in one call and ``forcings`` in
    another.
    """
    run = result(
        trajectory=(
            ToolCall(
                "plot_timeseries",
                {
                    "series": [
                        {"variable": "soil_moisture", "roof": "semi_intensive"}
                    ]
                },
            ),
            ToolCall(
                "plot_timeseries",
                {"series": [{"variable": "runoff", "roof": "gravel"}]},
            ),
        )
    )
    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {
                    "tool": "plot_timeseries",
                    "path": "series.*.variable",
                    "op": "set_eq",
                    "value": ["soil_moisture"],
                },
                {
                    "tool": "plot_timeseries",
                    "path": "series.*.roof",
                    "op": "set_eq",
                    "value": ["gravel"],
                },
            ],
        ),
    )

    assert score.value == 0.0
    assert "no single call satisfied all 2 checks" in score.rationale


def test_a_group_lets_a_template_ask_for_two_calls_to_one_tool() -> None:
    """T26(ii) compares two roofs, which is two runs and cannot be one call.

    Without the group key the tightened rule would make a cross-roof comparison
    unsatisfiable — the two ``roof_type`` checks would contradict each other
    inside one call — so the fix would have closed a hole by voiding a template.
    """
    tool = "predict_green_roof_water_balance_tool"
    run = result(
        trajectory=(
            ToolCall(tool, {"roof_type": "irrigated_extensive", "albedo": 0.3}),
            ToolCall(tool, {"roof_type": "non_irrigated_extensive", "albedo": 0.3}),
        )
    )
    checks = [
        {"tool": tool, "group": "a", "path": "roof_type", "op": "eq",
         "value": "irrigated_extensive"},
        {"tool": tool, "group": "a", "path": "albedo", "op": "present",
         "plausible": {"min": 0.0, "max": 1.0}},
        {"tool": tool, "group": "b", "path": "roof_type", "op": "eq",
         "value": "non_irrigated_extensive"},
        {"tool": tool, "group": "b", "path": "albedo", "op": "present",
         "plausible": {"min": 0.0, "max": 1.0}},
    ]
    exp = expectations(
        expected_tool_calls=[{"name": tool}], argument_checks=checks
    )

    assert score_trajectory(run, exp).value == 1.0

    # The second roof never ran: its group has no satisfying call, and the
    # rationale names the group rather than the tool alone.
    half = result(
        trajectory=(ToolCall(tool, {"roof_type": "irrigated_extensive", "albedo": 0.3}),)
    )
    score = score_trajectory(half, exp)
    assert score.value == 0.0
    assert f"{tool}[b]" in score.rationale


def test_a_relative_window_scores_identically_to_the_dates_it_denotes() -> None:
    """Or the plotting family measures date arithmetic, not source selection."""
    checks = [
        {
            "tool": "plot_timeseries",
            "path": "start_date",
            "op": "eq",
            "value": "2025-08-07",
            "resolve": "window",
        },
        {
            "tool": "plot_timeseries",
            "path": "end_date",
            "op": "eq",
            "value": "2025-08-13",
            "resolve": "window",
        },
    ]
    exp = expectations(
        expected_tool_calls=[{"name": "plot_timeseries"}], argument_checks=checks
    )
    absolute = result(
        trajectory=(
            ToolCall(
                "plot_timeseries",
                {"start_date": "2025-08-07", "end_date": "2025-08-13"},
            ),
        )
    )
    relative = result(
        trajectory=(ToolCall("plot_timeseries", {"past_days": 7}),)
    )

    assert score_trajectory(absolute, exp, as_of=AS_OF).value == 1.0
    assert score_trajectory(relative, exp, as_of=AS_OF).value == 1.0


def test_a_window_check_refuses_to_resolve_against_the_host_clock() -> None:
    run = result(trajectory=(ToolCall("plot_timeseries", {"past_days": 7}),))
    exp = expectations(
        expected_tool_calls=[{"name": "plot_timeseries"}],
        argument_checks=[
            {
                "tool": "plot_timeseries",
                "path": "start_date",
                "op": "eq",
                "value": "2025-08-07",
                "resolve": "window",
            }
        ],
    )

    with pytest.raises(ValueError, match="as_of"):
        score_trajectory(run, exp)


def test_a_window_that_does_not_resolve_fails_its_check_rather_than_raising() -> None:
    run = result(
        trajectory=(
            ToolCall("plot_timeseries", {"start_date": "2025-08-07", "past_days": 7}),
        )
    )
    score = score_trajectory(
        run,
        expectations(
            expected_tool_calls=[{"name": "plot_timeseries"}],
            argument_checks=[
                {
                    "tool": "plot_timeseries",
                    "path": "start_date",
                    "op": "eq",
                    "value": "2025-08-07",
                    "resolve": "window",
                }
            ],
        ),
        as_of=AS_OF,
    )

    assert score.value == 0.0


# ── Card recall: graded, over the union of every lookup call ─────────────────


def test_card_recall_is_graded_where_the_binary_trajectory_cannot_be() -> None:
    run = result(
        trajectory=(ToolCall("lookup_reference", {"topic": "retention_target"}),)
    )
    score = score_card_recall(
        run, expectations(gold_cards=["retention_target", "irrigation_rule"])
    )

    assert score.value == pytest.approx(0.5)
    assert "irrigation_rule" in score.rationale


def test_recall_is_the_union_of_every_lookup_call_not_the_last_one() -> None:
    run = result(
        trajectory=(
            ToolCall("lookup_reference", {"topic": "retention_target"}),
            ToolCall("text_to_sql_agent", {"request": "…"}),
            ToolCall("lookup_reference", {"topic": "irrigation_rule"}),
        )
    )
    score = score_card_recall(
        run, expectations(gold_cards=["retention_target", "irrigation_rule"])
    )

    assert score.value == 1.0


def test_a_card_the_case_never_wanted_costs_nothing() -> None:
    run = result(
        trajectory=(
            ToolCall("lookup_reference", {"topic": "retention_target"}),
            ToolCall("lookup_reference", {"topic": "et0_method"}),
        )
    )

    score = score_card_recall(run, expectations(gold_cards=["retention_target"]))

    assert score.value == 1.0


# ── Card recall: skipped, with coverage, on an empty gold set ────────────────


def test_an_empty_gold_card_set_skips_through_the_same_mechanism() -> None:
    """Same value, same aggregation branch, same coverage report as the answer skip."""
    score = score_card_recall(result(), expectations(gold_cards=[]))

    assert score.value == SKIPPED


def test_both_skips_report_coverage_on_the_same_arm() -> None:
    plot = score_case(
        answered(None, None),
        expectations(answer=None, answer_metric=SKIPPED, gold_cards=[]),
    )
    lookup = score_case(
        answered(True, None),
        expectations(
            answer=True,
            gold_cards=["retention_target"],
            expected_tool_calls=[{"name": "lookup_reference"}],
        ),
    )

    report = aggregate([plot, lookup])

    assert (report.answer.scored, report.answer.skipped) == (1, 1)
    assert (report.card_recall.scored, report.card_recall.skipped) == (1, 1)
    assert report.card_recall.coverage == 0.5


# ── Abstention: two numbers, never one ───────────────────────────────────────


def test_abstention_scores_the_agents_own_status() -> None:
    unanswerable = expectations(status="not_available", answer=None)

    abstained = answered(None, status="not_available")

    assert score_abstention(abstained, unanswerable).value == 1.0
    assert score_abstention(answered(3.0, "mm"), unanswerable).value == 0.0


def test_accuracy_and_the_false_abstention_rate_are_reported_apart() -> None:
    """A candidate that abstains from everything is perfect on one, worst on the
    other — which is the whole reason they are never one number."""
    unanswerable = expectations(status="not_available", answer=None)
    answerable = expectations(status="answered", answer=True)
    always_abstains = [
        score_case(answered(None, status="not_available"), unanswerable),
        score_case(answered(None, status="not_available"), answerable),
        score_case(answered(None, status="not_available"), answerable),
    ]

    report = aggregate(always_abstains)

    assert report.abstention.accuracy == 1.0
    assert report.abstention.unanswerable == 1
    assert report.abstention.false_abstention_rate == 1.0
    assert report.abstention.answerable == 2


def test_a_parse_failure_is_not_a_false_abstention() -> None:
    """It scores 0 on the metric, but it is not the agent declining to answer."""
    answerable = expectations(status="answered", answer=True)
    scored = score_case(result(contract=None), answerable)

    report = aggregate([scored])

    assert scored.metrics[ABSTENTION].value == 0.0
    assert report.abstention.false_abstention_rate == 0.0
    assert report.diagnostics["parse_failures"] == 1


def test_a_population_with_no_cases_reports_none_not_zero() -> None:
    report = aggregate([score_case(answered(True), expectations())])

    assert report.abstention.accuracy is None
    assert report.abstention.unanswerable == 0


# ── Harness exclusion, per arm, broken down by source ────────────────────────


def excluded_case(source: str) -> CaseResult:
    return result(
        harness_error=True,
        exclusions=(
            Exclusion(tool="get_weather_forecast_tool", source=source, details="…"),
        ),
        contract=None,
    )


def test_a_harness_error_leaves_every_aggregate_and_is_counted_by_source() -> None:
    good = score_case(answered(True), expectations(answer=True))
    scored = [
        good,
        score_case(excluded_case("upstream"), expectations(answer=True)),
        score_case(excluded_case("upstream"), expectations(answer=True)),
        score_case(excluded_case("rollout"), expectations(answer=True)),
    ]

    report = aggregate(scored, arm="baseline")

    assert report.cases == 4
    assert report.included == 1
    assert report.excluded == 3
    assert report.exclusions_by_source == {"rollout": 1, "upstream": 2}
    # One included case, correct: no excluded run pulls a mean down.
    assert report.answer.mean == 1.0
    assert report.trajectory.mean == 1.0
    assert report.selection_score == pytest.approx(good.selection_score)
    assert report.diagnostics["parse_failures"] == 0


def test_an_invalid_argument_fumble_stays_in_the_denominator() -> None:
    """Only ``upstream`` excludes; a fumble surfaces as a wrong answer."""
    fumbled = score_case(
        answered(99.0, "mm"),
        expectations(answer=40.0, unit="mm", tolerance={"kind": "abs", "value": 0.1}),
    )

    report = aggregate([fumbled])

    assert report.excluded == 0
    assert report.answer.mean == 0.0


def test_the_arms_are_reported_side_by_side() -> None:
    baseline = [score_case(answered(1.0, "mm"), expectations(answer=2.0, unit="mm"))]
    optimized = [score_case(answered(2.0, "mm"), expectations(answer=2.0, unit="mm"))]

    reports = aggregate_arms({"baseline": baseline, "optimized": optimized})

    assert reports["baseline"].answer.mean == 0.0
    assert reports["optimized"].answer.mean == 1.0
    assert reports["optimized"].arm == "optimized"


def test_fixer_iterations_are_reported_unavailable_rather_than_zero() -> None:
    report = aggregate([score_case(answered(True), expectations())])

    assert report.diagnostics["mean_fixer_iterations"] is None


def test_the_diagnostics_carry_what_is_reported_and_never_scored() -> None:
    run = result(
        contract=answered(True).contract,
        trajectory=(ToolCall("calc_irrigation", {}),),
        diagnostics={
            "steps": 3,
            "tokens": {"total": 1200},
            "latency_s": 2.0,
            "parse_failure": False,
            "step_cap_exceeded": False,
            "fixer_iterations": None,
        },
    )

    report = aggregate([score_case(run, expectations())])

    assert report.diagnostics["mean_steps"] == 3.0
    assert report.diagnostics["mean_tokens"] == 1200.0
    assert report.diagnostics["mean_latency_s"] == 2.0
    assert report.diagnostics["mean_extra_calls"] == 1.0


# ── The seam: a real rollout's result, scored ────────────────────────────────


def test_the_metrics_score_a_result_a_rollout_actually_produced(tmp_path) -> None:
    """The fixtures above are hand-built; this one comes off the shared path.

    Nothing is faked but the model: the card store is the packaged YAML, the
    trajectory is what ADK recorded, and the contract is what the parser read.
    """
    model = scripted(
        Call("lookup_reference", {"topic": "retention_target"}),
        Say(json.dumps({"status": "answered", "answer": 50.0, "unit": "L"})),
    )
    run = run_case(
        {
            "question": "What is the retention target?",
            "as_of": AS_OF,
            "case_id": "T12-0001",
            "template_id": "T12",
            "params": {},
        },
        model=model,
        cache_dir=tmp_path,
    )

    scored = score_case(
        run,
        expectations(
            answer=50.0,
            unit="mm",
            tolerance={"kind": "abs", "value": 0.1},
            expected_tool_calls=[{"name": "lookup_reference"}],
            gold_cards=["retention_target"],
        ),
        as_of=AS_OF,
    )

    assert not run.harness_error
    assert scored.metrics[ANSWER].value == 1.0  # L normalized to mm
    assert scored.metrics[TRAJECTORY].value == 1.0
    assert scored.metrics[CARD_RECALL].value == 1.0
    assert scored.metrics[ABSTENTION].value == 1.0
    assert scored.selection_score == pytest.approx(1.0)
