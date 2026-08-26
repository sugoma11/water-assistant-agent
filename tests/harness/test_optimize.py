"""The search wiring, and the four things it must hand the entry point (T126).

``optimize_prompts`` is called with a ``predict_fn``, the candidate uris, the
scorers and the aggregation, and the last of those is the one that fails
quietly: omit it and the objective silently becomes the unweighted mean of the
numeric scorer values, right up to the first case where a metric skips. So it is
**asserted to be passed** rather than trusted — once by
:func:`~harness.optimize.preflight` before a search spends anything, and once
here against the arguments the entry point actually received.

The entry point itself is stubbed in these tests. What a real call does is run
GEPA, and that is the smoke run's job (``just search-smoke``); what is checked
here is everything decided *around* it — the record mode, the cache switch, the
pre-filtered records, the reflection binding and the read assertion — because
each of those is a decision that would otherwise only surface as a number that
looked plausible. The stub itself lives in ``conftest.py``, because
``test_ledger.py`` drives the same search for a different question (T127).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import litellm
import pytest

from harness import optimize as optimize_module
from harness.candidates import (
    CANDIDATE_COMPONENTS,
    PROMPT_NAMES,
    baseline_texts,
    register_candidates,
)
from harness.optimize import (
    AggregationNotWiredError,
    preflight,
    run_search,
    search_llm_cache,
)
from harness.scorers import SCORERS
from harness.scoring import METRICS, aggregate_scores
from harness.train_data import load_split
from tests.harness.conftest import Recorded, SearchOutcome
from water_assistant_agent.assistant.settings import AssistantSettings, get_settings

# ── The aggregation is asserted, not assumed ─────────────────────────────────


def test_the_preflight_passes_on_this_repos_callable() -> None:
    """The check that runs before a search spends anything.

    Two halves: the object is ``aggregate_scores`` itself, so the weights are the
    pre-registered ones; and the installed mlflow, given this repo's scorers and
    that callable, turns a two-skip record into a number. The second is the half
    an identity check cannot make — it holds against the library actually
    installed.
    """
    preflight(aggregate_scores)


def test_the_preflight_refuses_anything_else() -> None:
    """Including a callable that would happen to work, which is the point.

    The failure mode is not "the objective raises" — it is "the objective is a
    weighting nobody chose". A stand-in that computes a perfectly reasonable
    number is exactly the thing that must not pass.
    """
    with pytest.raises(AggregationNotWiredError, match="aggregate_scores"):
        preflight(None)
    with pytest.raises(AggregationNotWiredError, match="aggregate_scores"):
        preflight(lambda scores: 1.0)


def test_the_entry_point_is_handed_this_repos_aggregation(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """The packet's exit criterion at the call site: the callable really is passed.

    Read off the arguments ``optimize_prompts`` received rather than off the
    source, because "we pass it" is the claim and an argument dropped in a
    refactor would leave the source looking right.
    """
    register_candidates(baseline_texts())

    run_search(limit=2, max_metric_calls=4, cases_dir=cases_dir)

    assert entry_point.kwargs["aggregation"] is aggregate_scores
    assert [scorer.name for scorer in entry_point.kwargs["scorers"]] == list(METRICS)
    assert entry_point.kwargs["scorers"] == list(SCORERS)


def test_the_candidate_uris_are_the_seven_pinned_components(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """One uri per optimizable component, at the pinned version — never one blob."""
    versions = register_candidates(baseline_texts())

    run_search(limit=1, max_metric_calls=4, versions=versions, cases_dir=cases_dir)

    uris = entry_point.kwargs["prompt_uris"]
    assert len(uris) == len(CANDIDATE_COMPONENTS)
    assert set(uris) == {
        f"prompts:/{PROMPT_NAMES[component]}/{versions[component]}"
        for component in CANDIDATE_COMPONENTS
    }


def test_the_adapter_satisfies_mlflows_own_signature_check() -> None:
    """The shape the entry point demands, checked with the entry point's checker.

    ``convert_predict_fn`` validates a ``predict_fn``'s signature against the
    record's ``inputs`` keys and then calls it as ``predict_fn(**request)``, so a
    callable taking one mapping is refused before a search starts — with a
    message about the dataset's shape rather than the function's. A ``**kwargs``
    signature satisfies it for any envelope, which is why the adapter has one.
    """
    from mlflow.genai.utils.data_validation import (
        _validate_input_keys_match_function_params,
    )
    from mlflow.exceptions import MlflowException

    from harness.predict import as_keyword_fn

    inputs = load_split("train")[0]["inputs"]
    adapted = as_keyword_fn(lambda mapping: dict(mapping))

    _validate_input_keys_match_function_params(
        inspect.signature(adapted).parameters, inputs.keys(), AssertionError()
    )
    assert adapted(**inputs) == inputs

    with pytest.raises(MlflowException, match="must be a dictionary"):
        _validate_input_keys_match_function_params(
            inspect.signature(lambda mapping: mapping).parameters,
            inputs.keys(),
            AssertionError(),
        )


# ── The search path is not the measurement path ──────────────────────────────


def test_the_search_runs_in_record_mode(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """§7's second protection, and it is an argument rather than a mechanism.

    A miss on a window the *candidate* chose records rather than fails, which is
    what lets the pre-filter answer only for the oracle's windows. The
    measurement run's default is the opposite and must stay so.
    """
    register_candidates(baseline_texts())

    run_search(limit=1, max_metric_calls=4, cases_dir=cases_dir)

    (rollout,) = scripted_rollouts
    assert rollout["allow_live"] is True


def test_the_llm_cache_is_on_inside_the_search_and_off_afterwards() -> None:
    """Both halves of the switch, because either alone is a cache that does nothing.

    ``configure_llm_cache`` installs ``litellm.cache``; ``litellm_extra`` sends
    ``caching`` on every request, and litellm consults the installed cache only
    when that is unset or ``True``. Turning on the first while the second says
    ``False`` gives a cache that is configured, logged and bypassed.
    """
    settings = AssistantSettings(llm_cache_enabled=False)

    with search_llm_cache(settings) as enabled:
        assert enabled is True
        assert settings.llm_cache_enabled is True
        assert settings.litellm_extra()["caching"] is True
        assert litellm.cache is not None

    assert settings.llm_cache_enabled is False
    assert settings.litellm_extra()["caching"] is False
    assert litellm.cache is None


def test_the_cache_is_put_back_even_when_the_search_raises() -> None:
    """Off is the measurement path's setting, so a failed search must not leave it on."""
    settings = AssistantSettings(llm_cache_enabled=False)

    with pytest.raises(RuntimeError, match="fell over"):
        with search_llm_cache(settings):
            raise RuntimeError("the search fell over")

    assert settings.llm_cache_enabled is False
    assert litellm.cache is None


