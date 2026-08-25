"""The four metrics as MLflow ``Scorer``s — T102's functions, reached from the search.

**One layer, no second implementation.** Each scorer here rebuilds the
:class:`~harness.run_case.CaseResult` from one record's ``outputs`` and hands it
to the per-case function ``harness/scoring.py`` already owns, so the search
scores a rollout with exactly the code the measurement run scores it with. The
testbed is frozen as of T107 and nothing in this module decides anything: it
adapts a shape (``inputs`` / ``outputs`` / ``expectations``) to a shape
(``CaseResult`` plus ``expectations``) and wraps a
:class:`~harness.scoring.MetricScore` as a ``Feedback``.

**The rationale is the search signal, not decoration.** MLflow forwards
``Feedback.rationale`` into GEPA's reflective dataset (``findings.md``
§ Optimizer internals), which is the only textual channel a candidate's failure
has into the next proposal. The four texts are §7's four: the trajectory diff
against gold, the cards fetched against the cards wanted, what went wrong
underneath the answer — the sub-agent's SQL errors above all — and the
abstention outcome.

**The scorer names are the weight keys**, deliberately. ``create_metric_from_
scorers`` builds ``{scorer.name: value}`` and hands that dict to the aggregation
callable (``findings.md``), and :func:`~harness.scoring.aggregate_scores` raises
on a name it has no registered weight for. So the names are
:data:`~harness.scoring.METRICS` and a renamed scorer fails loudly instead of
being dropped from the objective.

**Skipped stays non-numeric all the way through.** A skipping metric returns a
``Feedback`` whose value is :data:`~harness.scoring.SKIPPED`, and it is
:func:`~harness.scoring.aggregate_scores` — the mandatory ``aggregation=``
argument — that turns it into a skip. Without that callable MLflow makes the
objective the mean of the numeric values and then raises on the string, so the
two are wired together or not at all (``decisions.md`` § Candidate selection and
the scorers' aggregation).

**A rollout that never produced outputs scores 0 on every metric.**
``optimize_prompts`` catches a ``predict_fn`` exception and hands the scorers a
*string* in place of the record's outputs (``findings.md``), so a scorer that
assumed a mapping would take the whole search down with it. That case is §7's
**declared residual** on the search path: 0, said out loud in the rationale, and
counted per arm by :mod:`harness.train_data`. It is never a skip — every metric
skipping would leave nothing to select on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from mlflow.entities import Feedback
from mlflow.genai.scorers import Scorer, scorer

from harness.predict import case_result
from harness.run_case import CaseResult
from harness.scoring import (
    ABSTENTION,
    ANSWER,
    CARD_RECALL,
    TRAJECTORY,
    MetricScore,
    score_abstention,
    score_answer,
    score_card_recall,
    score_trajectory,
)

logger = structlog.get_logger(__name__)

_DETAIL_LIMIT = 300
"""How much of a failure's text reaches the rationale.

The rationale is read by a model with a budget, and an exception's tail is
rarely the informative end of it.
"""


@scorer(name=ANSWER)
def answer(
    inputs: Mapping[str, Any], outputs: Any, expectations: Mapping[str, Any]
) -> Feedback:
    """Exact match or tolerance against the oracle, after unit normalization (§7).

    **This is the metric that carries what went wrong underneath the answer.**
    A run whose sub-agent returned ``{"status": "error", …}`` or whose tool
    reported an ``upstream`` failure produced a wrong answer *for a reason*, and
    the reason is the half of the signal a bare 0 cannot carry — so
    :func:`run_evidence` is appended here rather than repeated across all four
    rationales, which share one record in the reflective dataset.

    The evidence is appended to the **skip** as well. A plot case has no answer
    to compare, but a plot case whose model call failed still has something
    reflection needs to read, and this is the only rationale that would
    otherwise be silent about it.
    """
    result = _case_result(outputs)
    if result is None:
        return _residual(ANSWER, outputs)
    return _feedback(score_answer(result, expectations), evidence=run_evidence(result))


@scorer(name=TRAJECTORY)
def trajectory(
    inputs: Mapping[str, Any], outputs: Any, expectations: Mapping[str, Any]
) -> Feedback:
    """Binary: every gold tool called, no must-not called, every argument check passed.

    ``as_of`` comes off the record's own ``inputs`` because an
    ``argument_check`` carrying ``resolve: "window"`` needs the case's clock to
    resolve a relative window against —
    :func:`~harness.scoring.score_trajectory` refuses to default it to the
    host's date, which would leak real time into a pinned case.
    """
    result = _case_result(outputs)
    if result is None:
        return _residual(TRAJECTORY, outputs)
    return _feedback(
        score_trajectory(result, expectations, as_of=inputs.get("as_of"))
    )


@scorer(name=CARD_RECALL)
def card_recall(
    inputs: Mapping[str, Any], outputs: Any, expectations: Mapping[str, Any]
) -> Feedback:
    """``|gold ∩ retrieved| / |gold|`` over the union of every lookup call (§7).

    Skips where the case wants no card, which is most of the suite; the coverage
    that skip implies is reported by :func:`~harness.scoring.aggregate` on the
    measurement path and dropped from the objective by
    :func:`~harness.scoring.aggregate_scores` here.
    """
    result = _case_result(outputs)
    if result is None:
        return _residual(CARD_RECALL, outputs)
    return _feedback(score_card_recall(result, expectations))


@scorer(name=ABSTENTION)
def abstention(
    inputs: Mapping[str, Any], outputs: Any, expectations: Mapping[str, Any]
) -> Feedback:
    """1 iff the agent's contract ``status`` is the one the case expects (§7).

    One value per case here, two numbers in §7's report: accuracy on the
    unanswerable cases and the false-abstention rate on the answerable ones are
    split by :func:`~harness.scoring.aggregate`, never blended. The split is a
    property of the population, so it lives where the population does and not in
    a per-record scorer.
    """
    result = _case_result(outputs)
    if result is None:
        return _residual(ABSTENTION, outputs)
    return _feedback(score_abstention(result, expectations))


SCORERS: tuple[Scorer, ...] = (answer, trajectory, card_recall, abstention)
"""§7's four metrics, in the order it lists them — ``optimize_prompts``' ``scorers=``.

