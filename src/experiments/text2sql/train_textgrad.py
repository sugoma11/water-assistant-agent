"""TextGrad training of the text-2-SQL system prompt with MLflow + litellm.

Sibling of ``train.py``: same dataset loader, same seeded train/val/test split, same
FLEX LLM-as-Judge and the same shared ``_run_optimization`` routine, but the optimizer
is :class:`TextGradPromptOptimizer` instead of ``GepaPromptOptimizer`` so the two
techniques are directly comparable in the MLflow UI (same metric names, judge and
registered prompt). The only knobs that differ from GEPA are the optimizer-model role
(``--optimizer-model``/``--optimizer-endpoint``) that drives TextGrad's backward/proposal
engine; both techniques stop on the same ``--budget`` money cap (FR2), with
``--batch-size``/``--val-gate-size`` left as structural knobs.

The split follows the 20 / 0 / 55 scheme (``specs/sampling_refactoring/spec.md``), the
same one GEPA uses: ``split_dataset`` yields a 20-record train split and a val
split that **is** a copy of it (D1), with the remaining 55
records held out as test. Gradient steps batch over train, the keep-best gate and the
excluded full-val baseline score val — i.e. the same 20 records — so
``{initial,final}_eval_score`` are *training* scores (C4) and only
``test_quality_{before,after}``, judged on the 55 held-out records, measures
generalization. Consequence for the gate (C5): ``--val-gate-size`` is now a subset of
20, not 25, so the recipe default ``textgrad_val_gate_size := "12"`` (``common.just``)
gates on 60% of the val/train split (it was 48% of the old 25-record val set), and
``--val-gate-size 0`` -- or any value ``>= 20`` -- degenerates to the full val set via
:meth:`TextGradPromptOptimizer._build_gate_set`.

Model-string convention: ``--model``/``--judge-model``/``--optimizer-model`` all take the
litellm provider-prefixed form (matching ``text2sql-train``): ``openai/<name>`` for the
blablador/kisski/local endpoints, ``openrouter/<vendor>/<name>`` for OpenRouter. The task
and judge models run through litellm so they keep the prefix; TextGrad's task and optimizer
engines are plain ``ChatExternalClient``s wrapping an OpenAI client, which send the model id
straight to the endpoint, so the litellm provider prefix is stripped
(:func:`_strip_litellm_provider_prefix`) before they are handed to the optimizer.
"""

import logging
import os
from pathlib import Path
from typing import Any

import click
import mlflow
from dotenv import load_dotenv

from water_assistant_agent.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    format_schema_for_prompt,
    load_schema,
)
from experiments.text2sql.harness import (
    ENDPOINTS,
    build_sql_judge_scorer,
    load_dataset,
    read_optimizer_params_for_logging,
    read_sampling_params,
    register_prompt_if_changed,
    setup_mlflow,
)
from experiments.text2sql.cost_meter import CostMeter
from experiments.text2sql.sampler import split_dataset
from experiments.text2sql.textgrad_optimizer import TextGradPromptOptimizer
from experiments.text2sql.train_common import (
    PROMPT_NAME,
    _run_optimization,
    read_price_config,
)


