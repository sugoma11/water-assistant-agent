"""The run ledger, and the four columns nothing else in the run would carry (T127).

``optimize_prompts`` already logs per-iteration candidate text, per-scorer metrics
and an eval-results table, so what is tested here is only the difference §6 asks
for: the candidate's *identity*, the case's, the served model id, the pins from
both sides, the trajectory and the cost. Each of those is a column that would
read plausibly while being wrong, which is why each has a test of its own rather
than one test over a row.

The rollout itself is stubbed. What one does is T100's and T121's; what this file
is about is the record kept of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mlflow
import pytest
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from harness.candidates import CANDIDATE_COMPONENTS, baseline_texts, register_candidates
from harness.ledger import (
    LEDGER_ARTIFACT,
    PINS_ARTIFACT,
    RunLedger,
    TaskModelWitness,
    WitnessedLiteLlm,
    candidate_id,
    ledgered,
    experiment_run,
)
from harness.optimize import run_search
from tests.harness.conftest import Recorded
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

PRICE_VARS = (
    "PRICE_TASK_INPUT",
    "PRICE_TASK_OUTPUT",
    "PRICE_JUDGE_INPUT",
    "PRICE_JUDGE_OUTPUT",
    "PRICE_OPTIMIZER_INPUT",
    "PRICE_OPTIMIZER_OUTPUT",
)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The six ``PRICE_*`` variables set, so a rollout can be costed."""
    for name in PRICE_VARS:
        monkeypatch.setenv(name, "2.0" if name.endswith("OUTPUT") else "1.0")


@pytest.fixture
def unpriced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The six ``PRICE_*`` variables unset — an unpriced run, which is a real state."""
    for name in PRICE_VARS:
        monkeypatch.delenv(name, raising=False)


def outputs(**overrides: Any) -> dict[str, Any]:
    """One rollout's ``outputs``, in the shape ``predict_fn`` emits them."""
    return {
        "case_id": "T01-0001",
        "template_id": "T01",
        "status": "answered",
        "answer": 39.4,
        "unit": "L",
        "explanation": "…",
        "final_text": "{}",
        "trajectory": [{"name": "text_to_sql_agent", "args": {"request": "…"}}],
        "harness_error": False,
        "exclusions": [],
        "diagnostics": {
            "steps": 1,
            "model_turns": 2,
            "tokens": {"prompt": 1000, "candidates": 500, "total": 1500},
            "latency_s": 3.5,
            "parse_failure": False,
            "step_cap_exceeded": False,
            "fixer_iterations": None,
        },
        **overrides,
    }


# ── The candidate's identity is its text ─────────────────────────────────────


def test_the_candidate_id_is_the_text_and_never_the_version() -> None:
    """The version cannot identify a candidate, so the id is over the text.

    Candidate text arrives as a process-global patch of ``PromptVersion.template``
    and every read still resolves the same pinned version, so a whole search
    reads back under one version number. An id keyed on that would file every
    candidate GEPA proposed as the same candidate.
    """
    seed = baseline_texts()
    proposed = {**seed, "root_instruction": seed["root_instruction"] + "\nAlso: …"}

    assert candidate_id(seed) == candidate_id(dict(seed))
    assert candidate_id(seed) != candidate_id(proposed)


def test_two_components_cannot_trade_bytes_and_hash_alike() -> None:
    """Length-delimited per component, so a boundary shift is a different candidate."""
    left = {component: "" for component in CANDIDATE_COMPONENTS}
    right = dict(left)
    left["root_instruction"], left["lookup_reference"] = "ab", ""
    right["root_instruction"], right["lookup_reference"] = "a", "b"

    assert candidate_id(left) != candidate_id(right)


def test_a_partial_candidate_has_no_id() -> None:
    """All seven or it is not a candidate — the rule ``register_candidates`` enforces."""
    partial = {component: "x" for component in CANDIDATE_COMPONENTS[:-1]}

    with pytest.raises(ValueError, match="No candidate text for component"):
        candidate_id(partial)


