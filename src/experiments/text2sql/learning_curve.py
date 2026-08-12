"""Periodic held-out test evaluation every K EUR of training spend (learning curves).

The cost-budget feature made money the *stopping* criterion; this module makes money the
**x-axis of a quality curve**. Every ``K`` EUR of billable spend, the running
optimization is paused at its next natural checkpoint and its **best-so-far** prompt is
scored on the held-out test split, so GEPA, TextGrad and SkillOpt can be compared as
functions of spend rather than only at their end points (plan
``specs/cost-budget-stopping/learning-curve-plan.md``).

Without this, a run records exactly two points on the generalization axis —
``test_quality_before`` (seed, 0 EUR) and ``test_quality_after`` (final, ``cost_total``
EUR) — because everything logged in between is scored on val, which under the 20 / 0 / 55
scheme *is* the train split (``train_common.SPLIT_SCHEME``) and measures no
generalization at all.

Design, in one paragraph (LC-D1): this module owns all of it — threshold arithmetic, the
evaluation, the content-hash cache, MLflow logging and the ``learning_curve.json``
artifact. Each technique contributes exactly one line at its existing budget checkpoint,
saying *"here is my best-so-far prompt"*: GEPA via the ``BudgetStopper`` callback,
TextGrad at its per-gradient-step check, SkillOpt at its per-rollout check. Probe spend
is metered into the meter's separate ``probe`` bucket, so instrumentation never charges
the budget (LC-FR4) yet is always recorded (LC-FR5); a probed run therefore buys exactly
the same optimization work as an unprobed one.

**Reporting only (LC-R1).** Nothing measured here feeds back into optimization or
candidate selection — the prompt a run returns is still the one its own val/gate
mechanism chose. The curve exists to show the shape of quality-per-EUR; picking the
best-scoring probe point post hoc would be test-set selection.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

import mlflow

from experiments.text2sql.cost_meter import CostMeter
from experiments.text2sql.harness import build_sql_judge_scorer, create_predict_fn

logger = logging.getLogger(__name__)

# The artifact every study run leaves behind; consumed by scripts/export_learning_curves.py.
CURVE_ARTIFACT = "learning_curve.json"

# Recorded as the ``probe_prompt_selection`` param so a later change of policy is visible
# in the run record rather than implicit in the code (LC-D3).
PROMPT_SELECTION = "best_so_far"


def template_sha256(template: str) -> str:
    """Content hash identifying which prompt a curve point measured (LC-SC5), and the
    key of the probe's score cache (LC-D5)."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


@dataclass
class CurvePoint:
    """One (spend, quality) observation. ``nominal_eur`` is the ``k * K`` boundary that
    triggered it (0 / ``cost_total`` for the endpoints) while ``spend_eur`` is the actual
    billable spend when it fired — they differ by the checkpoint overshoot (LC-EC3)."""

    spend_eur: float
    nominal_eur: float
    test_quality: float
    probe_cost_eur: float
    prompt_sha256: str
    prompt_changed: bool
    cache_hit: bool
    source: str  # "test_before" | "probe" | "test_after"
    wall_seconds: float


@dataclass
class NullProbe:
    """The disabled probe (LC-FR8): every method is a no-op, so a run without
    ``--probe-interval-eur`` behaves byte-identically to a pre-feature run. Also what
    ``build_probe`` returns when the test split is empty (LC-EC1)."""

    enabled: bool = field(default=False, init=False)

    def params(self) -> dict[str, Any]:
        """Recorded even when disabled, so every run states whether it carries a curve
        (a missing param would be indistinguishable from an old run)."""
        return {"probe_interval_eur": 0.0}

    def seed_curve(self, quality: float, seed_template: str) -> None:
        return None

    def maybe_probe(self, best_template_fn: Callable[[], str | None]) -> None:
        return None

    def close_curve(self, quality: float, final_template: str) -> None:
        return None

    def summary(self) -> dict[str, float]:
        return {}


