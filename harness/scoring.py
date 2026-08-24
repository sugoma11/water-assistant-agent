"""The four metrics of §7, and the two aggregations that read them.

**Two layers, because they have two different users.** The per-case functions —
:func:`score_answer`, :func:`score_trajectory`, :func:`score_card_recall`,
:func:`score_abstention` — take one recorded run and one case's ``expectations``
and return one :class:`MetricScore`. T122 wraps each of them as an MLflow
``Scorer`` so the *search* scores a rollout with exactly the code the
*measurement run* scores it with. Everything that needs more than one case —
harness exclusion, coverage, the per-arm breakdown, the diagnostics — is the
aggregation layer at the bottom of this module and stays on the measurement
path, where the population exists. Building the two as one function would mean
writing the per-case half twice.

There are also two things called aggregation and they are not the same:

* :func:`aggregate_scores` collapses **one record's four scorer values into one
  scalar**. It is the ``aggregation=`` callable ``optimize_prompts`` takes
  (``agent_architecture.md`` §6), it is **mandatory** — MLflow ships none, and
  the silent default averages the numeric values while a non-numeric one raises
  — and it is what GEPA selects on, since the Pareto front is over instances
  (``decisions.md`` § Candidate selection and the scorers' aggregation).
* :func:`aggregate` collapses **many cases into one arm's report**: the means,
  the coverage of each skippable metric, the exclusions broken down by source,
  the diagnostics.

**Skipped is a value, not a zero.** Two metrics have a legitimately undefined
case — the answer metric where the oracle answer is ``null`` (a plot
deliverable), card recall where ``gold_cards`` is empty — and both return
:data:`SKIPPED`, a non-numeric value. :func:`aggregate_scores` drops it and
renormalizes the remaining weights; :func:`aggregate` reports **coverage**
beside the mean. One mechanism, two users, implemented once (§7). Scoring a
plot family's null answer as 0 would charge its share of the suite against
fully correct cases.

**Nothing here re-runs anything.** A :class:`~harness.run_case.CaseResult` is a
fact about a run and these are functions of it, so a recorded pass can be
re-scored without a model.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog

from harness.assertions import site_day
from harness.run_case import CaseResult, ToolCall
from water_assistant_agent.assistant.tools.weather_client import (
    InvalidWindowError,
    resolve_window,
)
from water_assistant_agent.assistant.toolset import LOOKUP_TOOL

logger = structlog.get_logger(__name__)

ANSWER = "answer"
TRAJECTORY = "trajectory"
CARD_RECALL = "card_recall"
ABSTENTION = "abstention"

METRICS: tuple[str, ...] = (ANSWER, TRAJECTORY, CARD_RECALL, ABSTENTION)
"""The four metric names, which are also T122's scorer names and the weight keys."""

SKIPPED = "skipped"
"""The non-numeric value a metric returns where it is legitimately undefined.

A string rather than ``None`` or ``float("nan")``: it survives a ``Feedback``
value, it survives JSON, and it is the word the case envelope already uses
(``answer_metric: "skipped"``). What makes it a *skip* rather than an exception
is :func:`aggregate_scores` — MLflow raises on a non-numeric scorer value unless
an explicit aggregation callable is supplied (``findings.md``).
"""

SELECTION_WEIGHTS: Mapping[str, float] = {
    ANSWER: 0.40,
    TRAJECTORY: 0.30,
    ABSTENTION: 0.20,
    CARD_RECALL: 0.10,
}
"""What each metric is worth in the **one scalar selection runs on**.

Selection is blended whatever the four metrics do elsewhere, so these weights
are part of the method and are pre-registered with the budget and the
hyperparameters (T129) — never adjusted once a test number has been seen
(``decisions.md`` § Candidate selection and the scorers' aggregation). The
ordering is the argument for them: the answer is the deliverable; trajectory is
the routing judgment the candidate text can actually move; abstention is a
safety property that is near-constant on answerable cases and therefore acts
mostly as a penalty channel against a candidate that starts abstaining; card
recall is graded but narrow, scored on lookup templates only and riding along a
trajectory that already scores the lookup call.

No metric is protected from being traded against another here — that is what
blending means, and it is why the four reach reflection unblended.
"""

