"""The measurement path, and every switch that points the other way from the search (T128).

The rollout is stubbed. What one does is T100's; what this file is about is the
pass around it — replay rather than record, the LLM cache off rather than on, the
whole split rather than the pre-filtered one, and a failed rollout kept as an
excluded case rather than lost. Each of those is a decision that would otherwise
surface only as a number that looked plausible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.candidates import baseline_texts, register_candidates
from harness.measure import (
    BASELINE_ARM,
    OPTIMIZED_ARM,
    CaseOutcome,
    Measurement,
    arm_versions,
    measure,
    measure_condition,
    outcomes_from,
    write_optimized_arm,
)
from harness.preregistration import PreregistrationViolation
from harness.scoring import TRAJECTORY
from water_assistant_agent.assistant.settings import AssistantSettings


@pytest.fixture
def cases(tmp_path: Path) -> Path:
    """A three-record ``train.json`` and a two-record ``test_seen.json``, both real."""
    from harness.train_data import load_split

    directory = tmp_path / "cases"
    directory.mkdir()
    for split, count in (("train", 3), ("test_seen", 2)):
        (directory / f"{split}.json").write_text(
            json.dumps(load_split(split)[:count], indent=2), encoding="utf-8"
        )
    return directory


@pytest.fixture
def rollouts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """``run_case`` replaced by a fixed correct-shaped answer; the arguments are recorded."""
    seen: list[dict[str, Any]] = []

    def run_case(inputs: Any, **kwargs: Any) -> Any:
        seen.append({"inputs": dict(inputs), **kwargs})
        from harness.contract import parse_contract
        from harness.run_case import CaseResult

        text = '{"status": "answered", "answer": 1.0, "unit": "L", "explanation": "…"}'
        return CaseResult(
            case_id=str(inputs.get("case_id", "")),
            template_id=str(inputs.get("template_id", "")),
            contract=parse_contract(text),
            final_text=text,
            trajectory=(),
            harness_error=False,
            exclusions=(),
            diagnostics={"steps": 0, "parse_failure": False},
        )

    monkeypatch.setattr("harness.predict.run_case", run_case)
    return seen


# ── The switches point the other way from the search ─────────────────────────


def test_the_measurement_replays_and_never_records(
    registry: str, cases: Path, rollouts: list[dict[str, Any]]
) -> None:
    """``allow_live=False`` is the exclusion channel §7 says exists only on this path.

    A miss becomes a ``CacheMissError``, which the tool wrappers turn into an
    ``upstream`` error, which excludes the case. The search does the opposite for
    a reason that does not apply here: it has no exclusion channel at all.
    """
    versions = register_candidates(baseline_texts())

    measure_condition(
        arm=BASELINE_ARM, split="train", repeat=1, versions=versions, cases_dir=cases
    )

    assert rollouts
    assert all(rollout["allow_live"] is False for rollout in rollouts)


def test_the_whole_split_is_measured_and_not_the_pre_filtered_one(
    registry: str, cases: Path, rollouts: list[dict[str, Any]]
) -> None:
    """The pre-filter is the search's protection; here a case that cannot replay is a count."""
    versions = register_candidates(baseline_texts())

    result = measure_condition(
        arm=BASELINE_ARM, split="train", repeat=1, versions=versions, cases_dir=cases
    )

    assert result.report.cases == 3
    assert len(result.outcomes) == 3


