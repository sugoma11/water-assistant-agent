"""Shared building blocks for text-2-SQL experiments (eval and train).

These helpers are intentionally generic so both the MLflow evaluation script and a
future GEPA-style ``train`` setup can reuse them verbatim: endpoint resolution and
litellm kwargs, dataset loading, SQL generation, the LLM-as-Judge scorer, and MLflow
setup / global-param logging.
"""

import hashlib
import json
import logging
import numbers
import os
import subprocess
from collections.abc import Callable, Hashable
from datetime import datetime
from typing import Any

import click
import duckdb
import litellm
import mlflow
from mlflow.entities import Feedback
from mlflow.entities.model_registry import PromptVersion
from mlflow.genai import scorer
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from experiments.text2sql.cost_meter import active_meter, current_role
from water_assistant_agent.text2sql.core import (
    SYSTEM_PROMPT_TEMPLATE,
    USER_PROMPT_TEMPLATE,
    clean_sql,
)



# Endpoint name -> (api_base env var, api_key env var). Both endpoints are
# OpenAI-compatible, so the model identifier must carry an ``openai/`` prefix.
ENDPOINTS: dict[str, tuple[str, str]] = {
    "kisski": ("LLM_API_BASE_KISSKI", "LLM_API_KEY_KISSKI"),
    "blablador": ("LLM_API_BASE_BLABLADOR", "LLM_API_KEY_BLABLADOR"),
}


# ---------------------------------------------------------------------------
# LLM-as-Judge (FLEX-style, two branches)
# ---------------------------------------------------------------------------
# Prompts adapted from FLEX (https://arxiv.org/abs/2409.19014, NAACL 2025;
# https://github.com/HeegyuKim/FLEX utils/judge/base.py): after executing both
# queries, the EQ branch hunts for false positives WITHOUT showing the (matching)
# execution results, while the NEQ branch hunts for false negatives WITH both
# results included. Changes vs. upstream: SQLite3 compatibility notes replaced
# with DuckDB ones, BIRD-specific DB hints dropped, and the raw ```json
# {"correct": ...}``` output replaced by our structured SqlJudgeResponse
# (explanation first, then boolean score).
SQL_JUDGE_EQ_SYSTEM_PROMPT = """\
The Prediction Result matches the Ground Truth Result. However, this does not \
guarantee that the Prediction Query is correct. Carefully analyze the Prediction \
Query and evaluate its correctness considering the following criteria:

Correct Prediction Query
- If the Prediction Query missed some tables or columns, it is acceptable if the \
missing information does not affect the query's ability to answer the Question.
- Missing DISTINCT in SELECT is always allowed because we do not consider duplicate rows.

Incorrect Prediction Query
- Does not logically answer the Question or contains significant errors.
- Produces different results due to incorrect filtering or missing conditions, \
JOIN redundancy, or other fatal issues.
- Fails to handle null values, multiple rows, or other critical aspects of the Question.
- Does not consider nullable columns in aggregation functions (SUM, COUNT, AVG); \
NULL values can lead to unexpected results. For example, COUNT(*) in the prediction \
but COUNT(school) in the ground truth will produce different results if the column \
school does not have a NOT NULL constraint in the schema.
- Does not produce correct results when multiple rows satisfy the condition \
(e.g. min/max, multiple transactions in a day).
- Abused clauses (LIMIT, GROUP BY) to limit the results when the user didn't request it.

DuckDB Compatibility
- Both queries are DuckDB SQL: `/` performs float division (`//` is integer \
division), and logical operators, column/table names are case-insensitive.

Analysis Guidelines
1. Compare the Prediction Query with the Ground Truth Query within the context of \
the provided schema and question.
2. Predict the query's logical correctness based on the criteria mentioned above.

Respond with a JSON object: an "explanation" with your analysis first, then a \
boolean "score" (true if the Prediction Query is correct).
"""

