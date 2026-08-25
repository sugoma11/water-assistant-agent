"""The four ``Scorer``s, and the aggregation callable without which they raise (T122).

The packet's exit criterion is the second test in this file, and it is run
through MLflow's own ``create_metric_from_scorers`` rather than argued: the same
record, scored twice, once with this repo's :func:`aggregate_scores` and once
with the ``None`` the parameter defaults to. With it, a skipping metric is a
skip. Without it, the same skip is an ``MlflowException`` — which is what
"omitting the argument silently makes the objective the mean of the numeric
values" means in practice, and why the callable is asserted to be passed rather
than assumed.

Everything else here is about the wrapper, not about the metrics: the metrics
themselves are T102's and are tested against their own boundaries in
``test_scoring.py``. What is tested here is the adaptation — the names the
weights are keyed on, the rationale reaching ``Feedback``, and the one input
shape MLflow can hand a scorer that no rollout ever produces.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mlflow.entities import Feedback
from mlflow.exceptions import MlflowException
from mlflow.genai.optimize.util import create_metric_from_scorers

from harness.contract import parse_contract
from harness.predict import outputs
from harness.run_case import CaseResult, Exclusion, ToolCall
from harness.scorers import SCORERS, run_evidence
from harness.scoring import (
    ABSTENTION,
    ANSWER,
    CARD_RECALL,
    METRICS,
    SELECTION_WEIGHTS,
    SKIPPED,
    TRAJECTORY,
    aggregate_scores,
)
from water_assistant_agent.assistant.toolset import (
    LOOKUP_TOOL,
    PLOT_TOOL,
    TEXT_TO_SQL_TOOL,
    WEATHER_TOOL,
)

AS_OF = "2026-03-13T23:00:00+01:00"

INPUTS: dict[str, Any] = {
    "case_id": "T01-0001",
    "template_id": "T01",
    "as_of": AS_OF,
    "question": "What was the wetland roof's outflow in February 2026?",
    "params": {},
}


def rollout(
    *,
    answer: object = 39.4,
    unit: str | None = "L",
    status: str | None = "answered",
    trajectory: tuple[ToolCall, ...] = (),
    exclusions: tuple[Exclusion, ...] = (),
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One rollout's ``outputs``, built the way ``predict_fn`` builds them.

    Through :func:`~harness.predict.outputs` rather than as a hand-written dict,
    so a test cannot assert against a shape the search would never produce.
    """
    text = (
        ""
        if status is None
        else json.dumps(
            {"status": status, "answer": answer, "unit": unit, "explanation": "…"}
        )
    )
    return outputs(
        CaseResult(
            case_id=INPUTS["case_id"],
            template_id=INPUTS["template_id"],
            contract=parse_contract(text),
            final_text=text,
            trajectory=trajectory,
            harness_error=bool(exclusions),
            exclusions=exclusions,
            diagnostics={"steps": len(trajectory), **(diagnostics or {})},
        )
    )


def wants(**overrides: object) -> dict[str, Any]:
    """One case's ``expectations`` in §6.1's envelope."""
    fields: dict[str, Any] = {
        "status": "answered",
        "answer": 39.4,
        "unit": "L",
        "answer_metric": "scored",
        "tolerance": {"kind": "rel", "value": 0.02},
        "expected_tool_calls": [{"name": TEXT_TO_SQL_TOOL}],
        "must_not_tools": [LOOKUP_TOOL],
        "gold_cards": [],
        "argument_checks": [],
        "pins": {},
    }
    fields.update(overrides)
    return fields


def score_with(aggregation: Any, produced: Any, expectations: dict[str, Any]) -> Any:
    """One record through MLflow's own metric — the path ``optimize_prompts`` builds."""
    metric = create_metric_from_scorers(list(SCORERS), aggregation)
    return metric(
        inputs=INPUTS, outputs=produced, expectations=expectations, trace=None
    )


# ── The names the objective is keyed on ──────────────────────────────────────


def test_the_scorer_names_are_the_weight_keys() -> None:
    """A renamed scorer must fail loudly, and this is what makes it.

    ``create_metric_from_scorers`` builds ``{scorer.name: value}`` and hands that
    to the aggregation callable, which raises on a name it has no weight for. So
    the names are not cosmetic: they are the join between the two halves.
    """
    assert tuple(scorer.name for scorer in SCORERS) == METRICS
    assert set(METRICS) == set(SELECTION_WEIGHTS)


# ── The exit criterion: the skip runs through this repo's aggregation ─────────