UNIT_ALIASES: Mapping[str, str] = {"L": "mm", "%θ": "%"}
"""The whole normalization table (§7), and it is deliberately this small.

``L`` and ``mm`` are one quantity on the lysimeter columns, whose collection
area is 1 m², and ``%`` and ``%θ`` are one unit. **Nothing else converts**: a
millimetre answer against a percentage-point oracle is wrong, not rescalable.
"""

_WINDOW_ENDS: Mapping[str, int] = {
    "start_date": 0,
    "end_date": 1,
    "start": 0,
    "end": 1,
}
"""Which end of a resolved window a path's last segment names.

Both spellings, because §3.6 writes the plot tool's window as ``start`` / ``end``
while the implementation and the weather tool spell it ``start_date`` /
``end_date``; the vocabulary is one either way.
"""

_MISSING = object()
"""No such argument — distinct from an argument explicitly given as ``null``."""


@dataclass(frozen=True)
class MetricScore:
    """One metric's verdict on one case.

    ``value`` is a float, or :data:`SKIPPED` where the metric does not apply.
    ``rationale`` is **not decoration**: MLflow forwards a ``Feedback``'s
    rationale into GEPA's reflective dataset, so this text is the search signal
    (§7). It says what was wanted and what happened, in that order.
    """

    value: float | str
    rationale: str

    @property
    def skipped(self) -> bool:
        return self.value == SKIPPED

    @property
    def score(self) -> float | None:
        """The numeric value, or ``None`` where the metric skipped."""
        return None if self.skipped else float(self.value)


@dataclass(frozen=True)
class CaseScore:
    """Every metric over one case, plus what the aggregates need to split on.

    ``abstained`` is carried rather than derived from the abstention metric's
    value: a case scores 0 there both when the agent answered a case it should
    have declined **and** when it produced no parseable contract at all, and
    only the first of those is an abstention. The false-abstention rate is
    counted off this field, so a parse failure can never inflate it.
    """

    case_id: str
    template_id: str
    harness_error: bool
    exclusion_sources: tuple[str, ...]
    metrics: Mapping[str, MetricScore]
    unanswerable: bool
    abstained: bool
    selection_score: float
    diagnostics: Mapping[str, Any]


def score_answer(result: CaseResult, expectations: Mapping[str, Any]) -> MetricScore:
    """Exact match or tolerance against the oracle, **after unit normalization**.

    The comparison is dimensional before it is numeric: both units go through
    :data:`UNIT_ALIASES` and must then be the same, or the answer is wrong
    however close the number is. That table is the whole of what converts.

    **Skipped where the oracle answer is ``null``.** The envelope declares it as
    ``answer_metric: "skipped"`` and the schema's validator makes that imply a
    null answer; where the two disagree the null answer decides, because §7's
    rule is stated over the answer and there is genuinely nothing to compare
    against. An abstention case therefore skips here too — its whole content is
    the status, which :func:`score_abstention` scores — and never counts twice.
    """
    expected = expectations.get("answer")
    if expectations.get("answer_metric") == SKIPPED or expected is None:
        return MetricScore(
            SKIPPED,
            "The oracle answer is null, so there is nothing for the answer metric "
            "to compare against; the case counts towards coverage instead.",
        )
    if result.contract is None:
        return MetricScore(
            0.0,
            "No contract parsed out of the final message, so no answer was "
            "delivered; reported apart as a parse_failure diagnostic as well.",
        )

    tolerance = expectations.get("tolerance") or {"kind": "exact"}
    matched, detail = _answer_matches(
        expected=expected,
        actual=result.answer,
        expected_unit=expectations.get("unit"),
        actual_unit=result.unit,
        tolerance=tolerance,
    )
    return MetricScore(1.0 if matched else 0.0, detail)


