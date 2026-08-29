"""The search's training records, and the residuals its protections do not remove.

`harness_error` **is a property of the measurement path.** Inside
``optimize_prompts`` there is no exclusion channel at all — the optimizer
consumes one float per record — so §7 protects the search by construction
instead, in three moves, and this module owns two of them.

1. **The training records are pre-filtered to fully captured cases**
   (:func:`training_records`). A case whose *gold answer* cannot be replayed
   from the committed cache is dropped before the search sees it, so a residual
   failure is never something the suite already knew about.
2. A cache miss on a window the **candidate** chose records rather than fails.
   That is not here: it is ``allow_live=True`` on the search's
   ``predict_fn``, which is the whole of the mechanism (§5).
3. **What still failed is declared and counted** (:class:`ResidualLedger`,
   :class:`ResidualReport`). Only an unreachable service or a diverging canary
   survives the first two, and such a record scores 0 through the scorers'
   declared residual — a 0 nobody counted being indistinguishable from a
   candidate that answered badly.

**The pre-filter's question is the oracle's, not the rollout's.** A case is
"fully captured" when the requests its committed answer was computed from are in
``eval/cache/`` — which is exactly what a capture pass records, and the only
surface anything can be held to in advance (``decisions.md`` § The response
cache: capture runs over the oracle's window). What windows a *candidate* will
choose is unknowable before the search, which is why move 2 exists and why
asking the pre-filter to cover it would be asking it for an answer it cannot
have.

**Verified with the network physically blocked**, for the same reason
``scripts/capture_cache.py --verify`` is: the context is bound to a
``ReplayCache``, *and* ``httpx.AsyncClient.send`` is replaced for the duration,
so "captured" is a fact about the committed cache rather than about a live call
that happened to succeed.

**Two divergences, two different responses** (§7). Failure counts that differ
between arms mean the arms were not measured under the same conditions and the
run is **repeated**. Record counts that differ mean the arms explored different
argument space, which is a fact about the candidates rather than a fault — it is
**reported and not repaired**. :func:`compare_arms` says which of the two
happened, and never resolves either.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from harness.assertions import assert_model_replays
from harness.predict import PredictFn
from harness.run_case import (
    EVAL_CACHE_DIR,
    PINNED_DB,
    _as_instant,
    make_case_context,
)

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

CASES_DIR = REPO_ROOT / "eval" / "cases"
"""Where T114 emits ``{train,test_seen,test_unseen}.json`` — MLflow's ``train_data``."""

TRAIN = "train"

PREDICT_FN = "predict_fn"
"""The residual source for a rollout that raised out of ``predict_fn`` itself.

Distinct from the ``upstream`` / ``text_to_sql_agent`` / ``rollout`` sources
:class:`~harness.run_case.Exclusion` already carries: those are failures a
completed rollout *reported*, while this is one that never returned outputs at
all. ``optimize_prompts`` swallows it into a string (``findings.md``), so
without this it would be counted nowhere.
"""


class BlockedLiveCall(AssertionError):
    """The pre-filter's replay tried to open a socket, which decides nothing honestly."""


@dataclass(frozen=True)
class CaptureAudit:
    """Which training records survived the pre-filter, and why the others did not.

    ``dropped`` is a mapping rather than a count because the reason is the point:
    a case that stops replaying is either a capture gap to fill or a pin that
    moved underneath the suite, and the two are told apart by reading it.
    """

    kept: tuple[dict[str, Any], ...]
    dropped: Mapping[str, str]

    @property
    def cases(self) -> int:
        return len(self.kept) + len(self.dropped)

    def summary(self) -> str:
        return (
            f"{len(self.kept)} of {self.cases} training cases are fully captured; "
            f"{len(self.dropped)} dropped"
            + (f": {', '.join(sorted(self.dropped))}" if self.dropped else "")
        )