SQL_JUDGE_EQ_USER_PROMPT_TEMPLATE = """\
**Schema**
{schema}

**Question**
{question}

**Prediction Query**
{generated_sql}

**Ground Truth Query**
{reference_sql}

Note: the two queries have the same execution results.
"""

SQL_JUDGE_NEQ_SYSTEM_PROMPT = """\
The Prediction Result differs from the Ground Truth Result. However, this does not \
necessarily mean that the Prediction Query is incorrect. Analyze the differences \
between the Prediction Query and Ground Truth Query, considering the following:

Correct
- The Prediction Query logically answers the Question, even if the output structure \
differs from the Ground Truth Query.
- Do not consider column naming, column/row ordering.
- Some extra column or missing column in the output structure is acceptable if it \
does not affect the query's ability to answer the Question.
- Differences in the representation of values, such as formatting (percentile, \
YES/NO) or data types, are acceptable if they do not affect the query's logical \
correctness.
- Ambiguous questions may have multiple correct answers, so the Prediction Query \
may differ from the Ground Truth Query.
- Multiple rows are acceptable when calculating the min/max.

Incorrect
- The Prediction Query does not logically answer the Question or contains \
significant errors.
- The Prediction Query produces different results due to incorrect filtering or \
missing conditions, JOIN redundancy, or other fatal issues.
- The Prediction Query fails to handle null values, multiple rows, or other \
critical aspects of the Question.
- The result of the Prediction Query is significantly different from the Ground \
Truth Query even if its structure is similar.
- The Prediction Query fails to execute (its result is an execution error).

DuckDB Compatibility
- Both queries are DuckDB SQL: `/` performs float division (`//` is integer \
division), and logical operators, column/table names are case-insensitive.
- If the table schema and description are different, follow the schema provided in \
the prompt.

Provide a detailed comparison of the Prediction Query and Ground Truth Query, \
focusing on the nature and significance of their differences. If the Prediction \
Query is incorrect, explain the specific errors and how they affect the query's \
ability to answer the Question.

Respond with a JSON object: an "explanation" with your analysis first, then a \
boolean "score" (true if the Prediction Query is correct).
"""

SQL_JUDGE_NEQ_USER_PROMPT_TEMPLATE = """\
**Schema**
{schema}

**Question**
{question}

**Prediction Query**
{generated_sql}

**Prediction Result**
{generated_result}

**Ground Truth Query**
{reference_sql}

**Ground Truth Result**
{reference_result}

Note: the two queries have different execution results.
"""


class SqlJudgeResponse(BaseModel):
    explanation: str
    score: bool


# ---------------------------------------------------------------------------
# litellm helpers
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# Transient litellm/OpenAI errors worth retrying instead of aborting a whole
# (potentially hours-long) GEPA run: server disconnects surface as
# APIConnectionError or get mapped to InternalServerError, plus the usual
# timeout / rate-limit / 503 blips. Non-transient errors (BadRequest, auth, ...)
# fall through and raise immediately.
RETRYABLE_LLM_ERRORS: tuple[type[Exception], ...] = (
    litellm.APIConnectionError,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
    litellm.Timeout,
    litellm.RateLimitError,
    litellm.APIError,
)

# Env-tunable retry budget; 0 disables retries (raises on the first error).
LLM_MAX_ATTEMPTS = max(1, int(os.environ.get("LLM_MAX_ATTEMPTS", "10")))