def score_trajectory(
    result: CaseResult,
    expectations: Mapping[str, Any],
    *,
    as_of: datetime | str | None = None,
) -> MetricScore:
    """**Binary**: 1 iff every gold tool ran, no must-not ran, and every check passes.

    No partial credit and no extra-call penalty. Two thirds of a gold set is 0,
    and a call beyond it is free — so a template discriminates routing **only**
    through its ``must_not_tools``, and a shotgun candidate is caught by the
    distractor slots the catalog spreads across train rather than by an F1 whose
    penalty nobody could give a magnitude (``decisions.md`` § Trajectory scoring
    and routing probes). Extra calls are counted as a diagnostic instead, in
    :func:`score_case`.

    *as_of* is required only where an ``argument_check`` carries
    ``resolve: "window"``. It is not defaulted to the wall clock, for the reason
    :func:`~..tools.weather_client.resolve_window` refuses one: a relative window
    resolved against the host's real date would leak real time into a pinned
    case.
    """
    called = set(result.tool_names)
    gold = gold_names(expectations)
    missing = [name for name in dict.fromkeys(gold) if name not in called]
    forbidden = [
        name for name in expectations.get("must_not_tools") or () if name in called
    ]
    failures = [
        failure
        for (tool, group), checks in _checks_by_call(
            expectations.get("argument_checks") or ()
        ).items()
        if (
            failure := _group_check_failure(
                tool, group, checks, result.trajectory, as_of
            )
        )
        is not None
    ]

    if not (missing or forbidden or failures):
        return MetricScore(
            1.0,
            f"Called {sorted(called) or 'nothing'}; every gold tool "
            f"({sorted(set(gold)) or 'none required'}) ran, no must-not tool ran "
            f"and {len(expectations.get('argument_checks') or ())} argument "
            "check(s) passed.",
        )

    reasons: list[str] = []
    if missing:
        reasons.append(f"gold tool(s) never called: {', '.join(missing)}")
    if forbidden:
        reasons.append(f"must-not tool(s) called: {', '.join(forbidden)}")
    if failures:
        reasons.append(f"argument check(s) failed: {'; '.join(failures)}")
    return MetricScore(
        0.0,
        f"Called {sorted(called) or 'nothing'} — {'; '.join(reasons)}.",
    )


def score_card_recall(
    result: CaseResult, expectations: Mapping[str, Any]
) -> MetricScore:
    """``|gold ∩ retrieved| / |gold|`` over the **union of every lookup call**.

    Graded, which is the partial credit the binary trajectory cannot give. The
    union is over every ``lookup_reference`` call in the run, so a candidate that
    reads two cards one at a time is scored the same as one that read both
    first — there is no gold-query / agent-query split to make, because an exact
    lookup has no query to substitute and only the card *selection* is
    measurable (``decisions.md`` § Retrieval).

    **Skipped where ``gold_cards`` is empty** — the same mechanism the answer
    metric skips through, with coverage reported beside it.
    """
    gold = list(expectations.get("gold_cards") or ())
    if not gold:
        return MetricScore(
            SKIPPED,
            "The case wants no reference card, so recall is undefined; the case "
            "counts towards coverage instead.",
        )
    retrieved = retrieved_cards(result.trajectory)
    hit = [card for card in gold if card in retrieved]
    missed = [card for card in gold if card not in retrieved]
    recall = len(hit) / len(gold)
    return MetricScore(
        recall,
        f"Wanted {sorted(gold)}, retrieved {sorted(retrieved) or 'nothing'} — "
        f"recall {recall:.2f}"
        + (f", missing {sorted(missed)}." if missed else "."),
    )


def score_abstention(
    result: CaseResult, expectations: Mapping[str, Any]
) -> MetricScore:
    """1 iff the agent's contract ``status`` is the one the case expects.

    The scored quantity is the **agent's** status, on §2's two-valued terms: a
    tool's own ``not_available`` is the upstream cause that can make an
    abstention correct, not the thing measured.

    One value per case, and two numbers in the report: :func:`aggregate` splits
    this population in two and publishes accuracy on the unanswerable cases and
    the false-abstention rate on the answerable ones **separately**, never
    blended into one figure (§7).
    """
    expected = expectations.get("status")
    actual = result.status
    if actual is None:
        return MetricScore(
            0.0,
            f"The case expects status {expected!r}; no contract parsed out of the "
            "final message, so the agent stated no status at all.",
        )
    if actual == expected:
        held = (
            "declined a case that cannot be answered"
            if actual == "not_available"
            else "answered an answerable case"
        )
        return MetricScore(1.0, f"The agent {held} — status {actual!r}, as expected.")
    if expected == "not_available":
        return MetricScore(
            0.0,
            "The case cannot be answered at this site and the agent answered it "
            f"anyway (status {actual!r}).",
        )
    return MetricScore(
        0.0,
        f"The case is answerable and the agent abstained (status {actual!r}) — a "
        "false abstention.",
    )