def test_a_skip_is_a_skip_only_through_this_repos_aggregation_callable() -> None:
    """The packet's exit criterion, both halves of it.

    A plot deliverable is the case: the oracle answer is ``null`` so the answer
    metric skips, and the template wants no card so card recall skips too — both
    of §7's skips on one record, which is the record the two mechanisms have to
    agree on.

    With :func:`aggregate_scores` the record scores on the two metrics that are
    defined, renormalized. With the library default the same record raises,
    because the parameter ships no callable to fall back on and the string is
    not a number. The failure is the assertion: omitting the argument does not
    quietly weight the objective differently here — it stops the search.
    """
    produced = rollout(
        answer=None,
        unit=None,
        trajectory=(ToolCall(name=PLOT_TOOL, args={"series": []}),),
    )
    expectations = wants(
        answer=None,
        unit=None,
        answer_metric=SKIPPED,
        expected_tool_calls=[{"name": PLOT_TOOL}],
        must_not_tools=[],
    )

    score, rationales, individual = score_with(aggregate_scores, produced, expectations)

    # Trajectory and abstention are defined on every case and both are perfect
    # here, so the renormalized objective is 1.0 — not the 0.5 that scoring the
    # two skips as 0 would produce.
    assert score == pytest.approx(1.0)
    # The skips are non-numeric, so they never enter the per-scorer numbers
    # MLflow logs; the four rationales all reach the reflective dataset.
    assert set(individual) == {TRAJECTORY, ABSTENTION}
    assert set(rationales) == set(METRICS)
    assert "nothing for the answer metric to compare" in rationales[ANSWER]
    assert "wants no reference card" in rationales[CARD_RECALL]

    with pytest.raises(MlflowException, match="non-numerical values"):
        score_with(None, produced, expectations)


def test_the_weights_are_renormalized_rather_than_charged_as_zero() -> None:
    """A skipped metric costs the record nothing, which is what "skipped" means.

    The same rollout scored twice: once where the answer metric applies and it
    got the answer wrong, once where the oracle answer is null and the metric
    skips. Card recall skips in both — the template wants no card — so the
    denominator is the weight of the metrics that *applied*, and the skip is
    absent from it rather than present at 0.
    """
    trajectory = (ToolCall(name=TEXT_TO_SQL_TOOL, args={}),)
    wrong = rollout(answer=1.0, trajectory=trajectory)
    scored, _, _ = score_with(aggregate_scores, wrong, wants())
    skipped, _, _ = score_with(
        aggregate_scores,
        rollout(answer=None, unit=None, trajectory=trajectory),
        wants(answer=None, unit=None, answer_metric=SKIPPED),
    )

    earned = SELECTION_WEIGHTS[TRAJECTORY] + SELECTION_WEIGHTS[ABSTENTION]
    assert scored == pytest.approx(earned / (earned + SELECTION_WEIGHTS[ANSWER]))
    assert skipped == pytest.approx(1.0)


# ── The rationale is the reflection signal ───────────────────────────────────


def test_the_trajectory_rationale_is_the_diff_against_gold() -> None:
    """What was called, what was wanted, what was forbidden — in that order."""
    produced = rollout(
        trajectory=(ToolCall(name=LOOKUP_TOOL, args={"topic": "retention_target"}),)
    )

    (feedback,) = [
        scorer.run(inputs=INPUTS, outputs=produced, expectations=wants(), trace=None)
        for scorer in SCORERS
        if scorer.name == TRAJECTORY
    ]

    assert isinstance(feedback, Feedback)
    assert feedback.value == 0.0
    assert TEXT_TO_SQL_TOOL in feedback.rationale
    assert "never called" in feedback.rationale
    assert "must-not tool(s) called" in feedback.rationale


def test_the_answer_rationale_carries_the_sub_agents_sql_error() -> None:
    """§7's "SQL errors" reach reflection, and they reach it once.

    A frozen sub-agent that failed returns ``{"status": "error", …}`` and the
    rollout records it as an exclusion; without it in a rationale the reflective
    dataset sees a wrong answer and no reason for one. It rides on the answer
    metric because that is the score it explains, and because the four
    rationales share one record — repeating it four times would spend the
    reflection model's context on one fact.
    """
    detail = "Binder Error: no such column swc.outflow_l"
    produced = rollout(
        status=None,
        trajectory=(ToolCall(name=TEXT_TO_SQL_TOOL, args={}),),
        exclusions=(
            Exclusion(tool=TEXT_TO_SQL_TOOL, source="text_to_sql_agent", details=detail),
        ),
    )

    _, rationales, _ = score_with(aggregate_scores, produced, wants())

    assert detail in rationales[ANSWER]
    assert "text_to_sql_agent" in rationales[ANSWER]
    assert [name for name in METRICS if detail in rationales[name]] == [ANSWER]


