"""The measurement run: two arms, three splits, three repeats, and no optimizer (T128).

**The statistics are computed on this path and never on the optimizer's internal
scores.** GEPA's per-iteration numbers are a search diagnostic — they are
computed over subsamples it chose, on candidates it was in the middle of
proposing, with the LLM response cache on and a record-mode response cache
underneath. Reading a generalization gap off them would be reading a gap between
two things neither of which is a measurement.

**Every switch points the other way from the search** (``decisions.md`` § The
search records where the measurement run replays):

* ``allow_live=False``. The response cache **replays**, and a miss is a
  ``CacheMissError`` that the tool wrappers turn into an ``upstream`` error —
  which excludes the case as a ``harness_error``. That is the exclusion channel
  §7 says is a property of the measurement path, and it is why this module reads
  the **whole** split rather than :mod:`harness.train_data`'s pre-filtered one.
* **The LLM response cache is off.** The three repeats per condition *are* the
  replication, and the cache key is the full request — so a prompt-keyed hit
  would hand the first repeat's bytes to the other two and the three would
  collapse into one sample and two copies of it. The repeats measure residual
  nondeterminism rather than suppress it (``decisions.md`` § Replication and the
  LLM cache).

**One pinned seed, not three.** The endpoints serve open-weight models under no
documented seed contract, so three distinct seeds would be three keys and three
samples only where ``seed`` is honoured; where it is ignored the arrangement is
this one wearing bookkeeping that asserts a control which is not there.

**Paired on identical cases.** Every condition reads the same committed file in
file order, so a case is the same case in both arms and in all three repeats.
That is what makes the paired bootstrap of :mod:`harness.stats` a paired one, and
it is why the arms differ *only* in which registered prompt versions they read.

**Scored with the frozen functions.** A rollout's outputs go back through
:func:`~harness.predict.case_result` and into
:func:`~harness.scoring.score_case` — the same per-case functions T122 wraps as
scorers — so the search and the measurement run score a rollout with the same
code. Nothing in this module decides what a metric means.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow
import structlog

from harness.candidates import evaluation_pass, seed_versions
from harness.ledger import RunLedger, experiment_run, ledgered
from harness.predict import case_result
from harness.preregistration import MEASUREMENT
from harness.preregistration import enforce as enforce_prereg
from harness.preregistration import run_params as prereg_params
from harness.run_case import EVAL_CACHE_DIR, PINNED_DB
from harness.scoring import METRICS, ArmReport, CaseScore, aggregate, score_case
from harness.train_data import CASES_DIR, load_split
from water_assistant_agent.assistant.llm import configure_llm_cache
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

BASELINE_ARM = "baseline"
OPTIMIZED_ARM = "optimized"

SPLITS: tuple[str, ...] = ("train", "test_seen", "test_unseen")
"""The three committed splits. Which ones a run measures, and in what order, is
the **registration's** to say — see :func:`registered_protocol`."""

REPEATS = 3
"""Repeats per condition at one pinned seed, as ``decisions.md`` argues for them.

This is the *architecture's* number, not necessarily a given run's: a run takes
its repeat count from the pre-registration, which is the thing that can be
amended with a reason and a cost written beside it (T129). Keeping both is
deliberate — a constant that silently followed the registration would leave
nothing recording what the design asked for.
"""


def registered_protocol() -> tuple[list[str], int]:
    """The splits and repeat count this run is registered to measure.

    Read from ``eval/preregistration.json`` rather than defaulted from the
    constants above, because the registered protocol is what a measurement run
    *is*: a default that disagreed with the registration would make the honest
    invocation the one that gets refused, and the refusal is supposed to mean
    something.
    """
    from harness.preregistration import MEASUREMENT, load

    registered = load()[MEASUREMENT]
    return list(registered["splits"]), int(registered["repeats"])

