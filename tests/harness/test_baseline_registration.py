"""One registration, two uses: the reference arm and the search's seed (T125).

The handwritten baseline is the arm §7 measures against **and** the candidate
GEPA starts from. They are the same seven strings, and the only thing keeping
them the same is that there is one registration and two readers of it. A second
copy would drift the first time either was touched, and the drift would show up
as a search improving on something nobody measured.

So the claims here are about identity, not about the text:

1. A rollout with **no candidate installed** — the reference arm — runs on
   exactly the registered seed, instruction and every tool docstring alike.
2. Re-registering unchanged text mints no version, so re-pinning does not move
   the seed under a measurement.
3. ``--check`` catches a pinned version whose registered text is not the
   handwritten one, which is the failure ``just pins`` cannot see: it hashes what
   this repo *would* register, not what was registered.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from harness.candidates import (
    CANDIDATE_COMPONENTS,
    PROMPT_NAMES,
    ROOT_INSTRUCTION,
    VERSIONS_PIN,
    baseline_texts,
    evaluation_pass,
    register_candidates,
    seed_versions,
)
from harness.contract import EVALUATION_ROOT_INSTRUCTION
from harness.predict import make_predict_fn
from tests.harness.conftest import Say, scripted
from water_assistant_agent.assistant.toolset import TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]

AS_OF = "2025-06-15T08:00:00+02:00"

CONTRACT = json.dumps(
    {"status": "answered", "answer": 50.0, "unit": "%", "explanation": "The manual."}
)


def _load_script() -> Any:
    """Import ``scripts/register_candidates.py`` by path; ``scripts/`` is no package."""
    spec = importlib.util.spec_from_file_location(
        "register_candidates", REPO_ROOT / "scripts" / "register_candidates.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inputs(case_id: str) -> dict[str, Any]:
    return {
        "question": "What is the manual's retention target?",
        "as_of": AS_OF,
        "case_id": case_id,
        "template_id": "T12",
        "params": {},
    }


def test_the_reference_arm_runs_on_the_registered_seed(registry: str) -> None:
    """No candidate installed, and the rollout is the handwritten text exactly.

    This is the whole of "one registration, two uses". The search reads these
    prompts with a candidate patched over them; the reference arm reads the same
    prompts with nothing patched, and gets the baseline — not a second copy of
    it that happens to say the same thing today.
    """
    versions = register_candidates(baseline_texts())
    model = scripted(Say(CONTRACT))

    predict = make_predict_fn(versions=versions, model_factory=lambda: model)
    with evaluation_pass():
        produced = predict(_inputs("REF-1"))

    assert produced["status"] == "answered"
    request = model.requests[0]
    # A prefix rather than an equality: ADK appends its own identity block
    # ("You are an agent. Your internal name is …") after the static
    # instruction. What the seed owns is everything before it, verbatim.
    assert str(request.config.system_instruction).startswith(
        EVALUATION_ROOT_INSTRUCTION
    )
    declared = {
        declaration.name: declaration.description
        for tool in request.config.tools
        for declaration in tool.function_declarations
    }
    seed = baseline_texts()
    assert set(declared) == set(TOOL_NAMES)
    for name in TOOL_NAMES:
        assert declared[name] == seed[name]


def test_re_registering_the_seed_moves_nothing(registry: str) -> None:
    """Unchanged text mints no version, so the pinned seed survives a re-pin."""
    first = register_candidates(baseline_texts())
    second = register_candidates(baseline_texts())

    assert first == second == dict.fromkeys(CANDIDATE_COMPONENTS, 1)


def test_the_check_catches_a_pin_on_the_wrong_version(
    registry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version pinned against text that is not the handwritten one fails.

    ``just pins`` hashes what this repo *would* register and cannot see this:
    the seed text is unchanged, and what moved is which version the pin names.
    """
    script = _load_script()
    register_candidates(baseline_texts())
    edited = {**baseline_texts(), ROOT_INSTRUCTION: "ANSWER WITH THE CONTRACT."}
    versions = register_candidates(edited)
    assert versions[ROOT_INSTRUCTION] == 2

    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"pins": {VERSIONS_PIN: versions}}), encoding="utf-8")
    monkeypatch.setattr(script, "PINS_PATH", pins)

    assert script.check() == 1

    # And it passes once the pin names the version the seed really is at.
    pins.write_text(
        json.dumps({"pins": {VERSIONS_PIN: {**versions, ROOT_INSTRUCTION: 1}}}),
        encoding="utf-8",
    )
    assert script.check() == 0


def test_registration_writes_one_slot_and_leaves_the_rest(
    registry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin writer is targeted: it knows one slot and touches one slot."""
    script = _load_script()
    pins = tmp_path / "pins.json"
    pins.write_text(
        json.dumps(
            {
                "_comment": "kept",
                "pins": {VERSIONS_PIN: None, "water_duckdb_sha256": "abc"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(script, "PINS_PATH", pins)
    monkeypatch.setattr(sys, "argv", ["register_candidates.py"])

    assert script.main() == 0

    written = json.loads(pins.read_text(encoding="utf-8"))
    assert written["_comment"] == "kept"
    assert written["pins"]["water_duckdb_sha256"] == "abc"
    assert written["pins"][VERSIONS_PIN] == dict.fromkeys(CANDIDATE_COMPONENTS, 1)
    assert seed_versions(pins) == dict.fromkeys(CANDIDATE_COMPONENTS, 1)


def test_the_committed_pin_names_every_component() -> None:
    """The committed seed pin covers the whole surface, one version per component."""
    committed = json.loads(
        (REPO_ROOT / "eval" / "pins.json").read_text(encoding="utf-8")
    )["pins"]

    assert set(committed[VERSIONS_PIN]) == set(CANDIDATE_COMPONENTS)
    assert set(committed["candidate_prompts"]) == set(CANDIDATE_COMPONENTS)
    for component in CANDIDATE_COMPONENTS:
        assert committed["candidate_prompts"][component]["prompt"] == (
            PROMPT_NAMES[component]
        )