def score_case(
    result: CaseResult,
    expectations: Mapping[str, Any],
    *,
    as_of: datetime | str | None = None,
) -> CaseScore:
    """Every metric over one case, plus the selection scalar and the diagnostics.

    A ``harness_error`` case is scored like any other and **then excluded by
    :func:`aggregate`**: the metrics are cheap, the record is worth keeping, and
    the exclusion is a property of the aggregate rather than of the case.
    """
    metrics = {
        ANSWER: score_answer(result, expectations),
        TRAJECTORY: score_trajectory(result, expectations, as_of=as_of),
        CARD_RECALL: score_card_recall(result, expectations),
        ABSTENTION: score_abstention(result, expectations),
    }
    gold = gold_names(expectations)
    return CaseScore(
        case_id=result.case_id,
        template_id=result.template_id,
        harness_error=result.harness_error,
        exclusion_sources=tuple(exclusion.source for exclusion in result.exclusions),
        metrics=metrics,
        unanswerable=expectations.get("status") == "not_available",
        abstained=result.status == "not_available",
        selection_score=aggregate_scores(
            {name: score.value for name, score in metrics.items()}
        ),
        diagnostics={
            **result.diagnostics,
            "extra_calls": extra_calls(result.trajectory, gold),
        },
    )


def aggregate_scores(scores: Mapping[str, Any]) -> float:
    """The four scorer values for **one record**, blended into the selection scalar.

    This is ``optimize_prompts``' ``aggregation=`` argument and it is not
    optional: MLflow exports no callable, omitting it makes the objective the
    unweighted mean of the numeric values — a weighting nobody chose — and a
    non-numeric value then raises outright rather than skipping
    (``findings.md`` § Optimizer internals). Both of §7's skips are non-numeric
    by construction, so this function is what makes them skips.

    A skip is **dropped and the remaining weights renormalized**, so a plot case
    is not scored below an otherwise identical case merely for having no answer
    to compare. Values arrive either bare or wrapped in a ``Feedback``; the
    wrapper is unwrapped by attribute so this module never imports MLflow.

    Raises:
        ValueError: a scorer name with no registered weight, a value that is
            neither numeric nor :data:`SKIPPED`, or a record on which every
            metric skipped. All three are wiring faults that would otherwise
            reweight the objective silently.
    """
    total = 0.0
    weight = 0.0
    for name, raw in scores.items():
        # The name is checked before the value, so a scorer nobody weighted
        # cannot slip through on the one record where it happened to skip.
        if name not in SELECTION_WEIGHTS:
            raise ValueError(
                f"No selection weight for scorer {name!r}. The weights are part of "
                f"the method and are pre-registered: {', '.join(SELECTION_WEIGHTS)}."
            )
        value = getattr(raw, "value", raw)
        if value == SKIPPED:
            continue
        if not isinstance(value, (int, float)):
            raise ValueError(
                f"Scorer {name!r} returned {value!r}, which is neither a number nor "
                f"{SKIPPED!r}; a metric that does not apply must skip explicitly."
            )
        total += SELECTION_WEIGHTS[name] * float(value)
        weight += SELECTION_WEIGHTS[name]
    if weight == 0.0:
        raise ValueError(
            "Every metric skipped on this record, so there is nothing to select "
            "on. Trajectory and abstention are defined on every case."
        )
    return total / weight


@dataclass(frozen=True)
class MetricSummary:
    """One metric over one arm: the mean, and how much of the arm it covers."""

    mean: float | None
    scored: int
    skipped: int

    @property
    def coverage(self) -> float | None:
        """Share of the arm's included cases the metric was defined on."""
        cases = self.scored + self.skipped
        return None if cases == 0 else self.scored / cases


@dataclass(frozen=True)
class AbstentionSummary:
    """Two numbers, kept apart on purpose (§7).

    ``accuracy`` is over the unanswerable cases and ``false_abstention_rate``
    over the answerable ones. They are never averaged into one figure: a
    candidate that abstains from everything scores 1.0 on the first, and the
    only thing that says so is the second.
    """

    accuracy: float | None
    unanswerable: int
    false_abstention_rate: float | None
    answerable: int


@dataclass(frozen=True)
class ArmReport:
    """One candidate arm's results, with what was excluded from them stated."""

    arm: str
    cases: int
    included: int
    excluded: int
    exclusions_by_source: Mapping[str, int]
    answer: MetricSummary
    trajectory: MetricSummary
    card_recall: MetricSummary
    abstention: AbstentionSummary
    selection_score: float | None
    diagnostics: Mapping[str, Any]


