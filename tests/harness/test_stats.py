"""§7's reporting rules as arithmetic, and the three shapes it forbids (T128).

Every test here is over fabricated outcomes, and that is the point: these are
claims about *the statistics*, not about the agent, and a suite that had to run a
model to check an interval would be checking two things at once. The outcomes are
built to make one rule visible at a time.

The rules under test, in §7's own order: the bootstrap resamples ``template_id``
and not ``case_id``; the train number is a selection score; the two gaps are
separate objects; test_unseen's trajectory is a table; the diagnostics ride
beside the metrics with ``parse_failure`` kept apart from answer accuracy.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.measure import BASELINE_ARM, OPTIMIZED_ARM, CaseOutcome
from harness.scoring import ANSWER, SKIPPED, TRAJECTORY
from harness.stats import (
    SELECTION_SCORE,
    abstention_split,
    arm_value,
    bootstrap,
    case_values,
    compare,
    diagnostics,
    gaps,
    render,
    residual_nondeterminism,
    template_means,
    win_loss,
)


def outcome(
    *,
    arm: str = BASELINE_ARM,
    split: str = "test_seen",
    repeat: int = 1,
    case_id: str = "T01-0001",
    template_id: str = "T01",
    metrics: dict[str, Any] | None = None,
    selection_score: float = 1.0,
    harness_error: bool = False,
    unanswerable: bool = False,
    abstained: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> CaseOutcome:
    return CaseOutcome(
        arm=arm,
        split=split,
        repeat=repeat,
        case_id=case_id,
        template_id=template_id,
        metrics=metrics or {ANSWER: 1.0, TRAJECTORY: 1.0},
        selection_score=selection_score,
        harness_error=harness_error,
        unanswerable=unanswerable,
        abstained=abstained,
        diagnostics=diagnostics or {},
    )


def suite(values: dict[tuple[str, str], list[float]], *, split: str = "test_seen",
          repeats: int = 1, metric: str = TRAJECTORY) -> list[CaseOutcome]:
    """Outcomes from ``{(arm, template): [per-case values]}``, repeated *repeats* times."""
    return [
        outcome(
            arm=arm,
            split=split,
            repeat=repeat,
            case_id=f"{template}-{index:04d}",
            template_id=template,
            metrics={metric: value},
            selection_score=value,
        )
        for (arm, template), per_case in values.items()
        for index, value in enumerate(per_case)
        for repeat in range(1, repeats + 1)
    ]


# ── The bootstrap resamples templates, not cases ─────────────────────────────


def test_the_interval_is_over_templates_and_not_over_cases() -> None:
    """The rule with a number attached: case-level resampling reports it far too narrow.

    Two templates, each perfectly consistent across its four instances and
    disagreeing with the other. Resampling the eight *cases* would see eight
    independent observations and a narrow interval; resampling the two
    *templates* sees two, and the interval is as wide as the disagreement
    between them — which is the honest report, because a template that misroutes
    misroutes every instance of it.
    """
    outcomes = suite(
        {
            (BASELINE_ARM, "T01"): [0.0] * 4,
            (BASELINE_ARM, "T02"): [0.0] * 4,
            (OPTIMIZED_ARM, "T01"): [1.0] * 4,
            (OPTIMIZED_ARM, "T02"): [0.0] * 4,
        }
    )

    result = compare(outcomes, split="test_seen", metric=TRAJECTORY)

    assert result.templates == 2
    assert result.cases == 8
    assert result.difference is not None
    assert result.difference.point == pytest.approx(0.5)
    # The two templates disagree completely, so a two-template resample spans the
    # whole range: some draws take T01 twice and some take T02 twice.
    assert result.difference.low == pytest.approx(0.0)
    assert result.difference.high == pytest.approx(1.0)
    assert not result.difference.excludes_zero


def test_a_consistent_difference_across_templates_narrows_the_interval() -> None:
    """The same statistic, spread over templates rather than concentrated in one."""
    outcomes = suite(
        {
            (BASELINE_ARM, f"T{index:02d}"): [0.0] * 4 for index in range(1, 11)
        }
        | {(OPTIMIZED_ARM, f"T{index:02d}"): [1.0] * 4 for index in range(1, 11)}
    )

    result = compare(outcomes, split="test_seen", metric=TRAJECTORY)

    assert result.templates == 10
    assert result.difference is not None
    assert result.difference.point == pytest.approx(1.0)
    assert result.difference.excludes_zero


def test_the_bootstrap_is_reproducible_at_the_registered_seed() -> None:
    """Two runs of one analysis have to produce one interval, or nothing is checkable."""
    per_template = {"T01": 0.1, "T02": -0.3, "T03": 0.7, "T04": 0.0, "T05": 0.2}

    first = bootstrap(per_template)
    second = bootstrap(per_template)

    assert first == second
    assert bootstrap(per_template, rng_seed=7) != first


def test_an_empty_population_has_no_interval_rather_than_a_zero() -> None:
    """Never an interval around a number nobody measured."""
    assert bootstrap({}) is None


# ── The reduction: repeats first, then cases, then templates ─────────────────


def test_the_three_repeats_are_averaged_into_one_value_per_case() -> None:
    """They shrink per-case noise; they do not multiply n. The count stays templates."""
    outcomes = [
        outcome(case_id="T01-0001", metrics={TRAJECTORY: value}, repeat=repeat)
        for repeat, value in enumerate((1.0, 0.0, 1.0), start=1)
    ]

    values = case_values(outcomes, arm=BASELINE_ARM, split="test_seen", metric=TRAJECTORY)

    assert values == {"T01-0001": pytest.approx(2 / 3)}
    assert template_means(values, {"T01-0001": "T01"}) == {"T01": pytest.approx(2 / 3)}


def test_a_skip_is_absent_rather_than_zero() -> None:
    """Scoring a plot family's null answer as 0 would charge it against correct cases."""
    outcomes = [outcome(metrics={ANSWER: SKIPPED, TRAJECTORY: 1.0})]

    assert case_values(outcomes, arm=BASELINE_ARM, split="test_seen", metric=ANSWER) == {}
    assert case_values(
        outcomes, arm=BASELINE_ARM, split="test_seen", metric=TRAJECTORY
    ) == {"T01-0001": 1.0}