Passed with :func:`~harness.scoring.aggregate_scores` as ``aggregation=`` or not
at all: two of these four return a non-numeric value on the cases where they do
not apply, and without the callable that value is an exception rather than a
skip.
"""


def run_evidence(result: CaseResult) -> str:
    """What the run hit underneath its answer, as one sentence for reflection.

    ``""`` when the run hit nothing, so the common case adds no text at all.
    Two populations, both of them things a candidate can act on and neither of
    them visible in a score:

    * the **exclusions** — an ``upstream`` tool failure or the frozen
      sub-agent's ``{"status": "error", …}`` payload, which is where a SQL
      error surfaces at root level (``harness/run_case.py``);
    * the **step cap** — a candidate that spent §2's budget without answering,
      which reads as a parse failure everywhere else and is the one diagnostic
      that distinguishes a looping candidate from a malformed one.

    Not scored, either way. This is the text beside the number, and the number
    was decided by T102's frozen functions before this was assembled.
    """
    notes = [
        f"{item.tool or 'the rollout'} failed ({item.source}): "
        f"{_short(item.details)}"
        for item in result.exclusions
    ]
    if result.diagnostics.get("step_cap_exceeded"):
        notes.append(
            "the rollout spent the tool-step cap without producing a final answer"
        )
    if not notes:
        return ""
    return "During the run: " + "; ".join(notes) + "."


def _case_result(outputs: Any) -> CaseResult | None:
    """One record's outputs as a :class:`CaseResult`, or ``None`` if it has none.

    ``None`` means ``predict_fn`` raised and ``optimize_prompts`` replaced the
    outputs with its own failure string (``findings.md``). Reconstructed through
    :func:`~harness.predict.case_result`, which is the exact inverse of what
    ``predict_fn`` emitted, so nothing is re-derived here.
    """
    if not isinstance(outputs, Mapping):
        return None
    return case_result(outputs)


def _feedback(score: MetricScore, *, evidence: str = "") -> Feedback:
    """A :class:`MetricScore` as the ``Feedback`` MLflow logs and GEPA reflects on."""
    rationale = f"{score.rationale} {evidence}".strip() if evidence else score.rationale
    return Feedback(value=score.value, rationale=rationale)


def _residual(metric: str, outputs: Any) -> Feedback:
    """§7's declared residual: 0, and the reason said out loud.

    Never :data:`~harness.scoring.SKIPPED`. A skip means "this metric does not
    apply to this case"; a rollout that did not complete is a case on which
    every metric applies and none could be measured, and calling that a skip
    would quietly remove the record from the objective instead of scoring it.
    """
    logger.warning("Scoring a rollout that produced no outputs", metric=metric)
    return Feedback(
        value=0.0,
        rationale=(
            f"The rollout did not complete, so {metric} scores 0 as a declared "
            f"residual rather than being skipped: {_short(str(outputs))}"
        ),
    )


def _short(text: str) -> str:
    """*text* trimmed to :data:`_DETAIL_LIMIT` characters, marked where it was cut."""
    text = " ".join(text.split())
    return text if len(text) <= _DETAIL_LIMIT else f"{text[:_DETAIL_LIMIT]}…"