def aggregate(scored: Iterable[CaseScore], *, arm: str = "baseline") -> ArmReport:
    """Many cases into one arm's report — the measurement path's half of §7.

    **``harness_error`` cases leave every aggregate here**, including the
    diagnostics, and are counted separately with their sources broken out. That
    is deliberate asymmetry with the run-time taxonomy: an ``upstream`` failure
    is something the candidate could not have avoided and the harness cannot
    reproduce, while an ``invalid_argument`` fumble stays in the denominator and
    surfaces as a wrong answer or a false abstention. The per-arm counts are
    published beside the results rather than folded away, because a defect in
    this harness lands in them too.

    Coverage is reported per skippable metric, from the same skip the search's
    :func:`aggregate_scores` reads — one mechanism, two users.
    """
    everything = list(scored)
    excluded = [case for case in everything if case.harness_error]
    included = [case for case in everything if not case.harness_error]

    sources = Counter(
        source for case in excluded for source in case.exclusion_sources or ("unknown",)
    )
    unanswerable = [case for case in included if case.unanswerable]
    answerable = [case for case in included if not case.unanswerable]

    report = ArmReport(
        arm=arm,
        cases=len(everything),
        included=len(included),
        excluded=len(excluded),
        exclusions_by_source=dict(sorted(sources.items())),
        answer=_summarize(included, ANSWER),
        trajectory=_summarize(included, TRAJECTORY),
        card_recall=_summarize(included, CARD_RECALL),
        abstention=AbstentionSummary(
            accuracy=_mean([float(case.abstained) for case in unanswerable]),
            unanswerable=len(unanswerable),
            false_abstention_rate=_mean(
                [float(case.abstained) for case in answerable]
            ),
            answerable=len(answerable),
        ),
        selection_score=_mean([case.selection_score for case in included]),
        diagnostics=_diagnostics(included),
    )
    logger.info(
        "Arm scored",
        arm=arm,
        included=report.included,
        excluded=report.excluded,
        selection_score=report.selection_score,
    )
    return report


def aggregate_arms(arms: Mapping[str, Iterable[CaseScore]]) -> dict[str, ArmReport]:
    """:func:`aggregate` per arm — the paired shape every §7 comparison is read in."""
    return {name: aggregate(cases, arm=name) for name, cases in arms.items()}


def gold_names(expectations: Mapping[str, Any]) -> list[str]:
    """The gold trajectory's tool names, in the order the case lists them."""
    return [
        str(call.get("name", ""))
        for call in expectations.get("expected_tool_calls") or ()
    ]


def retrieved_cards(trajectory: Iterable[ToolCall]) -> set[str]:
    """Every card the run asked for, over the union of its lookup calls."""
    return {
        str(call.args["topic"])
        for call in trajectory
        if call.name == LOOKUP_TOOL and isinstance(call.args.get("topic"), str)
    }


def extra_calls(trajectory: Sequence[ToolCall], gold: Sequence[str]) -> int:
    """Calls beyond the gold set — free for trajectory, reported as a diagnostic.

    Counted as a multiset difference rather than ``len(trajectory) - len(gold)``,
    so a candidate that called a gold tool twice is charged for the second call
    and one that skipped a gold tool is not credited for the gap.
    """
    called = Counter(call.name for call in trajectory)
    wanted = Counter(gold)
    matched = sum(min(called[name], count) for name, count in wanted.items())
    return sum(called.values()) - matched


def normalize_unit(unit: str | None) -> str | None:
    """*unit* under §7's normalization table, which is :data:`UNIT_ALIASES` entire."""
    if unit is None:
        return None
    return UNIT_ALIASES.get(unit, unit)


