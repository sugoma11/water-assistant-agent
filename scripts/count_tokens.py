"""Count LLM tokens used by each prompt-optimization technique from MLflow traces.

Produces an honest cost comparison across GEPA, TextGrad, SkillOpt and FAPO by
reading token usage off the traced LLM spans of each training run in the
``train-text2sql`` experiment.

The hard part is de-duplication: two MLflow autologgers (litellm + openai) can be
active at once, so one physical LLM call can surface as two spans with identical
usage. The correct span to sum is therefore FRAMEWORK-DEPENDENT -- see
``TECHNIQUE_SPAN`` below.

Run::

    .venv/bin/python scripts/count_tokens.py --self-check
    .venv/bin/python scripts/count_tokens.py
    .venv/bin/python scripts/count_tokens.py --technique gepa,textgrad
    .venv/bin/python scripts/count_tokens.py --run-id <run_id>
"""

import argparse
import json
import os
import sys
from collections import defaultdict

import mlflow

EXPERIMENT_NAME = "train-text2sql"
EXPERIMENT_ID = "1"

TECHNIQUES = ("gepa", "textgrad", "skillopt", "fapo")

# Which span name's tokenUsage to sum, per technique (the de-dupe rule).
#   gepa      -> only litellm autolog active; the ``Completions`` span is a nested
#                child of ``litellm-completion``, so sum the litellm span only.
#   textgrad/ -> litellm AND openai autolog active. Each litellm call emits two
#   skillopt     separate root traces (litellm-completion + Completions, identical
#                usage), and raw-openai task/reflection calls appear ONLY as
#                Completions. Summing Completions captures everything exactly once.
#   fapo      -> not wired to MLflow yet; treat like the openai-autolog case.
TECHNIQUE_SPAN = {
    "gepa": "litellm-completion",
    "textgrad": "Completions",
    "skillopt": "Completions",
    "fapo": "Completions",
}

ROLES = ("task", "judge", "reflection", "other")

TOKEN_USAGE_ATTR = "mlflow.chat.tokenUsage"
SPAN_INPUTS_ATTR = "mlflow.spanInputs"

# TextGrad backward/optimizer reflection: the joined message content contains one
# of these fragments (the prompt varies, so we match on a stable substring).
TEXTGRAD_REFLECTION_MARKERS = (
    "optimization system",
    "improves a given",
    "provide feedback",
    "new variable",
)


def _maybe_json(value):
    """MLflow span attributes may be JSON-encoded strings; decode defensively."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def classify_technique(run_name):
    name = (run_name or "").lower()
    for technique in TECHNIQUES:
        if technique in name:
            return technique
    return None


def _content_text(content):
    """Flatten a message ``content`` (str or list of parts) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return " ".join(parts)
    return str(content)


def classify_role(span, other_prefixes):
    """Classify a counted span by the content of its input messages."""
    inputs = _maybe_json(span.attributes.get(SPAN_INPUTS_ATTR))
    messages = None
    if isinstance(inputs, dict):
        messages = inputs.get("messages")
    if not isinstance(messages, list) or not messages:
        other_prefixes.add("<no messages>")
        return "other"

    first = _content_text(messages[0].get("content", "")) if isinstance(messages[0], dict) else ""
    joined = " ".join(
        _content_text(m.get("content", "")) for m in messages if isinstance(m, dict)
    )

    if first.startswith("You are an expert DuckDB SQL author"):
        return "task"
    if first.startswith("The Prediction Result"):
        return "judge"
    if first.startswith("I provided an assistant with the following instructions"):
        return "reflection"
    if any(marker in joined for marker in TEXTGRAD_REFLECTION_MARKERS):
        return "reflection"

    other_prefixes.add(first[:50].replace("\n", " "))
    return "other"


def _empty_totals():
    return {"input": 0, "output": 0, "total": 0}


