"""SkillOpt training of the text-2-SQL system prompt with MLflow + litellm.

Sibling of ``train_gepa.py`` / ``train_textgrad.py``: same dataset loader, same seeded
train/val/test split, same FLEX LLM-as-Judge and the same shared ``_run_optimization``
routine, but the optimizer is :class:`SkillOptPromptOptimizer` so SkillOpt sits side by
side with GEPA and TextGrad in the MLflow UI (same metric names, judge and registered
prompt). The roles that differ from GEPA are the optimizer (reflection/edit) model that
runs on SkillOpt's own model layer (``--optimizer-model``/``--optimizer-endpoint``); the
sole stopping criterion is the money ``--budget`` shared by all techniques (FR2, C3),
while edit-budget/minibatch remain structural knobs shaping how a round works (FR10).

The split follows the 20 / 0 / 55 scheme (``specs/sampling_refactoring/spec.md``), the
same one GEPA and TextGrad use: ``split_dataset`` yields a 20-record train split and
a val split that **is** a copy of it (D1), with the remaining
55 records held out as test. Two ``SkillOptPromptOptimizer._build_cfg`` values follow
from it: the full-pass epoch (``train_size == batch_size == len(train_set)``) is now 20
records rather than 25, and the hard val gate ``sel_env_num = len(val_set)`` is 20 --
scored over the very records the round just trained on. ``minibatch_size`` (3) and
``edit_budget`` (2) are structural knobs and unchanged. Because the gate and the
excluded full-val baseline both run on train data, ``{initial,final}_eval_score`` are
*training* scores (C4); only ``test_quality_{before,after}``, judged on the 55 held-out
records, measures generalization.

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
import mlflow
from dotenv import load_dotenv

from water_assistant_agent.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    format_schema_for_prompt,
    load_schema,
)
from experiments.text2sql.harness import (
    ENDPOINTS,
    REASONING_EFFORT,
    build_sql_judge_scorer,
    load_dataset,
    read_optimizer_params_for_logging,
    register_prompt_if_changed,
    setup_mlflow,
)
from experiments.text2sql.cost_meter import CostMeter
from experiments.text2sql.learning_curve import build_probe
from experiments.text2sql.sampler import split_dataset
from experiments.text2sql.skillopt_optimizer import SkillOptPromptOptimizer
from experiments.text2sql.train_common import (
    PROMPT_NAME,
    _run_optimization,
    read_price_config,
)


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
    "--budget",
    required=True,
    type=click.FloatRange(min=0, min_open=True),
    help="Money budget for the optimization phase, in EUR (> 0). Optimization stops "
    "once billable spend reaches it; prices come from the PRICE_* env vars.",
)
@click.option(
    "--probe-interval-eur",
    default=0.0,
    show_default=True,
    type=click.FloatRange(min=0),
    help="Learning curve: evaluate the best-so-far prompt on the held-out test split "
    "every K EUR of billable spend (0 = off). Probe spend never charges --budget.",
)
@click.option(
    "--probe-workers",
    default=1,
    show_default=True,
    type=click.IntRange(min=1),
    help="Parallel workers for a learning-curve probe. 1 keeps a probe as sequential "
    "as the eval phases it must match; raise it to trade endpoint concurrency for "
    "wall clock (a probe is the one point where nothing else is in flight).",
)
@click.option(
    "--max-probes",
    default=20,
    show_default=True,
    type=click.IntRange(min=1),
    help="Safety cap on learning-curve probes per run (instrumentation cost).",
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
    edit_budget: int,
    minibatch_size: int,
    reflect_on_success: bool,
    budget: float,
    probe_interval_eur: float,
    probe_workers: int,
    max_probes: int,
    sampler_seed: int,
    use_prod_questions: bool,
) -> None:
    """Train the text-2-SQL system prompt with SkillOpt and log results to MLflow."""
    load_dotenv()

    # Validate prices + construct the meter before any MLflow run exists, so a
    # misconfigured price env refuses to start with the offending var named (EC5, SC6).
    meter = CostMeter(budget, read_price_config())

    experiment_name = os.environ.get("MLFLOW_TRAIN_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_TRAIN_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)
    # SkillOpt's reflection/edit (optimizer) model runs on its own `openai_chat` backend,
    # i.e. a raw `openai.OpenAI` client (skillopt_optimizer -> make_client), which the
    # shared `mlflow.litellm.autolog` in setup_mlflow never sees -- so without this the
    # optimizer's reflection tokens were untraced and every token count undercounted the
    # optimization overhead. Mirror train_textgrad: enable OpenAI autologging, scoped to
    # this entry point, so the reflection/edit calls emit traces carrying token usage. The
    # task rollout + judge still run through litellm and nest under their litellm span
    # rather than double-counting as standalone traces.
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

    # The judge scorer is the SAME shared FLEX judge the eval phases use, so training,
    # validation and the test phases score with one judge (FR9, A3). The task model keeps
    # its litellm `openai/` prefix (it runs through the project litellm path inside the
    # adapter rollout); the optimizer model runs on SkillOpt's own OpenAI-compatible
    # client, which strips the prefix internally.
    judge_scorer = build_sql_judge_scorer(judge_model, judge_endpoint, schema_text, db_path)
    probe = build_probe(
        meter=meter,
        interval_eur=probe_interval_eur,
        max_probes=max_probes,
        workers=probe_workers,
        test_set=test_set,
        model=model,
        endpoint=endpoint,
        judge_model=judge_model,
        judge_endpoint=judge_endpoint,
        schema_text=schema_text,
        db_path=db_path,
    )
    optimizer = SkillOptPromptOptimizer(
        task_model=model,
        task_endpoint=endpoint,
        optimizer_model=optimizer_model,
        optimizer_endpoint=optimizer_endpoint,
        judge_scorer=judge_scorer,
        schema_text=schema_text,
        val_set=val_set,
        cost_meter=meter,
        edit_budget=edit_budget,
        minibatch_size=minibatch_size,
        reflect_on_success=reflect_on_success,
        # Frozen thinking budget shared with every other role/technique (REASONING_EFFORT),
        # so SkillOpt's reflection/edit model reflects at the same effort GEPA's teacher and
        # the student/judge run at -- no longer a per-run CLI knob.
        reasoning_effort=REASONING_EFFORT,
        seed=sampler_seed,
        # Learning-curve probe: fires at the adapter's per-rollout checkpoint on the
        # best gate-validated skill, into the meter's separate probe bucket (LC-D1/D2).
        probe=probe,
    )

    click.echo(
        f"Running SkillOpt (budget={budget} EUR, edit_budget={edit_budget}, "
        f"minibatch_size={minibatch_size}, reflect_on_success={reflect_on_success}, "
        f"reasoning_effort={REASONING_EFFORT})..."
    )
    # Recorded on the SkillOpt run only (via extra_params, never via the shared
    # log_global_params), so GEPA/TextGrad runs keep byte-identical params (FR8, NFR3).
    # The task + judge roles/endpoints + sampler_seed + split sizes are logged by
    # _run_optimization (log_global_params + the shared log_params); here we add the
    # optimizer role and the OPTIMIZER_* sampling params that drive SkillOpt's
    # reflection/edit model (FR5, FR10). `reasoning_effort` is now frozen and logged
    # globally by log_global_params for every technique, so it is not repeated here.
    extra_params: dict[str, Any] = {
        "optimizer_model": optimizer_model,
        "optimizer_endpoint": optimizer_endpoint,
        "edit_budget": edit_budget,
        "minibatch_size": minibatch_size,
        "reflect_on_success": reflect_on_success,
        **read_optimizer_params_for_logging(),
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
        cost_meter=meter,
        probe=probe,
    )


if __name__ == "__main__":
    train_skillopt()
