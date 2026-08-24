# Implementation Plan: Statistical Comparison of Prompt-Tuning Techniques

Target notebook: `notebooks/prompt_opt_stats.ipynb`
Data source: MLflow experiment **1** (`http://localhost:5000/#/experiments/1/overview`)
Related: [`../cost-budget-stopping/spec.md`](../cost-budget-stopping/spec.md) ·
[`../cost-budget-stopping/learning-curve-plan.md`](../cost-budget-stopping/learning-curve-plan.md) ·
existing descriptive notebook `notebooks/prompt_opt_gains.ipynb`

Requirement IDs are prefixed `ST-`.

---

## 1. Why

`prompt_opt_gains.ipynb` already reports `gain = test_quality_after - test_quality_before`
as *mean ± std over seeds*. With 3 seeds per technique that std is an unusable estimator,
and the run-level means hide the fact that a single 55-item test evaluation carries a
binomial standard error of ≈ 6.7 pp (95% CI ≈ ±13 pp). The current headline numbers —
SkillOpt 0.745, TextGrad 0.800, GEPA 0.727 — are **all inside each other's confidence
intervals**, so the notebook currently invites a conclusion the data cannot support.

This notebook replaces run-level averaging with **per-item paired inference**: the same
55 test questions are answered before and after tuning, and (within a seed) by all three
techniques, so the evidence lives in the *item-level disagreements*, not in the aggregate
means. It must also state plainly which claims the current sample size can and cannot
support.

## 2. What is actually in experiment 1 (verified 2026-08-12)

| Fact | Value |
|------|-------|
| Total runs | 29 (10 top-level `technique` runs + 19 nested `genai_evaluate` children) |
| Complete runs | **9** = 3 techniques × 3 `sampler_seed`s (41, 42, 43) |
| Incomplete | `f7bd99bc` (gepa, seed 41) — no `test_quality_after`, superseded by `d3905b50` |
| Techniques | `gepa`, `textgrad`, `skillopt` |
| Student | `openrouter/qwen/qwen3.6-35b-a3b`, `temperature=0.05`, `top_k=1` |
| Judge | `openai/qwen3.6-35b-a3b` @ kisski3, `judge_temperature=0.0`, `judge_seed=42` |
| Commit | `ceb91f2` for all 9 runs |
| Budget | `1.75` EUR, **all 9** stopped with `optimization_stop_reason=budget_exhausted`, `cost_total ∈ [1.756, 1.916]` |
| Split | `20-0-55-val-eq-train`, `dataset_sha256=ea44f346…` identical everywhere |
| Test metric | `sql_is_correct` — binary, FLEX execution-accuracy + LLM-judge |

Observed test accuracy (55 items):

| seed | GEPA before→after | TextGrad before→after | SkillOpt before→after |
|------|-------------------|-----------------------|------------------------|
| 41 | 0.509 → 0.727 | 0.582 → 0.727 | 0.473 → 0.673 |
| 42 | 0.582 → 0.727 | 0.582 → **0.800** | 0.600 → 0.745 |
| 43 | 0.600 → 0.709 | 0.618 → **0.582** | 0.545 → 0.745 |

Two structural facts drive the whole design:

- **ST-F1 — The test split depends on the seed.** `sampler.split_indices` shuffles
  *groups* with `sampler_seed` and gives train the first 20 records, test the remaining
  55 (`src/experiments/text2sql/sampler.py:296-317`). So items are **exactly paired
  within a seed** (all 3 techniques × both phases see the identical 55 questions) and
  only **partially overlapping across seeds** (~40 of 55 expected common). Pairing across
  seeds must be done on item identity, never on position.
  *Verified against the traces*: the 18 eval runs collapse to exactly **three** distinct
  item sets, one per seed — `03caaaa566` (seed 41), `f5224c4bad` (seed 42), `beb88f669e`
  (seed 43), 55 items each, sha256 of the sorted `argilla_link` list. ST-V2 therefore
  already passes on today's data; keep it as a gate for future runs.
