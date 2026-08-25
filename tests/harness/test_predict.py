"""Two records, two clocks, one thread pool — a rollout through ``predict_fn`` (T121).

The packet's exit criterion, and it is run rather than argued: two records with
different ``as_of`` are evaluated **concurrently through ``predict_fn``**, under
MLflow's own ``ThreadPoolExecutor`` and with MLflow's own candidate patch
installed, and each comes back having seen its own bound.

Nothing here reaches around the entry point. ``_build_eval_fn`` is the private
function ``optimize_prompts`` builds its evaluation from
(``findings.md`` § Optimizer internals), and calling it directly is what makes
this a test of the integration rather than of a hand-rolled stand-in: the real
patch, the real worker threads, the real registry read. The model is the only
thing faked, as everywhere else in this suite.

**Concurrency is enforced, not hoped for.** Each record's model waits on a
shared :class:`threading.Barrier` before its first turn, so a pool that ran the
two records one after another would time out instead of passing quietly.

**"Its own bound" is a fact about the database, not about a rendered date.** Both
records plot the same window from the same table; the earlier ``as_of`` cuts it
and the later one does not, so the two rollouts come back with different point
counts off the pinned file. A shared context would make them identical.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from mlflow.genai.optimize.optimize import _build_eval_fn

from harness.candidates import (
    PROMPT_NAMES,
    ROOT_INSTRUCTION,
    baseline_texts,
    evaluation_pass,
    register_candidates,
)
from harness.predict import case_result, make_predict_fn, outputs
from harness.run_case import CaseResult, Exclusion, ToolCall, run_case
from tests.harness.conftest import (
    Call,
    Say,
    ScriptedLlm,
    results_seen,
    scripted,
    text_seen,
)
from water_assistant_agent.assistant.agents.root_agent.agent import MAX_TOOL_STEPS
from water_assistant_agent.assistant.toolset import LOOKUP_TOOL, PLOT_TOOL, TOOL_NAMES

EARLY = "2025-06-05T08:00:00+02:00"
LATE = "2025-06-15T08:00:00+02:00"
"""Two case clocks a week and a half apart, over one window of the pinned record."""

WINDOW = {"start_date": "2025-06-01", "end_date": "2025-06-10"}
"""Covered whole at :data:`LATE`; cut in the middle at :data:`EARLY`."""

SERIES = [
    {
        "source": "measured",
        "table": "swc",
        "variable": "soil_moisture",
        "roof": "irrigated_extensive",
    }
]

CONTRACT = json.dumps(
    {"status": "answered", "answer": None, "unit": None, "explanation": "Charted."}
)


class BarrierLlm(ScriptedLlm):
    """A scripted model that will not answer until its counterpart has arrived.

    ``threading.Barrier`` blocks the calling thread, and each record's rollout
    runs its event loop on its own worker thread, so waiting here holds one
    record inside ``predict_fn`` until the other is inside it too. If the pool
    ran them in sequence the barrier would break on its timeout and the rollout
    would come back as an exclusion naming it.
    """

    barrier: Any = None

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.turn == 0 and self.barrier is not None:
            self.barrier.wait(timeout=60)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def record(case_id: str, as_of: str) -> dict[str, Any]:
    """One training record in §6.1's envelope — ``inputs`` is all a rollout gets."""
    return {
        "inputs": {
            "question": "Chart the irrigated roof's soil moisture for early June.",
            "as_of": as_of,
            "case_id": case_id,
            "template_id": "T30",
            "params": {},
        }
    }


def plotting_model(barrier: threading.Barrier | None = None) -> BarrierLlm:
    """A model that draws the chart and then answers the contract."""
    return BarrierLlm(
        script=[Call(PLOT_TOOL, {"series": SERIES, **WINDOW}), Say(CONTRACT)],
        requests=[],
        turn=0,
        barrier=barrier,
    )


def test_two_records_with_different_as_of_run_concurrently(registry: str) -> None:
    """The exit criterion, through ``predict_fn`` and MLflow's own machinery."""
    versions = register_candidates(baseline_texts())
    # A candidate, not the seed: distinct text per component, keyed by prompt
    # name, which is what the patch matches on.
    candidate = {
        PROMPT_NAMES[component]: f"CANDIDATE TEXT FOR {component}. {text}"
        for component, text in baseline_texts().items()
    }
    barrier = threading.Barrier(2)
    models: list[BarrierLlm] = []

    def model_factory() -> BarrierLlm:
        model = plotting_model(barrier)
        models.append(model)
        return model

    eval_fn = _build_eval_fn(
        make_predict_fn(versions=versions, model_factory=model_factory), None
    )
    with evaluation_pass():
        results = eval_fn(candidate, [record("EARLY-1", EARLY), record("LATE-1", LATE)])

    by_case = {}
    for result in results:
        assert isinstance(result.outputs, dict), result.outputs
        by_case[result.outputs["case_id"]] = result.outputs
    for case_id, produced in by_case.items():
        assert produced["exclusions"] == [], f"{case_id}: {produced['exclusions']}"
        assert produced["status"] == "answered"
        assert [call["name"] for call in produced["trajectory"]] == [PLOT_TOOL]

    # One model per record: two rollouts running side by side share no client,
    # no context and no tool object.
    assert len(models) == 2
    charts = {_case_of(model): _plot_result(model) for model in models}
    assert set(charts) == {"EARLY-1", "LATE-1"}

    early, late = charts["EARLY-1"], charts["LATE-1"]
    assert early["status"] == late["status"] == "success"
    # Each saw its own bound: the same window, read through two different as-of
    # cuts of the same pinned file.
    assert late["series"][0]["truncated"] is False
    assert early["series"][0]["truncated"] is True
    assert early["series"][0]["stats"]["points"] < late["series"][0]["stats"]["points"]
    assert late["series"][0]["stats"]["last"] > EARLY[:10]
    assert early["series"][0]["stats"]["last"].startswith("2025-06-05")


