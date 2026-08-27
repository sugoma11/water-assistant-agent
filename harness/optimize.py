"""The search: ``optimize_prompts`` with GEPA, and the four things it is handed (§6).

**No adapter is written here, and that is the design.** MLflow ships
``MlflowGEPAAdapter``, which implements both of ``GEPAAdapter``'s methods and
logs per iteration against the sink already in use (``findings.md`` § Optimizer
internals). So: **GEPA owns the loop, MLflow owns the adapter, one rollout is one
call of ``predict_fn``**. What this repo supplies is the candidate surface
(:mod:`harness.candidates`), the prediction function (:mod:`harness.predict`),
the scorers (:mod:`harness.scorers`) and the aggregation
(:func:`~harness.scoring.aggregate_scores`) — and this module is where those four
meet the entry point.

**Selection runs on the aggregated scalar.** GEPA's Pareto front is over
*instances* unless ``frontier_type`` says otherwise and MLflow never sets it, so
the four metrics reach reflection unblended and selection blended
(``decisions.md`` § Candidate selection and the scorers' aggregation). The
``aggregation`` argument is therefore not a formality, and :func:`preflight`
**checks that it is wired rather than trusting that it is**: omitting it makes
the objective the unweighted mean of the numeric values and then raises on the
first skipping case, which is a failure a search would hit an hour in.

**The proposer is told which kind of component it is rewriting.** Six of the
seven are tool descriptions and GEPA's default metaprompt calls all seven
"instructions for an assistant", so the two templates of :mod:`harness.metaprompt`
are passed through ``gepa_kwargs`` as a per-component dict (T133). That is the
*only* thing ``gepa_kwargs`` carries, and the narrowness is load-bearing: a
``frontier_type`` smuggled in the same way would move selection off the
aggregated scalar, which is what makes the aggregation callable matter at all.

**The search path is not the measurement path, in exactly three places.**

* ``allow_live=True``. A cache miss on a window the *candidate* chose **records**
  rather than fails (§5) — §7's second protection, and the reason the pre-filter
  only has to answer for the oracle's windows (:mod:`harness.train_data`).
* **The LLM response cache is on**, installed here and nowhere else. It is off by
  default because the measurement run's three repeats per condition are the
  replication and a cache would collapse them into one sample and two copies of
  it (``decisions.md`` § Replication and the LLM cache). Inside a search the
  opposite holds: the same candidate is re-evaluated across iterations, and the
  key covers the tool declarations, so two candidates differing only in a
  docstring cannot share an entry.
* **Residuals are declared and counted**, since there is no exclusion channel
  inside ``optimize_prompts`` at all.

**The versions and the dependencies are pinned before anything runs.** The seed
versions resolve at wiring time, so an unpinned surface fails before the first
rollout; and ``mlflow`` and ``gepa`` are in ``dependency_versions`` because
candidate injection rests on a process-global patch of ``PromptVersion.template``
whose failure is a log warning (``findings.md``) — a version bump that moved it
would produce a search that optimized nothing and said so nowhere.

**A search reports how it differs from the pre-registration; it does not refuse
to differ.** ``eval/preregistration.json`` fixes the budget and the
hyperparameters before any test run (§7), and every run logs the registration's
digest beside the list of its own deviations — empty for the registered search,
and naming the budget for ``just search-smoke``. Refusing here would forbid the
smoke run, which is a legitimate thing to do and an illegitimate thing to
publish; the measurement driver is where a deviating run is refused, because a
*test number* under an unregistered budget is the failure the protocol exists to
prevent (T129).

**The search owns its MLflow run, and that is what makes the ledger possible.**
GEPA starts a run when none is active and ends the one it started
(``gepa/logging/experiment_tracker.py``), so a ledger written after
``optimize_prompts`` returned would land in a *second*, empty run — beside the
iteration tables it is supposed to sit with. Opening the run here means GEPA
reuses it, and the per-rollout ledger of T127 shares it with the per-iteration
artifacts of §6.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow.genai
import structlog
from mlflow.genai.optimize.optimizers import GepaPromptOptimizer
from mlflow.genai.optimize.types import PromptOptimizationResult

from harness.candidates import (
    PROMPT_NAMES,
    candidate_uris,
    evaluation_pass,
    read_candidates,
    seed_versions,
)
from harness.leakage import Leak, find_leaks
from harness.leakage import report as leakage_report
from harness.ledger import RunLedger, experiment_run, ledgered
from harness.metaprompt import reflection_prompt_templates, templates_digest
from harness.predict import as_keyword_fn
from harness.preregistration import SEARCH
from harness.preregistration import run_params as prereg_params
from harness.reflection import pinned_reflection_lm
from harness.run_case import EVAL_CACHE_DIR, PINNED_DB
from harness.scorers import SCORERS
from harness.scoring import ABSTENTION, SELECTION_WEIGHTS, SKIPPED, TRAJECTORY
from harness.scoring import aggregate_scores as AGGREGATION
from harness.train_data import (
    CASES_DIR,
    ResidualLedger,
    ResidualReport,
    cache_entries,
    guarded,
    residual_report,
    training_records,
)
from water_assistant_agent.assistant.llm import configure_llm_cache
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

logger = structlog.get_logger(__name__)

BASELINE_ARM = "baseline"
OPTIMIZED_ARM = "optimized"

DEFAULT_MAX_METRIC_CALLS = 500
"""GEPA's budget, in rollouts. Pre-registered per run (T129), never tuned on a result.