- **ST-F2 — The 9 `test-before` evaluations are replicates of one system.** All of them
  use the module-level baseline `SYSTEM_PROMPT_TEMPLATE` via
  `create_predict_fn(model, endpoint, schema_text)` (`train_common.py:134`), on the same
  commit. Within a seed, the 3 `test-before` runs are therefore *three independent
  measurements of the identical prompt on the identical items* — a free, direct estimate
  of the measurement noise floor (student sampling + judge stochasticity). It spans
  0.473–0.618 in the table above; that spread is **pure noise**, and it is the yardstick
  every technique difference must be held against.

## 3. Per-item data extraction

Child eval runs log **no artifacts**; per-item results live in MLflow **traces**. Each
trace carries three assessments:

| assessment | kind | use |
|------------|------|-----|
| `sql_is_correct` | feedback, `bool` | the outcome variable |
| `argilla_link` | expectation, `str` | **stable item key** (unique per source record) |
| `sql` | expectation, `str` | reference SQL (for qualitative drill-down) |

- **ST-D1** — Fetch with `mlflow.search_traces(locations=["1"], run_id=<child_run_id>,
  max_results=200)`. Note: `experiment_ids=` is deprecated in MLflow 3.13 and the call
  *errors* without `locations` when the run is outside experiment 0.
- **ST-D2** — Trace fetching is slow (≈30–60 s per 55-trace run, 18 runs → 10–20 min).
  Cache the extracted long table to `notebooks/.cache/prompt_opt_items.parquet`, keyed by
  `run_id`; the notebook re-fetches only runs missing from the cache.
- **ST-D3** — Build one long dataframe, 9 runs × 2 phases × 55 items = **990 rows**:
  `[parent_run_id, eval_run_id, technique, seed, phase ∈ {before, after}, item_id,
  question, correct ∈ {0,1}]`.
- **ST-D4** — Child runs map to parents via `tags.mlflow.parentRunId`; phase from the
  run-name prefix `test-before` / `test-after`.

## 4. Preflight validity gates (assert, don't assume)

Run these as hard assertions in the notebook; a failed gate invalidates the pairing that
every test below depends on.

- **ST-V1** — Exactly 55 distinct `item_id`s per eval run, no duplicates.
- **ST-V2** — Within each seed, the item sets of all 6 eval runs (3 techniques × 2 phases)
  are **identical**.
- **ST-V3** — Report the cross-seed item overlap matrix (|A∩B|) and the number of distinct
  items across the whole experiment (expected 75). Anything less than full overlap means
  seeds are *not* exchangeable blocks — hence the cluster bootstrap in §6.
- **ST-V4** — Homogeneity: single unique value across the 9 runs for `model`, `judge_model`,
  `temperature`, `judge_temperature`, `commit_hash`, `dataset_sha256`, `budget`,
  `split_scheme`, `optimization_stop_reason`. Any drift → the run is dropped and named in
  the output.
- **ST-V5** — Drop `f7bd99bc` explicitly by rule (`test_quality_after` is null), not by
  hand-picked run id.
- **ST-V6** — Recompute `mean(correct)` per eval run and check it equals the logged
  `sql_is_correct/mean` / `test_quality_{before,after}` to within 1e-9. This proves the
  trace extraction reconstructs the official metric.

## 5. Statistical tests

Outcome is binary and paired at the item level; every test is chosen to respect that.

### Q1 — Does prompt tuning improve quality at all (per technique)?

Paired within a run: same items, before vs after.

- **ST-Q1.1 — Exact McNemar per run (9 tests).** Build the 2×2 table of
  (before correct/incorrect) × (after correct/incorrect); let `b` = fixed-by-tuning,
  `c` = broken-by-tuning. Test with `scipy.stats.binomtest(b, b + c, 0.5)` (exact
  binomial — the correct choice here, `b + c` will be ~10–20, far too small for the
  χ² approximation). Report `b`, `c`, discordant total, and the exact p.
- **ST-Q1.2 — Pooled across the 3 seeds per technique.** With matched pairs the
  Mantel–Haenszel statistic collapses to the pooled McNemar form
  `(Σb − Σc)² / Σ(b + c)`; report it, but treat its p-value as *nominal only*, because
  ST-F1 means the seeds share items and the strata are not independent. The honest
  interval is the cluster bootstrap of ST-E2.
- **ST-Q1.3 — Sanity check on direction.** TextGrad seed 43 regressed (0.618 → 0.582).
  Report per-run gains individually; never let a technique mean hide a sign flip.