# Deterministic (no-jitter) exponential backoff: ~8, 16, 32, 60, 60s. Runs on a
# single eval worker, so there's nothing to de-sync; predictable slow retries give
# a flaky endpoint real time to recover.
@retry(
    retry=retry_if_exception_type(RETRYABLE_LLM_ERRORS),
    wait=wait_exponential(multiplier=8, min=8, max=60),
    stop=stop_after_attempt(LLM_MAX_ATTEMPTS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def completion_with_retry(**kwargs: Any) -> Any:
    """``litellm.completion`` wrapped in tenacity exponential backoff so a single
    dropped connection (or rate-limit blip) doesn't waste a whole training run.
    Only :data:`RETRYABLE_LLM_ERRORS` are retried; everything else raises at once.

    On success, usage is recorded to the active :class:`CostMeter` under the role
    contextvar (a no-op outside ``meter.active()`` — standalone eval and the
    test-before/after phases, FR12/NFR4). A response with no usage data is logged as an
    unmetered call rather than silently dropped (EC2, FR11)."""
    resp = litellm.completion(**kwargs)
    _record_to_active_meter(resp)
    return resp


def _record_to_active_meter(resp: Any) -> None:
    """Record ``resp`` usage to the active cost meter under the current role contextvar.
    Missing usage (or a missing/unknown role, R6) becomes an unmetered call with a loud
    warning instead of a silently missed charge (EC2)."""
    meter = active_meter()
    if meter is None:
        return
    role = current_role()
    usage = getattr(resp, "usage", None)
    if usage is None:
        meter.record_unmetered(role)
        return
    meter.record(role, usage.prompt_tokens, usage.completion_tokens)


def read_sampling_params(prefix: str) -> dict[str, Any]:
    """Read env-driven sampling params under ``{prefix}_*`` (e.g. ``LLM`` for
    generation, ``JUDGE`` for the LLM-as-Judge). ``top_k`` is only included when set.

    ``max_tokens`` defaults high (300000) so a *thinking* model's long reasoning is not
    truncated into an empty (``content=None``) completion -- TextGrad's engine otherwise
    caps at 2000, which a reflection/backward pass can blow past easily. Override per
    role via ``{prefix}_MAX_TOKENS`` if an endpoint rejects a value above its
    ``max_model_len``."""
    params: dict[str, Any] = {
        "temperature": float(os.environ.get(f"{prefix}_TEMPERATURE", "0.7")),
        "top_p": float(os.environ.get(f"{prefix}_TOP_P", "0.9")),
        "max_tokens": int(os.environ.get(f"{prefix}_MAX_TOKENS", "44000")),
        "seed": int(os.environ.get(f"{prefix}_SEED", "42")),
    }
    top_k = os.environ.get(f"{prefix}_TOP_K")
    if top_k is not None:
        params["top_k"] = int(top_k)
    return params


def read_optimizer_params_for_logging() -> dict[str, Any]:
    """``OPTIMIZER_*`` sampling params (for the TextGrad backward/proposal model),
    keyed with an ``optimizer_`` prefix so they log cleanly as run params without
    colliding with the unprefixed ``LLM`` generation params (``temperature``,
    ``seed``, ...).

    Logged on the TextGrad run only -- passed through ``train_textgrad``'s
    ``extra_params``, never added to the shared ``log_global_params`` -- so GEPA runs
    keep byte-identical params (FR8). Mirrors how the ``JUDGE_*`` params are logged
    with a ``judge_`` prefix in :func:`log_global_params`."""
    return {
        f"optimizer_{key}": value
        for key, value in read_sampling_params("OPTIMIZER").items()
    }


def read_endpoint_credentials(endpoint: str) -> tuple[str, str]:
    """Resolve an ``ENDPOINTS`` entry's ``(api_base, api_key)`` from the environment.

    Raises an actionable :class:`ValueError` when the role's ``*_API_BASE`` /
    ``*_API_KEY`` env vars are unset, instead of the bare ``KeyError`` an
    ``os.environ[...]`` read would otherwise surface (TextGrad retrospective finding
    #4); a missing credential is then a clear "set this env var" failure rather than an
    opaque crash. Callers validate endpoint-name membership (CLI ``click.Choice`` or the
    friendlier lookup in the caller), so this assumes ``endpoint`` is a known key."""
    api_base_var, api_key_var = ENDPOINTS[endpoint]
    missing = [name for name in (api_base_var, api_key_var) if not os.environ.get(name)]
    if missing:
        raise ValueError(
            f"Endpoint {endpoint!r} is selected but its credential env var(s) "
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} unset; set "
            f"{'it' if len(missing) == 1 else 'them'} in the environment (see "
            ".example.env) before optimizing."
        )
    return os.environ[api_base_var], os.environ[api_key_var]


# litellm sampling-param prefix -> cost-meter role for the metadata tag. Only ``LLM``
# (generation/task) and ``JUDGE`` reach ``build_completion_kwargs`` today; the mapping is
# defensive for any future prefix.
_PARAM_PREFIX_ROLE = {"LLM": "task", "JUDGE": "judge", "OPTIMIZER": "optimizer"}


def build_completion_kwargs(
    model: str, endpoint: str, param_prefix: str = "LLM"
) -> dict[str, Any]:
    """Assemble litellm.completion kwargs for the chosen endpoint and env-driven
    sampling params. ``param_prefix`` selects the env var family for the sampling
    params: ``LLM`` for generation, ``JUDGE`` for the LLM-as-Judge.

    Injects ``metadata={"cost_meter_role": <role>}`` so the GEPA reflection callback can
    dedupe these already-metered task/judge calls from the library-internal reflection
    calls it needs to attribute to ``optimizer`` (D1-3)."""
    try:
        ENDPOINTS[endpoint]
    except KeyError as exc:
        raise click.BadParameter(
            f"Unknown endpoint {endpoint!r}; expected one of {sorted(ENDPOINTS)}"
        ) from exc

    api_base, api_key = read_endpoint_credentials(endpoint)
    role = _PARAM_PREFIX_ROLE.get(param_prefix, param_prefix.lower())
    return {
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
        "metadata": {"cost_meter_role": role},
        **read_sampling_params(param_prefix),
    }


def validate_teacher_model(model: str) -> str:
    """Fail fast on a teacher model whose provider prefix isn't ``openai``.

    GEPA calls the reflection model via litellm's openai-provider env fallback
    (see ``configure_teacher_env``), so only ``openai/<name>`` (or the converted
    ``openai:/<name>``) can work here. Anything else — including provider typos
    like ``openain/<name>`` — would otherwise surface only mid-run, as a swallowed
    per-iteration reflection error, after the budget is already being spent.
    """
    provider = model.partition(":/" if ":/" in model else "/")[0]
    if provider != "openai":
        raise click.BadParameter(
            f"teacher model {model!r} has provider prefix {provider!r}, but the "
            "GEPA reflection call only supports 'openai/<name>' (the teacher "
            "endpoint is wired through the OPENAI_API_BASE/OPENAI_API_KEY "
            "fallback). Did you mistype the prefix?"
        )
    return model


def configure_teacher_env(endpoint: str) -> None:
    """Point litellm's openai-provider fallback at the teacher endpoint.

    GEPA calls the reflection model as ``litellm.completion(model="openai/<x>")``
    without ``api_base``/``api_key`` (gepa/api.py), so litellm falls back to the
    ``OPENAI_API_BASE``/``OPENAI_API_KEY`` env vars. Student and judge calls pass
    explicit kwargs which take precedence, so this never affects them.
    """
    api_base_var, api_key_var = ENDPOINTS[endpoint]
    os.environ["OPENAI_API_BASE"] = os.environ[api_base_var]
    os.environ["OPENAI_API_KEY"] = os.environ[api_key_var]


def to_mlflow_model_uri(model: str) -> str:
    """Convert a litellm-style model id (``openai/x``) to the MLflow model URI
    format (``openai:/x``) expected by ``GepaPromptOptimizer``.

    Idempotent: an already-converted ``openai:/x`` is returned unchanged.
    (Double-converting would yield ``openai::/x``, which MLflow's
    ``_parse_model_uri`` splits on ``:/`` into provider ``openai:`` — handing
    litellm the provider-less ``openai:/x`` and crashing the reflection call.)
    """
    if ":/" in model:
        return model
    provider, _, name = model.partition("/")
    return f"{provider}:/{name}"


def extract_usage(resp: Any, prefix: str) -> dict[str, str]:
    """Flatten litellm response usage into ``{prefix}_*_tokens`` string entries
    (Feedback metadata values must be strings). Missing usage -> empty dict."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}
    return {
        f"{prefix}_prompt_tokens": str(usage.prompt_tokens),
        f"{prefix}_completion_tokens": str(usage.completion_tokens),
        f"{prefix}_total_tokens": str(usage.total_tokens),
    }


# ---------------------------------------------------------------------------
# Data loading (kept out of the script so train can reuse it verbatim)
# ---------------------------------------------------------------------------
def load_dataset(
    questions_path: str, use_prod_questions: bool = False
) -> list[dict[str, Any]]:
    """Load a JSON list of {"question", "prod_question", "sql", "argilla_link"}
    records into the MLflow GenAI evaluation format. When ``use_prod_questions`` is
    set, the production-style ``prod_question`` phrasing is fed to the model instead
    of the clean ``question`` (the output key stays ``question`` so downstream code
    is unchanged)."""
    question_key = "prod_question" if use_prod_questions else "question"
    with open(questions_path) as f:
        records = json.load(f)
    return [
        {
            "inputs": {"question": rec[question_key]},
            "expectations": {
                "sql": rec["sql"],
                "argilla_link": rec.get("argilla_link", ""),
            },
        }
        for rec in records
    ]


# ---------------------------------------------------------------------------
# Generation (shared by eval and train)
# ---------------------------------------------------------------------------
def render_system_prompt(template: str, schema_text: str) -> str:
    """Substitute the schema into a system prompt template.

    Uses str.replace instead of str.format: GEPA-mutated templates may contain
    literal braces (e.g. SQL examples) that would crash str.format. If a mutated
    candidate lost the ``{schema}`` placeholder, append the schema so the student
    model never flies blind.
    """
    if "{schema}" in template:
        return template.replace("{schema}", schema_text)
    return f"{template}\n\nSchema:\n\n{schema_text}"


def _make_predict_fn(
    completion_kwargs: dict[str, Any], get_system_prompt: Callable[[], str]
) -> Callable[[str], dict[str, str]]:
    """Shared litellm-call body for the static and optimizable predict_fns.
    ``get_system_prompt`` is called per prediction so dynamic sources (the
    PromptVersion template patched by ``optimize_prompts``) are picked up."""

    def predict_fn(question: str) -> dict[str, str]:
        user_prompt = USER_PROMPT_TEMPLATE.format(question=question)
        resp = completion_with_retry(
            messages=[
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            **completion_kwargs,
        )
        sql = clean_sql(resp.choices[0].message.content or "")
        return {"sql": sql, **extract_usage(resp, "generation")}

    return predict_fn


def create_predict_fn(
    model: str,
    endpoint: str,
    schema_text: str,
    system_prompt_template: str = SYSTEM_PROMPT_TEMPLATE,
) -> Callable[[str], dict[str, str]]:
    """Build a predict_fn that generates DuckDB SQL for a question via litellm."""
    completion_kwargs = build_completion_kwargs(model, endpoint)
    system_prompt = render_system_prompt(system_prompt_template, schema_text)
    return _make_predict_fn(completion_kwargs, lambda: system_prompt)


def create_optimizable_predict_fn(
    model: str, endpoint: str, schema_text: str, prompt_version: PromptVersion
) -> Callable[[str], dict[str, str]]:
    """Build a predict_fn whose system prompt is read from ``prompt_version`` on
    EVERY call. ``mlflow.genai.optimize_prompts`` injects candidate prompts by
    patching ``PromptVersion.template``, so the template must be accessed at call
    time — a closure-baked prompt would never see GEPA's mutations (and MLflow
    would warn that the prompt was not used during evaluation)."""
    completion_kwargs = build_completion_kwargs(model, endpoint)
    return _make_predict_fn(
        completion_kwargs,
        lambda: render_system_prompt(prompt_version.template, schema_text),
    )


# ---------------------------------------------------------------------------
# Execution accuracy (original BIRD EX) + result rendering (ported from FLEX)
# ---------------------------------------------------------------------------
def run_query(cursor: "duckdb.DuckDBPyConnection", sql: str) -> tuple[list[tuple] | None, str | None]:
    """Execute SQL on a DuckDB cursor. Returns ``(rows, None)`` on success or
    ``(None, error message)`` on failure."""
    try:
        return cursor.execute(sql).fetchall(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _hashable_row(row: tuple) -> tuple:
    """DuckDB can return unhashable cells (lists/dicts for LIST/STRUCT columns);
    fall back to their string form so the set comparison still applies."""
    return tuple(cell if isinstance(cell, Hashable) else str(cell) for cell in row)


def execution_match(predicted_res: list[tuple], ground_truth_res: list[tuple]) -> bool:
    """Original BIRD execution-accuracy comparison (DAMO-ConvAI
    bird/llm/src/evaluation.py::execute_sql): order- and duplicate-insensitive
    set equality over the fetched rows."""
    return {_hashable_row(r) for r in predicted_res} == {
        _hashable_row(r) for r in ground_truth_res
    }


def format_execution_result(
    rows: list[tuple], max_str_length: int = 30, max_rows: int = 20
) -> str:
    """Render rows for the NEQ judge prompt, ported from FLEX
    (utils/prompts.py::execution_result2text_set): deduplicated rows as pipe-separated
    lines, truncated to the first/last 10 when long, plus the shape."""
    if not rows:
        return "Empty execution result"

    def fmt(cell: Any) -> str:
        if isinstance(cell, numbers.Number):
            return str(cell)
        text = str(cell).strip().replace("\n", " ")
        if len(text) > max_str_length:
            return f"{text[:max_str_length]}... {len(text)} chars"
        return text

    unique = list(dict.fromkeys(_hashable_row(r) for r in rows))
    num_rows, num_cols = len(unique), len(unique[0])
    shown: list[tuple | None] = (
        unique[:10] + [None] + unique[-10:] if num_rows > max_rows else unique
    )
    lines = [
        "..." if row is None else "| " + " | ".join(fmt(c) for c in row) + " |"
        for row in shown
    ]
    return "\n".join(lines) + f"\nshape=({num_rows}, {num_cols})"


# ---------------------------------------------------------------------------
# Scoring (shared by eval and train)
# ---------------------------------------------------------------------------
def build_sql_judge_scorer(
    judge_model: str, judge_endpoint: str, schema_text: str, db_path: str
) -> Callable[..., Feedback]:
    """Build a FLEX-style MLflow scorer: execute both queries against the DuckDB
    database, compute BIRD execution accuracy, then call the LLM-as-Judge on EVERY
    prediction — with the EQ prompt (false-positive check, no execution results
    shown) when results match, or the NEQ prompt (false-negative check, both
    results shown) when they differ or a query fails to execute.

    Returns a single ``Feedback`` per sample whose value is the correct/incorrect
    label and whose metadata carries the question, argilla link, generated SQL,
    EX outcome and judge branch, so all sample-wise fields land on the trace
    assessment.
    """
    completion_kwargs = build_completion_kwargs(
        judge_model, judge_endpoint, param_prefix="JUDGE"
    )
    con = duckdb.connect(db_path, read_only=True)

    def sql_is_correct(
        inputs: dict[str, Any],
        outputs: dict[str, Any] | str | None,
        expectations: dict[str, Any],
    ) -> Feedback:
        question = inputs.get("question", "")
        argilla_link = expectations.get("argilla_link", "")
        reference_sql = expectations.get("sql", "")
        # MLflow's optimize loop swallows predict_fn exceptions and replaces the
        # outputs with a plain error STRING (optimize.py::_run_single), so outputs
        # is not always the {"sql": ...} dict we return. Normalise that to "no SQL"
        # and surface the underlying predict_fn error in the rationale.
        if not isinstance(outputs, dict):
            return Feedback(
                name="sql_is_correct",
                value=False,
                rationale=(
                    f"predict_fn produced no SQL output: {outputs}"
                    if outputs
                    else "Model produced no SQL."
                ),
                metadata={
                    "question": str(question),
                    "argilla_link": str(argilla_link),
                    "generated_sql": "",
                },
            )
        generated_sql = outputs.get("sql", "")
        metadata = {
            "question": str(question),
            "argilla_link": str(argilla_link),
            "generated_sql": str(generated_sql),
            **{
                k: str(v)
                for k, v in outputs.items()
                if k.endswith("_tokens")
            },
        }
        if not generated_sql:
            return Feedback(
                name="sql_is_correct",
                value=False,
                rationale="Model produced no SQL.",
                metadata=metadata,
            )

        # ``con.cursor()`` opens a per-call cursor on the shared read-only
        # connection so the scorer stays safe under parallel scorer workers.
        cursor = con.cursor()
        try:
            gen_rows, gen_error = run_query(cursor, generated_sql)
            ref_rows, ref_error = run_query(cursor, reference_sql)
        finally:
            cursor.close()

        ex = gen_error is None and ref_error is None and execution_match(gen_rows, ref_rows)
        metadata["ex_match"] = str(ex)
        metadata["judge_branch"] = "eq" if ex else "neq"
        if gen_error is not None:
            metadata["generated_sql_error"] = gen_error
        if ref_error is not None:
            metadata["reference_sql_error"] = ref_error

        if ex:
            system_prompt = SQL_JUDGE_EQ_SYSTEM_PROMPT
            prompt = SQL_JUDGE_EQ_USER_PROMPT_TEMPLATE.format(
                schema=schema_text,
                question=question,
                generated_sql=generated_sql,
                reference_sql=reference_sql,
            )
        else:
            system_prompt = SQL_JUDGE_NEQ_SYSTEM_PROMPT
            prompt = SQL_JUDGE_NEQ_USER_PROMPT_TEMPLATE.format(
                schema=schema_text,
                question=question,
                generated_sql=generated_sql,
                generated_result=(
                    f"Execution error: {gen_error}"
                    if gen_error is not None
                    else format_execution_result(gen_rows)
                ),
                reference_sql=reference_sql,
                reference_result=(
                    f"Execution error: {ref_error}"
                    if ref_error is not None
                    else format_execution_result(ref_rows)
                ),
            )
        try:
            resp = completion_with_retry(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format=SqlJudgeResponse,
                **completion_kwargs,
            )
            # Record judge usage before parsing so it survives JSON-validation
            # failures (the except branch reuses this metadata dict).
            usage = extract_usage(resp, "judge")
            metadata.update(usage)
            verdict = SqlJudgeResponse.model_validate_json(
                resp.choices[0].message.content
            )
        except Exception as e:
            # Never let a judge failure (request error, empty/non-JSON content) raise
            # out of the scorer: that would leave the prediction trace with no
            # assessment at all. Emit a failed Feedback carrying the error instead.
            return Feedback(
                name="sql_is_correct",
                value=False,
                error=e,
                rationale=f"LLM-as-Judge call failed: {str(e)}",
                metadata=metadata,
            )
        return Feedback(
            name="sql_is_correct",
            value=verdict.score,
            rationale=verdict.explanation,
            metadata=metadata,
        )

    return scorer(sql_is_correct)


# ---------------------------------------------------------------------------
# MLflow setup & logging (shared by eval and train)
# ---------------------------------------------------------------------------
def setup_mlflow(tracking_uri: str, experiment_name: str) -> None:
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")
    os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_SCORER_WORKERS", "1")
    # Keep litellm autolog enabled but do NOT emit its own traces. mlflow.genai.evaluate
    # re-enables litellm autologging at its default (log_traces=True), which would make
    # every judge completion inside the scorer spawn a standalone, assessment-less trace.
    # log_traces=False suppresses those orphan traces while still letting evaluate trace
    # each prediction.
    mlflow.litellm.autolog(log_traces=True)
    litellm.suppress_debug_info = True
    litellm.enable_json_schema_validation = True


def make_run_name(label: str) -> str:
    """``{label}-{month}-{day}-{hh}-{mm}`` with slashes sanitized and the
    ``openai/`` provider prefix dropped from model names."""
    safe = label.replace("openai/", "").replace("/", "_")
    now = datetime.now()
    return f"{safe}-{now:%m-%d-%H-%M}"


def get_commit_hash() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def register_prompt_if_changed(name: str, template: str) -> PromptVersion | None:
    """Register a new prompt version only if the template differs from the latest
    version in the MLflow Prompt Registry.

    The in-code template is always the source of truth: the returned PromptVersion
    is used only for run linking, never for formatting, so the eval keeps working
    when MLflow is unavailable (returns None then).
    """
    try:
        latest = mlflow.genai.load_prompt(
            f"prompts:/{name}@latest", allow_missing=True, cache_ttl_seconds=0
        )
        if latest is not None and latest.template == template:
            click.echo(f"Prompt {name!r} unchanged from latest version; skipping registry update.")
            return latest
        return mlflow.genai.register_prompt(
            name=name,
            template=template,
            commit_message=f"auto-registered from code (commit {get_commit_hash()[:8]})",
        )
    except Exception as e:
        click.echo(f"WARNING: MLflow prompt registry unavailable for {name!r}: {e}")
        return None


def log_prompts_to_registry(prompts: dict[str, str]) -> None:
    """Register the given {name: template} prompts (deduplicated by content) and
    link the resulting versions to the active run, plus a ``prompt_*_version``
    param per prompt so runs are filterable by prompt version."""
    client = mlflow.MlflowClient()
    run_id = mlflow.active_run().info.run_id
    for prompt_name, template in prompts.items():
        version = register_prompt_if_changed(prompt_name, template)
        if version is not None:
            client.link_prompt_version_to_run(run_id, version)
            mlflow.log_param(f"prompt_{prompt_name}_version", version.version)


def log_global_params(
    *,
    model: str,
    endpoint: str,
    judge_model: str,
    judge_endpoint: str,
    questions_path: str,
    schema_path: str,
    schema_text: str,
    db_path: str,
) -> None:
    """Log run-level params, the ground-truth dataset (artifact + hash), the schema
    file (artifact + hash) and the two prompts used for the eval. Reused by train."""
    mlflow.log_param("model", model)
    mlflow.log_param("endpoint", endpoint)
    for key, value in read_sampling_params("LLM").items():
        mlflow.log_param(key, value)
    mlflow.log_param("judge_model", judge_model)
    mlflow.log_param("judge_endpoint", judge_endpoint)
    for key, value in read_sampling_params("JUDGE").items():
        mlflow.log_param(f"judge_{key}", value)
    mlflow.log_param("commit_hash", get_commit_hash())
    mlflow.log_param("dataset_sha256", hash_file(questions_path))
    mlflow.log_param("schema_sha256", hash_file(schema_path))
    mlflow.log_param("db_path", db_path)

    mlflow.log_artifact(questions_path)
    with open(schema_path) as f:
        mlflow.log_text(f.read(), "schema.py.txt")

    generation_prompt = (
        SYSTEM_PROMPT_TEMPLATE.format(schema=schema_text)
        + "\n\n"
        + USER_PROMPT_TEMPLATE.format(question="{question}")
    )
    mlflow.log_text(generation_prompt, "generation_prompt.txt")

    log_prompts_to_registry(
        {
            "text2sql_system": SYSTEM_PROMPT_TEMPLATE,
            "text2sql_user": USER_PROMPT_TEMPLATE,
        }
    )