def test_the_llm_cache_is_off_for_the_measurement(
    registry: str, cases: Path, rollouts: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three repeats *are* the replication; a prompt-keyed hit would collapse them.

    A process that ran a search earlier has the cache on, so the measurement
    turns it off explicitly rather than trusting the default it also happens to
    be.
    """
    import litellm

    settings = AssistantSettings(llm_cache_enabled=True)
    register_candidates(baseline_texts())
    monkeypatch.setattr(
        "harness.measure.arm_versions", lambda **_: {BASELINE_ARM: _versions()}
    )

    measure(
        arms={BASELINE_ARM: _versions()},
        splits=["train"],
        repeats=1,
        cases_dir=cases,
        settings=settings,
        exploratory=True,
    )

    assert litellm.cache is None


def test_every_repeat_sees_the_identical_cases(
    registry: str, cases: Path, rollouts: list[dict[str, Any]]
) -> None:
    """Pairing is what makes the paired bootstrap paired, so it is a property of the run."""
    register_candidates(baseline_texts())

    measurement = measure(
        arms={BASELINE_ARM: _versions(), OPTIMIZED_ARM: _versions()},
        splits=["train"],
        repeats=3,
        cases_dir=cases,
        exploratory=True,
    )

    by_condition = {
        (condition.arm, condition.repeat): [o.case_id for o in condition.outcomes]
        for condition in measurement.conditions
    }
    assert len(by_condition) == 6
    assert len(set(map(tuple, by_condition.values()))) == 1


def test_the_conditions_run_repeat_major(
    registry: str, cases: Path, rollouts: list[dict[str, Any]]
) -> None:
    """A drift during the run then lands across the arms rather than inside one.

    The difference is between a run that is noisier than it should be and a run
    whose comparison is not interpretable at all.
    """
    register_candidates(baseline_texts())

    measurement = measure(
        arms={BASELINE_ARM: _versions(), OPTIMIZED_ARM: _versions()},
        splits=["train"],
        repeats=2,
        cases_dir=cases,
        exploratory=True,
    )

    order = [(c.repeat, c.arm) for c in measurement.conditions]
    assert order == [
        (1, BASELINE_ARM),
        (1, OPTIMIZED_ARM),
        (2, BASELINE_ARM),
        (2, OPTIMIZED_ARM),
    ]


# ── A failed rollout is an excluded case, not a lost one ─────────────────────


def test_a_rollout_that_raised_stays_in_the_denominator(
    registry: str, cases: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no ``optimize_prompts`` here to catch it, so this path catches it itself.

    Losing the case would drop a candidate's worst runs and lift its mean, which
    is the bias ``decisions.md`` § Tool errors and harness exclusion is built to
    avoid.
    """
    versions = register_candidates(baseline_texts())

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the endpoint fell over")

    monkeypatch.setattr("harness.predict.run_case", explode)

    result = measure_condition(
        arm=BASELINE_ARM, split="train", repeat=1, versions=versions, cases_dir=cases
    )

    assert result.report.cases == 3
    assert result.report.excluded == 3
    assert result.report.included == 0
    assert result.report.exclusions_by_source == {"rollout": 3}
    assert all(outcome.harness_error for outcome in result.outcomes)


# ── The pre-registration bites here, and only here ───────────────────────────


def test_a_deviating_measurement_is_refused(
    registry: str, cases: Path, rollouts: list[dict[str, Any]]
) -> None:
    """A test number under an unregistered protocol is what the registration prevents."""
    register_candidates(baseline_texts())

    with pytest.raises(PreregistrationViolation, match="repeats"):
        measure(
            arms={BASELINE_ARM: _versions(), OPTIMIZED_ARM: _versions()},
            splits=["train", "test_seen", "test_unseen"],
            # Two repeats where the registration says one. Read off the file
            # rather than written as a literal, so an amendment to the repeat
            # count cannot turn this test into one that passes by agreeing.
            repeats=_registered_repeats() + 1,
            cases_dir=cases,
        )


# ── The arms differ in exactly one thing, and it is recorded by the search ───


def test_the_optimized_arm_is_written_by_the_search_and_read_by_the_measurement(
    tmp_path: Path,
) -> None:
    """A transcribed version number is how the measured arm stops being the selected one."""
    from harness.candidates import CANDIDATE_COMPONENTS, PROMPT_NAMES

    path = tmp_path / "optimized_candidate.json"
    prompts = [
        _Prompt(PROMPT_NAMES[component], 2) for component in CANDIDATE_COMPONENTS
    ]

    versions = write_optimized_arm(prompts, path=path, split="train")

    assert versions == {component: 2 for component in CANDIDATE_COMPONENTS}
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["split"] == "train"
    assert document["versions"] == versions


def test_no_recorded_search_means_no_optimized_arm(tmp_path: Path) -> None:
    """No fallback to ``@latest``: after a search that is a candidate nobody selected."""
    with pytest.raises(FileNotFoundError, match="nothing else it could be"):
        arm_versions(optimized_path=tmp_path / "missing.json")


# ── A written measurement is enough to re-analyse ────────────────────────────


def test_the_outcomes_round_trip_through_the_written_file(tmp_path: Path) -> None:
    """The statistics are a function of these rows, so a re-analysis needs no model."""
    outcome = CaseOutcome(
        arm=BASELINE_ARM,
        split="test_seen",
        repeat=2,
        case_id="T01-0001",
        template_id="T01",
        metrics={TRAJECTORY: 1.0},
        selection_score=0.75,
        harness_error=False,
        unanswerable=False,
        abstained=False,
        diagnostics={"steps": 2},
    )
    measurement = Measurement(versions={BASELINE_ARM: {"root_instruction": 1}})
    measurement.conditions.append(_condition(outcome))

    path = measurement.write(tmp_path / "measurement.json")

    assert outcomes_from(path) == (outcome,)


def _registered_repeats() -> int:
    from harness.preregistration import MEASUREMENT, load

    return int(load()[MEASUREMENT]["repeats"])


def _versions() -> dict[str, int]:
    from harness.candidates import CANDIDATE_COMPONENTS

    return {component: 1 for component in CANDIDATE_COMPONENTS}


class _Prompt:
    def __init__(self, name: str, version: int) -> None:
        self.name = name
        self.version = version


def _condition(outcome: CaseOutcome) -> Any:
    from harness.ledger import RunLedger
    from harness.measure import ConditionResult
    from harness.scoring import aggregate

    return ConditionResult(
        arm=outcome.arm,
        split=outcome.split,
        repeat=outcome.repeat,
        report=aggregate([], arm=outcome.arm),
        outcomes=(outcome,),
        ledger=RunLedger(arm=outcome.arm, split=outcome.split),
    )