### Q2 — Do the three techniques differ from each other?

Compared on `after` only, using within-seed pairing (ST-V2 guarantees identical items).

- **ST-Q2.1 — Cochran's Q per seed** (3 related binary samples, blocks = items).
  Implement directly (`Q = k(k−1)·Σ(G_j − Ḡ)² / (kΣL_i − ΣL_i²)`, `df = k−1`; χ²
  reference via `scipy.stats.chi2.sf`) — statsmodels is not installed. Report the count
  of *informative* items (those not all-correct or all-incorrect across the 3 techniques);
  everything else contributes zero information and inflates the apparent n.
- **ST-Q2.2 — Primary test: within-block permutation.** Blocks = `(seed, item)`; under H0
  the 3 technique outcomes within a block are exchangeable. Permute technique labels
  within each block, 10 000 draws, `seed=42`, statistic = max |Δ mean accuracy| over the
  3 pairwise contrasts (a maxT statistic, which controls the family-wise error rate over
  the 3 comparisons *inside* the permutation). This is the primary technique-comparison
  test: it needs no independence-across-seeds assumption and no asymptotics.
- **ST-Q2.3 — Pairwise exact McNemar** for each of the 3 technique pairs, per seed and
  pooled, as a legible companion to ST-Q2.2, with Holm–Bonferroni over the 3 pairs.
  Report raw and adjusted p side by side.
- **ST-Q2.4 — Model-based check (optional, secondary).** Logistic GEE
  `correct ~ C(technique) + C(seed)` with `groups=item_id`, exchangeable working
  correlation and cluster-robust SEs — the clean way to handle item reuse plus seed
  effects in one model. Requires adding `statsmodels>=0.14` to the `dev` dependency group
  in `pyproject.toml`. If the dep is rejected, skip it: ST-Q2.2 already carries the
  primary inference.

### Q3 — Is the observed technique spread larger than the measurement noise?

- **ST-Q3.1 — Noise floor from the `test-before` replicates (ST-F2).** Within each seed,
  for each of the 3 pairs of `test-before` runs, compute the per-item **discordance rate**
  (fraction of the 55 items where two replicates of the *same prompt* disagree) and the
  absolute difference in accuracy. Pool the 9 pairwise comparisons into a noise
  distribution.
- **ST-Q3.2 — Compare like with like.** Put the observed between-technique accuracy gaps
  (§2 table) on the same axis as that noise distribution. If a technique gap does not
  exceed the upper range of the same-prompt replicate gaps, say so explicitly in the
  conclusions.
- **ST-Q3.3 — Binomial reference.** Report the single-run binomial SE
  `sqrt(p(1−p)/55) ≈ 0.067` and the Wilson 95% CI for each reported accuracy, so every
  point estimate in the notebook is displayed with its uncertainty.

### Q4 — Run-level (seed-as-block) nonparametric check

- **ST-Q4.1 — Friedman test** on `test_quality_after`, 3 techniques × 3 seed blocks
  (`scipy.stats.friedmanchisquare`), plus Wilcoxon signed-rank pairwise.
- **ST-Q4.2 — State the floor explicitly.** With 3 blocks, Friedman's smallest attainable
  p is ≈ 0.036 (only under a perfectly consistent ranking) and Wilcoxon signed-rank with
  n=3 **cannot** reach p < 0.05 (minimum p = 0.25). Print these limits next to the
  results so a null result is not misread as evidence of equivalence.

## 6. Effect sizes, intervals, multiplicity

- **ST-E1 — Report effects, not just p-values**: risk difference (Δ accuracy in pp) as the
  headline, odds ratio as secondary.
- **ST-E2 — Cluster bootstrap** as the primary interval estimator: resample the ~75
  **source items** with replacement (not rows), carry every observation of a sampled item
  across all seeds/techniques/phases, recompute the statistic, 10 000 resamples,
  percentile CI, `numpy.random.default_rng(42)`. This is what makes the intervals honest
  under the partial cross-seed item overlap of ST-F1/ST-V3.
- **ST-E3 — Exact paired CI** per run for the before→after difference (Bonett–Price or the
  exact conditional binomial interval on `b/(b+c)` mapped to a risk difference) as a
  bootstrap-free cross-check.
