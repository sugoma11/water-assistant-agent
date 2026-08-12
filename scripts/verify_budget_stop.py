"""T021 / SC3 + SC5 verification for a cost-budget-stopped optimization run.

For a given ``train-text2sql`` run this asserts, offline (no LLM calls):

**SC3 — the returned prompt is the best-on-validation prompt of the logged
progression.** The returned prompt is the ``text2sql_system`` version registered
during the run's time window (runs verified with this script must therefore not
overlap other trainings of the same prompt). Common to all techniques: a new version
is registered *iff* the run improved (``final_eval_score > initial_eval_score``), and
an improved template differs from the seed and keeps the ``{schema}`` placeholder —
the honest no-improvement seed return (EC2). Per technique, the strongest observable
each run logs:

- **gepa**: the registered template must byte-equal the engine's best candidate in
  BOTH ``summary/best/text2sql_system`` (param) and the max-``valset_score`` row of
  the ``candidates.json`` artifact, and ``final_eval_score`` must equal the best of
  the logged ``eval_score`` progression.
- **skillopt**: ``final_eval_score`` must equal the best of the logged ``eval_score``
  progression (the gate-axis ``history.json`` rows re-logged by the optimizer; the
  hard val gate makes the kept score monotone, so best == the read-back best row).
- **textgrad**: selection runs on the val-*gate* axis while the reported
  ``final_eval_score`` is the best prompt's full-val pass, so the two are not
  comparable numbers; the observable is the gate progression itself — when its last
  step scores below its maximum, the final rewrite was reverted and the returned
  prompt is by construction the earlier best (best-not-last), which the registration
  decision above pins to the scores.

**LC-SC4 — a learning-curve run's spend buckets partition cleanly** and its curve
endpoints are the bracketing evaluations. Skipped for runs without probing.

**SC5 — the meter reconciles with the post-hoc trace audit.** The run's
``tokens_{role}_{input,output}`` metrics (the live meter, FR6) are compared per role
and direction against ``scripts/count_tokens.py``'s deduped span totals (the
independent audit axis, NFR2) within ``--tolerance`` (default 5%). Role mapping:
meter ``task``/``judge`` ≡ audit ``task``/``judge``; meter ``optimizer`` ≡ audit
``reflection`` **plus** ``other`` — SkillOpt's reflection/merge/ranking prompts and
part of TextGrad's backward prompts don't match the audit's reflection markers and
land in ``other``, so the ``other`` bucket is "explained" exactly when it reconciles,
token-for-token, into the optimizer role (verified exact on the Phase 3–5 runs). A
named, tolerated delta: mlflow's ``convert_predict_fn`` trace-validation probe is one
untraced task call per run (~5k tokens), so the audit's ``task`` row undercounts the
meter by that call (see T016/T020 notes in tasks.md).

Run::

    uv run python scripts/verify_budget_stop.py --run-id <run_id>
"""

import argparse
import math
import os
import sys

import mlflow
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import count_tokens  # noqa: E402  (sibling script, not a package)

PROMPT_NAME = "text2sql_system"
ROLES = ("task", "judge", "optimizer")
GEPA_CANDIDATE_TEXT_COLUMN = f"text:{PROMPT_NAME}"


def fail(msgs: list[str], text: str) -> None:
    msgs.append(text)
    print(f"  FAIL: {text}")


def resolve_run(run_id: str) -> mlflow.entities.Run:
    """Accept a full run id or a unique prefix within the train experiment."""
    client = mlflow.MlflowClient()
    try:
        return client.get_run(run_id)
    except Exception:
        runs = mlflow.search_runs(
            experiment_ids=[count_tokens.EXPERIMENT_ID], max_results=1000
        )
        matches = [r for r in runs.run_id if r.startswith(run_id)]
        if len(matches) != 1:
            raise SystemExit(
                f"run id {run_id!r} matches {len(matches)} runs in experiment "
                f"{count_tokens.EXPERIMENT_ID}; give a full or unambiguous id"
            )
        return client.get_run(matches[0])


