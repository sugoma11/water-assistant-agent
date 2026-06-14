"""GEPA training of the text-2-SQL system prompt with MLflow + litellm.

Optimizes ``text2sql_system`` (registered from the in-code SYSTEM_PROMPT_TEMPLATE)
with ``mlflow.genai.optimize_prompts`` + ``GepaPromptOptimizer``, reusing the eval
building blocks from ``experiments.text2sql.harness``: the dataset loader, the
litellm predict_fn and the FLEX-style LLM-as-Judge scorer (its boolean Feedback is
auto-converted to 1.0/0.0 by MLflow's metric aggregation).

The dataset is split train/val/test with a seeded sampler: GEPA reflects on
minibatches from the train split and Pareto-scores candidates on the val split,
while the baseline and the optimized prompt are each evaluated on val AND test
(``{val,test}_quality_{before,after}`` metrics on the parent run). There is no
explicit val-before eval phase: GEPA itself fully evaluates the seed prompt on the
valset at iteration 0 (gepa/core/engine.py), and that score comes back as
``result.initial_eval_score``. The budget passed to GEPA is therefore
``MAX_METRIC_CALLS + 2 * len(val)`` so the seed and best-candidate full valset
passes don't eat into the optimization budget proper.

Teacher (reflection) model note: GEPA calls litellm WITHOUT api_base/api_key, so
``configure_teacher_env`` points the ``OPENAI_API_BASE``/``OPENAI_API_KEY`` fallback
at the chosen endpoint — no global litellm config is needed.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import mlflow
from dotenv import load_dotenv
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from mlflow.genai import evaluate
from mlflow.genai.optimize import GepaPromptOptimizer

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    format_schema_for_prompt,
    load_schema,
)
from experiments.text2sql.harness import (
    ENDPOINTS,
    build_sql_judge_scorer,
    configure_teacher_env,
    create_optimizable_predict_fn,
    create_predict_fn,
    load_dataset,
    log_global_params,
    make_run_name,
    register_prompt_if_changed,
    setup_mlflow,
    to_mlflow_model_uri,
    validate_teacher_model,
)
from experiments.text2sql.sampler import split_dataset

MAX_METRIC_CALLS = 3

PROMPT_NAME = "text2sql_system"


def run_eval_phase(
    name: str,
    data: list[dict[str, Any]],
    predict_fn: Callable[..., dict[str, str]],
    judge_scorer: Callable[..., Any],
) -> float:
    """Evaluate ``predict_fn`` on ``data`` in a nested MLflow run (avoids metric
    name collisions between the before/after eval phases) and return the
    judge quality in [0, 1]."""
    click.echo(f"Evaluating phase {name!r} on {len(data)} samples...")
    with mlflow.start_run(run_name=name, nested=True):
        results = evaluate(data=data, predict_fn=predict_fn, scorers=[judge_scorer])
    quality = results.metrics.get("sql_is_correct/mean", 0.0)
    click.echo(f"{name}: {quality:.2%}")
    return quality


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
@click.command()
@click.option(
    "--questions-path",
    required=True,
    type=click.Path(exists=True),
    help="Ground-truth JSON ({question, sql, argilla_link} per record).",
)
@click.option(
    "--schema-path",
    required=True,
    type=click.Path(exists=True),
    help="Python file containing table_schema_dict.",
)
@click.option(
    "--db-path",
    required=True,
    type=click.Path(exists=True),
    help="DuckDB database file used to execute generated and reference SQL.",
)
@click.option("--model", required=True, help="Student model, e.g. openai/glm-4.7")
@click.option(
    "--endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the student model.",
)
@click.option("--judge-model", required=True, help="LLM-as-Judge model, e.g. openai/...")
@click.option(
    "--judge-endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the LLM-as-Judge.",
)
@click.option(
    "--teacher-model",
    required=True,
    help="GEPA reflection (teacher) model in litellm style, e.g. openai/alias-eve",
)
@click.option(
    "--teacher-endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the teacher model.",
)
@click.option(
    "--sampler-seed",
    default=42,
    show_default=True,
    type=int,
    help="Seed for the train/val/test sampler (also passed to GEPA).",
)
def train(
    questions_path: str,
    schema_path: str,
    db_path: str,
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    teacher_model: str,
    teacher_endpoint: str,
    sampler_seed: int,
) -> None:
    """Train the text-2-SQL system prompt with GEPA and log results to MLflow."""
    validate_teacher_model(teacher_model)
    load_dotenv()

    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)
    configure_teacher_env(teacher_endpoint)

    schema_text = format_schema_for_prompt(load_schema(schema_path))
    data = load_dataset(questions_path)
    train_set, val_set, test_set = split_dataset(
        data, sampler_seed, cache_path=Path(questions_path).with_name("question_embeddings.npz")
    )
    click.echo(
        f"Split {len(data)} samples (seed={sampler_seed}): "
        f"train={len(train_set)}, val={len(val_set)}, test={len(test_set)}"
    )

    prompt_version = register_prompt_if_changed(PROMPT_NAME, SYSTEM_PROMPT_TEMPLATE)
    if prompt_version is None:
        raise click.ClickException(
            "MLflow prompt registry is unavailable; optimize_prompts requires a "
            "registered prompt URI."
        )

    judge_scorer = build_sql_judge_scorer(judge_model, judge_endpoint, schema_text, db_path)
    baseline_predict_fn = create_predict_fn(model, endpoint, schema_text)

    gepa_kwargs: dict[str, Any] = {"valset": val_set, "seed": sampler_seed}
    reflection_prompt_template = gepa_kwargs.get(
        "reflection_prompt_template", InstructionProposalSignature.default_prompt_template
    )
    # GEPA fully evaluates the seed prompt on the valset at iteration 0 and the
    # accepted candidates along the way; budget two full valset passes on top of
    # the optimization budget proper.
    total_metric_calls = len(val_set) * 2 + MAX_METRIC_CALLS

    with mlflow.start_run(run_name=make_run_name(f"gepa-{model}")):
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
        mlflow.log_params(
            {
                "teacher_model": teacher_model,
                "teacher_endpoint": teacher_endpoint,
                "sampler_seed": sampler_seed,
                "max_metric_calls": MAX_METRIC_CALLS,
                "total_metric_calls": total_metric_calls,
                "train_size": len(train_set),
                "val_size": len(val_set),
                "test_size": len(test_set),
            }
        )
        mlflow.log_text(reflection_prompt_template, "reflection_prompt_template.txt")

        test_before = run_eval_phase("test-before", test_set, baseline_predict_fn, judge_scorer)
        mlflow.log_metric("test_quality_before", test_before)

        click.echo(
            f"Running GEPA ({total_metric_calls} metric calls max: "
            f"{MAX_METRIC_CALLS} optimization + 2x{len(val_set)} full valset passes)..."
        )
        result = mlflow.genai.optimize_prompts(
            predict_fn=create_optimizable_predict_fn(
                model, endpoint, schema_text, prompt_version
            ),
            train_data=train_set,
            prompt_uris=[prompt_version.uri],
            optimizer=GepaPromptOptimizer(
                reflection_model=to_mlflow_model_uri(teacher_model),
                max_metric_calls=total_metric_calls,
                display_progress_bar=True,
                gepa_kwargs=gepa_kwargs,
            ),
            scorers=[judge_scorer],
            enable_tracking=True,
        )
        optimized = result.optimized_prompts[0]
        click.echo(f"Optimized prompt registered as {optimized.uri}")

        # GEPA's iteration-0 full valset eval of the seed prompt doubles as the
        # baseline val measurement (no separate val-before phase needed).
        val_before = result.initial_eval_score or 0.0
        mlflow.log_metric("val_quality_before", val_before)

        optimized_predict_fn = create_predict_fn(
            model, endpoint, schema_text, system_prompt_template=optimized.template
        )
        val_after = run_eval_phase("val-after", val_set, optimized_predict_fn, judge_scorer)
        test_after = run_eval_phase("test-after", test_set, optimized_predict_fn, judge_scorer)
        mlflow.log_metrics(
            {"val_quality_after": val_after, "test_quality_after": test_after}
        )

    click.echo(
        f"val:  {val_before:.2%} -> {val_after:.2%}\n"
        f"test: {test_before:.2%} -> {test_after:.2%}\n"
        f"Optimized prompt: {optimized.uri}"
    )


if __name__ == "__main__":
    train()