def test_the_records_are_the_pre_filtered_ones(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """``train_data`` is what replayed, in §6.1's envelope, and never the raw file."""
    register_candidates(baseline_texts())

    search = run_search(limit=3, max_metric_calls=4, cases_dir=cases_dir)

    records = entry_point.kwargs["train_data"]
    assert len(records) == search.records == 3
    assert all({"inputs", "expectations"} <= set(record) for record in records)


def test_the_reflection_model_is_bound_for_the_duration(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """GEPA is handed the pinned uri, and the binding is gone once the search ends."""
    register_candidates(baseline_texts())
    original = litellm.completion

    search = run_search(limit=1, max_metric_calls=4, cases_dir=cases_dir)

    assert search.reflection_model == (
        get_settings().reflection_model.replace("/", ":/", 1)
    )
    assert entry_point.kwargs["optimizer"].reflection_model == search.reflection_model
    assert litellm.completion is original


def test_the_optimizer_is_gepa_with_the_budget_it_was_given(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """No adapter is written; the budget is pre-registered and passed straight through."""
    from mlflow.genai.optimize.optimizers import GepaPromptOptimizer

    register_candidates(baseline_texts())

    run_search(limit=1, max_metric_calls=7, cases_dir=cases_dir)

    optimizer = entry_point.kwargs["optimizer"]
    assert isinstance(optimizer, GepaPromptOptimizer)
    assert optimizer.max_metric_calls == 7
    # One key, and the narrowness is the assertion: `gepa_kwargs` is how the
    # per-component metaprompts reach the proposer (T133) and equally how a
    # `frontier_type` would reach the candidate selector — and selection runs on
    # the aggregated scalar, which is what makes the aggregation callable
    # load-bearing at all.
    assert set(optimizer.gepa_kwargs) == {"reflection_prompt_template"}
    assert "frontier_type" not in optimizer.gepa_kwargs


def test_the_proposer_is_told_which_kind_of_component_it_is_rewriting(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """The templates reach GEPA keyed by the names it dispatches on (T133).

    Read off the optimizer the entry point received, because the failure this
    guards against is silent at every layer: MLflow's merge would drop the key
    without a word if it set one of its own, and GEPA falls back to its default
    metaprompt — "instructions for an assistant", for a function declaration —
    with a single log line for a component it finds no template for.
    """
    from harness.metaprompt import reflection_prompt_templates

    register_candidates(baseline_texts())

    run_search(limit=1, max_metric_calls=4, cases_dir=cases_dir)

    templates = entry_point.kwargs["optimizer"].gepa_kwargs[
        "reflection_prompt_template"
    ]
    assert templates == reflection_prompt_templates()
    assert set(templates) == {
        PROMPT_NAMES[component] for component in CANDIDATE_COMPONENTS
    }


# ── What the search reports beside its score ─────────────────────────────────


def test_a_clean_search_reports_zero_residuals_and_zero_new_entries(
    registry: str,
    entry_point: Recorded,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """Zero is reported rather than omitted — that is what counting is for."""
    register_candidates(baseline_texts())

    search = run_search(limit=2, max_metric_calls=4, arm="smoke", cases_dir=cases_dir)

    assert search.residuals.arm == "smoke"
    assert search.residuals.residual_cases == 0
    assert search.residuals.recorded_entries == 0
    assert "0/2 residual case(s)" in search.summary()


def test_an_unread_candidate_component_fails_the_pass(
    registry: str,
    entry_point: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    cases_dir: Path,
) -> None:
    """A search whose rollouts read nothing optimized nothing, and says so.

    The framework's only signal is a log warning (``findings.md``), so the pass
    asserts instead. Here the entry point runs no rollout at all, which is the
    shape a ``predict_fn`` that never reached the registry would produce.
    """
    from harness.candidates import UnreadCandidateError

    register_candidates(baseline_texts())
    monkeypatch.setattr(
        optimize_module.mlflow.genai, "optimize_prompts", lambda **kwargs: SearchOutcome()
    )

    with pytest.raises(UnreadCandidateError):
        run_search(limit=1, max_metric_calls=4, cases_dir=cases_dir)


def test_a_split_with_nothing_captured_is_refused(
    registry: str, monkeypatch: pytest.MonkeyPatch, cases_dir: Path
) -> None:
    """Better than a search over zero records reporting a perfect score."""
    monkeypatch.setattr(optimize_module, "training_records", lambda *a, **k: [])

    with pytest.raises(ValueError, match="nothing to search over"):
        run_search(limit=1, max_metric_calls=4, cases_dir=cases_dir)