class LearningCurveProbe:
    """Evaluates the best-so-far prompt on the held-out test split every ``K`` EUR.

    Args:
        meter: the run's money meter. ``billable_cost`` is the curve's x-axis and the
            threshold input; ``probing()`` is the bucket probe spend lands in (LC-D2).
        interval_eur: ``K`` — EUR of billable spend between probes.
        max_probes: safety cap on the instrumentation bill (LC-EC7). A probe of the
            55-record split costs ≈ 0.35 EUR at current prices, so an accidentally tiny
            ``K`` could otherwise outspend the training run several times over.
        workers: probe-evaluation thread pool size (LC-D7). Defaults to 1, which keeps a
            probe as sequential as the bracketing eval phases whose axis it shares — at
            ≈ 35 min per 55-record point against the endpoints we use. Raising it trades
            peak endpoint concurrency for wall clock and cannot change the scores
            (per-call sampling params are constants, the meter is lock-guarded and the
            judge opens a per-call DuckDB cursor), but a checkpoint is the one moment
            nothing else is in flight, so the endpoint sees the full concurrency.
        test_set: the held-out split in MLflow eval format — the *same* records
            ``test_quality_{before,after}`` are measured on (LC-FR3).
        model / endpoint / judge_model / judge_endpoint / schema_text / db_path: the
            same task and judge roles the bracketing eval phases use, so every point of
            the curve sits on one axis.
    """

    enabled = True

    def __init__(
        self,
        *,
        meter: CostMeter,
        interval_eur: float,
        max_probes: int,
        workers: int,
        test_set: list[dict[str, Any]],
        model: str,
        endpoint: str,
        judge_model: str,
        judge_endpoint: str,
        schema_text: str,
        db_path: str,
    ) -> None:
        self._meter = meter
        self._interval = float(interval_eur)
        self._max_probes = int(max_probes)
        self._workers = max(1, int(workers))
        self._test_set = test_set
        self._model = model
        self._endpoint = endpoint
        self._schema_text = schema_text
        # Built once and reused across probes: the scorer opens a read-only DuckDB
        # connection, and one connection per probe would leak them over a long run.
        self._judge = build_sql_judge_scorer(
            judge_model, judge_endpoint, schema_text, db_path
        )
        self._next_threshold = self._interval
        self._points: list[CurvePoint] = []
        # sha256(template) -> test quality. Pre-seeded with test_quality_before so the
        # k=0 point is free and an early probe of a still-unimproved prompt costs
        # nothing (LC-D5, LC-EC4).
        self._cache: dict[str, float] = {}
        self._last_sha: str | None = None
        self._probe_count = 0
        self._cache_hits = 0
        self._failures = 0
        self._judge_errors = 0
        self._wall_seconds = 0.0
        self._cap_reached = False

    def params(self) -> dict[str, Any]:
        """Curve configuration, logged under identical names by every technique so the
        three runs of a comparison line up in the UI (LC-FR6)."""
        return {
            "probe_interval_eur": self._interval,
            "probe_test_size": len(self._test_set),
            "probe_workers": self._workers,
            "probe_max": self._max_probes,
            "probe_prompt_selection": PROMPT_SELECTION,
        }

    # -- curve endpoints (free: they reuse the bracketing phases, LC-FR7) ----
    def seed_curve(self, quality: float, seed_template: str) -> None:
        """Record the k=0 point from the already-paid ``test-before`` phase and seed the
        cache with it, so a probe firing while the best-so-far prompt is still the seed
        is answered for free."""
        sha = template_sha256(seed_template)
        self._cache[sha] = quality
        self._add_point(
            CurvePoint(
                spend_eur=0.0,
                nominal_eur=0.0,
                test_quality=quality,
                probe_cost_eur=0.0,
                prompt_sha256=sha,
                prompt_changed=True,
                cache_hit=False,
                source="test_before",
                wall_seconds=0.0,
            )
        )

    def close_curve(self, quality: float, final_template: str) -> None:
        """Record the final point from the already-paid ``test-after`` phase, at the
        run's actual billable spend, and write the artifact + summary metrics."""
        sha = template_sha256(final_template)
        self._add_point(
            CurvePoint(
                spend_eur=self._meter.billable_cost,
                nominal_eur=self._meter.billable_cost,
                test_quality=quality,
                probe_cost_eur=0.0,
                prompt_sha256=sha,
                prompt_changed=sha != self._last_sha,
                cache_hit=False,
                source="test_after",
                wall_seconds=0.0,
            )
        )
        mlflow.log_metrics(self.summary())

    # -- the hook -----------------------------------------------------------
    def maybe_probe(self, best_template_fn: Callable[[], str | None]) -> None:
        """THE checkpoint hook. Returns immediately unless billable spend has crossed
        the next ``k * K`` boundary; otherwise resolves the best-so-far prompt, scores it
        on the test split (or reuses a cached score for an unchanged prompt) and logs the
        point.

        Never raises (LC-FR9): a probe failure on an hours-long, expensive training run
        must be a hole in the curve, not a lost run. ``best_template_fn`` is a callable
        rather than a value so the technique only pays for resolving its best-so-far
        prompt (which for SkillOpt is a file read) when a probe actually fires."""
        try:
            spend = self._meter.billable_cost
            if spend < self._next_threshold:
                return
            nominal = self._next_threshold
            # Advance to the next multiple strictly above current spend: a checkpoint
            # that jumped several intervals (a GEPA iteration includes a full-val
            # candidate pass) fires ONE probe and does not back-fill the skipped
            # boundaries (LC-EC3).
            while self._next_threshold <= spend:
                self._next_threshold += self._interval
            if self._probe_count >= self._max_probes:
                if not self._cap_reached:
                    logger.warning(
                        "LEARNING CURVE: --max-probes (%d) reached at %.4f EUR; no "
                        "further curve points will be measured this run (LC-EC7).",
                        self._max_probes,
                        spend,
                    )
                    self._cap_reached = True
                return
            template = best_template_fn()
            if not template:
                logger.warning(
                    "LEARNING CURVE: could not resolve a best-so-far prompt at %.4f "
                    "EUR; skipping this curve point.",
                    spend,
                )
                self._failures += 1
                return
            self._probe(template, spend=spend, nominal=nominal)
        except Exception:
            self._failures += 1
            logger.warning(
                "LEARNING CURVE: probe failed and was skipped; the optimization "
                "continues and the curve simply has a hole here (LC-EC6).",
                exc_info=True,
            )

    def _probe(self, template: str, *, spend: float, nominal: float) -> None:
        sha = template_sha256(template)
        cost_before = self._meter.probe_cost
        started = time.monotonic()
        cached = self._cache.get(sha)
        if cached is not None:
            # The best-so-far prompt has not changed since it was last scored (every
            # rewrite was reverted at the gate, or none was accepted yet). Re-scoring an
            # identical prompt buys only judge/sampling noise -- the same call the two
            # optimizers already make when skipping their final full-val pass (LC-EC4).
            quality = cached
            self._cache_hits += 1
        else:
            with self._meter.probing():
                quality = self._evaluate(template)
            self._cache[sha] = quality
            self._probe_count += 1
        wall = time.monotonic() - started
        self._wall_seconds += wall
        probe_cost = self._meter.probe_cost - cost_before
        logger.info(
            "LEARNING CURVE: %.4f EUR (nominal %.2f) -> test %.2f%% on %d records "
            "[%s, %.1f min, %.4f EUR]",
            spend,
            nominal,
            quality * 100,
            len(self._test_set),
            "cached" if cached is not None else "measured",
            wall / 60,
            probe_cost,
        )
        self._add_point(
            CurvePoint(
                spend_eur=spend,
                nominal_eur=nominal,
                test_quality=quality,
                probe_cost_eur=probe_cost,
                prompt_sha256=sha,
                prompt_changed=sha != self._last_sha,
                cache_hit=cached is not None,
                source="probe",
                wall_seconds=wall,
            )
        )

    def _evaluate(self, template: str) -> float:
        """Score ``template`` on the test split: the same task predict path and the same
        FLEX judge ``run_eval_phase`` uses (LC-FR3), minus mlflow's ``evaluate``
        harness — which cannot be reused here because it opens a nested run inside the
        optimizer's own run context and is pinned to one worker.

        A record whose predict call raises scores 0, mirroring mlflow's own
        ``_run_single`` (it swallows predict errors into a string the scorer grades
        False). A judge-errored Feedback also scores 0, and is counted in
        ``probe_judge_errors`` so a degraded point is visible rather than silently
        pessimistic (LC-OQ2).

        **Why this deliberately differs from the training-side policy.** The optimizers
        refuse to turn an ungradable sample into a verdict at all: they *drop* it from the
        batch/rollout and count it in ``judge_ungradable_items``
        (``TextGradPromptOptimizer._judge``, ``Text2SqlEnvAdapter._rollout``), because
        there a fabricated INCORRECT verdict becomes a textual gradient or a gate score
        and would actively mislead the search. A probe is not training: its one job is to
        land on the same axis as ``test_quality_{before,after}``, and those come from
        ``mlflow.genai.evaluate``, which counts an errored Feedback as 0 in
        ``sql_is_correct/mean`` — its denominator includes ungradable rows (measured
        2026-08-12: 1 pass + 1 fail + 1 judge error → 0.333, not 0.5). Dropping them
        here would put interior curve points on a slightly more generous axis than the
        endpoints and quietly tilt every curve upward mid-run."""
        predict_fn = create_predict_fn(
            self._model, self._endpoint, self._schema_text, system_prompt_template=template
        )

        def score_one(record: dict[str, Any]) -> float:
            question = record["inputs"]["question"]
            expectations = record["expectations"]
            try:
                outputs = predict_fn(question)
            except Exception:
                logger.warning(
                    "LEARNING CURVE: predict_fn failed on a probe record; scoring it "
                    "incorrect (as mlflow's eval loop does).",
                    exc_info=True,
                )
                return 0.0
            feedback = self._judge(
                inputs={"question": question},
                outputs=outputs,
                expectations={
                    "sql": expectations.get("sql", ""),
                    "argilla_link": expectations.get("argilla_link", ""),
                },
            )
            if getattr(feedback, "error", None) is not None:
                self._judge_errors += 1
                return 0.0
            return float(bool(feedback.value))

        with ThreadPoolExecutor(
            max_workers=self._workers, thread_name_prefix="LearningCurveProbe"
        ) as pool:
            scores = list(pool.map(score_one, self._test_set))
        return sum(scores) / len(scores) if scores else 0.0

    # -- recording ----------------------------------------------------------
    def _add_point(self, point: CurvePoint) -> None:
        """Log the point as metrics and rewrite the artifact.

        The MLflow step is spend **in cents** (LC-D4), so the chart's x-axis is real
        money and curves from different runs and techniques overlay directly — stepping
        by probe ordinal would silently plot against "probe number", which stops being
        money as soon as a checkpoint skips an interval. The artifact is rewritten after
        every point so a killed run still leaves the points it paid for."""
        self._points.append(point)
        self._last_sha = point.prompt_sha256
        step = int(round(point.spend_eur * 100))
        mlflow.log_metrics(
            {
                "curve_test_quality": point.test_quality,
                "curve_spend_eur": point.spend_eur,
                "curve_nominal_eur": point.nominal_eur,
                "curve_probe_cost_eur": point.probe_cost_eur,
                "curve_prompt_changed": float(point.prompt_changed),
            },
            step=step,
        )
        self._write_artifact()

    def _write_artifact(self) -> None:
        run = mlflow.active_run()
        payload = {
            "run_id": run.info.run_id if run is not None else None,
            "budget": self._meter.budget,
            "probe_interval_eur": self._interval,
            "test_size": len(self._test_set),
            "prompt_selection": PROMPT_SELECTION,
            "points": [asdict(p) for p in self._points],
        }
        mlflow.log_text(json.dumps(payload, indent=2), CURVE_ARTIFACT)

    def summary(self) -> dict[str, float]:
        """End-of-run instrumentation scalars (LC-FR5/FR9). ``probe_cost_ratio`` is what
        makes the overhead visible per run: at current prices one 55-record point costs
        ≈ 20% of a 1.75 EUR training budget."""
        billable = self._meter.billable_cost
        return {
            "probe_count": float(self._probe_count),
            "probe_cache_hits": float(self._cache_hits),
            "probe_failures": float(self._failures),
            "probe_judge_errors": float(self._judge_errors),
            "probe_wall_minutes": self._wall_seconds / 60,
            "probe_cost_ratio": (self._meter.probe_cost / billable) if billable else 0.0,
            "probe_cap_reached": float(self._cap_reached),
        }


