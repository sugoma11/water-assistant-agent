"""Retroactively re-score finished training runs with graded execution metrics.

The live judge (``harness.build_sql_judge_scorer``) executes both queries, collapses
the two result sets to a boolean via ``execution_match``, and logs only that boolean.
This script recovers the per-item ``generated_sql`` from the trace assessments of each
``test-before`` / ``test-after`` eval phase, re-executes it against the DuckDB, and
recomputes graded metrics from the same result sets — deterministically, with no LLM
calls and no reruns.

Why: a binary score is quantised at one record — 4 pp on the pre-refactor 25-record
test set, 1.8 pp on the post-refactor 55-record one — which is the same order as the
method gaps we are trying to resolve. Graded scores remove the quantisation and make
partial movement (right joins, wrong aggregate) visible.

Split regimes: runs recorded before the 20/0/55 refactor
(``specs/sampling_refactoring/spec.md``) were tested on 25 records drawn under a
different partition; runs after it are tested on 55. **Paired-at-seed deltas must not
mix the two** (C1): ``after[technique][seed]`` averages every run found at a seed, so a
pre- and a post-refactor run at seed 41 would be silently pooled into one arm, and the
seeds themselves index different test sets across the two regimes. Nothing in this
script enforces that — select one regime via ``--experiment-ids``, and check the ``n``
column of the per-run means table (25 vs 55) before reading any delta. Post-refactor
parent runs also carry ``split_scheme=20-0-55-val-eq-train``. The script itself is
split-size agnostic: trace recovery is capped at 2000 traces per phase and every
aggregate is per-item.

Emits three things:
  1. per-item rows (CSV) — one line per scored sample, every metric
  2. per-run aggregates — binary vs graded, before/after
  3. paired method deltas at matched seeds, under each metric, with the SD that
     drives the power calculation

Also tabulates judge-vs-execution disagreement, which estimates how much the LLM
layer adds or corrupts relative to deterministic BIRD execution accuracy.

Usage:
    uv run python scripts/regrade_traces.py --db-path data/water.duckdb
"""

import csv
import json
import random
import statistics as st
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

import click
import duckdb
import mlflow
from scipy.stats import binomtest

from experiments.text2sql.harness import _hashable_row, execution_match, run_query

PHASES = ("test-before", "test-after")


# ---------------------------------------------------------------------------
# Graded metrics over the two result sets
# ---------------------------------------------------------------------------
def row_f1(gen_rows: list[tuple] | None, ref_rows: list[tuple] | None) -> float:
    """F1 over the row sets, using the same order/duplicate-insensitive hashing as
    ``execution_match`` — so ``row_f1 == 1.0`` exactly when ``ex_match`` is True.

    Two empty result sets score 1.0: returning nothing when nothing is expected is a
    correct answer, not a degenerate one. A failed query scores 0.0.
    """
    if gen_rows is None or ref_rows is None:
        return 0.0
    g = {_hashable_row(r) for r in gen_rows}
    t = {_hashable_row(r) for r in ref_rows}
    if not g and not t:
        return 1.0
    if not g or not t:
        return 0.0
    hit = len(g & t)
    if not hit:
        return 0.0
    precision, recall = hit / len(g), hit / len(t)
    return 2 * precision * recall / (precision + recall)


def grade(cursor: duckdb.DuckDBPyConnection, generated_sql: str, reference_sql: str) -> dict:
    """Every deterministic metric derivable from one (generated, reference) pair."""
    if not generated_sql:
        return {"executes": 0, "ex_match": 0, "row_f1": 0.0, "shape_match": 0, "n_rows": 0}

    gen_rows, gen_err = run_query(cursor, generated_sql)
    ref_rows, ref_err = run_query(cursor, reference_sql)

    ex = gen_err is None and ref_err is None and execution_match(gen_rows, ref_rows)
    # Column count is the coarsest shape signal available without column names;
    # a wrong-arity answer is a different kind of failure than a wrong-value one.
    shape = (
        1
        if gen_rows and ref_rows and len(gen_rows[0]) == len(ref_rows[0])
        else int(bool(gen_rows) == bool(ref_rows) and gen_err is None and ref_err is None)
    )
    return {
        "executes": int(gen_err is None),
        "ex_match": int(ex),
        "row_f1": round(row_f1(gen_rows, ref_rows), 4),
        "shape_match": shape,
        "n_rows": len(gen_rows) if gen_rows is not None else 0,
    }


