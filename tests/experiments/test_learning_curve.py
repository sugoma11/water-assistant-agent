"""Offline asserts for the learning-curve probe and the meter's probe bucket.

No endpoint, no MLflow server: everything covered here is deterministic arithmetic and
bookkeeping — threshold advance, the content-hash cache, the ``--max-probes`` cap, the
three-bucket accounting identity and GEPA's best-candidate extraction. The LLM-in-the-
loop behaviour is verified by the per-technique smoke runs instead (see
``specs/cost-budget-stopping/learning-curve-plan.md`` §9); this file covers the part
that has an answer a machine can check.
"""

from typing import Any

import pytest

from experiments.text2sql.cost_meter import ROLES, CostMeter, PriceConfig
from experiments.text2sql.learning_curve import (
    LearningCurveProbe,
    NullProbe,
    build_probe,
    template_sha256,
)
from experiments.text2sql.train_gepa import gepa_best_template

# 1 EUR per 1M tokens in every role/direction, so 1M recorded tokens == 1.00 EUR and the
# arithmetic in these tests is readable by eye.
UNIT_PRICES = PriceConfig(
    task_input=1.0,
    task_output=1.0,
    judge_input=1.0,
    judge_output=1.0,
    optimizer_input=1.0,
    optimizer_output=1.0,
)

TEST_RECORD = {
    "inputs": {"question": "how much water?"},
    "expectations": {"sql": "SELECT 1", "argilla_link": ""},
}


def make_meter(budget: float = 10.0) -> CostMeter:
    return CostMeter(budget, UNIT_PRICES)


def spend(meter: CostMeter, eur: float, role: str = "task") -> None:
    """Charge ``eur`` to the meter through its normal recording path."""
    meter.record(role, int(eur * 1_000_000), 0)