def versions_registered_during(run: mlflow.entities.Run) -> list[dict]:
    """``text2sql_system`` versions created inside the run's time window, scanned
    upward from the seed version the run logged (``prompt_text2sql_system_version``).
    Assumes no concurrent training registered versions in the same window."""
    from mlflow.genai import load_prompt

    seed_version = int(run.data.params[f"prompt_{PROMPT_NAME}_version"])
    found = []
    version = seed_version + 1
    while True:
        try:
            pv = load_prompt(f"prompts:/{PROMPT_NAME}/{version}")
        except Exception:
            break
        if run.info.start_time <= pv.creation_timestamp <= run.info.end_time:
            found.append({"version": version, "template": pv.template})
        version += 1
    return found


def seed_template_of(run: mlflow.entities.Run) -> str:
    from mlflow.genai import load_prompt

    seed_version = int(run.data.params[f"prompt_{PROMPT_NAME}_version"])
    return load_prompt(f"prompts:/{PROMPT_NAME}/{seed_version}").template


def eval_score_progression(run_id: str) -> list[tuple[int, float]]:
    hist = mlflow.MlflowClient().get_metric_history(run_id, "eval_score")
    return sorted(((m.step, m.value) for m in hist), key=lambda sv: sv[0])


# ---------------------------------------------------------------------------
# SC3
# ---------------------------------------------------------------------------
def verify_sc3(run: mlflow.entities.Run) -> list[str]:
    problems: list[str] = []
    params, metrics = run.data.params, run.data.metrics
    technique = params["technique"]
    initial = metrics["initial_eval_score"]
    final = metrics["final_eval_score"]
    improved = final > initial
    registered = versions_registered_during(run)
    progression = eval_score_progression(run.info.run_id)

    print(f"SC3 [{technique}] initial={initial:.4f} final={final:.4f} "
          f"stop_reason={params.get('optimization_stop_reason')!r}")
    print(f"  eval_score progression: {progression}")

    # Registration decision must match the scores (FR8 keep-best + EC2 honest
    # no-improvement): improved <=> exactly one new version, template != seed.
    if improved:
        if len(registered) != 1:
            fail(problems, f"improved run registered {len(registered)} new "
                 f"{PROMPT_NAME} versions in its window, expected exactly 1")
        else:
            new = registered[0]
            print(f"  registered {PROMPT_NAME}/{new['version']}")
            if new["template"] == seed_template_of(run):
                fail(problems, "registered template is byte-identical to the seed")
            if "{schema}" not in new["template"]:
                fail(problems, "registered template lost the {schema} placeholder")
    elif registered:
        fail(problems, "no-improvement run registered new version(s) "
             f"{[r['version'] for r in registered]}; expected the seed back (EC2)")
    else:
        print("  no improvement -> seed returned, nothing registered (EC2 ok)")

    best_logged = max((v for _, v in progression), default=None)
    if technique in ("gepa", "skillopt"):
        # Selection and reporting share one axis: the returned prompt's score is the
        # best of the logged progression.
        if best_logged is None:
            fail(problems, "no eval_score progression was logged")
        elif not math.isclose(final, best_logged, abs_tol=1e-9):
            fail(problems, f"final_eval_score={final} != best logged "
                 f"eval_score={best_logged} — returned prompt is not the best-on-val")
        else:
            print(f"  final_eval_score == best of progression ({best_logged})")
    if technique == "gepa" and improved and len(registered) == 1:
        template = registered[0]["template"]
        if template != params.get(f"summary/best/{PROMPT_NAME}"):
            fail(problems, f"registered template != summary/best/{PROMPT_NAME} param")
        rows = _gepa_candidates(run.info.run_id)
        if rows:
            best_row = max(rows, key=lambda r: r["valset_score"])
            if not best_row.get("is_best"):
                fail(problems, "max-valset_score candidate is not flagged is_best")
            if template != best_row[GEPA_CANDIDATE_TEXT_COLUMN]:
                fail(problems, "registered template != best candidates.json row")
            else:
                print(f"  registered template == best candidate "
                      f"(valset_score={best_row['valset_score']})")
        else:
            fail(problems, "candidates.json artifact missing or empty")
    if technique == "textgrad" and progression:
        last = progression[-1][1]
        if last < best_logged:
            print(f"  best-not-last: final gate step scored {last} < best "
                  f"{best_logged}, so the last rewrite was reverted before return")
        else:
            print("  last gate step is the running best (kept)")
    return problems