OPTIMIZED_ARM_FILE = REPO_ROOT / "eval" / "optimized_candidate.json"
"""Where a search records the versions its selected candidate was registered at.

The optimized arm has no identity until a search has produced one, which is why
the pre-registration names it by definition rather than by version (T129). This
file is what turns that definition into something a measurement can read, and it
is written by the search rather than typed by hand — a transcribed version number
is the one way the arm being measured could stop being the arm that was selected.
"""


@dataclass(frozen=True)
class CaseOutcome:
    """One case, one arm, one repeat: what was scored, kept flat for the statistics.

    A :class:`~harness.scoring.CaseScore` reduced to what
    :mod:`harness.stats` reads, plus the three keys that place it. ``metrics``
    keeps :data:`~harness.scoring.SKIPPED` as itself — a skip is not a zero, and
    collapsing it here would put it back.
    """

    arm: str
    split: str
    repeat: int
    case_id: str
    template_id: str
    metrics: Mapping[str, float | str]
    selection_score: float
    harness_error: bool
    unanswerable: bool
    abstained: bool
    diagnostics: Mapping[str, Any]

    @classmethod
    def of(cls, score: CaseScore, *, arm: str, split: str, repeat: int) -> CaseOutcome:
        return cls(
            arm=arm,
            split=split,
            repeat=repeat,
            case_id=score.case_id,
            template_id=score.template_id,
            metrics={name: score.metrics[name].value for name in METRICS},
            selection_score=score.selection_score,
            harness_error=score.harness_error,
            unanswerable=score.unanswerable,
            abstained=score.abstained,
            diagnostics=dict(score.diagnostics),
        )

    def as_row(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "split": self.split,
            "repeat": self.repeat,
            "case_id": self.case_id,
            "template_id": self.template_id,
            "metrics": dict(self.metrics),
            "selection_score": self.selection_score,
            "harness_error": self.harness_error,
            "unanswerable": self.unanswerable,
            "abstained": self.abstained,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> CaseOutcome:
        return cls(
            arm=row["arm"],
            split=row["split"],
            repeat=int(row["repeat"]),
            case_id=row["case_id"],
            template_id=row["template_id"],
            metrics=dict(row["metrics"]),
            selection_score=float(row["selection_score"]),
            harness_error=bool(row["harness_error"]),
            unanswerable=bool(row["unanswerable"]),
            abstained=bool(row["abstained"]),
            diagnostics=dict(row.get("diagnostics") or {}),
        )


@dataclass(frozen=True)
class ConditionResult:
    """One arm on one split at one repeat — the unit the three repeats are counted in."""

    arm: str
    split: str
    repeat: int
    report: ArmReport
    outcomes: tuple[CaseOutcome, ...]
    ledger: RunLedger

    def summary(self) -> str:
        report = self.report
        return (
            f"{self.arm}/{self.split}#{self.repeat}: "
            f"{report.included}/{report.cases} included, "
            f"{report.excluded} excluded {report.exclusions_by_source or ''}, "
            f"selection score {_fmt(report.selection_score)}, "
            f"answer {_fmt(report.answer.mean)} "
            f"(coverage {_fmt(report.answer.coverage)}), "
            f"trajectory {_fmt(report.trajectory.mean)}"
        )


@dataclass
class Measurement:
    """Every condition of one measurement run, and what it was run against.

    Written to disk as well as to MLflow, because the statistics are a function
    of these outcomes and nothing else: a re-analysis should never need the model
    back (``harness/scoring.py``'s "nothing here re-runs anything", one layer up).
    """

    versions: Mapping[str, Mapping[str, int]]
    conditions: list[ConditionResult] = field(default_factory=list)
    started: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def outcomes(self) -> tuple[CaseOutcome, ...]:
        return tuple(
            outcome
            for condition in self.conditions
            for outcome in condition.outcomes
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "arm_versions": {arm: dict(v) for arm, v in self.versions.items()},
            "reports": [
                {
                    "arm": condition.arm,
                    "split": condition.split,
                    "repeat": condition.repeat,
                    "cases": condition.report.cases,
                    "included": condition.report.included,
                    "excluded": condition.report.excluded,
                    "exclusions_by_source": dict(condition.report.exclusions_by_source),
                    "selection_score": condition.report.selection_score,
                    "diagnostics": dict(condition.report.diagnostics),
                }
                for condition in self.conditions
            ],
            "outcomes": [outcome.as_row() for outcome in self.outcomes],
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_document(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        logger.info("Measurement written", path=str(path), conditions=len(self.conditions))
        return path


def outcomes_from(path: Path) -> tuple[CaseOutcome, ...]:
    """A written measurement's outcomes, for a re-analysis with no model in reach."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return tuple(CaseOutcome.from_row(row) for row in document["outcomes"])


def arm_versions(
    *, optimized_path: Path = OPTIMIZED_ARM_FILE
) -> dict[str, dict[str, int]]:
    """The registered prompt versions each arm reads, which is the whole of their difference.

    The baseline is the pinned seed; the optimized arm is whatever the one
    registered search selected, read from the file that search wrote.

    Raises:
        FileNotFoundError: no search has recorded an optimized candidate. There is
            no default to fall back on — reading ``@latest`` would silently measure
            whatever was registered most recently, which after a search is a
            candidate nobody selected.
    """
    if not optimized_path.exists():
        raise FileNotFoundError(
            f"No optimized candidate recorded at {optimized_path}. Run the "
            "registered search first; the optimized arm is defined as what that "
            "search selects, and there is nothing else it could be."
        )
    optimized = json.loads(optimized_path.read_text(encoding="utf-8"))["versions"]
    return {
        BASELINE_ARM: seed_versions(),
        OPTIMIZED_ARM: {name: int(version) for name, version in optimized.items()},
    }


def write_optimized_arm(
    prompts: Iterable[Any], *, path: Path = OPTIMIZED_ARM_FILE, **provenance: Any
) -> dict[str, int]:
    """Record the versions a search registered its selected candidate at.

    Written by the search rather than typed afterwards: a transcribed version
    number is the one way the arm being measured could stop being the arm that
    was selected. The prompt *names* are mapped back to component keys here,
    because that is the direction :func:`~harness.candidates.read_candidates`
    reads them in.
    """
    from harness.candidates import PROMPT_NAMES

    by_name = {name: component for component, name in PROMPT_NAMES.items()}
    versions = {by_name[prompt.name]: int(prompt.version) for prompt in prompts}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"versions": dict(sorted(versions.items())), **provenance},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info("Optimized arm recorded", path=str(path), versions=versions)
    return versions


def measure_condition(
    *,
    arm: str,
    split: str,
    repeat: int,
    versions: Mapping[str, int],
    cases_dir: Path = CASES_DIR,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    settings: AssistantSettings | None = None,
    workers: int = 1,
) -> ConditionResult:
    """Run and score one arm on one split, once.

    Args:
        arm: ``baseline`` or ``optimized`` — the name every row is filed under.
        split: One of :data:`SPLITS`. The **whole** file: the measurement path
            has an exclusion channel, so a case that cannot replay is excluded
            and counted rather than filtered out in advance.
        repeat: Which of the three this is. Part of the row key, never averaged
            away here — :mod:`harness.stats` owns the reduction.
        versions: The registered prompt versions this arm reads.
        cases_dir: Where the emitted case files live.
        db_path: The pinned database.
        cache_dir: The committed response cache, replayed and never added to.
        settings: Where the model and the cache switch come from.
        workers: Rollouts in flight. Threads are safe here because replay never
            reaches the GR2L client's loop-bound singleton — T115's hazard is a
            capture-pass one — and because ``predict_fn`` builds everything per
            record with nothing ambient.

    Returns:
        The arm's report, the per-case outcomes and the run ledger.

    Raises:
        UnreadCandidateError: the pass ended with a candidate component unread.
            The same assertion the search makes, and it matters more here: a
            component the measurement never read is one both arms ran identically
            on, which would understate every difference between them.
    """
    settings = settings or get_settings()
    records = load_split(split, cases_dir=cases_dir)
    ledger = RunLedger(arm=arm, split=split, repeat=repeat, settings=settings)
    rollout = ledgered(
        ledger,
        versions=versions,
        db_path=db_path,
        cache_dir=cache_dir,
        # Replay. A miss is an `upstream` error and excludes the case, which is
        # §7's exclusion channel and exists only on this path.
        allow_live=False,
        expectations={
            record["inputs"]["case_id"]: record.get("expectations", {})
            for record in records
        },
    )

    def one(record: Mapping[str, Any]) -> CaseScore:
        inputs = record["inputs"]
        expectations = record.get("expectations", {})
        try:
            outputs = rollout(inputs)
        except Exception as exc:  # noqa: BLE001 - scored as a failed rollout, not lost
            # There is no `optimize_prompts` here to turn this into a string, so
            # the measurement path builds the same thing itself: a case that
            # produced no outputs is a `harness_error` with the reason attached,
            # excluded by `aggregate` and counted with its source. Losing it
            # would drop a candidate's worst runs and lift its mean.
            logger.exception("Rollout failed", case_id=inputs.get("case_id"))
            outputs = _failed_rollout(inputs, exc)
        return score_case(
            case_result(outputs), expectations, as_of=inputs.get("as_of")
        )

    with (
        experiment_run(f"{arm}-{split}-r{repeat}", nested=True),
        evaluation_pass(),
    ):
        mlflow.log_params(
            {
                "arm": arm,
                "split": split,
                "repeat": repeat,
                "cases": len(records),
                "candidate_versions": json.dumps(dict(sorted(versions.items()))),
            }
        )
        logger.info(
            "Condition starting", arm=arm, split=split, repeat=repeat, cases=len(records)
        )
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                scored = list(pool.map(one, records))
        else:
            scored = [one(record) for record in records]
        report = aggregate(scored, arm=arm)
        _log_report(report)
        ledger.log()

    result = ConditionResult(
        arm=arm,
        split=split,
        repeat=repeat,
        report=report,
        outcomes=tuple(
            CaseOutcome.of(score, arm=arm, split=split, repeat=repeat)
            for score in scored
        ),
        ledger=ledger,
    )
    logger.info("Condition complete", summary=result.summary())
    return result


def measure(
    *,
    arms: Mapping[str, Mapping[str, int]] | None = None,
    splits: Sequence[str] | None = None,
    repeats: int | None = None,
    cases_dir: Path = CASES_DIR,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    settings: AssistantSettings | None = None,
    workers: int = 1,
    exploratory: bool = False,
    run_name: str = "measurement",
) -> Measurement:
    """Every condition of one measurement run, under one parent MLflow run.

    The conditions run **repeat-major**: repeat 1 of every arm and split, then
    repeat 2, then repeat 3. A drift in the endpoint during the run then lands
    across the arms rather than inside one of them, which is the difference
    between a run that is noisier than it should be and a run whose comparison is
    not interpretable at all.

    Raises:
        PreregistrationViolation: the run's parameters are not the registered
            ones and it did not declare itself exploratory (T129). A test number
            under an unregistered protocol is what the registration exists to
            prevent, so this refusal is the one place the check bites.
    """
    settings = settings or get_settings()
    arms = arms or arm_versions()
    registered_splits, registered_repeats = registered_protocol()
    splits = registered_splits if splits is None else splits
    repeats = registered_repeats if repeats is None else repeats
    enforce_prereg(
        MEASUREMENT,
        {
            "arms": list(arms),
            "splits": list(splits),
            "repeats": repeats,
            "temperature": settings.llm_temperature,
            "seed": settings.llm_seed,
            "llm_cache": "on" if settings.llm_cache_enabled else "off",
            "response_cache_mode": "replay",
        },
        exploratory=exploratory,
    )
    # Off is the default and the registered value; installed explicitly all the
    # same, because a process that ran a search earlier has turned it on.
    configure_llm_cache(False, settings)

    measurement = Measurement(versions=arms)
    with experiment_run(run_name):
        mlflow.log_params(
            prereg_params(
                MEASUREMENT,
                {
                    "arms": list(arms),
                    "splits": list(splits),
                    "repeats": repeats,
                    "temperature": settings.llm_temperature,
                    "seed": settings.llm_seed,
                    "llm_cache": "on" if settings.llm_cache_enabled else "off",
                    "response_cache_mode": "replay",
                },
                exploratory=exploratory,
            )
        )
        for repeat in range(1, repeats + 1):
            for split in splits:
                for arm, versions in arms.items():
                    measurement.conditions.append(
                        measure_condition(
                            arm=arm,
                            split=split,
                            repeat=repeat,
                            versions=versions,
                            cases_dir=cases_dir,
                            db_path=db_path,
                            cache_dir=cache_dir,
                            settings=settings,
                            workers=workers,
                        )
                    )
    return measurement


def _failed_rollout(inputs: Mapping[str, Any], exc: BaseException) -> dict[str, Any]:
    """The outputs of a rollout that raised, in the shape ``predict_fn`` would emit.

    Built rather than propagated so the case stays in the denominator as a
    ``harness_error`` with its reason: the search path gets this shape from
    ``optimize_prompts`` catching the exception (``findings.md``), and the
    measurement path has to make it itself or lose the case entirely.
    """
    return {
        "case_id": str(inputs.get("case_id", "")),
        "template_id": str(inputs.get("template_id", "")),
        "status": None,
        "answer": None,
        "unit": None,
        "explanation": None,
        "final_text": "",
        "trajectory": [],
        "harness_error": True,
        "exclusions": [
            {
                "tool": "",
                "source": "rollout",
                "details": f"{type(exc).__name__}: {exc}",
            }
        ],
        "diagnostics": {"parse_failure": True},
    }


def _log_report(report: ArmReport) -> None:
    """One arm's numbers as MLflow metrics, skips and empty populations included.

    A metric with no population is **absent** rather than 0: ``aggregate``
    returns ``None`` there on purpose, and logging a 0 would put back exactly the
    fabricated number it refuses to invent.
    """
    metrics: dict[str, float] = {
        "cases": report.cases,
        "included": report.included,
        "excluded": report.excluded,
        "abstention.unanswerable": report.abstention.unanswerable,
        "abstention.answerable": report.abstention.answerable,
    }
    for name, summary in (
        ("answer", report.answer),
        ("trajectory", report.trajectory),
        ("card_recall", report.card_recall),
    ):
        metrics[f"{name}.scored"] = summary.scored
        metrics[f"{name}.skipped"] = summary.skipped
        if summary.mean is not None:
            metrics[name] = summary.mean
        if summary.coverage is not None:
            metrics[f"{name}.coverage"] = summary.coverage
    for name, value in (
        ("selection_score", report.selection_score),
        ("abstention.accuracy", report.abstention.accuracy),
        ("abstention.false_rate", report.abstention.false_abstention_rate),
    ):
        if value is not None:
            metrics[name] = value
    for name, value in report.diagnostics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[f"diagnostics.{name}"] = float(value)
    mlflow.log_metrics(metrics)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


__all__ = [
    "BASELINE_ARM",
    "OPTIMIZED_ARM",
    "OPTIMIZED_ARM_FILE",
    "REPEATS",
    "SPLITS",
    "CaseOutcome",
    "ConditionResult",
    "Measurement",
    "arm_versions",
    "measure",
    "measure_condition",
    "outcomes_from",
    "write_optimized_arm",
]