def count_run(run_id, experiment_id, technique, other_prefixes):
    """Sum de-duplicated token usage for one run, split by role."""
    span_name = TECHNIQUE_SPAN[technique]
    totals = _empty_totals()
    role_totals = {role: _empty_totals() for role in ROLES}
    counted_spans = 0

    traces = mlflow.search_traces(
        run_id=run_id,
        locations=[experiment_id],
        max_results=100000,
        return_type="list",
    )
    for trace in traces:
        for span in trace.data.spans:
            if span.name != span_name:
                continue
            usage = _maybe_json(span.attributes.get(TOKEN_USAGE_ATTR))
            if not isinstance(usage, dict):
                continue  # non-LLM span
            inp = int(usage.get("input_tokens", 0) or 0)
            out = int(usage.get("output_tokens", 0) or 0)
            tot = int(usage.get("total_tokens", 0) or 0)
            totals["input"] += inp
            totals["output"] += out
            totals["total"] += tot
            counted_spans += 1

            role = classify_role(span, other_prefixes)
            role_totals[role]["input"] += inp
            role_totals[role]["output"] += out
            role_totals[role]["total"] += tot

    return totals, role_totals, counted_spans


def _fmt(totals):
    return f"in={totals['input']:>10,} out={totals['output']:>10,} total={totals['total']:>12,}"


def gather_runs(tracking_uri, technique_filter, run_id_filter):
    mlflow.set_tracking_uri(tracking_uri)
    runs_df = mlflow.search_runs(experiment_ids=[EXPERIMENT_ID], max_results=1000)

    selected = []
    for _, row in runs_df.iterrows():
        run_id = row["run_id"]
        if run_id_filter and run_id != run_id_filter:
            continue
        run_name = row.get("tags.mlflow.runName", "")
        technique = classify_technique(run_name)
        if technique is None:
            continue
        if technique_filter and technique not in technique_filter:
            continue
        experiment_id = row.get("experiment_id") or mlflow.get_run(run_id).info.experiment_id
        selected.append((run_id, str(experiment_id), technique, run_name))
    return selected


def run_report(tracking_uri, technique_filter, run_id_filter):
    selected = gather_runs(tracking_uri, technique_filter, run_id_filter)
    other_prefixes = set()
    warnings = []

    per_technique = {
        t: {
            "total": _empty_totals(),
            "roles": {role: _empty_totals() for role in ROLES},
            "runs": 0,
        }
        for t in TECHNIQUES
    }

    print(f"Tracking URI: {tracking_uri}")
    print(f"Experiment:   {EXPERIMENT_NAME} (id={EXPERIMENT_ID})")
    print(f"Runs matched: {len(selected)}\n")
    print("=" * 100)
    print("PER-RUN")
    print("=" * 100)

    for run_id, experiment_id, technique, run_name in selected:
        totals, role_totals, counted_spans = count_run(
            run_id, experiment_id, technique, other_prefixes
        )
        agg = per_technique[technique]
        for key in totals:
            agg["total"][key] += totals[key]
            for role in ROLES:
                agg["roles"][role][key] += role_totals[role][key]
        agg["runs"] += 1

        print(f"\n[{technique}] {run_name}")
        print(f"  run_id: {run_id}  spans={counted_spans}  ({TECHNIQUE_SPAN[technique]})")
        print(f"  DEDUPED  {_fmt(totals)}")
        for role in ROLES:
            print(f"    {role:<10} {_fmt(role_totals[role])}")

        if technique in ("textgrad", "skillopt") and counted_spans == 0:
            msg = (
                f"WARNING: {technique} run '{run_name}' ({run_id}) has ZERO "
                f"'Completions' spans -- optimizer tokens are likely missing "
                f"(pre-fix / untraced-optimizer run)."
            )
            warnings.append(msg)
            print(f"  !! {msg}")

    print("\n" + "=" * 100)
    print("PER-TECHNIQUE AGGREGATE")
    print("=" * 100)
    for technique in TECHNIQUES:
        agg = per_technique[technique]
        if agg["runs"] == 0:
            print(f"\n[{technique}] no traced runs")
            continue
        print(f"\n[{technique}]  runs={agg['runs']}")
        print(f"  DEDUPED  {_fmt(agg['total'])}")
        for role in ROLES:
            print(f"    {role:<10} {_fmt(agg['roles'][role])}")

    if warnings:
        print("\n" + "=" * 100)
        print("WARNINGS")
        print("=" * 100)
        for msg in warnings:
            print(f"  - {msg}")

    print("\n" + "=" * 100)
    print("DISTINCT UNMATCHED 'other' FIRST-MESSAGE PREFIXES")
    print("=" * 100)
    if other_prefixes:
        for prefix in sorted(other_prefixes):
            print(f"  {prefix!r}")
    else:
        print("  (none)")

    return per_technique