def build_probe(
    *,
    meter: CostMeter,
    interval_eur: float,
    max_probes: int,
    workers: int,
    test_set: list[dict[str, Any]],
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    schema_text: str,
    db_path: str,
) -> LearningCurveProbe | NullProbe:
    """The probe for this run, or a :class:`NullProbe` when learning-curve measurement
    is off (``interval_eur <= 0``, the default — LC-FR8) or impossible (empty test
    split, LC-EC1: the ``to_test.json`` smoke file has 5 records, which the 20 / 0 / 55
    scheme turns into a 20-record train split and NO test split)."""
    if interval_eur <= 0:
        return NullProbe()
    if not test_set:
        logger.warning(
            "LEARNING CURVE: --probe-interval-eur is set but the test split is empty, "
            "so there is nothing to measure the curve on; disabling probes. Under the "
            "20 / 0 / 55 scheme a dataset of <= 20 records yields no test split (the "
            "to_test.json smoke file is train-path only) -- point --questions-path at "
            "the full question set for a learning-curve run."
        )
        return NullProbe()
    return LearningCurveProbe(
        meter=meter,
        interval_eur=interval_eur,
        max_probes=max_probes,
        workers=workers,
        test_set=test_set,
        model=model,
        endpoint=endpoint,
        judge_model=judge_model,
        judge_endpoint=judge_endpoint,
        schema_text=schema_text,
        db_path=db_path,
    )
