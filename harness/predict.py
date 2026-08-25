"""``predict_fn`` — one record in, one rollout's outputs out (§6).

This is the function MLflow calls per training record, and **one call of it is
one rollout**. GEPA owns the loop, MLflow owns the adapter, and what this repo
supplies here is the step between a candidate and a case: read the candidate's
text back through the prompt registry, split the root instruction from the tool
docstrings, and hand both to :func:`~harness.run_case.run_case`.

**It assembles arguments; it does not build the agent.** §6's sketch inlines
``build_root_agent(instruction=…, tools=build_toolset(ctx, docstrings=…))``,
which is the same construction ``run_case`` performs — and ``run_case`` is the
one rollout there is (T100). Delegating rather than repeating is what makes the
search and the measurement run identical on the path that has to be identical:
the same context, the same toolset, the same ``rollout_run_config()`` bound
(T032). A search that bounded its candidates differently is the one difference
that decides whether a looping candidate finishes at all, and it would not show
up as an error anywhere.

**Per record, nothing ambient.** ``inputs["as_of"]`` builds that record's
:class:`~..context.ScenarioContext`, that context builds that record's tools,
and no object survives the call. Records are evaluated in a
``ThreadPoolExecutor`` (``findings.md`` § Optimizer internals), so this is the
precondition for the search being correct at all rather than a preference.

**The candidate is re-read on every record, deliberately.** Candidate text
arrives as a process-global patch of ``PromptVersion.template`` that MLflow
installs for the duration of one batch and reverts afterwards, so the *same*
registry read returns different text on different iterations. Reading once and
caching the result would pin the whole search to whichever candidate happened to
be installed first — a bug that produces plausible numbers rather than an error.
The seed *versions* are resolved once, at wiring time: those identify which
prompts are being optimized and do not change within a run.

**One candidate per process at a time**, for the same reason. The patch is
process-global, so :func:`make_predict_fn` produces a callable bound to one
evaluation, and :func:`~harness.candidates.evaluation_pass` — which the caller
opens around the whole search — refuses to nest.

The returned dict is **JSON-serializable throughout**. MLflow logs it into the
per-iteration eval-results table and GEPA puts it in the reflective dataset the
reflection model reads (``findings.md``), so it has to survive
``mlflow.log_table`` and be legible as text. :func:`case_result` reconstructs the
:class:`~harness.run_case.CaseResult` from it without loss, which is what the
scorers (T122) take.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import structlog
from google.adk.models.base_llm import BaseLlm

from harness.candidates import ROOT_INSTRUCTION, read_candidates, seed_versions
from harness.contract import AnswerContract
from harness.run_case import (
    EVAL_CACHE_DIR,
    PINNED_DB,
    CaseResult,
    Exclusion,
    ToolCall,
    run_case,
)

logger = structlog.get_logger(__name__)

PredictFn = Callable[[Mapping[str, Any]], dict[str, Any]]
"""What ``optimize_prompts`` takes: one ``inputs`` mapping in, one dict out."""


def make_predict_fn(
    *,
    versions: Mapping[str, int] | None = None,
    model_factory: Callable[[], BaseLlm] | None = None,
    db_path: Path | str = PINNED_DB,
    cache_dir: Path | str = EVAL_CACHE_DIR,
    allow_live: bool = False,
) -> PredictFn:
    """Build the ``predict_fn`` for one evaluation.

    Args:
        versions: The registered prompt version per component. Defaults to the
            pinned seed versions (:func:`~harness.candidates.seed_versions`),
            resolved **here** rather than per record, so an unpinned surface
            fails at wiring time instead of on the first rollout.
        model_factory: Builds the task model, called **once per record**. A
            factory rather than a model because two rollouts running side by side
            must not share a client — the same reason ``run_case`` builds one per
            rollout when this is ``None``, which is the default and what a real
            search uses.
        db_path: The pinned database.
        cache_dir: The committed response cache.
        allow_live: ``False`` (default) replays. A search runs on replay; the
            capture pass is T116's and does not come through here.

    Returns:
        A callable taking one record's ``inputs`` and returning that rollout's
        outputs.

    Raises:
        MissingCandidatePinError: the seed versions are not pinned.
    """
    pinned = dict(seed_versions() if versions is None else versions)

    def predict_fn(inputs: Mapping[str, Any]) -> dict[str, Any]:
        """One rollout on the candidate currently installed in the registry."""
        # Read per record, never cached: the patch carrying candidate text is
        # installed and reverted around each batch, so a cached read would run
        # the whole search on one candidate and report it as several.
        texts = dict(read_candidates(pinned))
        instruction = texts.pop(ROOT_INSTRUCTION)
        # What is left is keyed by tool name, which is what `build_toolset`
        # takes; an unknown key raises there rather than being dropped (T029).
        # `run_case` passes it as `docstrings=` and never alongside `tools=`,
        # which is the pairing T030 refuses.
        result = run_case(
            inputs,
            instruction=instruction,
            docstrings=texts,
            model=None if model_factory is None else model_factory(),
            db_path=db_path,
            cache_dir=cache_dir,
            allow_live=allow_live,
        )
        return outputs(result)

    return predict_fn


def predict_fn(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """:func:`make_predict_fn` with every default — one rollout of one record.

    The convenience form, for a caller with nothing to override. A search builds
    its own through :func:`make_predict_fn` and keeps it for the run, because
    resolving the pinned versions once per record would re-read the pin file
    inside every worker thread for an answer that cannot change.
    """
    return make_predict_fn()(inputs)


def outputs(result: CaseResult) -> dict[str, Any]:
    """One rollout's :class:`~harness.run_case.CaseResult` as MLflow's ``outputs``.

    Flat and JSON-only, in that order of importance. The contract's four fields
    are lifted to the top level because they are what a reader — the reflection
    model included — looks for first; the trajectory, the exclusions and the
    diagnostics follow as the evidence behind them.

    Nothing is scored here and nothing is compared: this is a fact about the run,
    exactly as :class:`CaseResult` is (T100), and the scorers are T122's.
    """
    contract = result.contract
    return {
        "case_id": result.case_id,
        "template_id": result.template_id,
        "status": result.status,
        "answer": result.answer,
        "unit": result.unit,
        "explanation": None if contract is None else contract.explanation,
        "final_text": result.final_text,
        "trajectory": [
            {"name": call.name, "args": dict(call.args)} for call in result.trajectory
        ],
        "harness_error": result.harness_error,
        "exclusions": [
            {"tool": item.tool, "source": item.source, "details": item.details}
            for item in result.exclusions
        ],
        "diagnostics": dict(result.diagnostics),
    }


def case_result(record: Mapping[str, Any]) -> CaseResult:
    """Rebuild the :class:`CaseResult` :func:`outputs` was made from.

    The inverse, and lossless — every field of a ``CaseResult`` is JSON-shaped,
    which is why the outputs can be flat JSON without the scorers losing
    anything. T122's scorers take a ``CaseResult``, and this is how one record's
    outputs become one again.
    """
    status = record.get("status")
    contract = (
        None
        if status is None
        else AnswerContract(
            status=str(status),
            answer=record.get("answer"),
            unit=record.get("unit"),
            explanation=record.get("explanation") or "",
        )
    )
    return CaseResult(
        case_id=str(record.get("case_id", "")),
        template_id=str(record.get("template_id", "")),
        contract=contract,
        final_text=str(record.get("final_text", "")),
        trajectory=tuple(
            ToolCall(name=call["name"], args=dict(call.get("args") or {}))
            for call in record.get("trajectory") or ()
        ),
        harness_error=bool(record.get("harness_error")),
        exclusions=tuple(
            Exclusion(
                tool=item["tool"], source=item["source"], details=item.get("details", "")
            )
            for item in record.get("exclusions") or ()
        ),
        diagnostics=dict(record.get("diagnostics") or {}),
    )
