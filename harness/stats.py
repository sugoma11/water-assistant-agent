"""§7's reporting rules, computed over the measurement path's outcomes (T128).

Nothing here runs a model. The input is :mod:`harness.measure`'s
:class:`~harness.measure.CaseOutcome` rows and the output is the report §7
specifies, so a re-analysis needs the written measurement and nothing else.

**The unit of analysis is the template, not the case.** Trajectory is
route-determined and binary, so a candidate that misroutes a template misroutes
every instance of it; resampling cases would treat four instances of one routing
decision as four independent observations and report intervals several times too
narrow (``decisions.md`` § Splits, sizing and the holdout). Every interval in
this module therefore resamples ``template_id``.

**The three repeats shrink noise; they do not multiply *n*.** They are
near-replicates at temperature 0, so a case's three values are averaged into one
before anything else happens, and the count that reaches the bootstrap is the
number of *templates*. What the repeats buy separately is
:func:`residual_nondeterminism` — the share of cases whose repeats disagree,
which is the noise floor every arm difference is read against.

**A pair has to be a pair.** A case excluded as a ``harness_error`` in any repeat
of either arm leaves the paired comparison entirely, in both arms. That is not a
new rule: it follows from §7's exclusion and from the pre-registered pairing on
identical cases, since a case measured in one arm and not the other cannot be
differenced. It is reported — :attr:`Comparison.dropped` counts it — because a
comparison that quietly lost its hardest cases in one arm is exactly the bias the
exclusion rule is otherwise careful about.

**Three things §7 forbids, and this module refuses to compute.**

* An accuracy with an interval on ``test_unseen``'s trajectory. Seven templates
  support description, so that split's trajectory result is a per-template
  **win/loss table** (:func:`win_loss`) and there is no function here that will
  put an interval on it.
* A "training accuracy". The train number is the **selection score** — the final
  candidate was selected on those same instances — and it is labelled that way at
  every point it appears.
* One blended abstention number. Accuracy over the unanswerable cases and the
  false-abstention rate over the answerable ones are two numbers that never
  average (``harness/scoring.py``'s ``AbstentionSummary``, one layer up).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any

import numpy as np
import structlog

from harness.measure import BASELINE_ARM, OPTIMIZED_ARM, CaseOutcome
from harness.scoring import ABSTENTION, ANSWER, CARD_RECALL, SKIPPED, TRAJECTORY

logger = structlog.get_logger(__name__)

RESAMPLES = 10_000
RNG_SEED = 42
LEVEL = 0.95
"""The pre-registered bootstrap: 10 000 resamples, ``default_rng(42)``, percentile 95 %."""

SELECTION_SCORE = "selection_score"
"""The blended scalar, addressed as a metric so gaps can be taken on one quantity."""

TRAIN = "train"
TEST_SEEN = "test_seen"
TEST_UNSEEN = "test_unseen"


@dataclass(frozen=True)
class Interval:
    """A point estimate and its percentile bootstrap interval, over templates."""

    point: float
    low: float
    high: float
    templates: int

    def __str__(self) -> str:
        return f"{self.point:+.3f} [{self.low:+.3f}, {self.high:+.3f}] (n={self.templates} templates)"

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0.0 or self.high < 0.0


@dataclass(frozen=True)
class Comparison:
    """One metric on one split: both arms, their paired difference, and what was dropped."""

    split: str
    metric: str
    baseline: float | None
    optimized: float | None
    difference: Interval | None
    cases: int
    dropped: int
    templates: int

    def __str__(self) -> str:
        if self.difference is None:
            return (
                f"{self.split}/{self.metric}: baseline {_fmt(self.baseline)}, "
                f"optimized {_fmt(self.optimized)} — no paired population"
            )
        return (
            f"{self.split}/{self.metric}: baseline {_fmt(self.baseline)} → "
            f"optimized {_fmt(self.optimized)}, Δ {self.difference}"
        )


@dataclass(frozen=True)
class Gap:
    """One generalization gap, on one arm, in one quantity.

    ``interval`` is present only where the two splits share their templates —
    train and test_seen carry the same 25 — because a paired bootstrap needs a
    pairing. test_seen → test_unseen is a point estimate over disjoint template
    sets, seven of them on the far side, and inventing an interval for it is what
    §7's win/loss rule refuses one metric down.
    """

    arm: str
    metric: str
    frm: str
    to: str
    from_value: float | None
    to_value: float | None
    interval: Interval | None

    @property
    def gap(self) -> float | None:
        if self.from_value is None or self.to_value is None:
            return None
        return self.from_value - self.to_value

    def __str__(self) -> str:
        body = (
            f"{self.arm}/{self.metric} {self.frm} → {self.to}: "
            f"{_fmt(self.from_value)} → {_fmt(self.to_value)}, gap {_fmt(self.gap)}"
        )
        return body if self.interval is None else f"{body} {self.interval}"


@dataclass(frozen=True)
class TemplateVerdict:
    """One holdout template, both arms, and which way it went."""

    template_id: str
    baseline: float | None
    optimized: float | None
    cases: int

    @property
    def verdict(self) -> str:
        if self.baseline is None or self.optimized is None:
            return "no data"
        if self.optimized > self.baseline:
            return "win"
        if self.optimized < self.baseline:
            return "loss"
        return "tie"


# ── The reduction: repeats, then cases, then templates ───────────────────────


def case_values(
    outcomes: Iterable[CaseOutcome], *, arm: str, split: str, metric: str
) -> dict[str, float]:
    """One value per case: the mean over the repeats where the metric was numeric.

    A case that was a ``harness_error`` in a repeat contributes nothing from that
    repeat; a case excluded in **every** repeat is absent from the result, which
    is what §7's exclusion means one level down. A metric that
    :data:`~harness.scoring.SKIPPED` is likewise absent rather than 0 — the skip
    is the whole reason coverage is reported beside the mean.
    """
    collected: dict[str, list[float]] = {}
    for outcome in outcomes:
        if outcome.arm != arm or outcome.split != split or outcome.harness_error:
            continue
        value = _numeric(outcome, metric)
        if value is None:
            continue
        collected.setdefault(outcome.case_id, []).append(value)
    return {case_id: fmean(values) for case_id, values in collected.items()}


def excluded_cases(
    outcomes: Iterable[CaseOutcome], *, arm: str, split: str
) -> set[str]:
    """Every case this arm lost to a ``harness_error`` in at least one repeat."""
    return {
        outcome.case_id
        for outcome in outcomes
        if outcome.arm == arm and outcome.split == split and outcome.harness_error
    }


def template_of(outcomes: Iterable[CaseOutcome]) -> dict[str, str]:
    """``case_id`` → ``template_id``, which is the grouping every interval uses."""
    return {outcome.case_id: outcome.template_id for outcome in outcomes}


def template_means(
    values: Mapping[str, float], templates: Mapping[str, str]
) -> dict[str, float]:
    """Per-template means — the observations the bootstrap resamples."""
    grouped: dict[str, list[float]] = {}
    for case_id, value in values.items():
        grouped.setdefault(templates[case_id], []).append(value)
    return {template: fmean(group) for template, group in sorted(grouped.items())}


# ── The interval ─────────────────────────────────────────────────────────────


def bootstrap(
    per_template: Mapping[str, float],
    *,
    resamples: int = RESAMPLES,
    rng_seed: int = RNG_SEED,
    level: float = LEVEL,
) -> Interval | None:
    """Resample **templates** with replacement; percentile interval of the mean.

    ``None`` on an empty population — never an interval around a number nobody
    measured. A single template returns an interval of zero width, which is the
    honest report of one observation rather than a failure: the width comes from
    disagreement *between* templates and there is none to have.
    """
    observations = np.array(list(per_template.values()), dtype=float)
    if observations.size == 0:
        return None
    rng = np.random.default_rng(rng_seed)
    draws = rng.integers(
        0, observations.size, size=(resamples, observations.size), endpoint=False
    )
    means = observations[draws].mean(axis=1)
    tail = (1.0 - level) / 2.0
    low, high = np.quantile(means, [tail, 1.0 - tail])
    return Interval(
        point=float(observations.mean()),
        low=float(low),
        high=float(high),
        templates=observations.size,
    )


def compare(
    outcomes: Sequence[CaseOutcome],
    *,
    split: str,
    metric: str,
    baseline: str = BASELINE_ARM,
    optimized: str = OPTIMIZED_ARM,
    **kwargs: Any,
) -> Comparison:
    """The paired difference between two arms on one metric, over templates.

    The population is the cases usable in **both** arms: present, non-excluded in
    every repeat, and with the metric defined. Cases dropped by that intersection
    are counted rather than absorbed, because a comparison that lost its hardest
    cases in one arm only is the bias §7's exclusion rule is otherwise careful
    about.
    """
    templates = template_of(outcomes)
    left = case_values(outcomes, arm=baseline, split=split, metric=metric)
    right = case_values(outcomes, arm=optimized, split=split, metric=metric)
    lost = excluded_cases(outcomes, arm=baseline, split=split) | excluded_cases(
        outcomes, arm=optimized, split=split
    )
    paired = sorted((set(left) & set(right)) - lost)
    dropped = len((set(left) | set(right)) - set(paired))

    per_template_left = template_means({c: left[c] for c in paired}, templates)
    per_template_right = template_means({c: right[c] for c in paired}, templates)
    differences = {
        template: per_template_right[template] - per_template_left[template]
        for template in per_template_left
    }
    return Comparison(
        split=split,
        metric=metric,
        baseline=_mean_or_none(per_template_left.values()),
        optimized=_mean_or_none(per_template_right.values()),
        difference=bootstrap(differences, **kwargs) if differences else None,
        cases=len(paired),
        dropped=dropped,
        templates=len(per_template_left),
    )


def arm_value(
    outcomes: Iterable[CaseOutcome], *, arm: str, split: str, metric: str
) -> float | None:
    """One arm's template-weighted value for one metric on one split.

    Template-weighted because the template is the unit of analysis. With every
    template carrying the same number of instances and nothing excluded this is
    the case mean :func:`~harness.scoring.aggregate` reports; where an exclusion
    lands they differ, and the weighted one is the one the intervals are around.
    """
    outcomes = list(outcomes)
    values = case_values(outcomes, arm=arm, split=split, metric=metric)
    return _mean_or_none(template_means(values, template_of(outcomes)).values())


# ── The two gaps, reported separately ────────────────────────────────────────


def gaps(
    outcomes: Sequence[CaseOutcome],
    *,
    arm: str,
    metric: str = SELECTION_SCORE,
    **kwargs: Any,
) -> list[Gap]:
    """§7's two generalization gaps for one arm, as two objects that never merge.

    **train → test_seen** catches memorized constants and phrasing overfit: the
    two splits share all 25 templates and are disjoint per parameter value by
    construction, so a candidate that learnt a constant scores on one and not the
    other. Shared templates also make this gap *paired*, so it gets an interval.

    **test_seen → test_unseen** catches template overfit, to which test_seen is
    blind by construction — a candidate accreting a per-template routing rule
    scores perfectly there. The template sets are disjoint and the far side has
    seven, so this one is a point estimate. That is the same refusal
    :func:`win_loss` makes one metric down, and it is why the failure of greatest
    concern is visible only on the smallest split.

    The train side is the **selection score** wherever it appears: the final
    candidate was selected on those instances, so it is not an accuracy and is
    never called one.
    """
    values = {
        split: arm_value(outcomes, arm=arm, split=split, metric=metric)
        for split in (TRAIN, TEST_SEEN, TEST_UNSEEN)
    }
    return [
        Gap(
            arm=arm,
            metric=metric,
            frm=TRAIN,
            to=TEST_SEEN,
            from_value=values[TRAIN],
            to_value=values[TEST_SEEN],
            interval=_paired_gap(outcomes, arm=arm, metric=metric, **kwargs),
        ),
        Gap(
            arm=arm,
            metric=metric,
            frm=TEST_SEEN,
            to=TEST_UNSEEN,
            from_value=values[TEST_SEEN],
            to_value=values[TEST_UNSEEN],
            interval=None,
        ),
    ]


def _paired_gap(
    outcomes: Sequence[CaseOutcome], *, arm: str, metric: str, **kwargs: Any
) -> Interval | None:
    """The train → test_seen gap per template, bootstrapped over the shared templates."""
    templates = template_of(outcomes)
    train = template_means(
        case_values(outcomes, arm=arm, split=TRAIN, metric=metric), templates
    )
    seen = template_means(
        case_values(outcomes, arm=arm, split=TEST_SEEN, metric=metric), templates
    )
    shared = {
        template: train[template] - seen[template]
        for template in sorted(set(train) & set(seen))
    }
    return bootstrap(shared, **kwargs) if shared else None


# ── The holdout is described, not inferred ───────────────────────────────────


def win_loss(
    outcomes: Sequence[CaseOutcome],
    *,
    split: str = TEST_UNSEEN,
    metric: str = TRAJECTORY,
    baseline: str = BASELINE_ARM,
    optimized: str = OPTIMIZED_ARM,
) -> list[TemplateVerdict]:
    """The holdout's result as a per-template table — never an accuracy with an interval.

    Seven templates support description and not inference, and this is the shape
    of that sentence in code: one row per template, both arms' values, and which
    way it went. There is deliberately no function in this module that will
    produce an interval for it, so the rule cannot be broken by picking the wrong
    call.
    """
    templates = template_of(outcomes)
    left = template_means(
        case_values(outcomes, arm=baseline, split=split, metric=metric), templates
    )
    right = template_means(
        case_values(outcomes, arm=optimized, split=split, metric=metric), templates
    )
    counts: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.split == split and outcome.arm == baseline:
            counts[outcome.template_id] = counts.get(outcome.template_id, 0) + 1
    return [
        TemplateVerdict(
            template_id=template,
            baseline=left.get(template),
            optimized=right.get(template),
            cases=counts.get(template, 0),
        )
        for template in sorted(set(left) | set(right) | set(counts))
    ]


# ── What the repeats bought, and what rides beside the metrics ───────────────


def residual_nondeterminism(
    outcomes: Iterable[CaseOutcome], *, arm: str, split: str, metric: str
) -> tuple[float | None, int]:
    """The share of cases whose repeats did not agree, and how many cases were checked.

    This is what the three repeats measure that one rollout cannot. The cache is
    off precisely so that they can: a prompt-keyed hit would hand the first
    repeat's bytes to the other two and this number would be 0 by construction,
    reported as if the endpoint were deterministic (``decisions.md`` § Replication
    and the LLM cache).

    Every arm difference is read against it. A difference inside the noise floor
    is a difference this suite cannot distinguish from the endpoint's own
    variation.
    """
    collected: dict[str, list[float]] = {}
    for outcome in outcomes:
        if outcome.arm != arm or outcome.split != split or outcome.harness_error:
            continue
        value = _numeric(outcome, metric)
        if value is not None:
            collected.setdefault(outcome.case_id, []).append(value)
    checked = [values for values in collected.values() if len(values) > 1]
    if not checked:
        return None, 0
    disagreed = sum(1 for values in checked if len(set(values)) > 1)
    return disagreed / len(checked), len(checked)


def diagnostics(
    outcomes: Iterable[CaseOutcome], *, arm: str, split: str
) -> dict[str, float | None]:
    """§7's diagnostics for one condition: reported beside the metrics, never scored.

    ``parse_failures`` is a **count and not a rate against answer accuracy**: a
    candidate degrading the output format has to stay distinguishable from one
    degrading reasoning, and folding the two together is exactly what makes them
    indistinguishable. ``fixer_iterations`` stays ``None`` wherever no rollout
    could observe it — the sub-agent's retries run on its own ``Runner`` and
    nothing writes the count into the state delta that reaches the root side.
    """
    rows = [
        outcome
        for outcome in outcomes
        if outcome.arm == arm and outcome.split == split and not outcome.harness_error
    ]
    if not rows:
        return {}
    fixer = [
        float(value)
        for outcome in rows
        if (value := outcome.diagnostics.get("fixer_iterations")) is not None
    ]
    return {
        "mean_steps": _mean_of(rows, "steps"),
        "mean_extra_calls": _mean_of(rows, "extra_calls"),
        "mean_latency_s": _mean_of(rows, "latency_s"),
        "mean_tokens": _mean_or_none(
            float((outcome.diagnostics.get("tokens") or {}).get("total", 0))
            for outcome in rows
        ),
        "mean_fixer_iterations": _mean_or_none(fixer),
        "parse_failures": float(
            sum(1 for outcome in rows if outcome.diagnostics.get("parse_failure"))
        ),
        "step_cap_exceeded": float(
            sum(1 for outcome in rows if outcome.diagnostics.get("step_cap_exceeded"))
        ),
        "rollouts": float(len(rows)),
    }


def abstention_split(
    outcomes: Iterable[CaseOutcome], *, arm: str, split: str
) -> dict[str, float | int | None]:
    """Accuracy over the unanswerable cases and the false-abstention rate, apart.

    Never one number. A candidate that abstains from everything scores 1.0 on the
    first, and the only thing that says so is the second.
    """
    rows = [
        outcome
        for outcome in outcomes
        if outcome.arm == arm and outcome.split == split and not outcome.harness_error
    ]
    unanswerable = [outcome for outcome in rows if outcome.unanswerable]
    answerable = [outcome for outcome in rows if not outcome.unanswerable]
    return {
        "accuracy": _mean_or_none(float(o.abstained) for o in unanswerable),
        "unanswerable": len(unanswerable),
        "false_abstention_rate": _mean_or_none(
            float(o.abstained) for o in answerable
        ),
        "answerable": len(answerable),
    }


# ── The report ───────────────────────────────────────────────────────────────


def render(outcomes: Sequence[CaseOutcome], **kwargs: Any) -> str:
    """§7's report, in the order §7 states its rules.

    Assembled here rather than in the driver so that a re-analysis of a written
    measurement produces the identical text — the report is a function of the
    outcomes, and a driver that formatted its own would be a second one.
    """
    arms = sorted({outcome.arm for outcome in outcomes})
    metrics = (ANSWER, TRAJECTORY, CARD_RECALL, ABSTENTION, SELECTION_SCORE)
    lines: list[str] = []

    lines.append("PER-ARM VALUES (template-weighted; train is a SELECTION SCORE)")
    for split in (TRAIN, TEST_SEEN, TEST_UNSEEN):
        for metric in metrics:
            values = "  ".join(
                f"{arm} {_fmt(arm_value(outcomes, arm=arm, split=split, metric=metric))}"
                for arm in arms
            )
            lines.append(f"  {split:<12} {metric:<16} {values}")

    lines.append("")
    lines.append("ABSTENTION, THE TWO NUMBERS THAT NEVER AVERAGE")
    for split in (TRAIN, TEST_SEEN, TEST_UNSEEN):
        for arm in arms:
            numbers = abstention_split(outcomes, arm=arm, split=split)
            lines.append(
                f"  {split:<12} {arm:<10} accuracy {_fmt(numbers['accuracy'])} "
                f"over {numbers['unanswerable']} unanswerable; "
                f"false abstention {_fmt(numbers['false_abstention_rate'])} "
                f"over {numbers['answerable']} answerable"
            )

    if BASELINE_ARM in arms and OPTIMIZED_ARM in arms:
        lines.append("")
        lines.append(
            "PAIRED DIFFERENCE, BOOTSTRAP OVER template_id "
            f"({RESAMPLES} resamples, rng {RNG_SEED}, {LEVEL:.0%} percentile)"
        )
        for split in (TRAIN, TEST_SEEN):
            for metric in metrics:
                lines.append(
                    f"  {compare(outcomes, split=split, metric=metric, **kwargs)}"
                )
        lines.append(
            "  test_unseen: no interval — seven templates support description, "
            "not inference. See the win/loss table."
        )

        lines.append("")
        lines.append("THE TWO GENERALIZATION GAPS, REPORTED SEPARATELY")
        for arm in arms:
            for gap in gaps(outcomes, arm=arm, **kwargs):
                lines.append(f"  {gap}")
        lines.append(
            "  train → test_seen catches memorized constants and phrasing "
            "overfit; test_seen → test_unseen catches template overfit, to which "
            "test_seen is blind by construction."
        )

        lines.append("")
        lines.append("test_unseen TRAJECTORY — PER-TEMPLATE WIN/LOSS, NEVER AN ACCURACY")
        for verdict in win_loss(outcomes):
            lines.append(
                f"  {verdict.template_id:<8} baseline {_fmt(verdict.baseline)}  "
                f"optimized {_fmt(verdict.optimized)}  "
                f"{verdict.verdict:<8} ({verdict.cases} cases)"
            )
        tally = {"win": 0, "loss": 0, "tie": 0, "no data": 0}
        for verdict in win_loss(outcomes):
            tally[verdict.verdict] += 1
        lines.append(
            f"  tally: {tally['win']} win, {tally['loss']} loss, {tally['tie']} tie"
            + (f", {tally['no data']} no data" if tally["no data"] else "")
        )

    lines.append("")
    lines.append("RESIDUAL NONDETERMINISM — WHAT THE THREE REPEATS MEASURED")
    for split in (TRAIN, TEST_SEEN, TEST_UNSEEN):
        for arm in arms:
            rate, checked = residual_nondeterminism(
                outcomes, arm=arm, split=split, metric=SELECTION_SCORE
            )
            lines.append(
                f"  {split:<12} {arm:<10} {_fmt(rate)} of {checked} case(s) "
                "disagreed across their repeats"
            )

    lines.append("")
    lines.append("DIAGNOSTICS — REPORTED, NEVER SCORED")
    for split in (TRAIN, TEST_SEEN, TEST_UNSEEN):
        for arm in arms:
            numbers = diagnostics(outcomes, arm=arm, split=split)
            if not numbers:
                continue
            lines.append(
                f"  {split:<12} {arm:<10} "
                + ", ".join(f"{name} {_fmt(value)}" for name, value in numbers.items())
            )
    lines.append(
        "  parse_failures is a count kept apart from answer accuracy, so a "
        "candidate degrading the output format stays distinguishable from one "
        "degrading reasoning."
    )
    return "\n".join(lines)


def _numeric(outcome: CaseOutcome, metric: str) -> float | None:
    """One outcome's value for *metric*, or ``None`` where it skipped or is absent."""
    if metric == SELECTION_SCORE:
        return outcome.selection_score
    value = outcome.metrics.get(metric)
    if value is None or value == SKIPPED or isinstance(value, str):
        return None
    return float(value)


def _mean_of(rows: Sequence[CaseOutcome], key: str) -> float | None:
    return _mean_or_none(
        float(outcome.diagnostics.get(key, 0) or 0) for outcome in rows
    )


def _mean_or_none(values: Iterable[float]) -> float | None:
    collected = list(values)
    return fmean(collected) if collected else None


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


__all__ = [
    "LEVEL",
    "RESAMPLES",
    "RNG_SEED",
    "SELECTION_SCORE",
    "Comparison",
    "Gap",
    "Interval",
    "TemplateVerdict",
    "abstention_split",
    "arm_value",
    "bootstrap",
    "case_values",
    "compare",
    "diagnostics",
    "gaps",
    "render",
    "residual_nondeterminism",
    "template_means",
    "win_loss",
]