def _gepa_candidates(run_id: str) -> list[dict]:
    import json

    try:
        path = mlflow.artifacts.download_artifacts(
            run_id=run_id, artifact_path="candidates.json"
        )
    except Exception:
        return []
    with open(path) as f:
        table = json.load(f)
    return [dict(zip(table["columns"], row)) for row in table["data"]]


# ---------------------------------------------------------------------------
# SC5
# ---------------------------------------------------------------------------
def verify_sc5(run: mlflow.entities.Run, tolerance: float) -> list[str]:
    problems: list[str] = []
    metrics = run.data.metrics
    technique = run.data.params["technique"]
    other_prefixes: set[str] = set()
    _, audit_roles, spans = count_tokens.count_run(
        run.info.run_id, run.info.experiment_id, technique, other_prefixes
    )

    print(f"SC5 [{technique}] {spans} deduped spans "
          f"({count_tokens.TECHNIQUE_SPAN[technique]}); tolerance {tolerance:.0%}")
    # meter optimizer ≡ audit reflection + other (see module docstring).
    audit = {
        "task": audit_roles["task"],
        "judge": audit_roles["judge"],
        "optimizer": {
            d: audit_roles["reflection"][d] + audit_roles["other"][d]
            for d in ("input", "output")
        },
    }
    for role in ROLES:
        for direction in ("input", "output"):
            meter_tok = metrics.get(f"tokens_{role}_{direction}", 0.0)
            audit_tok = audit[role][direction]
            denom = max(meter_tok, audit_tok, 1.0)
            rel = abs(meter_tok - audit_tok) / denom
            status = "ok" if rel <= tolerance else "FAIL"
            print(f"  {role:<10} {direction:<6} meter={meter_tok:>12,.0f} "
                  f"audit={audit_tok:>12,.0f} diff={rel:>7.2%} {status}")
            if rel > tolerance:
                fail(problems, f"{role}/{direction}: meter {meter_tok:,.0f} vs audit "
                     f"{audit_tok:,.0f} differ by {rel:.2%} > {tolerance:.0%}")

    other_total = audit_roles["other"]["total"]
    if other_total:
        print(f"  audit 'other' bucket = {other_total:,} tokens, mapped into the "
              f"optimizer role (audit markers don't match this technique's "
              f"optimizer-side prompts); prefixes: {sorted(other_prefixes)}")
    unmetered = metrics.get("unmetered_calls")
    print(f"  unmetered_calls={unmetered:.0f}" if unmetered is not None
          else "  unmetered_calls metric missing")
    if unmetered:
        fail(problems, f"{unmetered:.0f} unmetered calls — spend is not exact")
    return problems