def _answer_matches(
    *,
    expected: Any,
    actual: Any,
    expected_unit: str | None,
    actual_unit: str | None,
    tolerance: Mapping[str, Any],
) -> tuple[bool, str]:
    """Compare one answer against its oracle, and say what the comparison found."""
    if isinstance(expected, bool):
        if not isinstance(actual, bool):
            return False, (
                f"The oracle is {expected}; the agent answered {actual!r}, which is "
                "not a yes/no value."
            )
        return expected == actual, (
            f"The oracle is {expected}; the agent answered {actual}."
        )

    if isinstance(expected, (int, float)):
        wanted, given = normalize_unit(expected_unit), normalize_unit(actual_unit)
        if wanted != given:
            return False, (
                f"The oracle is {expected} {expected_unit or '(no unit)'}; the agent "
                f"answered in {actual_unit or '(no unit)'}. Only L↔mm and %θ↔% are "
                "the same quantity, so nothing rescales these two."
            )
        number = _as_number(actual)
        if number is None:
            return False, (
                f"The oracle is {expected} {expected_unit or ''}".rstrip()
                + f"; the agent answered {actual!r}, which is not a number."
            )
        ok = _within(number, float(expected), tolerance)
        return ok, (
            f"The oracle is {expected} {expected_unit or ''}".rstrip()
            + f"; the agent answered {number} — off by "
            f"{abs(number - float(expected)):g}, {'inside' if ok else 'outside'} "
            f"{_describe(tolerance)}."
        )

    return str(expected) == str(actual), (
        f"The oracle is {expected!r}; the agent answered {actual!r}."
    )