def test_a_case_excluded_in_one_arm_leaves_the_pair_in_both() -> None:
    """A case measured in one arm and not the other cannot be differenced.

    Counted rather than absorbed: a comparison that quietly lost its hardest
    cases in one arm is the bias the exclusion rule is otherwise careful about.
    """
    outcomes = [
        outcome(arm=BASELINE_ARM, case_id="T01-0001", metrics={TRAJECTORY: 1.0}),
        outcome(arm=BASELINE_ARM, case_id="T01-0002", metrics={TRAJECTORY: 1.0}),
        outcome(arm=OPTIMIZED_ARM, case_id="T01-0001", metrics={TRAJECTORY: 1.0}),
        outcome(
            arm=OPTIMIZED_ARM,
            case_id="T01-0002",
            metrics={TRAJECTORY: 0.0},
            harness_error=True,
        ),
    ]

    result = compare(outcomes, split="test_seen", metric=TRAJECTORY)

    assert result.cases == 1
    assert result.dropped == 1


# ── The train number is a selection score ────────────────────────────────────


def test_the_two_gaps_are_two_objects_and_only_one_carries_an_interval() -> None:
    """Separate because they catch different failures, and the far one has 7 templates.

    train and test_seen share their 25 templates, so that gap is paired and gets
    an interval. test_seen and test_unseen share none, so that one is a point
    estimate — the same refusal the win/loss table makes one metric down.
    """
    outcomes = (
        suite({(BASELINE_ARM, "T01"): [1.0] * 4, (BASELINE_ARM, "T02"): [1.0] * 4},
              split="train")
        + suite({(BASELINE_ARM, "T01"): [0.5] * 5, (BASELINE_ARM, "T02"): [0.5] * 5},
                split="test_seen")
        + suite({(BASELINE_ARM, "T20"): [0.0] * 8}, split="test_unseen")
    )

    first, second = gaps(outcomes, arm=BASELINE_ARM, metric=SELECTION_SCORE)

    assert (first.frm, first.to) == ("train", "test_seen")
    assert first.gap == pytest.approx(0.5)
    assert first.interval is not None
    assert first.interval.point == pytest.approx(0.5)

    assert (second.frm, second.to) == ("test_seen", "test_unseen")
    assert second.gap == pytest.approx(0.5)
    assert second.interval is None