def _strip_litellm_provider_prefix(model: str) -> str:
    """Drop the litellm provider prefix so TextGrad's ``ChatExternalClient`` sends the bare
    model id the OpenAI-compatible endpoint expects. Two prefix forms reach here: the
    ``openai/<name>`` aliases (blablador/kisski/local) and the ``openrouter/<vendor>/<name>``
    aliases. OpenRouter ids keep an internal slash (``qwen/qwen3.6-35b-a3b``), so only the
    leading provider token is stripped -- NOT everything up to the last slash. GEPA never
    needs this because it calls through litellm, which consumes the prefix itself; that is
    why the same ``openrouter/...`` alias worked for GEPA but 400'd ("not a valid model
    ID") here when only ``openai/`` was stripped."""
    for prefix in ("openai/", "openrouter/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


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
    help="TextGrad backward/proposal model that rewrites the prompt, e.g. openai/glm-4.7",
)
@click.option(
    "--optimizer-endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the optimizer (backward) model.",
)
@click.option(
    "--batch-size",
    default=2,
    show_default=True,
    type=int,
    help="Records per gradient step.",
)
@click.option(
    "--val-gate-size",
    default=8,
    show_default=True,
    type=int,
    help="Size of the fixed val subset scored for the per-step keep-best gate. Pass 0 "
    "to gate on the full val set (the old, pricier behavior).",
)
@click.option(
    "--budget",
    required=True,
    type=click.FloatRange(min=0, min_open=True),
    help="Money budget for the optimization phase, in EUR (> 0). Optimization stops "
    "once billable spend reaches it; prices come from the PRICE_* env vars.",
)
@click.option(
    "--sampler-seed",
    default=42,
    show_default=True,
    type=int,
    help="Seed for the train/val/test sampler (also seeds the per-epoch train shuffle).",
)
@click.option(
    "--use-prod-questions",
    is_flag=True,
    default=False,
    help="Feed the production-style 'prod_question' phrasing to the model "
    "instead of the clean 'question'.",
)
def train_textgrad(
    questions_path: str,
    schema_path: str,
    db_path: str,
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    optimizer_model: str,
    optimizer_endpoint: str,
    batch_size: int,
    val_gate_size: int,
    budget: float,
    sampler_seed: int,
    use_prod_questions: bool,
) -> None:
    """Train the text-2-SQL system prompt with TextGrad and log results to MLflow."""
    load_dotenv()

    # Validate prices + construct the meter before any MLflow run exists, so a
    # misconfigured price env refuses to start with the offending var named (EC5, SC6).
    meter = CostMeter(budget, read_price_config())

    # Nothing in src/ configures logging, so the root logger's default WARNING threshold
    # silently dropped every `logger.info(...)` in the optimizer (baseline val eval_score,
    # per-step keep/revert). Install a root StreamHandler at WARNING (keeps litellm/httpx/
    # mlflow quiet) and lift only the project's `experiments` logger to INFO so those
    # progress lines reach the console.
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("experiments").setLevel(logging.INFO)

    experiment_name = os.environ.get("MLFLOW_TRAIN_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_TRAIN_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)
    # TextGrad's task forward AND backward/reflection engines reach the endpoint through
    # the raw `openai` SDK (ChatExternalClient -> openai.OpenAI), which the shared
    # `mlflow.litellm.autolog` in setup_mlflow never sees -- so unlike GEPA (whose
    # reflection runs through litellm) TextGrad emitted zero reflection traces. Enable
    # OpenAI autologging here, scoped to the TextGrad entry point so GEPA/eval runs are
    # unaffected, to capture the gradient-step forwards and the optimizer's prompt
    # rewrites as traces. litellm's own OpenAI-provider calls (val/judge) then nest under
    # their litellm span rather than double-counting as standalone traces.
    mlflow.openai.autolog()

    schema_text = format_schema_for_prompt(load_schema(schema_path))
    data = load_dataset(questions_path, use_prod_questions)
    train_set, val_set, test_set = split_dataset(
        data,
        sampler_seed,
        cache_path=Path(questions_path).with_name("question_embeddings.npz"),
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

    # The optimizer's task + judge forward go through TextGrad's ChatExternalClient,
    # which sends the model id straight to the OpenAI-compatible endpoint, so strip the
    # litellm provider prefix (the judge scorer below keeps it -- it runs via litellm like
    # GEPA's). The judge scorer is the SAME shared FLEX judge the eval phases use, so
    # training and validation score with one judge (FR9, A3).
    judge_scorer = build_sql_judge_scorer(judge_model, judge_endpoint, schema_text, db_path)
    optimizer = TextGradPromptOptimizer(
        task_model=_strip_litellm_provider_prefix(model),
        task_endpoint=endpoint,
        optimizer_model=_strip_litellm_provider_prefix(optimizer_model),
        optimizer_endpoint=optimizer_endpoint,
        judge_scorer=judge_scorer,
        schema_text=schema_text,
        cost_meter=meter,
        # Drive TextGrad's gradient-step forward with the SAME LLM_* sampling params the
        # litellm eval/test path uses, so the task model is consistent across paths
        # (NFR2). These params are already logged by log_global_params.
        task_sampling_params=read_sampling_params("LLM"),
        # Drive the backward/reflection engine with the OPTIMIZER_* params (already
        # logged via read_optimizer_params_for_logging) so its max_tokens budget is
        # honored instead of TextGrad's hardcoded 2000 default.
        optimizer_sampling_params=read_sampling_params("OPTIMIZER"),
        val_set=val_set,
        # 0 is the CLI sentinel for "gate on the full val set"; the optimizer takes None.
        val_gate_size=val_gate_size or None,
        batch_size=batch_size,
        seed=sampler_seed,
        display_progress_bar=True,
    )

    click.echo(
        f"Running TextGrad (budget={budget} EUR, batch_size={batch_size}, "
        f"val_gate_size={val_gate_size})..."
    )
    extra_params: dict[str, Any] = {
        "optimizer_model": optimizer_model,
        "optimizer_endpoint": optimizer_endpoint,
        "batch_size": batch_size,
        "val_gate_size": val_gate_size,
        **read_optimizer_params_for_logging(),
    }
    _run_optimization(
        optimizer=optimizer,
        technique="textgrad",
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
        cost_meter=meter,
    )


if __name__ == "__main__":
    train_textgrad()