def _as_number(value: Any) -> float | None:
    """*value* as a float, reading a stringified number as the number it is.

    A quantity written ``"12.5"`` is the same quantity as ``12.5``, and this
    metric measures the quantity. A candidate degrading the *format* is what the
    ``parse_failure`` diagnostic isolates, and charging it twice would make the
    answer metric the noisier of the two.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _within(actual: float, expected: float, tolerance: Mapping[str, Any]) -> bool:
    """Is *actual* inside *tolerance*? ``rel`` is a fraction, so 2 % is 0.02."""
    kind = tolerance.get("kind", "exact")
    if kind == "abs":
        return abs(actual - expected) <= float(tolerance["value"])
    if kind == "rel":
        return abs(actual - expected) <= float(tolerance["value"]) * abs(expected)
    return actual == expected


def _describe(tolerance: Mapping[str, Any]) -> str:
    kind = tolerance.get("kind", "exact")
    if kind == "abs":
        return f"the ±{tolerance['value']:g} absolute tolerance"
    if kind == "rel":
        return f"the ±{float(tolerance['value']) * 100:g}% relative tolerance"
    return "an exact match"


def _checks_by_call(
    checks: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str | None], list[Mapping[str, Any]]]:
    """Group the checks that must hold **of one call**, in declaration order.

    The key is the tool plus the check's optional ``group``. Ungrouped checks on
    one tool share a group, which is the common case and the tight one: a plot's
    source, variable, roof and window describe a single call, so they are scored
    against a single call. A template that genuinely needs *two* calls to one
    tool — the cross-roof comparison of T26(ii), one run per roof — tags them
    into two groups, and each group is then satisfied independently.
    """
    grouped: dict[tuple[str, str | None], list[Mapping[str, Any]]] = {}
    for check in checks:
        key = (str(check.get("tool", "")), check.get("group"))
        grouped.setdefault(key, []).append(check)
    return grouped


def _group_check_failure(
    tool: str,
    group: str | None,
    checks: Sequence[Mapping[str, Any]],
    trajectory: Sequence[ToolCall],
    as_of: datetime | str | None,
) -> str | None:
    """``None`` if **one** call to *tool* satisfies **every** check, else why not.

    Existential over the calls, universal over the group's checks — and the
    quantifier order is the whole point. Existential over calls, because an extra
    call costs nothing: a candidate that fumbled a variable name and repaired it
    did make the right call, and failing the check on the fumble would
    reintroduce the extra-call penalty ``decisions.md`` § Trajectory scoring and
    routing probes rejects through the back door. Universal over the group's
    checks, because the alternative — each check hunting the trajectory for its
    own satisfying call — lets a run assemble a pass out of fragments: one call
    with the right roofs and the wrong variable, another with the right variable
    and the wrong roofs, and no correct call anywhere. On the plotting family
    that is the *entire* scored surface (§7), and on T26(i) it would let the
    compositional holdout pass by setting ``albedo`` on one call and ``forcings``
    on another, never composing them.

    The rationale names the closest call rather than every call, since it is
    GEPA's reflective signal and a list of near-misses per call teaches nothing.
    Ties go to the later call: that is the candidate's most recent attempt.
    """
    calls = [call for call in trajectory if call.name == tool]
    where = f"{tool}[{group}]" if group else tool
    if not calls:
        paths = ", ".join(str(check.get("path")) for check in checks)
        return f"{where}.{{{paths}}} — {tool} was never called"
    closest: list[str] | None = None
    for call in calls:
        reasons = [
            f"{check.get('path')} — {reason}"
            for check in checks
            if (reason := _call_check_failure(check, call, as_of)) is not None
        ]
        if not reasons:
            return None
        if closest is None or len(reasons) <= len(closest):
            closest = reasons
    assert closest is not None
    if len(checks) == 1:
        return f"{where}.{'; '.join(closest)}"
    return (
        f"{where} — no single call satisfied all {len(checks)} checks; "
        f"the closest missed {len(closest)}: {'; '.join(closest)}"
    )


def _call_check_failure(
    check: Mapping[str, Any], call: ToolCall, as_of: datetime | str | None
) -> str | None:
    """``None`` if *call* satisfies *check*, else what the check found."""
    path = str(check.get("path", ""))
    op = check.get("op")
    if check.get("resolve") == "window":
        value = _resolved_window_value(path, call.args, as_of)
    else:
        value = _at_path(call.args, path)

    if op == "present":
        if value is _MISSING or value is None:
            return "no such argument"
        if "*" in path.split(".") and not value:
            # A wildcard collects, so it returns `[]` rather than `_MISSING` when
            # no member carries the field — and `[]` is neither missing nor None,
            # which would make `present` on a wildcard a check that cannot fail:
            # it would pass for any call that merely passed a list.
            return "no member of the list carries it"
        bounds = check.get("plausible")
        if not bounds:
            return None
        number = _as_number(value)
        if number is None:
            return f"{value!r} is not a number, so it cannot be plausible"
        low, high = bounds.get("min"), bounds.get("max")
        if (low is not None and number < low) or (high is not None and number > high):
            return f"{number:g} is outside the plausible range [{low}, {high}]"
        return None

    if value is _MISSING:
        return "no such argument"
    expected = check.get("value")
    if op == "set_eq":
        if _as_key_set(value) == _as_key_set(expected):
            return None
        return f"{value!r} is not the same set as {expected!r}"
    if _equal(value, expected):
        return None
    return f"{value!r} != {expected!r}"


def _resolved_window_value(
    path: str, args: Mapping[str, Any], as_of: datetime | str | None
) -> Any:
    """One end of the call's window, resolved through **layer 1's own resolver**.

    A candidate that wrote ``past_days=30`` and one that wrote the thirty dates
    must score identically, or the plotting family measures date arithmetic
    rather than source and variable selection (``decisions.md`` § Plotting). The
    resolver is the same function both tool wrappers call, so the two cannot
    drift; the check's *expected* value is compared as written, since the
    generator emits it absolute.

    Raises:
        ValueError: no *as_of* to resolve against, or a path whose last segment
            names neither end of a window. Both are faults in the caller or the
            case, and resolving against the host's wall clock instead is exactly
            what ``resolve_window`` refuses to do.
    """
    parent_path, _, leaf = path.rpartition(".")
    index = _WINDOW_ENDS.get(leaf)
    if index is None:
        raise ValueError(
            f"An argument check with resolve: \"window\" must address one end of a "
            f"window ({', '.join(_WINDOW_ENDS)}); {path!r} names {leaf!r}."
        )
    if as_of is None:
        raise ValueError(
            "A window-resolving argument check needs the case's as_of; resolving "
            "a relative window against the host's date would leak real time into "
            "a pinned case."
        )
    parent = args if not parent_path else _at_path(args, parent_path)
    if not isinstance(parent, Mapping):
        return _MISSING
    today: date = site_day(as_of)
    try:
        window = resolve_window(
            parent.get("start_date", parent.get("start")),
            parent.get("end_date", parent.get("end")),
            parent.get("past_days"),
            parent.get("forecast_days"),
            today=today,
        )
    except InvalidWindowError:
        return _MISSING
    return window[index]


def _at_path(args: Any, path: str) -> Any:
    """Walk a dotted *path* into one call's argument object.

    Mapping keys and list indices both, so ``series.0.source`` and
    ``forcings.precip.2026-03-15`` are the same kind of address. There is no
    path into a tool *result*: the scored surface is the agent-supplied half of
    a call, which is what keeps the plotting family measuring the model rather
    than the resolver (§6.1).

    **``*`` collects one field across a list**, so ``series.*.roof`` is the roofs
    of every series in the call. It exists because the catalog scores a plot's
    roofs "as a set match" (``questions.md`` §2 H) and there was no way to say
    that: an index addresses one series and so imposes an order the request does
    not have, while ``set_eq`` on ``series`` itself compares whole declarations
    and fails a candidate that added an optional key it was entitled to add. The
    wildcard addresses the field, which is the thing the check is about.

    A member missing the field is skipped rather than collected as ``None`` —
    ``set_eq`` is then a statement about the series that carry the field, which
    is what makes ``series.*.roof`` meaningful on a chart whose weather series
    legitimately has no roof.

    The wildcard is **list-only** (``forcings.*`` is ``_MISSING``, since forcings
    is a mapping) and it **collapses duplicates** under ``set_eq``, so
    ``series.*.roof`` constrains which roofs were drawn and never how many series
    carry them — cardinality is ``series.*.source``'s to constrain. Collecting
    nothing yields ``[]``, not ``_MISSING``; :func:`_call_check_failure` reads
    that as an absence for ``present`` and as an empty set for ``set_eq``.
    """
    return _walk(args, path.split("."))


def _walk(value: Any, segments: Sequence[str]) -> Any:
    """:func:`_at_path`'s recursion, split out so ``*`` can branch over a list."""
    for position, segment in enumerate(segments):
        if segment == "*":
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return _MISSING
            rest = segments[position + 1 :]
            collected = [
                found
                for item in value
                if (found := _walk(item, rest) if rest else item) is not _MISSING
            ]
            return collected
        if isinstance(value, Mapping):
            if segment not in value:
                return _MISSING
            value = value[segment]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            try:
                index = int(segment)
            except ValueError:
                return _MISSING
            if not -len(value) <= index < len(value):
                return _MISSING
            value = value[index]
        else:
            return _MISSING
    return value