SELF_CHECK_BY_ID = {
    "78ab5951db3d4d6793d0b7c472557026": 1_798_127,  # gepa
    "888fa7fe332d4658a4576214540e3d84": 285_398,     # textgrad
}
SELF_CHECK_BY_NAME = {
    "textgrad-alias-qwen36-35b-07-01-10-04": 1_972_885,  # textgrad
}


def self_check(tracking_uri):
    mlflow.set_tracking_uri(tracking_uri)
    runs_df = mlflow.search_runs(experiment_ids=[EXPERIMENT_ID], max_results=1000)
    other_prefixes = set()

    checks = []  # (label, expected, actual)
    for _, row in runs_df.iterrows():
        run_id = row["run_id"]
        run_name = row.get("tags.mlflow.runName", "")
        technique = classify_technique(run_name)
        if technique is None:
            continue
        experiment_id = str(row.get("experiment_id") or EXPERIMENT_ID)

        expected = None
        label = None
        if run_id in SELF_CHECK_BY_ID:
            expected = SELF_CHECK_BY_ID[run_id]
            label = f"id={run_id}"
        elif run_name in SELF_CHECK_BY_NAME:
            expected = SELF_CHECK_BY_NAME[run_name]
            label = f"name={run_name}"
        if expected is None:
            continue

        run_status = mlflow.get_run(run_id).info.status
        totals, _, _ = count_run(run_id, experiment_id, technique, other_prefixes)
        checks.append((label, expected, totals["total"], run_status))

    print("SELF-CHECK")
    print("=" * 100)
    ok = True
    seen_labels = set()
    for label, expected, actual, run_status in checks:
        seen_labels.add(label)
        # A FINISHED run's deduped total must match the recorded ground truth exactly.
        # A RUNNING run is still accumulating traces since the anchor was captured, so
        # the ground truth is only a monotonic lower bound (actual >= expected).
        if run_status == "RUNNING":
            passed = actual >= expected
            note = f" (RUNNING; ground truth is a lower bound, run has grown +{actual - expected:,})"
        else:
            passed = actual == expected
            note = ""
        status = "PASS" if passed else "FAIL"
        if not passed:
            ok = False
        print(f"  [{status}] {label}: expected={expected:,} actual={actual:,}{note}")

    # Flag any anchor we never found among the runs.
    expected_labels = {f"id={k}" for k in SELF_CHECK_BY_ID}
    expected_labels |= {f"name={k}" for k in SELF_CHECK_BY_NAME}
    for missing in sorted(expected_labels - seen_labels):
        ok = False
        print(f"  [FAIL] {missing}: anchor run not found in experiment")

    print("=" * 100)
    print("SELF-CHECK PASSED" if ok else "SELF-CHECK FAILED")
    return ok


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--technique",
        action="append",
        default=None,
        help="Filter by technique (repeatable, or comma-separated): "
        "gepa,textgrad,skillopt,fapo",
    )
    parser.add_argument("--run-id", default=None, help="Restrict to a single run id")
    parser.add_argument(
        "--tracking-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Verify de-dupe against known ground-truth deduped totals",
    )
    return parser.parse_args(argv)


def normalize_techniques(raw):
    if not raw:
        return None
    out = set()
    for item in raw:
        for piece in item.split(","):
            piece = piece.strip().lower()
            if not piece:
                continue
            if piece not in TECHNIQUES:
                raise SystemExit(f"unknown technique: {piece!r} (choose from {TECHNIQUES})")
            out.add(piece)
    return out or None


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    technique_filter = normalize_techniques(args.technique)

    if args.self_check:
        ok = self_check(args.tracking_uri)
        return 0 if ok else 1

    run_report(args.tracking_uri, technique_filter, args.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
