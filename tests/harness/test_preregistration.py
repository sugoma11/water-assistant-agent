"""The pre-registration, and what makes it one rather than a note (T129).

§7's last reporting rule is a protocol: budget and hyperparameters are fixed
before any test run, method debugging happens on train, and every arm is reported
on test. What is tested here is that the commitment is *checkable* — the
registered values are the ones the code would use, a deviating run is named as
deviating, and the digest travels with the run so a number can be traced to the
registration it was produced under.

The registration itself is data, so several of these read the committed file. That
is deliberate: a test asserting a registration against a fixture would pass while
the committed one said something else, which is the only failure that matters
here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.optimize import DEFAULT_MAX_METRIC_CALLS, run_search
from harness.preregistration import (
    MEASUREMENT,
    PREREG_FILE,
    SEARCH,
    PreregistrationViolation,
    deviations,
    digest,
    enforce,
    load,
    run_params,
)
from harness.scoring import METRICS, SELECTION_WEIGHTS
from water_assistant_agent.assistant.settings import get_settings


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    """A registration of this shape, somewhere the committed one is not."""
    path = tmp_path / "preregistration.json"
    path.write_text(
        json.dumps({"search": {"max_metric_calls": 100, "split": "train"}}),
        encoding="utf-8",
    )
    return path


# ── The registered values are the ones the code uses ─────────────────────────


def test_the_registered_selection_weights_are_the_modules_own() -> None:
    """The weights are part of the method, so the two statements of them must agree.

    ``aggregate_scores`` raises on a scorer it has no weight for, so the module's
    table is what a search actually selects on; the registration is what was
    promised. A drift between them would leave a run selecting on one weighting
    and reporting another.
    """
    registered = load()["search"]["selection_weights"]

    assert registered == dict(SELECTION_WEIGHTS)
    assert set(registered) == set(METRICS)
    assert sum(registered.values()) == pytest.approx(1.0)


def test_the_registered_budget_is_the_modules_default() -> None:
    """A budget registered as 100 and defaulted at 60 is not a registered budget."""
    assert load()["search"]["max_metric_calls"] == DEFAULT_MAX_METRIC_CALLS


def test_the_registered_decoding_is_the_pinned_decoding() -> None:
    """One pinned seed at temperature 0 — the same numbers ``eval/pins.json`` carries."""
    settings = get_settings()
    measurement = load()[MEASUREMENT]

    assert measurement["temperature"] == settings.llm_temperature
    assert measurement["seed"] == settings.llm_seed
    # Off is the measurement path's setting and also the process default, so a
    # run that forgot to turn it off would still be the registered one.
    assert measurement["llm_cache"] == "off"
    assert settings.llm_cache_enabled is False


def test_the_registered_reflection_model_is_the_configured_one() -> None:
    """The second model a search depends on, and the one no rollout would notice."""
    assert load()[SEARCH]["reflection_model"] == get_settings().reflection_model


def test_the_metaprompts_are_not_in_this_registration_and_a_search_says_so() -> None:
    """The two templates are search hyperparameters, and this registration is spent.

    T133's per-component metaprompts change what every proposal is asked for, so
    a search before them and one after are not comparable — which makes them a
    *new* registration's business rather than an amendment to the one the P8c run
    was measured under. Amending it would also be an amendment made after a test
    rollout, which is the one thing every entry in ``amendments`` evidences it is
    not.

    Until the rerun's registration (T134) records
    :func:`~harness.metaprompt.templates_digest` under ``search.gepa_kwargs``, a
    search reports the difference in its own record — the asymmetry §6 already
    runs on: reported for a search, refused for a measurement. So this test fails
    the moment they are registered, and the fix then is to assert the registered
    digest rather than its absence.
    """
    from harness.metaprompt import templates_digest
    from harness.optimize import gepa_kwargs_summary, search_gepa_kwargs

    assert load()[SEARCH]["gepa_kwargs"] == {}

    (line,) = deviations(
        SEARCH, {"gepa_kwargs": gepa_kwargs_summary(search_gepa_kwargs())}
    )
    assert "search.gepa_kwargs" in line
    assert templates_digest() in line


def test_the_registration_covers_every_reporting_rule_of_section_7() -> None:
    """Each rule is a registered key, so none can be quietly dropped at write-up.

    Named individually rather than counted: the failure this guards against is a
    rule going missing, and a count would pass on a rule replaced by another.
    """
    analysis = load()["analysis"]

    assert analysis["bootstrap"]["unit"] == "template_id"
    assert analysis["repeat_reduction"].startswith("mean over the repeats per case")
    assert analysis["train_number"] == "selection score"
    assert analysis["gaps"] == ["train->test_seen", "test_seen->test_unseen"]
    assert "no interval" in analysis["test_unseen_trajectory"]
    assert "never blended" in analysis["abstention"]
    assert "mean_extra_calls" in analysis["diagnostics"]
    assert "apart from answer accuracy" in analysis["parse_failure"]


def test_an_amendment_is_appended_with_its_reason_and_its_cost() -> None:
    """Never a value rewritten in place: the digest moves either way, only one leaves a record.

    An amendment made *before any test rollout* is legitimate — nothing it could
    be responding to exists yet — and that is exactly what has to be evidenced,
    so every entry states it. What it must never do is arrive quietly: an
    amendment that gave up a reporting rule has to say which, and why, and what
    the run no longer measures because of it.
    """
    for amendment in load()["amendments"]:
        assert amendment["before_any_test_rollout"] is True
        assert amendment["what"] and amendment["why"] and amendment["cost"]
        assert amendment["not_amended"]


def test_a_run_of_one_repeat_measures_no_noise_floor_and_the_registration_says_so() -> None:
    """The cost of amendment 1, written where a reader of the numbers will meet it.

    Three repeats were the replication. One repeat cannot disagree with itself,
    so the quantity every arm difference was to be read against is *unmeasured* —
    which is a different claim from zero, and the registration is where the
    difference is stated rather than left to whoever reads the report.
    """
    registered = load()

    assert registered["measurement"]["repeats"] == 1
    assert registered["analysis"]["residual_nondeterminism"].startswith("NOT MEASURED")
    assert registered["measurement"]["llm_cache"] == "off"


def test_the_protocol_records_where_debugging_happened_and_what_is_reported() -> None:
    """"Never best of" is the whole of what the missing fourth split is replaced by."""
    protocol = load()["protocol"]

    assert "train only" in " ".join(protocol["method_debugging"])
    assert "never" in " ".join(protocol["reporting"]).lower()
    assert "not stopped early" in " ".join(protocol["stopping"])


def test_the_baseline_arms_known_defect_is_registered_in_advance() -> None:
    """The 0.50 abstention entry is a choice, and it has to be one *before* the run.

    T107 left T17a's contract-encoding failure for the search because the encoding
    lives in the optimizable text. Registering that in advance is what stops a
    candidate repairing it from being read afterwards as a baseline quietly
    weakened to make room for an improvement.
    """
    baseline = " ".join(load()["arms"]["baseline"])

    assert "0.50 abstention accuracy" in baseline
    assert "T17a" in baseline


def test_the_optimized_arm_is_one_searchs_selection_and_not_a_best_of() -> None:
    """The arm is defined before the search runs, so no result can redefine it."""
    optimized = " ".join(load()["arms"]["optimized"])

    assert "Not the best of several" in optimized
    assert load()[SEARCH]["runs"] == 1


# ── A deviating run says so ──────────────────────────────────────────────────


def test_the_registered_parameters_deviate_from_nothing(elsewhere: Path) -> None:
    assert deviations(SEARCH, {"max_metric_calls": 100}, elsewhere) == []


def test_a_changed_parameter_is_named_with_both_values(elsewhere: Path) -> None:
    """Both values, because "deviates" without them is a sentence nobody can act on."""
    (line,) = deviations(SEARCH, {"max_metric_calls": 8}, elsewhere)

    assert "search.max_metric_calls" in line
    assert "registered 100" in line
    assert "ran 8" in line


def test_a_parameter_nobody_registered_is_itself_a_deviation(elsewhere: Path) -> None:
    """A parameter outside the registration is a parameter nobody committed to."""
    (line,) = deviations(SEARCH, {"reflection_model": "openai/x"}, elsewhere)

    assert "not pre-registered" in line


def test_a_deviating_run_is_refused_unless_it_declares_itself(
    elsewhere: Path,
) -> None:
    """The measurement driver's rule: a test number under an unregistered budget.

    Declaring the run exploratory does not make the deviation go away — it is
    still returned and still logged. The flag exists so that silence never has to
    be interpreted as either.
    """
    with pytest.raises(PreregistrationViolation, match="max_metric_calls"):
        enforce(SEARCH, {"max_metric_calls": 8}, path=elsewhere)

    declared = enforce(SEARCH, {"max_metric_calls": 8}, exploratory=True, path=elsewhere)
    assert len(declared) == 1


def test_a_missing_registration_is_not_an_empty_one(tmp_path: Path) -> None:
    """Defaulting to "anything goes" would be the failure wearing a success."""
    with pytest.raises(FileNotFoundError, match="not a registered run"):
        load(tmp_path / "nothing.json")


# ── The digest travels with the run ──────────────────────────────────────────


def test_the_digest_moves_when_the_registration_is_edited(tmp_path: Path) -> None:
    """An amendment has to be visible, or a registration is editable in place."""
    path = tmp_path / "preregistration.json"
    path.write_text('{"search": {"max_metric_calls": 100}}', encoding="utf-8")
    before = digest(path)
    path.write_text('{"search": {"max_metric_calls": 200}}', encoding="utf-8")

    assert digest(path) != before
    assert len(before) == 64


def test_the_run_params_carry_the_digest_and_the_deviations() -> None:
    """What a reader needs to tell which registration a number was produced under."""
    params = run_params(SEARCH, {"max_metric_calls": DEFAULT_MAX_METRIC_CALLS})

    assert params["prereg.sha256"] == digest(PREREG_FILE)
    assert params["prereg.deviations"] == "none"
    assert params["prereg.exploratory"] == "false"

    smoke = run_params(SEARCH, {"max_metric_calls": 8}, exploratory=True)
    assert "max_metric_calls" in smoke["prereg.deviations"]
    assert smoke["prereg.exploratory"] == "true"


def test_a_search_records_its_own_deviations(
    registry: str,
    entry_point: Any,
    scripted_rollouts: list[dict[str, Any]],
    cases_dir: Path,
) -> None:
    """A smoke search is self-labelling: it runs, and its record says what it was.

    Driven through ``run_search`` because the claim is about the search, not
    about the checker — a param dropped in the wiring would leave both the
    checker and its own test looking right.
    """
    import mlflow

    from harness.candidates import baseline_texts, register_candidates
    from harness.ledger import EXPERIMENT

    versions = register_candidates(baseline_texts())

    run_search(limit=2, max_metric_calls=4, versions=versions, cases_dir=cases_dir)

    experiment = mlflow.get_experiment_by_name(EXPERIMENT)
    (run,) = mlflow.search_runs(
        [experiment.experiment_id], output_format="list", max_results=1
    )
    params = run.data.params
    assert params["prereg.sha256"] == digest(PREREG_FILE)
    assert (
        f"search.max_metric_calls: registered {DEFAULT_MAX_METRIC_CALLS}, ran 4"
        in params["prereg.deviations"]
    )
    assert "search.limit: registered None, ran 2" in params["prereg.deviations"]