class ResidualLedger:
    """Every declared residual of one arm, recorded across the pass's worker threads.

    Guarded for the same reason :class:`~harness.candidates._ReadLog` is: records
    are evaluated in a ``ThreadPoolExecutor`` (``findings.md``), so declarations
    arrive from several threads at once.

    It observes and never intervenes. :func:`guarded` re-raises whatever it saw,
    so the scorers still score the record 0 as a declared residual and the
    optimizer still gets its float — the ledger only makes sure somebody counted.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._declared: list[tuple[str, str, str]] = []

    def declare(self, case_id: str, source: str, details: str) -> None:
        """Record that *case_id* failed on *source*, with *details* as the evidence."""
        with self._lock:
            self._declared.append((case_id, source, details))
        logger.warning(
            "Declared residual", case_id=case_id, source=source, details=details
        )

    @property
    def declared(self) -> tuple[tuple[str, str, str], ...]:
        with self._lock:
            return tuple(self._declared)

    @property
    def cases(self) -> frozenset[str]:
        """The distinct cases that failed — the count §7 compares between arms.

        Cases rather than failures: one rollout can report two exclusions, and a
        candidate that fumbled twice on one case has not compromised two.
        """
        return frozenset(case_id for case_id, _, _ in self.declared)

    @property
    def by_source(self) -> dict[str, int]:
        """Failures broken out by source, which is the diagnosis half of the count."""
        return dict(sorted(Counter(source for _, source, _ in self.declared).items()))


@dataclass(frozen=True)
class ResidualReport:
    """One arm's residuals beside the entries its rollouts recorded (§7).

    The two numbers are published together because they answer different
    questions and are confused otherwise: ``residual_cases`` is what the search
    could not measure, ``recorded_entries`` is what it discovered — a candidate
    reaching for a window the oracle never asked for is a fact about the
    candidate, not a fault.
    """

    arm: str
    records: int
    residual_cases: int
    residuals_by_source: Mapping[str, int]
    recorded_entries: int

    def summary(self) -> str:
        return (
            f"{self.arm}: {self.residual_cases}/{self.records} residual case(s)"
            + (f" {self.residuals_by_source}" if self.residuals_by_source else "")
            + f", {self.recorded_entries} new cache entr"
            + ("y" if self.recorded_entries == 1 else "ies")
        )


def load_split(split: str = TRAIN, *, cases_dir: Path = CASES_DIR) -> list[dict[str, Any]]:
    """One committed split as MLflow ``train_data`` — the file, unmodified.

    Each element already projects onto ``{"inputs": …, "expectations": …}``
    (§6.1), which is why the search and the measurement run read the same rows.
    """
    path = cases_dir / f"{split}.json"
    records: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return records


def training_records(
    split: str = TRAIN,
    *,
    cases_dir: Path = CASES_DIR,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """*split* pre-filtered to the cases that replay — the search's ``train_data``.

    Args:
        split: Which committed split to load. The search reads ``train``; the
            two test splits are the measurement run's and never come through
            here.
        cases_dir: Where the emitted case files live.
        db_path: The pinned database.
        cache_dir: The committed response cache the replay must be satisfiable
            from.
        limit: Keep at most this many *captured* records, in file order. For a
            short search over a handful of cases; ``None`` is the whole split.

    Returns:
        The records that are fully captured, in file order.

    The audit is logged rather than returned, because a caller wanting the drop
    list should be asking :func:`fully_captured` for it.
    """
    audit = fully_captured(
        load_split(split, cases_dir=cases_dir), db_path=db_path, cache_dir=cache_dir
    )
    logger.info("Pre-filtered the training records", split=split, audit=audit.summary())
    kept = list(audit.kept)
    return kept if limit is None else kept[:limit]


def fully_captured(
    records: Iterable[Mapping[str, Any]],
    *,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
) -> CaptureAudit:
    """Replay each case's oracle from the committed cache and keep the ones that hold.

    A case that replays is one whose committed answer is reproducible without a
    network, which is what makes a residual failure during the search a fact
    about the run rather than a gap the suite carried in.

    An oracle that **refuses** — :class:`~eval.oracles.base.OracleInputError` —
    counts as captured. Refusal is a materialization outcome, not a failure: the
    abstention templates have nothing to fetch and nothing to record, and their
    gold status is the refusal itself.
    """
    return asyncio.run(
        _fully_captured(list(records), db_path=db_path, cache_dir=cache_dir)
    )


def guarded(predict_fn: PredictFn, ledger: ResidualLedger) -> PredictFn:
    """*predict_fn* with every failure it produces declared into *ledger*.

    Two shapes of failure and they arrive differently. A rollout that completed
    and reported an exclusion carries it in its own outputs, so it is read off
    them. A rollout that raised never returns outputs at all — ``_run_single``
    catches the exception and hands the scorers a string (``findings.md``) — so
    it is caught here and **re-raised**: the ledger counts it, and MLflow's own
    handling still turns it into the record the scorers score 0.
    """

    def counted(inputs: Mapping[str, Any]) -> dict[str, Any]:
        case_id = str(inputs.get("case_id", ""))
        try:
            produced = predict_fn(inputs)
        except Exception as exc:
            ledger.declare(case_id, PREDICT_FN, f"{type(exc).__name__}: {exc}")
            raise
        for exclusion in produced.get("exclusions") or ():
            ledger.declare(
                case_id,
                str(exclusion.get("source", "unknown")),
                f"{exclusion.get('tool') or 'the rollout'}: {exclusion.get('details', '')}",
            )
        return produced

    return counted


def cache_entries(cache_dir: Path | str = EVAL_CACHE_DIR) -> frozenset[str]:
    """The committed cache's entry names — snapshot it before an arm and after."""
    return frozenset(path.name for path in Path(cache_dir).glob("*.json"))


