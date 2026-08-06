"""T023 / SC5 verification: a SkillOpt optimizer failure marks the MLflow run FAILED
with no optimized result reported and a clear, actionable error.

This drives ``SkillOptPromptOptimizer.optimize`` through a real ``mlflow.start_run``
(temp file store, so no live server is touched) with the **optimizer role endpoint
deliberately made unreachable** -- its ``*_API_BASE``/``*_API_KEY`` env vars are unset,
the EC1 "optimizer endpoint unreachable / misconfigured" failure class. ``optimize``
resolves the optimizer-role endpoint while building the trainer cfg, before any real LLM
call, so the run fails there; the failure then propagates exactly as it would in the CLI
(``_run_optimization`` wraps the same ``optimize`` call in ``mlflow.start_run``). The
``eval_fn`` is stubbed only to satisfy the call signature -- it is no longer invoked,
since the initial/final val scores now come from the trainer's own summary.

Asserts SC5: (1) a clear error is raised, (2) the enclosing run ends ``FAILED``,
(3) no optimized prompt / PromptOptimizerOutput is produced.

Run:  uv run python scripts/verify_skillopt_sc5.py

The same failure is reproducible live through the real CLI by pointing the optimizer
role at an endpoint whose credentials are unset (task/judge on a reachable endpoint),
e.g.::

    LLM_API_KEY_KISSKI_1= LLM_API_BASE_KISSKI_1= \
      uv run text2sql-train-skillopt --model openai/<task> --endpoint blablador \
        --judge-model openai/<judge> --judge-endpoint blablador \
        --optimizer-model <opt> --optimizer-endpoint kisski \
        --epochs 1 --edit-budget 1 --minibatch-size 1 \
        --questions-path <q.json> --schema-path <schema.py> --db-path data/water.duckdb

The live run reaches the optimizer-role resolution and fails identically (the reflection
model's cold-start latency, ~7500s in the Phase-1 spike, is why the deterministic offline
path above is the primary check). This stub path is offline, fast and CI-safe.
"""

import sys
import tempfile
from types import SimpleNamespace

import mlflow

from experiments.text2sql import harness
from experiments.text2sql.skillopt_optimizer import SkillOptPromptOptimizer

# An endpoint whose credential env vars are unset -> "unreachable / misconfigured
# optimizer endpoint" (EC1). Registered only for this check; the CLI uses kisski/blablador.
UNREACHABLE_ENDPOINT = "__unreachable_optimizer__"
harness.ENDPOINTS[UNREACHABLE_ENDPOINT] = ("SC5_UNSET_API_BASE", "SC5_UNSET_API_KEY")

TARGET = {"text2sql_system": "Write DuckDB SQL.\n\nSchema:\n\n{schema}\n"}
TRAIN = [{"id": "1", "inputs": {"question": "q"}, "expectations": {"sql": "SELECT 1"}}]
VAL = [{"id": "2", "inputs": {"question": "q2"}, "expectations": {"sql": "SELECT 2"}}]


def stub_eval_fn(prompts, dataset):
    """Offline stand-in for MLflow's eval_fn: a fixed baseline val score, no LLM call."""
    return [SimpleNamespace(score=0.0) for _ in dataset]


def main() -> int:
    optimizer = SkillOptPromptOptimizer(
        task_model="openai/task",
        task_endpoint="blablador",
        optimizer_model="optimizer",
        optimizer_endpoint=UNREACHABLE_ENDPOINT,
        judge_scorer=lambda **_: SimpleNamespace(value=True, rationale="", error=None),
        schema_text="schema",
        val_set=VAL,
        epochs=1,
        edit_budget=1,
        minibatch_size=1,
    )

    with tempfile.TemporaryDirectory() as store:
        mlflow.set_tracking_uri(f"sqlite:///{store}/mlflow.db")
        mlflow.set_experiment("sc5-verification")

        run_id = None
        raised = None
        try:
            with mlflow.start_run() as run:
                run_id = run.info.run_id
                optimizer.optimize(stub_eval_fn, TRAIN, TARGET, enable_tracking=False)
        except Exception as exc:  # noqa: BLE001 - we assert on the propagated error
            raised = exc

        status = mlflow.get_run(run_id).info.status

    ok = True
    if raised is None:
        print("FAIL: optimize() did not raise on an unreachable optimizer endpoint")
        ok = False
    else:
        print(f"clear error raised: {type(raised).__name__}: {raised}")
        if "unreachable" not in str(raised).lower() and "unset" not in str(raised).lower():
            print("FAIL: error is not actionable about the endpoint credentials")
            ok = False
    if status != "FAILED":
        print(f"FAIL: run status is {status!r}, expected 'FAILED'")
        ok = False
    else:
        print("run marked FAILED with no optimized prompt reported")

    print("SC5 VERIFIED" if ok else "SC5 NOT VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