def test_the_train_number_is_reported_as_a_selection_score() -> None:
    """Never "training accuracy" — the final candidate was selected on those instances."""
    outcomes = suite({(BASELINE_ARM, "T01"): [0.8] * 4}, split="train")

    assert arm_value(
        outcomes, arm=BASELINE_ARM, split="train", metric=SELECTION_SCORE
    ) == pytest.approx(0.8)
    text = render(outcomes)
    assert "train is a SELECTION SCORE" in text
    assert "training accuracy" not in text.lower()


# ── The holdout is described, not inferred ───────────────────────────────────


def test_the_holdout_trajectory_is_a_table_and_never_an_accuracy() -> None:
    """Seven templates support description; the shape of that sentence is a table."""
    outcomes = suite(
        {
            (BASELINE_ARM, "T20"): [0.0] * 8,
            (BASELINE_ARM, "T22"): [1.0] * 8,
            (OPTIMIZED_ARM, "T20"): [1.0] * 8,
            (OPTIMIZED_ARM, "T22"): [0.5] * 8,
        },
        split="test_unseen",
    )

    table = win_loss(outcomes)

    assert [(row.template_id, row.verdict) for row in table] == [
        ("T20", "win"),
        ("T22", "loss"),
    ]
    assert all(row.cases == 8 for row in table)


def test_the_report_says_why_the_holdout_carries_no_interval() -> None:
    """A reader has to be told, or an absent interval reads as an oversight."""
    outcomes = suite(
        {
            (BASELINE_ARM, "T20"): [0.0] * 8,
            (OPTIMIZED_ARM, "T20"): [1.0] * 8,
        },
        split="test_unseen",
    )

    text = render(outcomes)

    assert "no interval — seven templates support description" in text
    assert "PER-TEMPLATE WIN/LOSS, NEVER AN ACCURACY" in text


# ── What the repeats measured, and what rides beside the metrics ─────────────


def test_the_repeats_measure_residual_nondeterminism() -> None:
    """The reason the LLM cache is off: a hit would make this 0 by construction."""
    steady = [
        outcome(case_id="T01-0001", selection_score=1.0, repeat=repeat)
        for repeat in (1, 2, 3)
    ]
    wobbly = [
        outcome(case_id="T01-0002", selection_score=value, repeat=repeat)
        for repeat, value in enumerate((1.0, 0.0, 1.0), start=1)
    ]

    rate, checked = residual_nondeterminism(
        steady + wobbly, arm=BASELINE_ARM, split="test_seen", metric=SELECTION_SCORE
    )

    assert (rate, checked) == (0.5, 2)


def test_one_repeat_measures_no_nondeterminism_and_says_so() -> None:
    """Reported as unmeasured rather than as zero — a single rollout cannot disagree."""
    assert residual_nondeterminism(
        [outcome()], arm=BASELINE_ARM, split="test_seen", metric=SELECTION_SCORE
    ) == (None, 0)


def test_parse_failures_are_counted_apart_from_answer_accuracy() -> None:
    """So a candidate degrading the format stays distinguishable from one degrading reasoning."""
    outcomes = [
        outcome(case_id="T01-0001", diagnostics={"parse_failure": True, "steps": 3}),
        outcome(case_id="T01-0002", diagnostics={"parse_failure": False, "steps": 1}),
    ]

    numbers = diagnostics(outcomes, arm=BASELINE_ARM, split="test_seen")

    assert numbers["parse_failures"] == 1.0
    assert numbers["mean_steps"] == pytest.approx(2.0)
    assert "answer" not in numbers


def test_an_unobservable_diagnostic_stays_none_rather_than_zero() -> None:
    """`fixer_iterations` is not measurable from the root side; a 0 would fabricate it."""
    numbers = diagnostics([outcome()], arm=BASELINE_ARM, split="test_seen")

    assert numbers["mean_fixer_iterations"] is None


def test_the_two_abstention_numbers_never_average() -> None:
    """A candidate abstaining from everything scores 1.0 on the first number."""
    outcomes = [
        outcome(case_id="T17a-1", unanswerable=True, abstained=True),
        outcome(case_id="T17a-2", unanswerable=True, abstained=False),
        outcome(case_id="T01-1", unanswerable=False, abstained=True),
        outcome(case_id="T01-2", unanswerable=False, abstained=False),
    ]

    numbers = abstention_split(outcomes, arm=BASELINE_ARM, split="test_seen")

    assert numbers["accuracy"] == pytest.approx(0.5)
    assert numbers["false_abstention_rate"] == pytest.approx(0.5)
    assert numbers["unanswerable"] == 2
    assert numbers["answerable"] == 2