def test_the_candidate_reaches_both_the_instruction_and_every_tool(
    registry: str,
) -> None:
    """The registry read is the channel, and it carries all seven components.

    The patch MLflow installs is matched by prompt *name*, so this is the whole
    of §6's claim in one assertion: the root instruction arrives as the system
    instruction and each tool's candidate text arrives as its declaration's
    description — the sub-agent's outward one included, which is the component
    that is not a Python docstring.
    """
    versions = register_candidates(baseline_texts())
    candidate = {
        PROMPT_NAMES[component]: f"CANDIDATE TEXT FOR {component}. {text}"
        for component, text in baseline_texts().items()
    }
    model = scripted(Say(CONTRACT))

    eval_fn = _build_eval_fn(
        make_predict_fn(versions=versions, model_factory=lambda: model), None
    )
    with evaluation_pass():
        eval_fn(candidate, [record("SEEN-1", LATE)])

    request = model.requests[0]
    assert "CANDIDATE TEXT FOR root_instruction." in str(
        request.config.system_instruction
    )
    declared = {
        declaration.name: declaration.description
        for tool in request.config.tools
        for declaration in tool.function_declarations
    }
    assert set(declared) == set(TOOL_NAMES)
    for name in TOOL_NAMES:
        assert declared[name].startswith(f"CANDIDATE TEXT FOR {name}.")


def test_the_rollout_run_config_bounds_the_candidate(registry: str) -> None:
    """T032's cap reaches the runner through ``predict_fn``, not only through ``run_case``.

    A candidate that loops is the case this decides, and the two paths must bound
    it identically or the search and the measurement run disagree on whether it
    finishes at all. Scripted to spend more steps than §2 allows: with the cap the
    run ends as a step-cap exhaustion, and the script is left with turns unplayed.
    """
    versions = register_candidates(baseline_texts())
    shotgun = [Call(LOOKUP_TOOL, {"topic": "retention_target"})] * (MAX_TOOL_STEPS + 4)
    model = scripted(*shotgun, Say(CONTRACT))

    predict = make_predict_fn(versions=versions, model_factory=lambda: model)
    with evaluation_pass():
        produced = predict(record("CAP-1", LATE)["inputs"])

    assert produced["diagnostics"]["step_cap_exceeded"] is True
    assert produced["status"] is None
    assert produced["diagnostics"]["parse_failure"] is True
    # Bounded by the cap and not by the script running dry: the model was asked
    # for exactly the allowed number of turns and the rest of the script is
    # untouched. Without the run config ADK's own default is 500.
    assert model.turn == MAX_TOOL_STEPS + 1
    assert len(produced["trajectory"]) == MAX_TOOL_STEPS + 1
    assert produced["harness_error"] is False


def test_the_outputs_round_trip_to_a_case_result(tmp_path: Any) -> None:
    """MLflow's outputs are JSON, and a scorer gets its ``CaseResult`` back whole.

    The dict has to survive ``mlflow.log_table`` and be legible in the reflective
    dataset, and the scorers (T122) take a ``CaseResult``; both hold only because
    every field of one is JSON-shaped.
    """
    model = scripted(Call(LOOKUP_TOOL, {"topic": "retention_target"}), Say(CONTRACT))
    result = run_case(
        record("RT-1", LATE)["inputs"], model=model, cache_dir=tmp_path / "cache"
    )

    produced = outputs(result)

    json.dumps(produced)  # raises if anything in it is not serializable
    assert case_result(produced) == result


def test_a_parse_failure_and_an_exclusion_survive_the_round_trip() -> None:
    """The two outcomes a scorer must still be able to tell apart after the trip."""
    result = CaseResult(
        case_id="X-1",
        template_id="X",
        contract=None,
        final_text="The manual sets the retention target at 50 %.",
        trajectory=(ToolCall(name=LOOKUP_TOOL, args={"topic": "retention_target"}),),
        harness_error=True,
        exclusions=(
            Exclusion(tool=LOOKUP_TOOL, source="upstream", details="cache miss"),
        ),
        diagnostics={"steps": 1, "parse_failure": True},
    )

    assert case_result(outputs(result)) == result


def test_an_unpinned_surface_fails_at_wiring_time(registry: str) -> None:
    """A missing component is refused before the first rollout, not during one."""
    versions = register_candidates(baseline_texts())
    del versions[ROOT_INSTRUCTION]

    predict = make_predict_fn(versions=versions)
    with pytest.raises(Exception, match=ROOT_INSTRUCTION):
        predict(record("MISSING-1", LATE)["inputs"])


def _case_of(model: BarrierLlm) -> str:
    """Which record this model served, read off the date block it was sent.

    The clock reaches the model through §2's per-invocation instruction provider,
    so the date in the first request is that rollout's ``as_of`` and identifies
    it.
    """
    seen = text_seen(model.requests[0])
    matched = [
        case_id
        for case_id, as_of in (("EARLY-1", EARLY), ("LATE-1", LATE))
        if as_of[:10] in seen
    ]
    assert len(matched) == 1, f"the date block named {matched}, not one case"
    return matched[0]


def _plot_result(model: BarrierLlm) -> dict[str, Any]:
    """The chart this rollout's tool actually returned, off the model's second turn.

    Read from what the model was *given* rather than from the outputs, which
    carry the call's arguments and no result at all — §6.1's checks address
    arguments, so a ``CaseResult`` deliberately holds no tool result.
    """
    (chart,) = results_seen(model.requests[1])
    return dict(chart)
