"""One case runs end to end, and the error taxonomy decides what is excluded (T100).

The packet's exit criterion in its executable form. Three claims:

1. A case runs the whole path — context, toolset, agent, runner, contract — and
   comes back as a structured result with a real trajectory behind it.
2. An injected ``upstream`` error marks the case ``harness_error`` where an
   ``invalid_argument`` leaves it scored (`decisions.md` § Tool errors and
   harness exclusion).
3. A final message parsing to neither status is reported as a ``parse_failure``
   and never as a wrong answer (§7).

Only the model is faked. The database is the pinned file bounded at the case's
own ``as_of``, the card store is the packaged YAML, and the cache is a replay
cache over an empty directory — so the ``upstream`` error the second claim needs
is a real cache miss on a real fetch, not a stubbed return value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.adk.events.event import Event
from google.genai import types

from harness.run_case import (
    CaseResult,
    Exclusion,
    ToolCall,
    run_case,
    scan_for_exclusions,
)
from tests.harness.conftest import (
    Call,
    Say,
    Think,
    results_seen,
    scripted,
    text_seen,
)

AS_OF = "2025-08-14T08:00:00+02:00"

CONTRACT = json.dumps(
    {
        "status": "answered",
        "answer": 50.0,
        "unit": "%",
        "explanation": "The manual states the retention target.",
    }
)


def case(question: str, **overrides: object) -> dict[str, object]:
    """A case envelope's ``inputs`` half — the whole of what a rollout is given."""
    return {
        "question": question,
        "as_of": AS_OF,
        "case_id": "T12-0001",
        "template_id": "T12",
        "params": {},
        **overrides,
    }


def run(script: object, tmp_path: Path, **kwargs: object) -> CaseResult:
    return run_case(
        case("What is the manual's retention target?"),
        model=script,
        cache_dir=tmp_path / "cache",
        **kwargs,
    )


def test_a_case_runs_end_to_end(tmp_path: Path) -> None:
    """The exit criterion: one case, the whole path, a structured result."""
    model = scripted(
        Call("lookup_reference", {"topic": "retention_target"}), Say(CONTRACT)
    )
    result = run(model, tmp_path)

    assert result.harness_error is False
    assert result.exclusions == ()
    assert result.status == "answered"
    assert result.answer == 50.0
    assert result.unit == "%"
    assert result.trajectory == (
        ToolCall(name="lookup_reference", args={"topic": "retention_target"}),
    )
    assert result.case_id == "T12-0001"
    assert result.template_id == "T12"
    assert result.diagnostics["steps"] == 1
    assert result.diagnostics["parse_failure"] is False
    assert result.diagnostics["step_cap_exceeded"] is False
    assert result.diagnostics["latency_s"] >= 0


def test_the_tool_really_ran(tmp_path: Path) -> None:
    """The rollout is not a trajectory recorder: the card came back from the store.

    Asserted on what the model was *given* on its second turn, which is the only
    place a tool result exists — `CaseResult` deliberately carries none, because
    §6.1's argument checks address arguments and never a result.
    """
    model = scripted(
        Call("lookup_reference", {"topic": "retention_target"}), Say(CONTRACT)
    )
    run(model, tmp_path)

    assert len(model.requests) == 2
    (card,) = results_seen(model.requests[1])
    assert card["topic"] == "retention_target"
    assert card["provenance"] in {"rendered", "static"}
    assert card["values"], "the card came back empty — nothing was actually read"


def test_the_frozen_clock_reaches_the_model(tmp_path: Path) -> None:
    """The case's ``as_of``, not the wall clock — §4's seam, through §2's provider."""
    model = scripted(Say(CONTRACT))
    run(model, tmp_path)

    assert "2025-08-14" in text_seen(model.requests[0])


def test_the_candidate_instruction_is_what_the_model_is_sent(tmp_path: Path) -> None:
    """A rollout runs on the candidate's text, and on nothing else."""
    model = scripted(Say(CONTRACT))
    run(model, tmp_path, instruction="ANSWER WITH THE CONTRACT.")

    assert "ANSWER WITH THE CONTRACT." in str(
        model.requests[0].config.system_instruction
    )


def test_an_upstream_error_marks_the_case_a_harness_error(tmp_path: Path) -> None:
    """A replay cache miss on a real fetch — the injected ``upstream`` of the exit.

    The window predates the station record, so the composite falls to Archive
    whole; Archive is bound to an empty replay cache, so the miss is unfillable
    and the wrapper types it ``upstream``. That is exactly what `decisions.md`
    § Tool errors and harness exclusion calls a harness error: something the
    candidate could not have avoided and the harness cannot reproduce.
    """
    model = scripted(
        Call(
            "get_weather_forecast_tool",
            {"start_date": "2024-05-01", "end_date": "2024-05-07"},
        ),
        Say(CONTRACT),
    )
    result = run(model, tmp_path)

    assert result.harness_error is True
    assert [item.source for item in result.exclusions] == ["upstream"]
    assert result.exclusions[0].tool == "get_weather_forecast_tool"
    # Excluded, and still fully reported: the run is not thrown away.
    assert result.status == "answered"
    assert result.tool_names == ("get_weather_forecast_tool",)