# ---------------------------------------------------------------------------
# Recover per-item predictions from trace assessments
# ---------------------------------------------------------------------------
def assessed_items(locations: list[str], run_id: str) -> list[dict[str, Any]]:
    """The ``sql_is_correct`` assessments of one eval-phase run, as plain dicts.

    Each eval phase logs one assessed trace per test record plus untagged judge
    Completions traces, so the assessment filter is what selects the scored samples.
    """
    traces = mlflow.search_traces(
        locations=locations,
        filter_string=f"attributes.run_id='{run_id}'",
        max_results=2000,
        return_type="list",
    )
    items = []
    for t in traces:
        for a in t.info.assessments or []:
            if a.name != "sql_is_correct":
                continue
            md = a.metadata or {}
            value = getattr(a, "value", None)
            if value is None and getattr(a, "feedback", None) is not None:
                value = a.feedback.value
            items.append(
                {
                    "question": md.get("question", ""),
                    "generated_sql": md.get("generated_sql", ""),
                    "judge": int(str(value).lower() == "true"),
                    # The scorer's except branch returns value=False on a judge call
                    # failure (auth error, unparseable JSON), so an infrastructure
                    # outage is indistinguishable from a phase of wrong answers in
                    # `sql_is_correct/mean`. Carry the flag so aggregates can drop them.
                    "judge_error": int(getattr(a, "error", None) is not None),
                    "ex_match_logged": int(str(md.get("ex_match", "")).lower() == "true"),
                    "judge_branch": md.get("judge_branch", ""),
                }
            )
    return items


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def sd(xs: list[float]) -> float:
    return st.stdev(xs) if len(xs) > 1 else float("nan")