- **ST-E4 — Multiplicity**: two declared families — (i) 3 per-technique improvement tests
  (Q1), (ii) 3 pairwise technique contrasts (Q2). Holm within each family; ST-Q2.2's maxT
  already controls family (ii) internally. No correction across families; state that.
- **ST-E5 — Cost is controlled, not a variable.** All 9 runs exhausted the same 1.75 EUR
  budget (`cost_total` spread < 10 %), so all comparisons are *quality at equal spend*.
  Report `cost_total` mean ± range as a control check; do not compute quality-per-EUR
  ratios from a controlled variable.

## 7. Power and what to run next

- **ST-P1 — Post-hoc MDE, single run.** With 55 paired items and a discordance rate around
  0.25 (~14 discordant pairs), exact McNemar needs roughly a 12/2 split to reach p < 0.05
  — i.e. a **single run can only detect gains of ≈ 18 pp or larger**. Compute the exact
  number from the observed discordance rather than quoting this ballpark.
- **ST-P2 — MDE for the pooled 3-seed comparison** via the same exact calculation on
  pooled discordant counts (~40 pairs → ≈ 10–12 pp).
- **ST-P3 — Seeds needed.** Simulate from the observed per-item correctness rates
  (parametric bootstrap over items, resampling splits the way `split_indices` does) to
  estimate the number of seeds required for 80 % power at α = 0.05 to detect a 5 pp and a
  10 pp technique gap. Output a small table `Δ × n_seeds → power`. This is the notebook's
  actionable deliverable: it tells us whether the 3-seed comparison is worth extending
  and by how much.

## 8. Notebook layout

| § | Content |
|---|---------|
| 0 | Config: tracking URI, experiment, `.env` load, RNG seed 42, cache path |
| 1 | Load runs, apply ST-V5 filter, print the 9-run provenance table (§2) |
| 2 | Per-item extraction from traces + parquet cache (ST-D1–D4) |
| 3 | Validity gates ST-V1–V6 (assertions + overlap matrix) |
| 4 | Descriptives: per-run before/after with Wilson CIs, gain forest plot, item-difficulty histogram |
| 5 | Q1 — improvement per technique (ST-Q1.1–Q1.3) |
| 6 | Q2 — technique contrasts (ST-Q2.1–Q2.4) |
| 7 | Q3 — noise floor vs observed gaps (ST-Q3.1–Q3.3) |
| 8 | Q4 — Friedman/Wilcoxon with the attainability caveat |
| 9 | Power & seeds needed (ST-P1–P3) |
| 10 | **Conclusions**: an explicit "supported / not supported by this data" list |

Figures go to `notebooks/figures/`, consistent with the existing notebooks.

## 9. Dependencies

- Already available: `scipy 1.17.1`, `pandas 2.3.3`, `numpy 2.4.4`, `matplotlib 3.10.9`,
  `mlflow 3.13.0`, `pyarrow 24.0.0`.
- **Missing**: `statsmodels` — needed only for ST-Q2.4 (GEE). Add `statsmodels>=0.14` to
  `[dependency-groups].dev` in `pyproject.toml`, or drop ST-Q2.4. Cochran's Q, McNemar,
  Holm and the bootstrap are all implemented directly on scipy/numpy.

## 10. Known limitations to write into the conclusions

- **L1** — 3 seeds is a blocking constraint, not a nuisance: no pairwise technique
  difference of the observed size (≈ 2–7 pp) can be established at this n. Expect the
  main finding to be "each technique improves over the seed prompt; the techniques are
  not distinguishable from each other at this sample size."
- **L2** — The judge is a shared, unreplicated source of error. `judge_temperature=0` and
  `judge_seed=42` make it near-deterministic, so judge bias is *not* estimable from this
  data and is absorbed into every accuracy equally. A re-judge replication (same
  predictions, second judge pass) would separate it; out of scope here.
- **L3** — Item reuse across seeds means seeds are not independent replicates. Handled by
  ST-E2/ST-Q2.2, but it caps the effective sample size well below 9 × 55.
- **L4** — Learning-curve probes (`learning-curve-plan.md`) are not yet logged in this
  experiment; comparison is at the endpoints only. Once probe metrics land, the natural
  extension is comparing *curves* (area under quality-vs-spend) rather than endpoints.