**It has to clear the trainset's size before it buys anything.** MLflow passes
``trainset`` and no ``valset``, so GEPA evaluates the seed candidate over the
whole split before proposing — ``len(trainset)`` metric calls — and each accepted
candidate costs another full evaluation, with reflection minibatches of 3 in
between (``gepa/api.py:328``). A budget equal to the split's size is therefore
spent entirely on measuring the seed and the search returns it unchanged, which
is a no-op that reports itself as a completed search. At 100 train cases, 500
leaves 400 after the seed's evaluation.
"""


class AggregationNotWiredError(RuntimeError):
    """The objective is not this repo's aggregation callable.

    Raised before a search spends anything. Omitting ``aggregation=`` does not
    fail at wiring time — MLflow builds the metric happily and the objective
    silently becomes the unweighted mean of the numeric scorer values, right up
    until the first case where a metric skips (``findings.md``). That is an hour
    into a search, and the run before it was scored on a weighting nobody chose.
    """


@dataclass(frozen=True)
class SearchResult:
    """One search, and what it is safe to conclude from it.

    ``residuals`` is beside ``result`` rather than inside it because §7 reads
    them together: a score improved over an arm that lost four cases to an
    outage is not an improvement anybody measured. ``ledger`` is beside both for
    the same kind of reason: the score says how well the run did and the ledger
    says what it did it against (T127).
    """

    result: PromptOptimizationResult
    residuals: ResidualReport
    records: int
    reflection_model: str
    ledger: RunLedger
    leaks: tuple[Leak, ...] = ()

    def summary(self) -> str:
        return (
            f"{self.records} record(s), "
            f"{self.result.initial_eval_score} → {self.result.final_eval_score}; "
            f"{self.residuals.summary()}; {self.ledger.summary()}; "
            f"{leakage_report(self.leaks)}"
        )


def preflight(aggregation: Any = AGGREGATION) -> None:
    """Refuse to start unless the objective really is this repo's callable.

    Two checks, and they fail for two different reasons.

    **Identity** — the object handed to ``optimize_prompts`` is
    :func:`~harness.scoring.aggregate_scores` itself, so the weights the run is
    selecting on are the pre-registered ones (T129) and not some caller's.

    **Behaviour** — the library's own ``create_metric_from_scorers``, given this
    repo's scorers and this callable, turns a record where two metrics skip into
    a number rather than an exception. That is the half no identity check can
    make: it holds against the *installed* mlflow, so a version bump that changed
    how a non-numeric scorer value is treated is caught here rather than an hour
    into a search.

    Raises:
        AggregationNotWiredError: either check failed.
    """
    from mlflow.genai.optimize.util import create_metric_from_scorers

    if aggregation is not AGGREGATION:
        raise AggregationNotWiredError(
            "The objective must be harness.scoring.aggregate_scores. Omitting it "
            "makes the objective the unweighted mean of the numeric scorer values "
            "— a weighting nobody chose — and a skipping metric then raises."
        )
    metric = create_metric_from_scorers(list(SCORERS), aggregation)
    score, rationales, individual = metric(
        inputs=_PREFLIGHT_INPUTS,
        outputs=_PREFLIGHT_OUTPUTS,
        expectations=_PREFLIGHT_EXPECTATIONS,
        trace=None,
    )
    if not isinstance(score, (int, float)) or set(individual) != {
        TRAJECTORY,
        ABSTENTION,
    }:
        raise AggregationNotWiredError(
            f"The installed mlflow scored a two-skip record as {score!r} with "
            f"per-scorer values {sorted(individual)}; §7 expects a number over "
            f"{sorted((TRAJECTORY, ABSTENTION))} alone. The skip semantics no "
            "longer hold on this version."
        )
    logger.info(
        "Aggregation wired",
        objective=f"{AGGREGATION.__module__}.{AGGREGATION.__qualname__}",
        two_skip_record=score,
        rationales=sorted(rationales),
    )


@contextmanager
def search_llm_cache(settings: AssistantSettings | None = None) -> Iterator[bool]:
    """Turn the LLM response cache on for a search, and put it back afterwards.

    **The cache has two halves and both have to agree.**
    :func:`~water_assistant_agent.assistant.llm.configure_llm_cache` installs
    ``litellm.cache``; ``AssistantSettings.litellm_extra`` sends ``caching`` on
    every request. litellm consults the installed cache only when ``caching`` is
    unset or ``True`` (``findings.md``), and this repo always sends it — so
    installing the cache while the setting says ``False`` produces a cache that
    is configured, logged, and bypassed on every call.

    Off is the default because that is the measurement path's setting (T033).
    This is the one place it is turned on, and it is turned back off on the way
    out so a process that ran a search does not carry the cache into whatever it
    does next.
    """
    settings = settings or get_settings()
    previous = settings.llm_cache_enabled
    settings.llm_cache_enabled = True
    enabled = configure_llm_cache(True, settings)
    try:
        yield enabled
    finally:
        settings.llm_cache_enabled = previous
        configure_llm_cache(previous, settings)


def search_gepa_kwargs() -> dict[str, Any]:
    """What GEPA is handed past MLflow's own arguments, and the whole of it.

    One key. ``GepaPromptOptimizer`` builds its call as
    ``self.gepa_kwargs | {…}``, so anything MLflow sets itself is overridden here
    and anything it does not is passed straight through — which is how the
    per-component metaprompts reach the proposer (T133) and equally how a
    ``frontier_type`` would reach the candidate selector. The dict is built in
    one place so that what a search adds is one readable list rather than a
    literal at the call site.
    """
    return {"reflection_prompt_template": reflection_prompt_templates()}


def gepa_kwargs_summary(gepa_kwargs: Mapping[str, Any]) -> dict[str, str]:
    """*gepa_kwargs* in the form a registration can carry: the templates by digest.

    The templates are tens of kilobytes and a registered hyperparameter has to be
    readable, comparable and short enough to log as a run parameter — so what is
    registered and reported is :func:`~harness.metaprompt.templates_digest`,
    which moves when any template or any dispatch key moves. Every other key is
    summarised as itself: an argument nobody registered is exactly what the
    deviation list exists to name.
    """
    summary: dict[str, str] = {}
    for key, value in gepa_kwargs.items():
        if key == "reflection_prompt_template":
            summary[key] = f"sha256:{templates_digest(value)}"
        else:
            summary[key] = repr(value)
    return summary


def run_search(
    *,
    split: str = "train",
    limit: int | None = None,
    max_metric_calls: int = DEFAULT_MAX_METRIC_CALLS,
    arm: str = OPTIMIZED_ARM,
    versions: Mapping[str, int] | None = None,
    display_progress_bar: bool = False,
    cases_dir: Path = CASES_DIR,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    settings: AssistantSettings | None = None,
) -> SearchResult:
    """Run one search and report what it produced beside what it could not measure.

    Args:
        split: Which committed split to search over. ``train`` always, in a real
            run: the two test splits are the measurement run's and a search that
            touched them would make its own numbers uninterpretable.
        limit: Search over at most this many *captured* records. A short search
            for a smoke run; ``None`` is the whole split.
        max_metric_calls: GEPA's rollout budget, pre-registered per run (T129).
        arm: The name the residual report is filed under.
        versions: The registered prompt version per component. Defaults to the
            pinned seed, resolved here so an unpinned surface fails before the
            first rollout rather than inside a worker thread.
        display_progress_bar: GEPA's own progress bar.
        cases_dir: Where the emitted case files live. A binding rather than a
            constant for the same reason ``db_path`` and ``cache_dir`` are: the
            pre-filter is part of the path under test, so a test narrows the
            *file* rather than being allowed to hand records in past it.
        db_path: The pinned database.
        cache_dir: The committed response cache, which this pass may **add** to.
        settings: Where the reflection model and the cache switch come from.

    Returns:
        The optimizer's result, the arm's residual report, and the reflection
        model the candidates were proposed by.

    Raises:
        AggregationNotWiredError: the objective is not this repo's callable.
        MetapromptError: a per-component metaprompt is one gepa would refuse.
        MissingCandidatePinError: the seed versions are not pinned.
        UnreadCandidateError: the pass ended with a candidate component unread —
            a component nobody read is frozen while still appearing optimizable.
    """
    settings = settings or get_settings()
    pinned = dict(seed_versions() if versions is None else versions)
    records = training_records(
        split, cases_dir=cases_dir, db_path=db_path, cache_dir=cache_dir, limit=limit
    )
    if not records:
        raise ValueError(
            f"No fully captured records in {split!r}; there is nothing to search over."
        )
    preflight(AGGREGATION)
    # Built here rather than at the call site so the templates are validated
    # against the installed gepa before the seed candidate's evaluation is paid
    # for, and so the run's registered summary is taken from the same object.
    gepa_kwargs = search_gepa_kwargs()

    ledger = ResidualLedger()
    run_ledger = RunLedger(arm=arm, split=split, settings=settings)
    before = cache_entries(cache_dir)
    # `ledgered` innermost, so it sees a raising rollout before `guarded` does and
    # the row exists for the failure too; `as_keyword_fn` outermost, because that
    # is the shape the entry point inspects — `convert_predict_fn` validates the
    # signature against the record's `inputs` keys and then calls
    # `predict_fn(**request)` (`findings.md`).
    predict_fn = as_keyword_fn(
        guarded(
            ledgered(
                run_ledger,
                versions=pinned,
                db_path=db_path,
                cache_dir=cache_dir,
                # Record mode: §7's second search-path protection. A miss on a
                # window the candidate chose is a discovery, not an exclusion.
                allow_live=True,
                expectations={
                    record["inputs"]["case_id"]: record.get("expectations", {})
                    for record in records
                },
            ),
            ledger,
        )
    )

    with (
        experiment_run(f"search-{arm}-{split}"),
        search_llm_cache(settings),
        pinned_reflection_lm(settings) as reflection_uri,
        evaluation_pass(),
    ):
        # Reported, never refused. A short search at a smoke budget is a useful
        # thing to run and a useless thing to report as the registered one, and
        # the difference belongs in the run's own record rather than in whoever
        # reads it afterwards (T129).
        mlflow.log_params(
            prereg_params(
                SEARCH,
                {
                    "split": split,
                    "limit": limit,
                    "max_metric_calls": max_metric_calls,
                    "reflection_model": settings.reflection_model,
                    "gepa_kwargs": gepa_kwargs_summary(gepa_kwargs),
                    "llm_cache": "on",
                    "response_cache_mode": "record",
                    "selection_weights": dict(SELECTION_WEIGHTS),
                },
            )
        )
        logger.info(
            "Search starting",
            arm=arm,
            records=len(records),
            max_metric_calls=max_metric_calls,
            reflection_model=reflection_uri,
            **gepa_kwargs_summary(gepa_kwargs),
        )
        result = mlflow.genai.optimize_prompts(
            predict_fn=predict_fn,
            train_data=records,
            prompt_uris=list(candidate_uris(pinned).values()),
            optimizer=GepaPromptOptimizer(
                reflection_model=reflection_uri,
                max_metric_calls=max_metric_calls,
                display_progress_bar=display_progress_bar,
                gepa_kwargs=gepa_kwargs,
            ),
            scorers=list(SCORERS),
            # Mandatory, and checked by preflight() rather than assumed (§6).
            aggregation=AGGREGATION,
        )
        # Inside the run GEPA has been sharing, so the per-rollout ledger sits
        # with the per-iteration tables rather than in a run of its own (T127).
        run_ledger.log()

    # Reported, never refused — the asymmetry §6 runs on for the
    # pre-registration, for the same reason: refusing here would discard a
    # search that has already been paid for, and the candidate is still the
    # thing the search selected. The measurement driver is where a leak becomes
    # a number in the thesis, and that is where it is refused (T140).
    leaks = find_leaks(
        {name: prompt.template for name, prompt in _selected_texts(result).items()},
        read_candidates(pinned),
    )
    logger.info("Answer leakage", arm=arm, summary=leakage_report(leaks))

    search = SearchResult(
        result=result,
        residuals=residual_report(
            arm,
            ledger=ledger,
            records=len(records),
            entries_before=before,
            cache_dir=cache_dir,
        ),
        records=len(records),
        reflection_model=reflection_uri,
        ledger=run_ledger,
        leaks=tuple(leaks),
    )
    logger.info("Search complete", arm=arm, summary=search.summary())
    return search


def _selected_texts(result: PromptOptimizationResult) -> dict[str, Any]:
    """The winner's text per component key, from the prompts the search registered.

    ``optimized_prompts`` carries registry *names* (``agent_tool_…``) while every
    other surface here is keyed by component, so the mapping is undone once and
    here rather than at each reader.
    """
    by_name = {prompt.name: prompt for prompt in result.optimized_prompts}
    return {
        component: by_name[name]
        for component, name in PROMPT_NAMES.items()
        if name in by_name
    }


_PREFLIGHT_INPUTS: Mapping[str, Any] = {
    "case_id": "PREFLIGHT",
    "template_id": "PREFLIGHT",
    "as_of": "2026-03-13T23:00:00+01:00",
    "question": "preflight",
    "params": {},
}

_PREFLIGHT_OUTPUTS: Mapping[str, Any] = {
    "case_id": "PREFLIGHT",
    "template_id": "PREFLIGHT",
    "status": "answered",
    "answer": None,
    "unit": None,
    "explanation": "preflight",
    "final_text": "{}",
    "trajectory": [],
    "harness_error": False,
    "exclusions": [],
    "diagnostics": {},
}

_PREFLIGHT_EXPECTATIONS: Mapping[str, Any] = {
    "status": "answered",
    "answer": None,
    "unit": None,
    "answer_metric": SKIPPED,
    "expected_tool_calls": [],
    "must_not_tools": [],
    "gold_cards": [],
    "argument_checks": [],
    "pins": {},
}
"""A record on which **both** of §7's skips fire — the shape the check is about.

A plot deliverable in miniature: the oracle answer is null, so the answer metric
skips, and there is no gold card, so card recall does. Nothing is run and no
model is called; this is a statement about the library's arithmetic, not about
the agent.
"""
