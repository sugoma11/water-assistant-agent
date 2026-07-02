"""GEPA training of the text-2-SQL system prompt with MLflow + litellm.

Optimizes ``text2sql_system`` (registered from the in-code SYSTEM_PROMPT_TEMPLATE)
with ``mlflow.genai.optimize_prompts`` + ``GepaPromptOptimizer``, reusing the eval
building blocks from ``experiments.text2sql.harness``: the dataset loader, the
litellm predict_fn and the FLEX-style LLM-as-Judge scorer (its boolean Feedback is
auto-converted to 1.0/0.0 by MLflow's metric aggregation).

The dataset is split train/val/test with a seeded sampler: GEPA reflects on
minibatches from the train split and Pareto-scores candidates on the val split.
There are no explicit val eval phases: GEPA fully evaluates the seed prompt (and
every accepted candidate) on the valset (gepa/core/engine.py), so the baseline and
best-candidate val scores come straight back as ``result.initial_eval_score`` /
``result.final_eval_score``, which optimize_prompts already logs on this run as
``{initial,final}_eval_score`` (no need to re-log them). Only the test split is
evaluated by hand (``test_quality_{before,after}``), since GEPA never sees it. The
budget passed to GEPA is ``MAX_METRIC_CALLS + 2 * len(val)`` so the seed and
best-candidate full valset passes don't eat into the optimization budget proper.

Teacher (reflection) model note: GEPA calls litellm WITHOUT api_base/api_key, so
``configure_teacher_env`` points the ``OPENAI_API_BASE``/``OPENAI_API_KEY`` fallback
at the chosen endpoint — no global litellm config is needed.
"""

import os
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv
from gepa.strategies.instruction_proposal import InstructionProposalSignature
from mlflow.genai.optimize import GepaPromptOptimizer

from water_assistant_agent.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    format_schema_for_prompt,
    load_schema,
)
from experiments.text2sql.harness import (
    ENDPOINTS,
    configure_teacher_env,
    load_dataset,
    register_prompt_if_changed,
    setup_mlflow,
    to_mlflow_model_uri,
    validate_teacher_model,
)
from experiments.text2sql.sampler import split_dataset
from experiments.text2sql.train_common import PROMPT_NAME, _run_optimization

MAX_METRIC_CALLS = 100


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
@click.option(
    "--use-prod-questions",
    is_flag=True,
    default=False,
    help="Feed the production-style 'prod_question' phrasing to the model "
    "instead of the clean 'question'.",
)
def train_gepa(
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
    use_prod_questions: bool,
) -> None:
    """Train the text-2-SQL system prompt with GEPA and log results to MLflow."""
    validate_teacher_model(teacher_model)
    load_dotenv()

    experiment_name = os.environ.get("MLFLOW_TRAIN_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_TRAIN_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)
    configure_teacher_env(teacher_endpoint)

    schema_text = format_schema_for_prompt(load_schema(schema_path))
    data = load_dataset(questions_path, use_prod_questions)
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

    gepa_kwargs: dict[str, Any] = {"valset": val_set, "seed": sampler_seed}
    reflection_prompt_template = gepa_kwargs.get(
        "reflection_prompt_template", InstructionProposalSignature.default_prompt_template
    )
    # GEPA fully evaluates the seed prompt on the valset at iteration 0 and the
    # accepted candidates along the way; budget two full valset passes on top of
    # the optimization budget proper.
    total_metric_calls = len(val_set) * 2 + MAX_METRIC_CALLS
    click.echo(
        f"Running GEPA ({total_metric_calls} metric calls max: "
        f"{MAX_METRIC_CALLS} optimization + 2x{len(val_set)} full valset passes)..."
    )
    _run_optimization(
        optimizer=GepaPromptOptimizer(
            reflection_model=to_mlflow_model_uri(teacher_model),
            max_metric_calls=total_metric_calls,
            display_progress_bar=True,
            gepa_kwargs=gepa_kwargs,
        ),
        technique="gepa",
        extra_params={
            "teacher_model": teacher_model,
            "teacher_endpoint": teacher_endpoint,
            "max_metric_calls": MAX_METRIC_CALLS,
            "total_metric_calls": total_metric_calls,
        },
        model=model,
        endpoint=endpoint,
        judge_model=judge_model,
        judge_endpoint=judge_endpoint,
        questions_path=questions_path,
        schema_path=schema_path,
        schema_text=schema_text,
        db_path=db_path,
        train_set=train_set,
        val_set=val_set,
        test_set=test_set,
        prompt_version=prompt_version,
        sampler_seed=sampler_seed,
        extra_artifacts={"reflection_prompt_template.txt": reflection_prompt_template},
    )


if __name__ == "__main__":
    train_gepa()
