"""Export the learning curves of finished training runs as one tidy CSV.

Each optimization run measured with ``--probe-interval-eur`` carries a
``learning_curve.json`` artifact: the held-out test quality of its best-so-far prompt at
a series of spend levels (see ``specs/cost-budget-stopping/learning-curve-plan.md``).
This script collects those curves across runs into a single long-format table so a
notebook or paper can plot quality-per-EUR for GEPA, TextGrad and SkillOpt on one shared
money axis:

    run_id, run_name, technique, sampler_seed, budget, probe_interval_eur,
    spend_eur, test_quality, probe_cost_eur, prompt_changed, cache_hit, source,
    point_index

Runs whose artifact is missing fall back to the ``curve_test_quality`` metric history
(step = spend in cents), which carries the same points with less metadata — so a run
whose artifact upload failed still contributes its curve.

Run::

    uv run python scripts/export_learning_curves.py --out curves.csv
    uv run python scripts/export_learning_curves.py --technique gepa,textgrad
    uv run python scripts/export_learning_curves.py --run-id 0cbca2b7 --run-id fb34ae87
"""

import argparse
import csv
import json
import os
import sys

import mlflow
from dotenv import load_dotenv

EXPERIMENT_ID = "1"
CURVE_ARTIFACT = "learning_curve.json"

COLUMNS = (
    "run_id",
    "run_name",
    "technique",
    "sampler_seed",
    "budget",
    "probe_interval_eur",
    "point_index",
    "spend_eur",
    "nominal_eur",
    "test_quality",
    "probe_cost_eur",
    "prompt_changed",
    "cache_hit",
    "source",
    "prompt_sha256",
)


def curve_points(run: "mlflow.entities.Run") -> list[dict]:
    """The run's curve points, from the artifact when present, else reconstructed from
    the metric history (which keys quality by spend-in-cents, LC-D4)."""
    try:
        path = mlflow.artifacts.download_artifacts(
            run_id=run.info.run_id, artifact_path=CURVE_ARTIFACT
        )
        with open(path) as f:
            return json.load(f)["points"]
    except Exception:
        pass

    client = mlflow.MlflowClient()
    history = client.get_metric_history(run.info.run_id, "curve_test_quality")
    if not history:
        return []
    print(
        f"  note: {run.info.run_id[:8]} has no {CURVE_ARTIFACT}; falling back to the "
        "curve_test_quality metric history (spend recovered from the step, in cents)",
        file=sys.stderr,
    )
    return [
        {
            "spend_eur": m.step / 100,
            "nominal_eur": "",
            "test_quality": m.value,
            "probe_cost_eur": "",
            "prompt_changed": "",
            "cache_hit": "",
            "source": "metric_history",
            "prompt_sha256": "",
        }
        for m in sorted(history, key=lambda m: m.step)
    ]


def rows_for(run: "mlflow.entities.Run") -> list[dict]:
    params = run.data.params
    base = {
        "run_id": run.info.run_id,
        "run_name": run.data.tags.get("mlflow.runName", ""),
        "technique": params.get("technique", ""),
        "sampler_seed": params.get("sampler_seed", ""),
        "budget": params.get("budget", ""),
        "probe_interval_eur": params.get("probe_interval_eur", ""),
    }
    rows = []
    for index, point in enumerate(curve_points(run)):
        row = dict(base, point_index=index)
        row.update({key: point.get(key, "") for key in COLUMNS if key in point})
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id", action="append", default=[],
        help="restrict to these runs (repeatable); default: every run in the experiment",
    )
    parser.add_argument(
        "--technique", default="",
        help="comma-separated technique filter, e.g. 'gepa,textgrad'",
    )
    parser.add_argument("--out", default="learning_curves.csv")
    parser.add_argument(
        "--tracking-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    args = parser.parse_args()

    load_dotenv()  # artifact-store credentials + tracking config
    mlflow.set_tracking_uri(args.tracking_uri)
    client = mlflow.MlflowClient()

    if args.run_id:
        runs = [client.get_run(rid) for rid in args.run_id]
    else:
        runs = client.search_runs(
            [EXPERIMENT_ID], max_results=1000, order_by=["attributes.start_time ASC"]
        )
    techniques = {t.strip() for t in args.technique.split(",") if t.strip()}

    rows: list[dict] = []
    for run in runs:
        technique = run.data.params.get("technique")
        if not technique or (techniques and technique not in techniques):
            continue  # nested eval-phase runs and filtered-out techniques
        run_rows = rows_for(run)
        if run_rows:
            print(f"{run.info.run_id[:8]} {technique:<9} {len(run_rows)} curve points")
            rows.extend(run_rows)

    if not rows:
        print(
            "No learning-curve points found. Runs measure a curve only when started "
            "with --probe-interval-eur > 0.",
            file=sys.stderr,
        )
        return 1

    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    curves = len({row["run_id"] for row in rows})
    print(f"wrote {len(rows)} points from {curves} run(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
