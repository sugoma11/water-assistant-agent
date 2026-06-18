"""SkillOpt training of the text-2-SQL system prompt with MLflow + litellm.

Sibling of ``train_gepa.py`` / ``train_textgrad.py``: same dataset loader, same seeded
train/val/test split, same FLEX LLM-as-Judge and the same shared ``_run_optimization``
routine, but the optimizer is :class:`SkillOptPromptOptimizer` so SkillOpt sits side by
side with GEPA and TextGrad in the MLflow UI (same metric names, judge and registered
prompt). The roles that differ from GEPA are the optimizer (reflection/edit) model that
runs on SkillOpt's own model layer (``--optimizer-model``/``--optimizer-endpoint``) and the
effort budget expressed as epochs/edit-budget/minibatch instead of metric calls (FR10).

Model-string convention: ``--model``/``--judge-model``/``--optimizer-model`` all take the
litellm ``openai/<name>`` form (matching ``text2sql-train``). The task and judge models run
through litellm so they keep the prefix; SkillOpt's optimizer engine is a plain
OpenAI-compatible client that sends the model id straight to the endpoint, so the
``openai/`` prefix is stripped before it is handed to the optimizer (done inside
:class:`SkillOptPromptOptimizer`).
"""

import os
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    format_schema_for_prompt,
    load_schema,
)
from experiments.text2sql.harness import (
    ENDPOINTS,
    build_sql_judge_scorer,
    load_dataset,
    register_prompt_if_changed,
    setup_mlflow,
)
from experiments.text2sql.sampler import split_dataset
from experiments.text2sql.skillopt_optimizer import SkillOptPromptOptimizer
from experiments.text2sql.train_common import PROMPT_NAME, _run_optimization


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
@click.option("--model", required=True, help="Task model, e.g. openai/glm-4.7")
@click.option(
    "--endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the task model.",
)
@click.option("--judge-model", required=True, help="LLM-as-Judge model, e.g. openai/...")
@click.option(
    "--judge-endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the LLM-as-Judge.",
)
@click.option(
    "--optimizer-model",
    required=True,
    help="SkillOpt reflection/edit model that rewrites the skill, e.g. openai/glm-4.7",
)
@click.option(
    "--optimizer-endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the optimizer (reflection/edit) model.",
)
@click.option(
    "--epochs",
    required=True,
    type=int,
    help="Number of full passes over the train split (SkillOpt num_epochs).",
)
@click.option(
    "--edit-budget",
    required=True,
    type=int,
    help="Max edits applied per round (constant SkillOpt edit budget, FR13).",
)
@click.option(
    "--minibatch-size",
    required=True,
    type=int,
    help="SkillOpt reflection minibatch size.",
)
@click.option(
    "--reflect-on-success/--no-reflect-on-success",
    default=False,
    show_default=True,
    help="Enable success reflection (failure reflection is always on, FR11).",
)
@click.option(
    "--sampler-seed",
    default=42,
    show_default=True,
    type=int,
    help="Seed for the train/val/test sampler (also seeds SkillOpt's seed/split_seed).",
)
@click.option(
    "--use-prod-questions",
    is_flag=True,
    default=False,
    help="Feed the production-style 'prod_question' phrasing to the model "
    "instead of the clean 'question'.",
)
def train_skillopt(
    questions_path: str,
    schema_path: str,
    db_path: str,
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    optimizer_model: str,
    optimizer_endpoint: str,
    epochs: int,
    edit_budget: int,
    minibatch_size: int,
    reflect_on_success: bool,
    sampler_seed: int,
    use_prod_questions: bool,
) -> None:
    """Train the text-2-SQL system prompt with SkillOpt and log results to MLflow."""
    load_dotenv()

    experiment_name = os.environ.get("MLFLOW_TRAIN_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_TRAIN_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)

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

    # The judge scorer is the SAME shared FLEX judge the eval phases use, so training,
    # validation and the test phases score with one judge (FR9, A3). The task model keeps
    # its litellm `openai/` prefix (it runs through the project litellm path inside the
    # adapter rollout); the optimizer model runs on SkillOpt's own OpenAI-compatible
    # client, which strips the prefix internally.
    judge_scorer = build_sql_judge_scorer(judge_model, judge_endpoint, schema_text, db_path)
    optimizer = SkillOptPromptOptimizer(
        task_model=model,
        task_endpoint=endpoint,
        optimizer_model=optimizer_model,
        optimizer_endpoint=optimizer_endpoint,
        judge_scorer=judge_scorer,
        schema_text=schema_text,
        val_set=val_set,
        epochs=epochs,
        edit_budget=edit_budget,
        minibatch_size=minibatch_size,
        reflect_on_success=reflect_on_success,
        seed=sampler_seed,
    )

    click.echo(
        f"Running SkillOpt ({epochs} epoch(s), edit_budget={edit_budget}, "
        f"minibatch_size={minibatch_size}, reflect_on_success={reflect_on_success})..."
    )
    extra_params: dict[str, Any] = {
        "optimizer_model": optimizer_model,
        "optimizer_endpoint": optimizer_endpoint,
        "epochs": epochs,
        "edit_budget": edit_budget,
        "minibatch_size": minibatch_size,
        "reflect_on_success": reflect_on_success,
    }
    _run_optimization(
        optimizer=optimizer,
        technique="skillopt",
        extra_params=extra_params,
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
    )


if __name__ == "__main__":
    train_skillopt()
