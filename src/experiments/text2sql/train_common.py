"""Shared, technique-agnostic prompt-optimization plumbing for the text-2-SQL trainers.

Both the GEPA (``train_gepa``) and TextGrad (``train_textgrad``) CLIs reuse the same
MLflow run scaffolding from here so their logged params/metrics stay directly
comparable: the registered prompt name, the nested before/after eval phase, and the
``_run_optimization`` routine that opens the run, logs the global + technique-specific
params, runs the test-before/after evals around ``mlflow.genai.optimize_prompts``, and
reads back the optimizer-logged val scores for the summary echo.
"""

from collections.abc import Callable
from typing import Any

import click
import mlflow
from mlflow.entities.model_registry import PromptVersion
from mlflow.genai import evaluate
from mlflow.genai.optimize.optimizers import BasePromptOptimizer

from experiments.text2sql.harness import (
    build_sql_judge_scorer,
    create_optimizable_predict_fn,
    create_predict_fn,
    log_global_params,
    make_run_name,
)

PROMPT_NAME = "text2sql_system"


def run_eval_phase(
    name: str,
    model: str,
    data: list[dict[str, Any]],
    predict_fn: Callable[..., dict[str, str]],
    judge_scorer: Callable[..., Any],
) -> float:
    """Evaluate ``predict_fn`` on ``data`` in a nested MLflow run (avoids metric
    name collisions between the before/after eval phases) and return the
    judge quality in [0, 1]."""
    click.echo(f"Evaluating phase {name!r} on {len(data)} samples...")
    with mlflow.start_run(run_name=make_run_name(f"{name}-{model}"), nested=True):
        results = evaluate(data=data, predict_fn=predict_fn, scorers=[judge_scorer])
    quality = results.metrics.get("sql_is_correct/mean", 0.0)
    click.echo(f"{name}: {quality:.2%}")
    return quality


# ---------------------------------------------------------------------------
# Shared optimization routine (technique-agnostic: GEPA, TextGrad, ...)
# ---------------------------------------------------------------------------
def _run_optimization(
    *,
    optimizer: BasePromptOptimizer,
    technique: str,
    extra_params: dict[str, Any],
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    questions_path: str,
    schema_path: str,
    schema_text: str,
    db_path: str,
    train_set: list[dict[str, Any]],
    val_set: list[dict[str, Any]],
    test_set: list[dict[str, Any]],
    prompt_version: PromptVersion,
    sampler_seed: int,
    extra_artifacts: dict[str, str] | None = None,
) -> None:
    """Open the run, optimize the prompt with ``optimizer``, log before/after metrics.

    Technique-agnostic body shared by every prompt-optimization CLI (GEPA, TextGrad):
    it opens the MLflow run, logs the global params + the ``technique`` tag + the
    technique-specific ``extra_params``/``extra_artifacts``, runs the test-before eval
    phase, calls ``mlflow.genai.optimize_prompts`` with the supplied ``optimizer``, runs
    the test-after eval phase, and logs ``test_quality_{before,after}``.

    The optimizer already logs the val progression (``eval_score`` steps) and
    ``result.initial/final_eval_score`` on this run, so those are read back only for
    the summary echo here -- never re-logged -- which is what keeps any two techniques'
    logged params/metrics directly comparable. Building the judge scorer and the three
    predict_fns here (rather than in each CLI) guarantees both techniques score with the
    exact same judge under the same metric names.
    """
    judge_scorer = build_sql_judge_scorer(judge_model, judge_endpoint, schema_text, db_path)
    baseline_predict_fn = create_predict_fn(model, endpoint, schema_text)

    with mlflow.start_run(run_name=make_run_name(f"{technique}-{model}")):
        log_global_params(
            model=model,
            endpoint=endpoint,
            judge_model=judge_model,
            judge_endpoint=judge_endpoint,
            questions_path=questions_path,
            schema_path=schema_path,
            schema_text=schema_text,
            db_path=db_path,
        )
        mlflow.log_param("technique", technique)
        mlflow.log_params(
            {
                "sampler_seed": sampler_seed,
                "train_size": len(train_set),
                "val_size": len(val_set),
                "test_size": len(test_set),
                **extra_params,
            }
        )
        for artifact_path, text in (extra_artifacts or {}).items():
            mlflow.log_text(text, artifact_path)

        test_before = run_eval_phase(
            "test-before", model, test_set, baseline_predict_fn, judge_scorer
        )
        mlflow.log_metric("test_quality_before", test_before)

        result = mlflow.genai.optimize_prompts(
            predict_fn=create_optimizable_predict_fn(
                model, endpoint, schema_text, prompt_version
            ),
            train_data=train_set,
            prompt_uris=[prompt_version.uri],
            optimizer=optimizer,
            scorers=[judge_scorer],
            enable_tracking=True,
        )
        optimized = result.optimized_prompts[0]
        click.echo(f"Optimized prompt registered as {optimized.uri}")

        # The optimizer fully evaluates the seed and the best candidate on the
        # valset and already logs these on this run as initial_eval_score/
        # final_eval_score, so we don't re-log them; we only keep the values for
        # the summary echo below.
        val_before = result.initial_eval_score or 0.0
        val_after = result.final_eval_score or 0.0

        optimized_predict_fn = create_predict_fn(
            model, endpoint, schema_text, system_prompt_template=optimized.template
        )
        test_after = run_eval_phase(
            "test-after", model, test_set, optimized_predict_fn, judge_scorer
        )
        mlflow.log_metric("test_quality_after", test_after)

    click.echo(
        f"val:  {val_before:.2%} -> {val_after:.2%}\n"
        f"test: {test_before:.2%} -> {test_after:.2%}\n"
        f"Optimized prompt: {optimized.uri}"
    )
