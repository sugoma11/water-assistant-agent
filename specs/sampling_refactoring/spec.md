# Feature Specification: 20 / 0 / 55 Sampling Refactor

## Overview / Context

Every prompt-optimization technique in this harness (GEPA, TextGrad, SkillOpt) draws
its data from one shared, seeded, group-aware sampler
(`src/experiments/text2sql/sampler.py`). It currently splits the 75-record text-2-SQL
dataset into **three equal thirds — 25 train / 25 val / 25 test**.

This refactor changes the sampling strategy to **20 train / 0 val / 55 test**, where
"0 val" means *the validation split is the training split* (`val == train`) rather
than an empty list: every technique keeps its validation machinery (Pareto scoring,
keep-best gates) intact, it simply validates on the same 20 records it trains on.
The 55 records freed up go to the held-out test split, which is the only split that
feeds `test_quality_before` / `test_quality_after` — the numbers the technique
comparison actually rests on.

The seeded, group-aware nature of the split is unchanged: different `--sampler-seed`
values still yield different (and still near-duplicate-safe) train/test partitions,
so the multi-seed replicate design (seeds 41–44) keeps working.

## Goals

- Move the shared sampler from a 3-way `(1/3, 1/3, 1/3)` ratio split to a target
  **train size of 20**, with **all remaining records held out as test** (55 on the
  75-record set).
- Make the validation split an exact copy of the train split for every technique, so
  each optimizer's existing val-driven logic (GEPA's Pareto valset, TextGrad's
  keep-best gate, SkillOpt's hard val gate) runs unchanged on train data.
- Preserve per-seed split variation: seeds 41–44 must still produce materially
  different train sets, disjoint from their test sets, covering the dataset.
- Apply the change to GEPA, TextGrad and SkillOpt **one technique per phase**, so a
  regression can be attributed to a single trainer.

## Non-Goals

- Changing the near-duplicate grouping rules (SQL-shape masking, question-embedding
  threshold), the judge, the dataset, or any technique's algorithm.
- Changing the money budget (`--budget`) or the cost-metering semantics. The
  test-before/after phases sit outside `meter.active()` and stay unmetered.
- Retro-fitting or re-labelling MLflow runs recorded under the old 25/25/25 split.

## Decisions

- **D1 — `val` is a copy of `train`, not an empty list.** Returning `[]` would trip
  `MIN_SPLIT_SIZE` guards in `textgrad_optimizer.py:552` and
  `skillopt_optimizer.py:613`, and would make GEPA's `valset` degenerate. The sampler
  returns `(train, list(train), test)` — a *new list object* holding the same record
  dicts, so no optimizer can alias-mutate one split through the other, while
  `len(val) == len(train)` and content is identical.
- **D2 — target counts, not ratios.** `split_indices` moves from
  `ratios=(1/3, 1/3, 1/3)` to a two-way `train_size` target with test taking the
  remainder. Verified on `data/text2sql/deflated_75_sqls_prod.json`: the existing
  largest-remaining-deficit assignment yields **exactly 20 / 55** for seeds 41–44,
  disjoint, covering all 75 records.
- **D3 — staged rollout.** Phase 1 adds the new scheme to the sampler *behind an
  explicit `train_size=` argument* while the old ratio path stays the default;
  Phases 2–4 flip exactly one trainer each; Phase 5 makes 20/0/55 the default and
  deletes the ratio path. This is what makes "one phase = one approach" possible for
  a helper all three techniques share.
- **D4 — group machinery untouched.** On the current dataset every record is its own
  near-duplicate group (75 groups, largest = 1), so exact target sizes are always
  reachable. The group-aware assignment is kept anyway so a future dataset with real
  paraphrase groups still cannot leak train records into test.
- **D5 — TextGrad keeps `val_gate_size = 12` (T014).** The per-step keep-best gate
  stays at the current `textgrad_val_gate_size := "12"` in `common.just`; no change is
  applied. Under the new split that subset covers **60% of the 20-record val/train
  split** (it was 48% of the old 25-record val set), so the gate signal gets *stronger*
  for free — raising it to 16 (80%) or 0 (full val) would buy a marginally less noisy
  accept/revert decision at a 33% / 67% higher per-step gate cost. The optimization
  phase is money-capped (`--budget`), and gate evals are billable while the full-val
  baseline is not, so every euro spent widening the gate is a gradient step not taken.
  Cheap-gate-plus-more-steps stays the right trade at n=20. Revisit only if the run
  logs show the gate accepting prompts that the full-val `final_eval_score` then
  contradicts.

## Consequences to accept

- **C1 — old runs are not comparable.** `test_quality_{before,after}` from
  pre-refactor runs was measured on a 25-record test set drawn under a different
  partition. Post-refactor numbers are measured on 55 different records. Every
  comparison arm (all techniques × all seeds) must be re-run; mixing the two in
  `scripts/regrade_traces.py` paired-at-seed deltas is invalid.
- **C2 — test phases get ~2.2× more expensive in wall-clock and endpoint quota.**
  110 judged predictions per run (2 phases × 55) instead of 50. This spend is
  unmetered (outside `meter.active()`), so `--budget` is unaffected, but the KISSKI
  per-key rate limit is. Expect longer runs; the two-key `-k2` recipes help.
- **C3 — the optimization phase gets marginally cheaper.** Each full-val pass now
  scores 20 records instead of 25 (GEPA's seed + per-accepted-candidate passes,
  TextGrad's excluded full-val baseline, SkillOpt's gate).
- **C4 — `initial_eval_score` / `final_eval_score` become training scores.** With
  `val == train` they no longer measure held-out generalization; only
  `test_quality_{before,after}` does. This is the intended trade — it is recorded as
  a `split_scheme` MLflow param so the two regimes are distinguishable in the UI.
- **C5 — TextGrad's val gate covers a larger share of the val set.** The configured
  `textgrad_val_gate_size := "12"` goes from 48% of a 25-record val set to **60% of the
  20-record one** (16 would be 80%, was 64%). The knob still behaves as documented
  (`0` or `>= len(val)` degenerates to the full set); the value is kept unchanged — see
  D5 for the reasoning.