# ---------------------------------------------------------------------------
# LC-SC4 (learning curve): bucket identity + curve endpoints
# ---------------------------------------------------------------------------
def verify_learning_curve(run: mlflow.entities.Run) -> list[str]:
    """For a run carrying a learning curve, assert the two properties that make the
    curve trustworthy:

    **LC-SC4 — the three spend buckets partition every metered call.** The per-role
    ``cost_*`` cover billable + excluded + probe, so their sum must equal
    ``cost_total + cost_excluded + cost_probe``. If probe spend had leaked into the
    billable bucket, a probed run would have bought less optimization than an unprobed
    one at the same ``--budget`` — the failure mode that would silently invalidate the
    whole comparison (LC-FR4).

    **LC-FR7 — the curve's endpoints are the bracketing evaluations**, not extra paid
    ones: the first and last ``curve_test_quality`` points must equal
    ``test_quality_{before,after}``.
    """
    problems: list[str] = []
    metrics = run.data.metrics
    interval = float(run.data.params.get("probe_interval_eur", 0.0) or 0.0)
    if interval <= 0:
        print("LC: probing was off for this run (probe_interval_eur=0); nothing to check")
        return problems

    cost_probe = metrics.get("cost_probe", 0.0)
    per_role = sum(metrics.get(f"cost_{role}", 0.0) for role in ROLES)
    buckets = metrics.get("cost_total", 0.0) + metrics.get("cost_excluded", 0.0) + cost_probe
    print(
        f"LC [{run.data.params['technique']}] interval={interval} EUR, "
        f"probes={metrics.get('probe_count', 0):.0f} "
        f"(cache hits {metrics.get('probe_cache_hits', 0):.0f}, "
        f"failures {metrics.get('probe_failures', 0):.0f}), "
        f"cost_probe={cost_probe:.4f} "
        f"({metrics.get('probe_cost_ratio', 0.0):.0%} of billable), "
        f"probe wall {metrics.get('probe_wall_minutes', 0.0):.0f} min"
    )
    if not math.isclose(per_role, buckets, rel_tol=1e-6, abs_tol=1e-9):
        fail(problems, f"bucket identity broken: sum(cost_role)={per_role:.6f} != "
             f"cost_total+cost_excluded+cost_probe={buckets:.6f} (LC-SC4)")

    curve = sorted(
        (
            (m.step, m.value)
            for m in mlflow.MlflowClient().get_metric_history(
                run.info.run_id, "curve_test_quality"
            )
        ),
        key=lambda sv: sv[0],
    )
    print(f"  curve (spend cents -> test quality): {[(s, round(v, 4)) for s, v in curve]}")
    if not curve:
        fail(problems, "no curve_test_quality points logged despite probing being on")
        return problems
    for name, expected, (step, got) in (
        ("test_quality_before", metrics.get("test_quality_before"), curve[0]),
        ("test_quality_after", metrics.get("test_quality_after"), curve[-1]),
    ):
        if expected is None:
            continue
        if not math.isclose(expected, got, rel_tol=1e-9, abs_tol=1e-9):
            fail(problems, f"curve endpoint at step {step} is {got:.4f} but {name} is "
                 f"{expected:.4f}; endpoints must reuse the bracketing evals (LC-FR7)")
    if metrics.get("probe_failures"):
        print(f"  NOTE: {metrics['probe_failures']:.0f} probe(s) failed — the curve has "
              "holes, but the optimization itself was unaffected (LC-FR9)")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--tracking-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    parser.add_argument(
        "--tolerance", type=float, default=0.05,
        help="max per-role, per-direction relative token difference (SC5, NFR2)",
    )
    args = parser.parse_args()

    load_dotenv()  # artifact-store credentials + tracking config
    mlflow.set_tracking_uri(args.tracking_uri)
    run = resolve_run(args.run_id)
    name = run.data.tags.get("mlflow.runName", "")
    print(f"run {run.info.run_id} ({name}), status {run.info.status}")
    budget = float(run.data.params["budget"])
    cost_total = run.data.metrics.get("cost_total", 0.0)
    print(f"budget={budget} EUR, billable cost_total={cost_total:.4f}, "
          f"overshoot={max(0.0, cost_total - budget):.4f} (R3), "
          f"cost_excluded={run.data.metrics.get('cost_excluded', 0.0):.4f}")

    problems = verify_sc3(run)
    problems += verify_sc5(run, args.tolerance)
    problems += verify_learning_curve(run)

    print("SC3+SC5+LC VERIFIED" if not problems else
          f"NOT VERIFIED — {len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