# ── The served model id is witnessed, not assumed ────────────────────────────


@pytest.mark.asyncio
async def test_the_witness_records_what_the_endpoint_said_it_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADK fills ``model_version`` from litellm's ``response.model``; that is the id.

    Recorded without duplicates, because a rollout takes several turns and
    normally gets one answer to this question — and a rollout that got two is
    the finding the column exists for.
    """

    async def served(self: LiteLlm, llm_request: Any, stream: bool = False) -> Any:
        for version in ("qwen3.6-35b-a3b", "qwen3.6-35b-a3b", "something-else"):
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text="…")]),
                model_version=version,
            )

    monkeypatch.setattr(LiteLlm, "generate_content_async", served)
    model = WitnessedLiteLlm(model="openai/qwen3.6-35b-a3b")

    async for _ in model.generate_content_async(object()):
        pass

    assert model.served == ["qwen3.6-35b-a3b", "something-else"]


def test_the_witness_is_built_exactly_as_the_agent_builds_its_model() -> None:
    """A witness that decoded differently would be witnessing a different rollout.

    Compared against ``root_agent.agent``'s own builder rather than against a
    transcription of it, so a change to the pinned decoding parameters cannot
    leave the witness behind.
    """
    from water_assistant_agent.assistant.agents.root_agent.agent import _build_model

    built = _build_model()
    witness = TaskModelWitness().build()

    assert witness.model == built.model
    assert witness._additional_args == built._additional_args


def test_a_row_reports_no_served_id_rather_than_the_requested_one(
    unpriced: None,
) -> None:
    """The column's whole value is that it is a second, independent statement.

    A rollout whose model was never witnessed reports ``None`` — filling it in
    from the pin would turn evidence into a copy of the claim, which is the one
    thing ``task_model_canary_sha256`` exists to prevent. The requested column
    keeps the pin's own ``<provider>/<model>`` spelling for the same reason:
    normalizing either side to match the other is how a divergence gets hidden.
    """
    ledger = RunLedger(arm="baseline", split="train")

    row = ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(),
        candidate=baseline_texts(),
        served=(),
    )

    assert row.served_model_id is None
    assert row.requested_model_id == get_settings().root_agent_model


# ── Both sides' pins, and the cost ───────────────────────────────────────────


def test_a_row_carries_the_runs_pins_and_the_cases_own(unpriced: None) -> None:
    """Two pin sets, two questions: what the rollout ran against, what the oracle did.

    An answer is comparable with a rollout only where both agree, so the row
    carries the digest of the run's whole pin file *and* the case's own stamp
    rather than choosing between them.
    """
    ledger = RunLedger(arm="baseline", split="train")
    case_pins = {"duckdb_sha256": "abc", "weather_source": "archive"}

    row = ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(),
        expectations={"pins": case_pins},
        candidate=baseline_texts(),
    )

    assert json.loads(row.case_pins) == case_pins
    assert row.pins_sha256 == ledger.pins_sha256
    assert len(row.pins_sha256) == 64


def test_the_trajectory_is_kept_with_its_arguments(unpriced: None) -> None:
    """Arguments and not just names: an argument check reads them, and so does a reader."""
    ledger = RunLedger(arm="baseline", split="train")

    row = ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(
            trajectory=[{"name": "plot_timeseries", "args": {"series": [{"roof": "a"}]}}]
        ),
        candidate=baseline_texts(),
    )

    assert json.loads(row.trajectory) == [
        {"name": "plot_timeseries", "args": {"series": [{"roof": "a"}]}}
    ]


def test_cost_is_the_reported_tokens_at_the_repositorys_own_prices(
    priced: None,
) -> None:
    """1000 prompt tokens at €1/Mtok plus 500 completion at €2/Mtok is €0.002."""
    ledger = RunLedger(arm="baseline", split="train")

    row = ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(),
        candidate=baseline_texts(),
    )

    assert row.cost_eur == pytest.approx(1000 * 1.0 / 1e6 + 500 * 2.0 / 1e6)
    assert ledger.total_cost() == pytest.approx(row.cost_eur)


def test_an_unpriced_run_reports_no_cost_rather_than_zero(unpriced: None) -> None:
    """0 is a number. A run whose cost was never measured has to say so."""
    ledger = RunLedger(arm="baseline", split="train")

    row = ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(),
        candidate=baseline_texts(),
    )

    assert row.cost_eur is None
    assert ledger.total_cost() is None
    assert "cost unpriced" in ledger.summary()


# ── The seam: one rollout, one row ───────────────────────────────────────────


def test_a_rollout_that_raised_is_still_a_row_and_still_raises(
    registry: str, unpriced: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger observes and never intervenes, exactly as the residual one does.

    The row exists — a rollout that fell over is a fact about the run — and the
    exception continues on to ``guarded`` and to ``optimize_prompts``, which is
    what still scores the record 0 as a declared residual.
    """
    versions = register_candidates(baseline_texts())

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the endpoint fell over")

    monkeypatch.setattr("harness.predict.run_case", explode)
    ledger = RunLedger(arm="baseline", split="train")
    rollout = ledgered(ledger, versions=versions)

    with pytest.raises(RuntimeError, match="fell over"):
        rollout({"case_id": "T01-0001", "template_id": "T01"})

    (row,) = ledger.rows
    assert row.case_id == "T01-0001"
    assert row.harness_error is True
    assert row.parse_failure is True
    assert row.status is None


