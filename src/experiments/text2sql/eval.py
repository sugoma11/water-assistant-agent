"""Text-2-SQL evaluation with MLflow + litellm.

Generates DuckDB SQL with a student model (via litellm against an OpenAI-compatible
endpoint), then scores each prediction FLEX-style: both queries are executed against
the DuckDB database, BIRD execution accuracy decides the judge branch (EQ =
false-positive check without results, NEQ = false-negative check with results), and
the LLM-as-Judge delivers the final correct/incorrect verdict on every sample.
Everything is logged to MLflow: global params, the ground-truth dataset (artifact +
hash), all prompts, an aggregate quality score in [0, 1], and per-sample traces
carrying the question, argilla link, generated SQL, EX outcome, judge branch and
rationale (as MLflow ``Feedback``).

The generic building blocks (``load_dataset``, ``create_predict_fn``,
``build_sql_judge_scorer``, ``setup_mlflow``, ``log_global_params`` ...) live in
``experiments.text2sql.harness`` so a future GEPA-style ``train`` setup can reuse them.
"""

import os

import click
import mlflow
from dotenv import load_dotenv
from mlflow.genai import evaluate

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.text2sql.core import (
    format_schema_for_prompt,
    load_schema,
)
from experiments.text2sql.harness import (
    ENDPOINTS,
    build_sql_judge_scorer,
    create_predict_fn,
    load_dataset,
    log_global_params,
    make_run_name,
    setup_mlflow,
)


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
@click.option("--model", required=True, help="Generation model, e.g. openai/glm-4.7")
@click.option(
    "--endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for generation.",
)
@click.option("--judge-model", required=True, help="LLM-as-Judge model, e.g. openai/...")
@click.option(
    "--judge-endpoint",
    required=True,
    type=click.Choice(sorted(ENDPOINTS)),
    help="Inference endpoint for the LLM-as-Judge.",
)
@click.option(
    "--use-prod-questions",
    is_flag=True,
    default=False,
    help="Feed the production-style 'prod_question' phrasing to the model "
    "instead of the clean 'question'.",
)
def eval(
    questions_path: str,
    schema_path: str,
    db_path: str,
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    use_prod_questions: bool,
) -> None:
    """Evaluate text-2-SQL generation and log results to MLflow."""
    load_dotenv()

    experiment_name = os.environ.get("MLFLOW_EVAL_EXPERIMENT_NAME")
    if not experiment_name:
        raise click.ClickException("MLFLOW_EVAL_EXPERIMENT_NAME is not set in the environment.")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    setup_mlflow(tracking_uri, experiment_name)

    schema_text = format_schema_for_prompt(load_schema(schema_path))
    data = load_dataset(questions_path, use_prod_questions)
    predict_fn = create_predict_fn(model, endpoint, schema_text)
    judge_scorer = build_sql_judge_scorer(
        judge_model, judge_endpoint, schema_text, db_path
    )

    click.echo(f"Running evaluation on {len(data)} samples...")
    with mlflow.start_run(run_name=make_run_name(model)):
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
        results = evaluate(data=data, predict_fn=predict_fn, scorers=[judge_scorer])
        quality = results.metrics.get("sql_is_correct/mean", 0.0)
        mlflow.log_metric("final_quality", quality)

    click.echo(f"Final quality: {quality:.2%}")


if __name__ == "__main__":
    eval()