@click.command()
@click.option("--questions-path", default="data/text2sql/deflated_75_sqls_prod.json")
@click.option("--db-path", default="data/water.duckdb")
@click.option("--tracking-uri", default="http://localhost:5000")
@click.option("--experiment-ids", default="1,2,3", help="Comma-separated experiment ids.")
@click.option("--out", default="temp/regrade_items.csv", help="Per-item CSV output.")
@click.option(
    "--include-judge-errors",
    is_flag=True,
    help="Keep items whose judge call errored (counted as incorrect, as the live run did).",
)
def main(
    questions_path: str,
    db_path: str,
    tracking_uri: str,
    experiment_ids: str,
    out: str,
    include_judge_errors: bool,
) -> None:
    mlflow.set_tracking_uri(tracking_uri)
    locations = experiment_ids.split(",")

    # Reference SQL is not in the trace metadata, so join back to the dataset by
    # question text. Records carry both a raw and a prod phrasing and the run picks
    # one via --use-prod-questions, so key on both.
    records = json.loads(Path(questions_path).read_text())
    ref_by_q: dict[str, str] = {}
    for r in records:
        for key in ("question", "prod_question"):
            if r.get(key):
                ref_by_q[r[key]] = r["sql"]

    parents = mlflow.search_runs(
        experiment_ids=locations,
        filter_string="attributes.status='FINISHED'",
        output_format="pandas",
    )
    parents = parents[parents.get("params.technique").notna()]
    click.echo(f"{len(parents)} finished runs with a technique param\n")

    con = duckdb.connect(db_path, read_only=True)
    rows: list[dict[str, Any]] = []
    unmatched = 0

    for _, p in parents.iterrows():
        technique = p["params.technique"]
        seed = p.get("params.sampler_seed")
        children = mlflow.search_runs(
            experiment_ids=locations,
            filter_string=f"tags.mlflow.parentRunId='{p['run_id']}'",
            output_format="pandas",
        )
        for _, ch in children.iterrows():
            name = ch.get("tags.mlflow.runName") or ""
            phase = next((ph for ph in PHASES if name.startswith(ph)), None)
            if phase is None:
                continue
            items = assessed_items(locations, ch["run_id"])
            cursor = con.cursor()
            try:
                for it in items:
                    ref = ref_by_q.get(it["question"])
                    if ref is None:
                        unmatched += 1
                        continue
                    rows.append(
                        {
                            "parent_run": p["run_id"],
                            "technique": technique,
                            "seed": seed,
                            "phase": phase,
                            **it,
                            **grade(cursor, it["generated_sql"], ref),
                        }
                    )
            finally:
                cursor.close()
            click.echo(f"  {technique:<9} seed={seed:<3} {phase:<11} {len(items):>3} items")

    if not rows:
        raise click.ClickException("No assessed traces recovered — check experiment ids.")
    if unmatched:
        click.echo(f"\n! {unmatched} items could not be joined to a reference SQL by question")

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    click.echo(f"\n{len(rows)} scored items -> {out}")

    metrics = ("judge", "ex_match", "row_f1", "executes", "shape_match")

    # A failed judge call is missing data, not a wrong answer: averaging it in as 0
    # turns an outage into a plausible-looking score. Drop by default.
    errored = [r for r in rows if r["judge_error"]]
    if errored and not include_judge_errors:
        by_run = defaultdict(int)
        for r in errored:
            by_run[(r["technique"], r["seed"], r["phase"])] += 1
        click.echo(f"\n! dropping {len(errored)} items whose judge call errored:")
        for k, c in sorted(by_run.items()):
            click.echo(f"    {k[0]} seed={k[1]} {k[2]}: {c} items")
        click.echo("  (re-run with --include-judge-errors to reproduce the logged metrics)")
        rows = [r for r in rows if not r["judge_error"]]

    # ---- per-run aggregates -------------------------------------------------
    agg: dict[tuple, dict[str, float]] = {}
    for r in rows:
        agg.setdefault((r["technique"], r["seed"], r["parent_run"], r["phase"]), defaultdict(list))
        for m in metrics:
            agg[(r["technique"], r["seed"], r["parent_run"], r["phase"])][m].append(r[m])

    click.echo("\n=== per-run means ===")
    click.echo(f"{'technique':<9} {'seed':<5} {'phase':<11} {'n':>3}  " + "  ".join(f"{m:>11}" for m in metrics))
    for k in sorted(agg, key=lambda x: (x[0], str(x[1]), x[3])):
        v = agg[k]
        n = len(v["judge"])
        click.echo(
            f"{k[0]:<9} {str(k[1]):<5} {k[3]:<11} {n:>3}  "
            + "  ".join(f"{mean(v[m]):>11.3f}" for m in metrics)
        )

    # ---- paired method deltas at matched seeds ------------------------------
    # after-scores keyed by (technique, seed); duplicate runs at one seed are averaged
    after: dict[str, dict[str, list[dict[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for (tech, seed, _run, phase), v in agg.items():
        if phase == "test-after":
            after[tech][str(seed)].append({m: mean(v[m]) for m in metrics})

    click.echo("\n=== paired method deltas at matched seeds ===")
    for a, b in combinations(sorted(after), 2):
        shared = sorted(set(after[a]) & set(after[b]))
        if not shared:
            continue
        click.echo(f"\n{a} - {b}   (seeds: {', '.join(shared)})")
        for m in metrics:
            diffs = [
                mean([x[m] for x in after[a][s]]) - mean([x[m] for x in after[b][s]])
                for s in shared
            ]
            d_sd = sd(diffs)
            # n=4 needs |mean| >= 1.591*SD for p<0.05 (paired t, df=3); see t_crit/sqrt(n)
            click.echo(
                f"  {m:<12} n={len(diffs)}  mean={mean(diffs) * 100:+6.2f}pp  "
                f"SD={d_sd * 100:5.2f}pp  " + (f"|mean|/SD={abs(mean(diffs)) / d_sd:.2f}" if d_sd else "")
            )

    # ---- within-method before/after: McNemar on item-level pairs ------------
    # Every test record is scored twice in a run (baseline prompt, optimized prompt),
    # so before/after pairs at the item level -- far more powerful than comparing run
    # means. Two corrections matter:
    #   * McNemar's power comes from the DISCORDANT pairs (b+c), not the pair count;
    #     items right-both-times or wrong-both-times carry no information.
    #   * Records recur across seeds (a record is in test with p=1/3 per seed under the
    #     old thirds, p=55/75 under 20/0/55 — so recurrence is now the common case), and
    #     repeats of one question share its difficulty, so the naive exact test
    #     overstates significance. The cluster bootstrap resamples QUESTIONS and is
    #     the number to report.
    paired: dict[tuple[str, str], list[tuple[str, int, int]]] = defaultdict(list)
    per_run: dict[tuple, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        per_run[(r["technique"], r["seed"], r["parent_run"])][r["phase"]][r["question"]] = r
    for key, phases in per_run.items():
        if "test-before" not in phases or "test-after" not in phases:
            continue
        for q in set(phases["test-before"]) & set(phases["test-after"]):
            for m in ("judge", "ex_match"):
                paired[(key[0], m)].append(
                    (q, int(phases["test-before"][q][m]), int(phases["test-after"][q][m]))
                )

    click.echo("\n=== within-method before/after (item-level McNemar) ===")
    rng = random.Random(0)
    for (tech, m), prs in sorted(paired.items()):
        b = sum(1 for _, x, y in prs if x == 0 and y == 1)
        c = sum(1 for _, x, y in prs if x == 1 and y == 0)
        n, dis = len(prs), b + c
        uniq = len({q for q, _, _ in prs})
        p = binomtest(min(b, c), dis, 0.5).pvalue if dis else 1.0

        by_q: dict[str, list[int]] = defaultdict(list)
        for q, x, y in prs:
            by_q[q].append(y - x)
        qs = list(by_q)
        boots = sorted(
            st.fmean([d for q in (rng.choice(qs) for _ in qs) for d in by_q[q]])
            for _ in range(20000)
        )
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
        click.echo(
            f"  {tech:<9} {m:<9} {n:>3} pairs / {uniq:>3} distinct questions | "
            f"improved={b:<3} regressed={c:<3} informative={dis:<3} | "
            f"net={(b - c) / n * 100:+5.1f}pp  p={p:.1e}  "
            f"cluster-boot 95% CI [{lo * 100:+5.1f}, {hi * 100:+5.1f}]pp"
        )

    # ---- judge vs deterministic execution accuracy --------------------------
    click.echo("\n=== judge vs execution match (all items) ===")
    conf = defaultdict(int)
    for r in rows:
        conf[(r["ex_match"], r["judge"])] += 1
    total = len(rows)
    for (ex, ju), c in sorted(conf.items(), reverse=True):
        tag = {
            (1, 1): "agree correct",
            (0, 0): "agree incorrect",
            (1, 0): "judge overrides EX -> incorrect (claimed false positive)",
            (0, 1): "judge overrides EX -> correct   (claimed false negative)",
        }[(ex, ju)]
        click.echo(f"  ex={ex} judge={ju}  {c:>4} ({c / total:5.1%})  {tag}")
    disagree = conf[(1, 0)] + conf[(0, 1)]
    click.echo(f"  disagreement rate: {disagree / total:.1%}")

    stale = sum(1 for r in rows if r["ex_match"] != r["ex_match_logged"])
    click.echo(
        f"\nreplay check: {stale}/{total} items where re-executed ex_match differs from "
        "the value logged at run time (should be 0 if the DB is unchanged)"
    )


if __name__ == "__main__":
    main()