def test_every_rollout_of_a_search_is_ledgered(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
    priced: None,
) -> None:
    """One row per rollout, filed under the candidate that produced it.

    Driven through ``run_search`` rather than through the seam directly, because
    the claim is that a *search* keeps this ledger — an argument dropped in the
    wiring would leave both the seam and its test looking right.
    """
    versions = register_candidates(baseline_texts())

    search = run_search(limit=3, max_metric_calls=4, versions=versions, cases_dir=cases_dir)

    rows = search.ledger.rows
    assert len(rows) == 3
    assert {row.case_id for row in rows} == {
        record["inputs"]["case_id"] for record in entry_point.kwargs["train_data"]
    }
    assert {row.candidate_id for row in rows} == {candidate_id(baseline_texts())}
    assert all(row.arm == "optimized" and row.split == "train" for row in rows)


def test_the_ledger_and_the_pins_land_in_the_active_run(
    registry: str, priced: None
) -> None:
    """The table and the whole pin file, read back off the run that holds them.

    Read back rather than asserted at the call site: a digest per row identifies
    a pin set, and only the committed file says what was in it, so the file has
    to actually arrive.
    """
    ledger = RunLedger(arm="baseline", split="test_seen", repeat=2)
    ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(),
        candidate=baseline_texts(),
        served=("qwen3.6-35b-a3b",),
    )

    with experiment_run("ledger-test") as run:
        ledger.log()

    table = mlflow.load_table(LEDGER_ARTIFACT, run_ids=[run.info.run_id])
    assert list(table["case_id"]) == ["T01-0001"]
    assert list(table["served_model_id"]) == ["qwen3.6-35b-a3b"]
    assert list(table["repeat"]) == [2]

    logged = mlflow.artifacts.load_dict(f"{run.info.artifact_uri}/{PINS_ARTIFACT}")
    assert logged == ledger.pins
    params = mlflow.get_run(run.info.run_id).data.params
    assert params["ledger.split"] == "test_seen"
    assert params["ledger.served_model_ids"] == "qwen3.6-35b-a3b"


def test_the_ledger_settings_are_the_runs_settings() -> None:
    """The requested model id is read off the settings the run was given, not the process."""
    settings = AssistantSettings(root_agent_model="openai/some-other-model")
    ledger = RunLedger(arm="baseline", split="train", settings=settings)

    row = ledger.record(
        inputs={"case_id": "T01-0001", "template_id": "T01"},
        outputs=outputs(),
        candidate=baseline_texts(),
    )

    assert row.requested_model_id == "openai/some-other-model"