class FakeProbe(LearningCurveProbe):
    """A probe whose evaluation is a scripted list instead of an LLM call, so the
    threshold/cache/cap logic can be exercised offline. Everything else — the real
    ``__init__``, the meter bucket, the point bookkeeping — is the production code."""

    def __init__(self, meter: CostMeter, scores: list[float], **kwargs: Any) -> None:
        super().__init__(
            meter=meter,
            interval_eur=kwargs.pop("interval_eur", 1.0),
            max_probes=kwargs.pop("max_probes", 20),
            workers=1,
            test_set=[TEST_RECORD],
            model="openai/fake",
            endpoint="kisski",
            judge_model="openai/fake",
            judge_endpoint="kisski",
            schema_text="",
            db_path="",
        )
        self._scores = list(scores)
        self.evaluated: list[str] = []

    def _evaluate(self, template: str) -> float:
        self.evaluated.append(template)
        # Charge a fixed 0.5 EUR of spend from inside meter.probing(), so bucket routing
        # is exercised rather than simulated.
        self._meter.record("task", 500_000, 0)
        return self._scores.pop(0) if self._scores else 0.0


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cut the two seams that would reach outside the process: the judge scorer (which
    opens a DuckDB connection and resolves endpoint credentials) and MLflow logging
    (there is no active run here)."""
    import mlflow

    from experiments.text2sql import learning_curve

    monkeypatch.setattr(
        learning_curve, "build_sql_judge_scorer", lambda *a, **k: (lambda **kw: None)
    )
    monkeypatch.setattr(mlflow, "log_metrics", lambda *a, **k: None)
    monkeypatch.setattr(mlflow, "log_text", lambda *a, **k: None)
    monkeypatch.setattr(mlflow, "active_run", lambda: None)


def build_fake(meter: CostMeter, scores: list[float], **kwargs: Any) -> FakeProbe:
    return FakeProbe(meter, scores, **kwargs)


# ---------------------------------------------------------------------------
# Meter: the third bucket (LC-D2, LC-SC4)
# ---------------------------------------------------------------------------
def test_probe_spend_never_charges_the_budget() -> None:
    meter = make_meter(budget=2.0)
    spend(meter, 1.0)
    with meter.probing():
        spend(meter, 5.0)  # a huge probe...
    assert meter.billable_cost == pytest.approx(1.0)  # ...changes nothing billable
    assert meter.probe_cost == pytest.approx(5.0)
    assert not meter.exhausted()


def test_bucket_precedence_is_probe_over_excluded_over_billable() -> None:
    meter = make_meter()
    spend(meter, 1.0)
    with meter.excluded():
        spend(meter, 2.0)
    with meter.probing():
        spend(meter, 4.0)
    summary = meter.spend_summary()
    assert summary["cost_total"] == pytest.approx(1.0)
    assert summary["cost_excluded"] == pytest.approx(2.0)
    assert summary["cost_probe"] == pytest.approx(4.0)
    # LC-SC4: the three buckets partition every metered call.
    per_role = sum(summary[f"cost_{role}"] for role in ROLES)
    assert per_role == pytest.approx(
        summary["cost_total"] + summary["cost_excluded"] + summary["cost_probe"]
    )
    assert summary["tokens_probe_input"] == 4_000_000


def test_probing_is_not_reentrant() -> None:
    meter = make_meter()
    with meter.probing():
        with pytest.raises(RuntimeError, match="not"):
            with meter.probing():
                pass


# ---------------------------------------------------------------------------
# Threshold arithmetic (LC-FR1, LC-EC3)
# ---------------------------------------------------------------------------
def test_probe_fires_once_per_interval() -> None:
    meter = make_meter()
    probe = build_fake(meter, [0.5, 0.6, 0.7], interval_eur=1.0)
    for template in ["a", "b", "c", "d", "e", "f"]:
        spend(meter, 0.4)  # billable: 0.4 0.8 1.2 1.6 2.0 2.4
        probe.maybe_probe(lambda t=template: t)
    # Boundaries 1.0 and 2.0 are crossed at "c" (1.2) and "e" (2.0); "f" (2.4) is below
    # the next boundary (3.0), so it does not probe. Probe spend itself is in its own
    # bucket, so it never pushes the curve's x-axis forward (LC-FR4).
    assert probe.evaluated == ["c", "e"]
    assert [p.nominal_eur for p in probe._points] == pytest.approx([1.0, 2.0])


def test_a_checkpoint_skipping_several_intervals_fires_one_probe() -> None:
    """LC-EC3: a GEPA iteration can cost more than K; the skipped boundaries are not
    back-filled and the point records its ACTUAL spend, not the boundary."""
    meter = make_meter()
    probe = build_fake(meter, [0.5], interval_eur=0.5)
    spend(meter, 2.6)
    probe.maybe_probe(lambda: "far")
    assert len(probe.evaluated) == 1
    point = probe._points[-1]
    assert point.spend_eur == pytest.approx(2.6)
    assert point.nominal_eur == pytest.approx(0.5)
    # Next threshold is the first multiple strictly above 2.6, not 1.0.
    assert probe._next_threshold == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Cache (LC-D5, LC-EC4)
# ---------------------------------------------------------------------------
def test_unchanged_prompt_is_not_rescored() -> None:
    meter = make_meter()
    probe = build_fake(meter, [0.42], interval_eur=1.0)
    spend(meter, 1.0)
    probe.maybe_probe(lambda: "same")
    cost_after_first = meter.probe_cost
    spend(meter, 1.0)
    probe.maybe_probe(lambda: "same")
    assert probe.evaluated == ["same"]  # scored once
    assert len(probe._points) == 2  # but recorded twice
    assert probe._points[-1].cache_hit is True
    assert probe._points[-1].test_quality == pytest.approx(0.42)
    assert probe._points[-1].probe_cost_eur == pytest.approx(0.0)
    assert probe._points[-1].prompt_changed is False
    assert meter.probe_cost == pytest.approx(cost_after_first)


def test_seed_curve_makes_an_unimproved_prompt_free() -> None:
    """LC-FR7 + LC-D5: the k=0 point reuses test_quality_before and seeds the cache, so
    probing a still-unimproved prompt costs nothing."""
    meter = make_meter()
    probe = build_fake(meter, [0.99], interval_eur=1.0)
    probe.seed_curve(0.60, "SEED")
    spend(meter, 1.5)
    probe.maybe_probe(lambda: "SEED")
    assert probe.evaluated == []
    assert probe._points[-1].test_quality == pytest.approx(0.60)
    assert meter.probe_cost == pytest.approx(0.0)


def test_seed_cache_key_matches_what_the_optimizers_probe() -> None:
    """The k=0 point is seeded with SYSTEM_PROMPT_TEMPLATE while TextGrad and SkillOpt
    probe ``recombine(<instruction block>)``. The free-seed-probe path (LC-D5) only works
    because that round-trip is byte-identical for the seed — assert it, so a future edit
    to the template or the marker cannot silently turn every seed probe into a paid
    55-record evaluation."""
    from water_assistant_agent.text2sql.core import SYSTEM_PROMPT_TEMPLATE
    from experiments.text2sql.prompt_skill import instruction_block, recombine

    assert recombine(instruction_block(SYSTEM_PROMPT_TEMPLATE)) == SYSTEM_PROMPT_TEMPLATE

    meter = make_meter()
    probe = build_fake(meter, [0.99], interval_eur=1.0)
    probe.seed_curve(0.60, SYSTEM_PROMPT_TEMPLATE)
    spend(meter, 1.0)
    probe.maybe_probe(lambda: recombine(instruction_block(SYSTEM_PROMPT_TEMPLATE)))
    assert probe.evaluated == []
    assert probe._points[-1].cache_hit is True


def test_prompt_changed_tracks_the_previous_point() -> None:
    meter = make_meter()
    probe = build_fake(meter, [0.5, 0.6], interval_eur=1.0)
    probe.seed_curve(0.4, "SEED")
    spend(meter, 1.0)
    probe.maybe_probe(lambda: "V1")
    spend(meter, 1.0)
    probe.maybe_probe(lambda: "V1")
    changed = [p.prompt_changed for p in probe._points]
    assert changed == [True, True, False]
    assert probe._points[1].prompt_sha256 == template_sha256("V1")


# ---------------------------------------------------------------------------
# Robustness (LC-FR9, LC-EC6, LC-EC7)
# ---------------------------------------------------------------------------
def test_max_probes_caps_the_instrumentation_bill() -> None:
    meter = make_meter()
    probe = build_fake(meter, [0.1, 0.2, 0.3, 0.4], interval_eur=1.0, max_probes=2)
    for i in range(4):
        spend(meter, 1.0)
        probe.maybe_probe(lambda i=i: f"v{i}")
    assert len(probe.evaluated) == 2
    assert probe.summary()["probe_cap_reached"] == 1.0


def test_a_failing_probe_does_not_break_the_run() -> None:
    meter = make_meter()
    probe = build_fake(meter, [], interval_eur=1.0)

    def boom() -> str:
        raise RuntimeError("endpoint down")

    spend(meter, 1.0)
    probe.maybe_probe(boom)  # must not raise
    assert probe.summary()["probe_failures"] == 1.0


def test_unresolvable_best_prompt_is_a_hole_not_a_crash() -> None:
    meter = make_meter()
    probe = build_fake(meter, [], interval_eur=1.0)
    spend(meter, 1.0)
    probe.maybe_probe(lambda: None)
    assert probe.evaluated == []
    assert probe.summary()["probe_failures"] == 1.0


# ---------------------------------------------------------------------------
# Disabled path (LC-FR8, LC-EC1)
# ---------------------------------------------------------------------------
def test_build_probe_disabled_by_default_and_on_empty_test_split() -> None:
    meter = make_meter()
    common = dict(
        meter=meter,
        max_probes=20,
        workers=4,
        model="openai/x",
        endpoint="kisski",
        judge_model="openai/x",
        judge_endpoint="kisski",
        schema_text="",
        db_path="",
    )
    assert isinstance(build_probe(interval_eur=0.0, test_set=[TEST_RECORD], **common), NullProbe)
    assert isinstance(build_probe(interval_eur=0.5, test_set=[], **common), NullProbe)


def test_null_probe_is_inert() -> None:
    probe = NullProbe()
    probe.seed_curve(0.5, "seed")
    probe.maybe_probe(lambda: "x")
    probe.close_curve(0.6, "final")
    assert probe.summary() == {}
    assert probe.params() == {"probe_interval_eur": 0.0}


# ---------------------------------------------------------------------------
# GEPA best-candidate extraction (LC-FR2, LC-R2)
# ---------------------------------------------------------------------------
class FakeGepaState:
    def __init__(self, candidates: list[dict[str, str]], subscores: list[dict[int, float]]):
        self.program_candidates = candidates
        self.prog_candidate_val_subscores = subscores


def test_gepa_best_template_picks_the_highest_mean() -> None:
    state = FakeGepaState(
        [{"p": "seed"}, {"p": "better"}, {"p": "worse"}],
        [{0: 0.5, 1: 0.5}, {0: 1.0, 1: 0.5}, {0: 0.0, 1: 0.0}],
    )
    assert gepa_best_template(state, "p") == "better"


def test_gepa_best_template_breaks_ties_on_coverage() -> None:
    """Mirrors FullEvaluationPolicy: equal means -> the more fully evaluated candidate,
    so a candidate scored on one lucky record cannot outrank a fully evaluated one."""
    state = FakeGepaState(
        [{"p": "one_record"}, {"p": "fully_evaluated"}],
        [{0: 1.0}, {0: 1.0, 1: 1.0, 2: 1.0}],
    )
    assert gepa_best_template(state, "p") == "fully_evaluated"


def test_gepa_best_template_returns_none_on_an_unfamiliar_state() -> None:
    """LC-R2: a gepa version bump degrades to a warned, skipped curve point rather than
    taking the training run down with it."""
    assert gepa_best_template(object(), "p") is None
    assert gepa_best_template(FakeGepaState([], []), "p") is None
    assert gepa_best_template(FakeGepaState([{"other": "x"}], [{0: 1.0}]), "p") is None


# ---------------------------------------------------------------------------
# MLflow surface (LC-FR6, LC-SC2) -- against a temp file store, no server
# ---------------------------------------------------------------------------
def test_curve_is_logged_as_metrics_and_artifact(tmp_path, monkeypatch) -> None:
    """The unit tests above stub MLflow out; this one exercises the real logging path:
    metric steps are spend in cents (LC-D4) and learning_curve.json is written after
    every point, so a killed run keeps the points it paid for."""
    import json

    import mlflow

    from experiments.text2sql import learning_curve

    monkeypatch.undo()  # drop the autouse offline stubs for mlflow (keep the judge stub)
    monkeypatch.setattr(
        learning_curve, "build_sql_judge_scorer", lambda *a, **k: (lambda **kw: None)
    )
    # sqlite + an explicit artifact dir: the file store is refused by current mlflow, and
    # the sqlite store would otherwise drop artifacts into ./mlruns in the repo.
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    mlflow.set_experiment(
        experiment_id=mlflow.create_experiment(
            "learning-curve-test", artifact_location=f"file://{tmp_path}/artifacts"
        )
    )

    meter = make_meter()
    with mlflow.start_run() as run:
        probe = build_fake(meter, [0.75], interval_eur=1.0)
        mlflow.log_params(probe.params())
        probe.seed_curve(0.50, "SEED")
        spend(meter, 1.2)
        probe.maybe_probe(lambda: "IMPROVED")
        probe.close_curve(0.80, "FINAL")

    client = mlflow.MlflowClient()
    history = client.get_metric_history(run.info.run_id, "curve_test_quality")
    points = sorted((m.step, m.value) for m in history)
    # steps are spend in cents: 0 EUR, 1.2 EUR, and the final billable total (1.2)
    assert [s for s, _ in points] == [0, 120, 120]
    assert [v for _, v in points] == pytest.approx([0.50, 0.75, 0.80])

    finished = client.get_run(run.info.run_id)
    assert finished.data.params["probe_interval_eur"] == "1.0"
    assert finished.data.params["probe_prompt_selection"] == "best_so_far"
    assert finished.data.metrics["probe_count"] == 1.0

    artifact = mlflow.artifacts.download_artifacts(
        run_id=run.info.run_id, artifact_path=learning_curve.CURVE_ARTIFACT
    )
    with open(artifact) as f:
        payload = json.load(f)
    assert [p["source"] for p in payload["points"]] == ["test_before", "probe", "test_after"]
    assert payload["points"][1]["spend_eur"] == pytest.approx(1.2)
    assert payload["points"][1]["probe_cost_eur"] == pytest.approx(0.5)
    assert payload["test_size"] == 1


# ---------------------------------------------------------------------------
# The evaluation body (LC-FR3, LC-EC6, LC-OQ2)
# ---------------------------------------------------------------------------
class FakeFeedback:
    def __init__(self, value: bool, error: Exception | None = None) -> None:
        self.value = value
        self.error = error
        self.rationale = ""


def test_evaluate_scores_the_split_and_degrades_honestly(monkeypatch) -> None:
    """A failed predict call scores 0 (as mlflow's own eval loop does) and a judge error
    scores 0 *and* is counted, so a degraded point is visible rather than silently
    pessimistic. The pass-rate is the mean over the split."""
    from experiments.text2sql import learning_curve

    records = [
        {"inputs": {"question": f"q{i}"}, "expectations": {"sql": "SELECT 1"}}
        for i in range(4)
    ]

    def fake_predict_fn(model, endpoint, schema_text, system_prompt_template=None):
        def predict(question: str) -> dict:
            if question == "q3":
                raise RuntimeError("endpoint dropped the connection")
            return {"sql": f"SELECT '{question}'"}

        return predict

    def fake_judge(*, inputs, outputs, expectations):
        question = inputs["question"]
        if question == "q2":
            return FakeFeedback(False, error=RuntimeError("judge unreachable"))
        return FakeFeedback(question == "q0")

    monkeypatch.setattr(learning_curve, "create_predict_fn", fake_predict_fn)
    monkeypatch.setattr(learning_curve, "build_sql_judge_scorer", lambda *a, **k: fake_judge)

    meter = make_meter()
    probe = LearningCurveProbe(
        meter=meter,
        interval_eur=1.0,
        max_probes=5,
        workers=2,
        test_set=records,
        model="openai/fake",
        endpoint="kisski",
        judge_model="openai/fake",
        judge_endpoint="kisski",
        schema_text="",
        db_path="",
    )
    # q0 correct, q1 incorrect, q2 judge error -> 0, q3 predict failure -> 0
    assert probe._evaluate("TEMPLATE") == pytest.approx(0.25)
    assert probe.summary()["probe_judge_errors"] == 1.0