def test_an_invalid_argument_leaves_the_case_scored(tmp_path: Path) -> None:
    """The other half of the claim, and the one that makes the metric honest.

    Excluding argument errors too would be exploitable: a candidate's own fumbles
    would leave the denominator and *raise* its average, so the worse it handled
    a template the better it would look. Here the candidate fumbles, recovers,
    and the case scores through the normal metrics with nothing excluded.
    """
    model = scripted(
        Call("lookup_reference", {"topic": "not_a_topic"}),
        Call("lookup_reference", {"topic": "retention_target"}),
        Say(CONTRACT),
    )
    result = run(model, tmp_path)

    assert result.harness_error is False
    assert result.exclusions == ()
    assert result.diagnostics["steps"] == 2
    assert result.status == "answered"
    # The fumble happened — it was typed `invalid_argument` and handed back.
    (fumble,) = results_seen(model.requests[1])
    assert fumble["error_type"] == "invalid_argument"


def test_a_message_parsing_to_neither_status_is_a_parse_failure(tmp_path: Path) -> None:
    """Reported apart from the answer, so format and reasoning stay distinguishable.

    The run is *not* a harness error and *not* an answer: `status` is `None`, so
    a scorer cannot mistake it for a wrong value, and the diagnostic is what
    says what actually went wrong.
    """
    model = scripted(
        Call("lookup_reference", {"topic": "retention_target"}),
        Say("The manual sets the retention target at 50 %."),
    )
    result = run(model, tmp_path)

    assert result.diagnostics["parse_failure"] is True
    assert result.contract is None
    assert result.status is None
    assert result.answer is None
    assert result.harness_error is False
    assert result.final_text.startswith("The manual sets")


def test_spending_the_step_cap_is_scored_not_excluded(tmp_path: Path) -> None:
    """A candidate that loops to the cap caused that itself, so it stays in.

    It leaves no final message, so it lands as a parse failure with
    `step_cap_exceeded` beside it — the diagnostic that tells this apart from a
    candidate that answered in the wrong format.
    """
    model = scripted(*[Call("lookup_reference", {"topic": "retention_target"})] * 9)
    result = run(model, tmp_path)

    assert result.diagnostics["step_cap_exceeded"] is True
    assert result.diagnostics["parse_failure"] is True
    assert result.harness_error is False
    assert result.final_text == ""
    assert 0 < result.diagnostics["steps"] <= 7


def test_a_reasoning_models_scratchpad_is_not_its_final_message(tmp_path: Path) -> None:
    """The thought part is dropped; the reply beside it is the whole final text (T134).

    Not cosmetic. ``parse_contract`` would mostly survive a prepended trace — it
    scans backwards for the last embedded object — so the failure this guards
    against is quiet: the scored ``explanation`` and the recorded final message
    would be a chain of thought that is not stable at temperature 0, and the
    numbers would be read as if a candidate had written it.
    """
    thought = 'The user wants the retention target. I should answer {"status": "guess"}.'
    model = scripted(
        Call("lookup_reference", {"topic": "retention_target"}),
        Think(thought, CONTRACT),
    )

    result = run(model, tmp_path)

    assert result.final_text == CONTRACT
    assert thought not in result.final_text
    assert result.diagnostics["parse_failure"] is False
    assert result.status == "answered"
    assert result.answer == 50.0


def _response_event(name: str, payload: object) -> Event:
    return Event(
        author="root_agent",
        content=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(name=name, response=payload)
                )
            ],
        ),
    )


def test_the_sub_agents_untyped_error_excludes_on_its_name() -> None:
    """`text_to_sql_agent` carries no ``error_type``, so it is found by tool name.

    Its contract is `{"status": "error", "error_details": …}` and the sub-agent is
    frozen, so the `invalid_argument` / `upstream` split is simply unavailable
    there. T100 resolves that by treating the whole population as upstream rather
    than by guessing which half a given failure belongs to.
    """
    events = [
        _response_event(
            "text_to_sql_agent",
            {"status": "error", "error_details": "no such column: Kiesdach"},
        )
    ]
    assert scan_for_exclusions(events) == (
        Exclusion(
            tool="text_to_sql_agent",
            source="text_to_sql_agent",
            details="no such column: Kiesdach",
        ),
    )


def test_the_sub_agents_error_is_found_through_adks_boxing() -> None:
    """ADK boxes a non-dict return as ``{"result": …}``; the scan unwraps it.

    The sub-agent answers in prose often enough that its tool returns a bare
    string, and a scan reading `response` straight would then never see the
    status it is looking for.
    """
    boxed = {"result": json.dumps({"status": "error", "error_details": "syntax error"})}
    assert scan_for_exclusions([_response_event("text_to_sql_agent", boxed)]) == (
        Exclusion(
            tool="text_to_sql_agent", source="text_to_sql_agent", details="syntax error"
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "success", "sql": "SELECT 1", "reasoning": "…"},
        {"result": "The gravel roof retained 41 % of the rain."},
    ],
)
def test_a_sub_agent_result_that_is_not_an_error_does_not_exclude(payload: object) -> None:
    """A prose answer is not a failure, and neither is a repaired query.

    The inner fixer loop runs on `AgentTool`'s own `Runner`, so its retries never
    reach this event stream at all — a query that failed twice and then worked is
    a success here, which is what it is.
    """
    assert scan_for_exclusions([_response_event("text_to_sql_agent", payload)]) == ()


def test_an_invalid_argument_from_any_tool_does_not_exclude() -> None:
    events = [
        _response_event(
            "lookup_reference",
            {"status": "error", "error_type": "invalid_argument", "error_details": "…"},
        )
    ]
    assert scan_for_exclusions(events) == ()