def residual_report(
    arm: str,
    *,
    ledger: ResidualLedger,
    records: int,
    entries_before: frozenset[str],
    cache_dir: Path | str = EVAL_CACHE_DIR,
) -> ResidualReport:
    """One arm's report: what it could not measure, beside what it discovered."""
    report = ResidualReport(
        arm=arm,
        records=records,
        residual_cases=len(ledger.cases),
        residuals_by_source=ledger.by_source,
        recorded_entries=len(cache_entries(cache_dir) - entries_before),
    )
    logger.info("Arm residuals", **{"summary": report.summary()})
    return report


def compare_arms(reports: Sequence[ResidualReport]) -> list[str]:
    """§7's two divergences, named and never resolved.

    Diverging **failure** counts mean the arms did not run under the same
    conditions — an outage that caught one of them — so the comparison between
    them is not interpretable and the run is repeated. Diverging **record**
    counts mean the arms reached for different windows, which is what different
    candidates do; it is reported so a reader knows the cache grew under the
    measurement, and repaired by nobody.

    Returns:
        One line per finding, empty when the arms agree on both counts. Fewer
        than two arms have nothing to diverge and return empty.
    """
    if len(reports) < 2:
        return []
    verdicts: list[str] = []
    failures = {report.arm: report.residual_cases for report in reports}
    if len(set(failures.values())) > 1:
        verdicts.append(
            f"REPEAT THE RUN — residual failures diverge between arms: {failures}. "
            "The arms were not measured under the same conditions, so the "
            "comparison between them is not interpretable (§7)."
        )
    recorded = {report.arm: report.recorded_entries for report in reports}
    if len(set(recorded.values())) > 1:
        verdicts.append(
            f"REPORTED, NOT REPAIRED — newly recorded cache entries diverge between "
            f"arms: {recorded}. The arms explored different argument space, which is "
            "a fact about the candidates and not a fault (§7)."
        )
    for verdict in verdicts:
        logger.warning("Arm comparison", verdict=verdict)
    return verdicts


async def _fully_captured(
    records: list[Mapping[str, Any]],
    *,
    db_path: Path | str,
    cache_dir: Path | str,
) -> CaptureAudit:
    """:func:`fully_captured`'s pass, on one event loop and with sockets blocked.

    One loop for the whole pass because that is T115's hazard — ``gr2l_client``
    binds its ``httpx.AsyncClient`` to the loop that created it — and because
    building one context per distinct ``as_of`` is what makes the pass cheap: a
    context opens a DuckDB connection and builds the as-of views.
    """
    from eval.oracles import ORACLES
    from eval.oracles.base import OracleInputError

    kept: list[dict[str, Any]] = []
    dropped: dict[str, str] = {}
    contexts: dict[str, Any] = {}

    original = httpx.AsyncClient.send

    async def blocked(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> Any:
        raise BlockedLiveCall(
            f"the capture pre-filter attempted a live call: {request.method} {request.url}"
        )

    httpx.AsyncClient.send = blocked  # type: ignore[method-assign]
    try:
        for record in records:
            inputs = record["inputs"]
            case_id = str(inputs["case_id"])
            as_of = str(inputs["as_of"])
            if as_of not in contexts:
                ctx = make_case_context(
                    _as_instant(as_of),
                    db_path=db_path,
                    cache_dir=cache_dir,
                    allow_live=False,
                )
                assert_model_replays(ctx)
                contexts[as_of] = ctx
            try:
                await ORACLES[str(inputs["template_id"])](inputs, contexts[as_of])
            except OracleInputError:
                # A refusal is an outcome, not a gap: the abstention templates
                # fetch nothing and their gold status *is* the refusal.
                kept.append(dict(record))
            except Exception as exc:  # noqa: BLE001 - the reason is the finding
                dropped[case_id] = f"{type(exc).__name__}: {exc}"
            else:
                kept.append(dict(record))
    finally:
        httpx.AsyncClient.send = original  # type: ignore[method-assign]

    return CaptureAudit(kept=tuple(kept), dropped=dropped)


__all__ = [
    "CaptureAudit",
    "ResidualLedger",
    "ResidualReport",
    "cache_entries",
    "compare_arms",
    "fully_captured",
    "guarded",
    "load_split",
    "residual_report",
    "training_records",
]
