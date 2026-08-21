"""The answer contract parses, and what it refuses is a parse failure (T101).

Two claims are under test and they are not the same claim. The first is that a
well-formed contract survives the shapes a real model actually emits — bare,
fenced, trailed by prose. The second is the one §7 rests on: a message that
states **neither** status is `None` here, so the harness can report it as a
`parse_failure` diagnostic instead of scoring it as a wrong answer.
"""

from __future__ import annotations

import pytest

from harness.contract import (
    CONTRACT_STATUSES,
    CONTRACT_UNITS,
    EVALUATION_ROOT_INSTRUCTION,
    AnswerContract,
    parse_contract,
)
from water_assistant_agent.assistant.agents.root_agent.agent import ROOT_INSTRUCTION


def test_the_instructed_shape_parses() -> None:
    """One object, nothing around it — what the instruction asks for."""
    parsed = parse_contract(
        '{"status": "answered", "answer": 12.5, "unit": "mm", '
        '"explanation": "Summed the outflow column."}'
    )
    assert parsed == AnswerContract(
        status="answered", answer=12.5, unit="mm", explanation="Summed the outflow column."
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('```json\n{"status": "answered", "answer": true}\n```', True),
        ('```\n{"status": "answered", "answer": false}\n```', False),
        ('Here is the answer:\n{"status": "answered", "answer": true}\n', True),
        ('{"status": "answered", "answer": false} — the roof missed the target.', False),
    ],
)
def test_the_shapes_a_model_actually_emits_parse(message: str, expected: bool) -> None:
    """A fence or a sentence around the object is a formatting slip, not a failure.

    Scoring these as parse failures would spend the diagnostic on models that
    answered correctly, and would make the metric that is supposed to isolate
    format degradation the noisiest number in the report.
    """
    parsed = parse_contract(message)
    assert parsed is not None
    assert parsed.answer is expected


def test_the_last_object_is_the_answer() -> None:
    """A model that restates its answer has stated the later one."""
    parsed = parse_contract(
        'First pass: {"status": "answered", "answer": 1, "unit": "mm"}\n'
        'Corrected: {"status": "answered", "answer": 2, "unit": "mm"}'
    )
    assert parsed is not None
    assert parsed.answer == 2


def test_a_brace_inside_the_explanation_does_not_close_the_object() -> None:
    """The scan is string-aware, so an explanation may quote the contract itself."""
    parsed = parse_contract(
        '{"status": "not_available", "answer": null, "unit": null, '
        '"explanation": "The shape is {status, answer, unit, explanation}."}'
    )
    assert parsed is not None
    assert parsed.status == "not_available"
    assert parsed.explanation.endswith("{status, answer, unit, explanation}.")


@pytest.mark.parametrize(
    "message",
    [
        "The gravel roof retained 41 % of the rain.",
        '{"status": "needs_clarification", "answer": null}',
        "Which of the two roofs did you mean?",
        '{"answer": 12.5, "unit": "mm"}',
        "{not json at all}",
        "",
    ],
)
def test_a_message_stating_neither_status_is_a_parse_failure(message: str) -> None:
    """§7's dividing line: no status, no contract — and no wrong answer either.

    `needs_clarification` is in the list deliberately. It is the status the
    contract was decided *not* to have (`decisions.md` § The answer contract),
    and a candidate that invents it has degraded the format rather than the
    reasoning.
    """
    assert parse_contract(message) is None


def test_none_and_a_missing_final_message_are_parse_failures() -> None:
    """A run that produced no final message at all delivered no contract."""
    assert parse_contract(None) is None


def test_a_wrong_unit_is_carried_not_rejected() -> None:
    """Normalization belongs to the answer scorer, so nothing is coerced here.

    A candidate answering 12.5 `liters` has answered in a unit the contract does
    not name — which the answer metric must see and score. Rejecting it here
    would file it under format degradation instead, where it does not belong.
    """
    parsed = parse_contract('{"status": "answered", "answer": 12.5, "unit": "liters"}')
    assert parsed is not None
    assert parsed.unit == "liters"
    assert parsed.unit not in CONTRACT_UNITS


def test_missing_optional_fields_default_rather_than_fail() -> None:
    parsed = parse_contract('{"status": "not_available"}')
    assert parsed == AnswerContract(
        status="not_available", answer=None, unit=None, explanation=""
    )


def test_the_evaluation_instruction_carries_the_contract() -> None:
    """The candidate instruction is what puts the contract in front of the model."""
    for status in CONTRACT_STATUSES:
        assert f'"{status}"' in EVALUATION_ROOT_INSTRUCTION
    for field in ("status", "answer", "unit", "explanation"):
        assert f"**`{field}`**" in EVALUATION_ROOT_INSTRUCTION
    for unit in CONTRACT_UNITS:
        if unit is not None:
            assert unit in EVALUATION_ROOT_INSTRUCTION


def test_the_evaluation_instruction_forbids_asking_a_question_back() -> None:
    """The no-clarification clause is explicit, and production's is not touched.

    Without it a clarifying question scores as a wrong answer — the contract has
    no turn type for one, and the generation filter is what makes that safe
    (`decisions.md` § The answer contract). So the clause has to be in the text
    rather than left to the JSON shape to imply.
    """
    assert "never ask one back" in EVALUATION_ROOT_INSTRUCTION
    assert "There is nobody to answer it" in EVALUATION_ROOT_INSTRUCTION
    # Production keeps the clarification, and keeps it in its own words.
    assert "Is this a new problem statement" in ROOT_INSTRUCTION
    assert "Is this a new problem statement" not in EVALUATION_ROOT_INSTRUCTION
    assert "status" not in ROOT_INSTRUCTION.split("### Your responsibilities")[0]


def test_the_two_instructions_are_authored_apart() -> None:
    """Neither text is derived from the other, so an edit to one cannot move it.

    The evaluation instruction is byte-stable after the freeze (T107); production
    prose is not. A derivation would couple them, which is exactly what the
    freeze forbids.
    """
    assert EVALUATION_ROOT_INSTRUCTION != ROOT_INSTRUCTION
    assert EVALUATION_ROOT_INSTRUCTION not in ROOT_INSTRUCTION
    assert ROOT_INSTRUCTION not in EVALUATION_ROOT_INSTRUCTION