def test_the_evidence_names_the_step_cap_and_says_nothing_when_there_is_none() -> None:
    """A looping candidate is distinguishable from a malformed one, in words.

    Both leave no final message and both read as a parse failure; only the
    diagnostic tells them apart, so it is stated rather than left to be inferred
    from a 0.
    """
    looping = rollout(status=None, diagnostics={"step_cap_exceeded": True})
    clean = rollout()

    from harness.predict import case_result

    assert "tool-step cap" in run_evidence(case_result(looping))
    assert run_evidence(case_result(clean)) == ""


def test_the_skipped_answer_still_reports_what_the_run_hit() -> None:
    """A plot case whose model call failed has a reason, and the skip keeps it.

    The answer metric is the only rationale carrying run evidence, so a skip
    that dropped it would leave the plotting family — whose answer always
    skips — silent about every upstream failure it ever hits.
    """
    produced = rollout(
        answer=None,
        unit=None,
        trajectory=(ToolCall(name=PLOT_TOOL, args={"series": []}),),
        exclusions=(
            Exclusion(tool=PLOT_TOOL, source="upstream", details="cache miss on GR2L"),
        ),
    )

    _, rationales, _ = score_with(
        aggregate_scores,
        produced,
        wants(
            answer=None,
            unit=None,
            answer_metric=SKIPPED,
            expected_tool_calls=[{"name": PLOT_TOOL}],
            must_not_tools=[],
        ),
    )

    assert "nothing for the answer metric to compare" in rationales[ANSWER]
    assert "cache miss on GR2L" in rationales[ANSWER]


def test_the_card_recall_rationale_names_what_was_wanted_and_what_arrived() -> None:
    """Graded, and legible: the union of the lookups against the gold set."""
    produced = rollout(
        trajectory=(
            ToolCall(name=LOOKUP_TOOL, args={"topic": "retention_target"}),
            ToolCall(name=LOOKUP_TOOL, args={"topic": "roof_buildup"}),
        )
    )

    _, rationales, individual = score_with(
        aggregate_scores,
        produced,
        wants(
            gold_cards=["retention_target", "irrigation_rule"],
            expected_tool_calls=[{"name": LOOKUP_TOOL}],
            must_not_tools=[],
        ),
    )

    assert individual[CARD_RECALL] == pytest.approx(0.5)
    assert "retention_target" in rationales[CARD_RECALL]
    assert "irrigation_rule" in rationales[CARD_RECALL]


# ── The one shape MLflow can hand a scorer that no rollout produces ───────────


def test_a_rollout_that_never_completed_scores_zero_on_every_metric() -> None:
    """``predict_fn`` raising becomes a *string* in ``outputs`` (``findings.md``).

    ``_run_single`` catches the exception and hands the scorers
    ``"Failed to invoke the predict_fn with …"``, so a scorer that assumed a
    mapping would take the whole search down on the first harness defect. It is
    §7's declared residual instead: 0 everywhere, the reason in every rationale,
    and never a skip — a record on which every metric skipped has nothing to
    select on and :func:`aggregate_scores` refuses it.
    """
    failed = "Failed to invoke the predict_fn with {'case_id': 'T01-0001'}: boom"

    score, rationales, individual = score_with(aggregate_scores, failed, wants())

    assert score == 0.0
    assert set(individual) == set(METRICS)
    assert all(value == 0.0 for value in individual.values())
    for name in METRICS:
        assert "declared residual" in rationales[name]
        assert "boom" in rationales[name]


def test_an_abstention_case_scores_the_agents_status_and_skips_the_answer() -> None:
    """The unanswerable case, whose whole content is the status (§7).

    Its answer is null, so the answer metric skips and the case is never
    counted twice — once for declining and once for having declined.
    """
    produced = rollout(
        answer=None, unit=None, status="not_available", trajectory=(
            ToolCall(name=WEATHER_TOOL, args={"past_days": 3}),
        )
    )

    _, rationales, individual = score_with(
        aggregate_scores,
        produced,
        wants(
            status="not_available",
            answer=None,
            unit=None,
            answer_metric=SKIPPED,
            expected_tool_calls=[{"name": WEATHER_TOOL}],
            must_not_tools=[],
        ),
    )

    assert individual[ABSTENTION] == 1.0
    assert ANSWER not in individual
    assert "declined a case that cannot be answered" in rationales[ABSTENTION]