def _equal(actual: Any, expected: Any) -> bool:
    """``==``, except that a bool is never a number: ``True`` is not ``1`` here."""
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    return bool(actual == expected)


def _as_key_set(value: Any) -> frozenset[str]:
    """A collection as a set of canonical keys, so unhashable members compare too.

    A series selection is a set match: its order carries no meaning, and its
    members may be objects.
    """
    items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    return frozenset(json.dumps(item, sort_keys=True, default=str) for item in items)


def _summarize(cases: Sequence[CaseScore], metric: str) -> MetricSummary:
    """One metric over an arm's included cases, with its skips counted apart."""
    values = [
        score
        for case in cases
        if (score := case.metrics[metric].score) is not None
    ]
    skipped = sum(1 for case in cases if case.metrics[metric].skipped)
    return MetricSummary(mean=_mean(values), scored=len(values), skipped=skipped)


def _diagnostics(cases: Sequence[CaseScore]) -> dict[str, Any]:
    """§7's diagnostics: reported, never scored.

    ``fixer_iterations`` is ``None`` rather than 0 wherever the rollout could not
    observe it — the sub-agent's transpile/validate retries run on its own
    ``Runner`` and nothing writes the count into the state delta that reaches the
    root side. Publishing a fabricated zero would make an unavailable diagnostic
    look like a measured one.
    """
    reported = [
        case.diagnostics.get("fixer_iterations")
        for case in cases
        if case.diagnostics.get("fixer_iterations") is not None
    ]
    return {
        "mean_steps": _mean(
            [float(case.diagnostics.get("steps", 0)) for case in cases]
        ),
        "mean_extra_calls": _mean(
            [float(case.diagnostics.get("extra_calls", 0)) for case in cases]
        ),
        "mean_latency_s": _mean(
            [float(case.diagnostics.get("latency_s", 0.0)) for case in cases]
        ),
        "mean_tokens": _mean(
            [
                float((case.diagnostics.get("tokens") or {}).get("total", 0))
                for case in cases
            ]
        ),
        "parse_failures": sum(
            1 for case in cases if case.diagnostics.get("parse_failure")
        ),
        "step_cap_exceeded": sum(
            1 for case in cases if case.diagnostics.get("step_cap_exceeded")
        ),
        "mean_fixer_iterations": _mean([float(value) for value in reported]),
    }


def _mean(values: Sequence[float]) -> float | None:
    """The mean, or ``None`` on an empty population — never a 0 standing in for one."""
    return sum(values) / len(values) if values else None
