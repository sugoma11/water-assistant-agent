"""GEPA training of the text-2-SQL system prompt with MLflow + litellm.

Optimizes ``text2sql_system`` (registered from the in-code SYSTEM_PROMPT_TEMPLATE)
with ``mlflow.genai.optimize_prompts`` + ``GepaPromptOptimizer``, reusing the eval
building blocks from ``experiments.text2sql.harness``: the dataset loader, the
litellm predict_fn and the FLEX-style LLM-as-Judge scorer (its boolean Feedback is
auto-converted to 1.0/0.0 by MLflow's metric aggregation).

The dataset is split by the seeded sampler under the 20 / 0 / 55 scheme
(``specs/sampling_refactoring/spec.md``): GEPA reflects on minibatches from the
20-record train split and Pareto-scores candidates on a val split that **is** that
same train split (``split_dataset`` returns a copy of train as val, D1), so all
val-driven machinery runs unchanged — on training data.
There are no explicit val eval phases: GEPA fully evaluates the seed prompt (and
every accepted candidate) on the valset (gepa/core/engine.py), so the baseline and
best-candidate val scores come straight back as ``result.initial_eval_score`` /
``result.final_eval_score``, which optimize_prompts already logs on this run as
``{initial,final}_eval_score`` (no need to re-log them). Those two are therefore
*training* scores and measure no generalization (C4); the only generalization signal
is the held-out test split, evaluated by hand on 55 records
(``test_quality_{before,after}``), which GEPA never sees.

The only stop is the money budget (FR2): a ``BudgetStopper`` checked by the engine
at each iteration boundary, wired through ``gepa_kwargs["stop_callbacks"]`` while
``max_metric_calls`` becomes a never-firing sentinel. Of GEPA's valset passes, only
the seed pass is a bracketing eval — it runs before the first stopper invocation
and is moved to the excluded bucket by the stopper's first-call snapshot (D3), and
under the 20 / 0 / 55 split it covers 20 records rather than 25; the
best candidate's score is read back from its acceptance-time full-val pass, which
is part of the search and therefore billable like every other candidate eval
(exclusion semantics confirmed 2026-07-03, plan revision log). Reflection spend is
metered to the ``optimizer`` role by a litellm success callback, since GEPA calls
the teacher inside the library without our completion kwargs.

Teacher (reflection) model note: GEPA calls litellm WITHOUT api_base/api_key, so
``configure_teacher_env`` points the ``OPENAI_API_BASE``/``OPENAI_API_KEY`` fallback
at the chosen endpoint — no global litellm config is needed.
"""

import os
from pathlib import Path
from typing import Any

import click
import litellm
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
    REASONING_EFFORT,
    apply_openrouter_provider,
    configure_teacher_env,
    llm_retry,
    load_dataset,
    register_prompt_if_changed,
    setup_mlflow,
    to_mlflow_model_uri,
    validate_teacher_model,
)
from experiments.text2sql.cost_meter import (
    BudgetStopper,
    CostMeter,
    litellm_reflection_callback,
)
from experiments.text2sql.learning_curve import build_probe
from experiments.text2sql.sampler import split_dataset
from experiments.text2sql.train_common import (
    PROMPT_NAME,
    _run_optimization,
    read_price_config,
)

# The money budget is the only real stop (FR2): MLflow always sets max_metric_calls on
# gepa.optimize, so it becomes a huge sentinel whose MaxMetricCallsStopper never fires
# next to the BudgetStopper (CompositeStopper, "any" mode). Its only other use is the
# tqdm progress-bar denominator (cosmetic, T015c).
MAX_METRIC_CALLS_SENTINEL = 10**9


def gepa_best_template(gepa_state: Any, prompt_name: str) -> str | None:
    """GEPA's best-so-far candidate for the learning curve (LC-FR2), read off the engine
    state the stopper is handed at each iteration boundary.

    This is ``FullEvaluationPolicy.get_best_program`` inlined (gepa 0.1.1,
    ``gepa/strategies/eval_policy.py``): the candidate with the highest mean over its
    evaluated valset subscores, ties broken by coverage. The engine applies that same
    rule to pick the ``best_candidate`` it finally returns, so a probe measures exactly
    the prompt this run would deliver if the budget stopped it here — not the candidate
    that happens to be under evaluation.

    Inlined rather than called because the policy object is not reachable from a
    ``StopperProtocol`` callback, which only receives the state. That couples this to
    gepa's state attributes, so it returns ``None`` (a warned, skipped curve point)
    instead of raising if a version bump moves them (LC-R2)."""
    subscores = getattr(gepa_state, "prog_candidate_val_subscores", None)
    candidates = getattr(gepa_state, "program_candidates", None)
    if not subscores or not candidates:
        return None
    best_idx, best_avg, best_coverage = -1, float("-inf"), -1
    for idx, scores in enumerate(subscores):
        coverage = len(scores)
        avg = sum(scores.values()) / coverage if coverage else float("-inf")
        if avg > best_avg or (avg == best_avg and coverage > best_coverage):
            best_idx, best_avg, best_coverage = idx, avg, coverage
    if best_idx < 0 or best_idx >= len(candidates):
        return None
    return candidates[best_idx].get(prompt_name)


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
    budget: float,
    probe_interval_eur: float,
    probe_workers: int,
    max_probes: int,
    sampler_seed: int,
    use_prod_questions: bool,
) -> None:
    """Train the text-2-SQL system prompt with GEPA and log results to MLflow."""
    # validate_teacher_model(teacher_model)
    load_dotenv()

    # Validate prices + construct the meter before any MLflow run exists, so a
    # misconfigured price env refuses to start with the offending var named (EC5, SC6).
    meter = CostMeter(budget, read_price_config())

    experiment_name = os.environ.get("MLFLOW_TRAIN_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_TRAIN_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)
    configure_teacher_env(teacher_endpoint)

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
    # GEPA's natural checkpoint IS the stopper call, so the learning-curve hook rides it
    # (LC-D1). The first invocation doubles as a validation of the state-shape coupling
    # (LC-R2): it happens after the seed full-val pass but before any iteration, so the
    # extraction must yield the seed template. A warning here surfaces a gepa version
    # bump within minutes of the run starting; it deliberately does not raise, because
    # instrumentation must never kill an hours-long training run (LC-FR9).
    checkpoint_validated = False

    def on_checkpoint(gepa_state: Any) -> None:
        nonlocal checkpoint_validated
        if not checkpoint_validated:
            checkpoint_validated = True
            if gepa_best_template(gepa_state, PROMPT_NAME) is None:
                click.echo(
                    "WARNING: GEPA best-candidate extraction returned nothing at the "
                    "first checkpoint; the learning curve will have no interior points "
                    "for this run. gepa's GEPAState layout has probably changed -- see "
                    "gepa_best_template (LC-R2)."
                )
        probe.maybe_probe(lambda: gepa_best_template(gepa_state, PROMPT_NAME))

    gepa_kwargs: dict[str, Any] = {
        "valset": val_set,
        "seed": sampler_seed,
        # The engine checks stop callbacks at each iteration boundary, and only after
        # the seed full-val pass (gepa/core/engine.py: seed eval precedes the main
        # loop), so the stopper's first-call snapshot moves exactly that pass into the
        # excluded bucket (D3, T015a). Every later full-val pass belongs to an accepted
        # candidate — part of the search, billable.
        "stop_callbacks": [BudgetStopper(meter, on_checkpoint=on_checkpoint)],
    }
    reflection_prompt_template = gepa_kwargs.get(
        "reflection_prompt_template", InstructionProposalSignature.default_prompt_template
    )
    click.echo(f"Running GEPA (budget {meter.budget} EUR)...")
    # GEPA calls the reflection model inside the library (plain litellm.completion, no
    # cost_meter_role tag), so a litellm success callback meters those calls to the
    # `optimizer` role. It only records while this meter is active — the optimization
    # phase proper — so the test-before/after phases inside this bracket stay unmetered
    # (FR12/EC3); registration is scoped to the GEPA run because only GEPA produces
    # untagged litellm calls.
    reflection_callback = litellm_reflection_callback(meter)
    litellm.success_callback.append(reflection_callback)
    # GEPA calls the teacher as `litellm.completion(model=..., messages=...)` inside the
    # library (gepa/api.py) with none of our sampling kwargs, so it would otherwise reflect
    # at the endpoint's default effort AND abort the whole run on the first transient
    # error (502/503/disconnect) — the in-library call never routes through
    # completion_with_retry. Wrap litellm.completion for the duration of the run to give
    # the bare teacher call the frozen REASONING_EFFORT and the harness llm_retry policy.
    # `import litellm; litellm.completion(...)` resolves the attribute per call, so patching
    # the module attribute intercepts the in-library teacher call too.
    original_completion = litellm.completion
    retrying_completion = llm_retry(original_completion)

    def _completion_with_frozen_effort(*args: Any, **kwargs: Any) -> Any:
        # Our own task/judge calls arrive here THROUGH completion_with_retry (it too
        # resolves litellm.completion per call) already carrying reasoning_effort
        # (build_completion_kwargs); pass them straight to the original so their retry
        # policy isn't nested into attempts^2. The teacher call carries no
        # reasoning_effort, so it gets the effort, the allowed_openai_params escape
        # hatch that stops litellm rejecting the param client-side for our self-hosted
        # aliases, and the retry policy.
        if "reasoning_effort" in kwargs:
            return original_completion(*args, **kwargs)
        kwargs["reasoning_effort"] = REASONING_EFFORT
        kwargs.setdefault("allowed_openai_params", ["reasoning_effort"])
        # ...and, on an OpenRouter teacher, the run's provider pin. This patch is
        # process-wide, but only the teacher branch is pinned and only against the
        # *teacher's* endpoint, so a student/judge on kisski/blablador (whose calls
        # returned above already carrying their own endpoint's pin, or none) can never
        # be handed an OpenRouter-only body field.
        apply_openrouter_provider(kwargs, teacher_endpoint)
        return retrying_completion(*args, **kwargs)

    litellm.completion = _completion_with_frozen_effort
    try:
        _run_optimization(
            optimizer=GepaPromptOptimizer(
                reflection_model=to_mlflow_model_uri(teacher_model),
                max_metric_calls=MAX_METRIC_CALLS_SENTINEL,
                display_progress_bar=True,
                gepa_kwargs=gepa_kwargs,
            ),
            technique="gepa",
            extra_params={
                "teacher_model": teacher_model,
                "teacher_endpoint": teacher_endpoint,
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
            cost_meter=meter,
            probe=probe,
            extra_artifacts={"reflection_prompt_template.txt": reflection_prompt_template},
        )
    finally:
        litellm.completion = original_completion
        litellm.success_callback.remove(reflection_callback)


if __name__ == "__main__":
    train_gepa()
