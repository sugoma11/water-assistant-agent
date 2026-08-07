# Tasks: 20 / 0 / 55 Sampling Refactor

Spec: [`spec.md`](./spec.md)

Conventions: `[P]` = parallelizable with sibling `[P]` tasks (different files, no
shared edits). Each task names a concrete file target. Phases are ordered so earlier
tasks unblock later ones; **do not start a phase until its predecessor is green.**

Target split: **20 train / 0 val (val == train) / 55 test**, seeded and group-aware
as today. Phase layout follows the "one phase changes one approach" rule — the shared
sampler gains the new scheme first (opt-in, old behavior still the default), then
GEPA, TextGrad and SkillOpt flip one at a time, and only then does the new scheme
become the default.

---

## Phase 1 — Sampler core (opt-in; no technique behavior change yet)

- [x] T001 In `src/experiments/text2sql/sampler.py`, add module constant
  `TRAIN_SIZE = 20` next to `Q_THRESHOLD`, with a comment stating the resulting split
  on the 75-record set (20 train / 55 test) and that val is a copy of train (D1).
- [x] T002 In `sampler.py`, add
  `split_indices_train_test(group_ids, seed, train_size=TRAIN_SIZE) -> tuple[list[int], list[int]]`
  next to the existing `split_indices`: same shuffle-then-largest-remaining-deficit
  algorithm, but over two buckets with deficits `[train_size, len(group_ids) - train_size]`
  and ties broken train → test. Do **not** touch the existing 3-way `split_indices`
  (it is still the default path until Phase 5). (depends: T001)
- [x] T003 In `sampler.py`, give `split_dataset` a keyword-only
  `train_size: int | None = None`:
  - `None` → current behavior exactly (3-way `split_indices`, thirds) — this is what
    all three trainers still get after Phase 1;
  - an int → `split_indices_train_test`, returning `(train, list(train), test)`.
    `val` must be a **new list object** holding the same record dicts (D1), never the
    same object as `train`.
  Update the module docstring to describe both regimes. (depends: T002)
- [x] T004 In `sampler.py`, add `--train-size` to the `text2sql-sample` CLI
  (`type=int`, `default=None`, help: "Target train size; val.json is a copy of
  train.json and the rest goes to test. Omit for the legacy equal-thirds split.").
  When set, write `train.json`, `val.json` (identical content to `train.json`) and
  `test.json`, and echo the three sizes. (depends: T003)
- [x] T005 Add `scripts/verify_sampling_2055.py` (sibling of
  `scripts/verify_budget_stop.py`): loads `data/text2sql/deflated_75_sqls_prod.json`
  with the warm `data/text2sql/question_embeddings.npz` cache (no API calls) and
  asserts, for seeds 41–44 with `train_size=20`:
  `len(train) == 20`, `len(test) == 55`, `val == train` and `val is not train`,
  `set(train) & set(test) == ∅`, `train ∪ test` covers all 75 records, splits are
  deterministic across two calls with the same seed, and pairwise train overlap
  between distinct seeds is < 20 (variation preserved). Print the overlap matrix.
  (depends: T003)
- [x] T006 Run `uv run python scripts/verify_sampling_2055.py` — all assertions pass
  (sizes 20+55 for every seed, `val == train` / `val is not train`, disjoint,
  covering, deterministic, variation preserved). The script checks **both** question
  regimes, because grouping runs on the question text and the reference values above
  are the *clean*-question ones while every `just` recipe trains with
  `--use-prod-questions`:
  - clean questions: 75 groups / largest 1; overlaps 41v42=7, 41v43=7, 41v44=7,
    42v43=4, 42v44=5, 43v44=5 (matches the expected values as written);
  - prod questions (**what the trainers get**): 74 groups / largest 2; overlaps
    41v42=2, 41v43=4, 41v44=10, 42v43=4, 42v44=2, 43v44=6. Exact 20/55 is still
    reached with the size-2 group, which never straddles the split. Seeds 41 and 44
    share 10 of 20 train records here — still materially different sets, but the
    thinnest per-seed variation in the design; worth knowing when reading the
    replicate spread. (depends: T005)
- [x] T007 Confirm Phase 1 is inert for the trainers: `git diff` touches only
  `sampler.py` + the new script, and none of `train_gepa.py` / `train_textgrad.py` /
  `train_skillopt.py` passes `train_size`, so all three still split 25/25/25.
  Confirmed twice over: the only `src/` file in the diff is `sampler.py`, and the
  legacy path was diffed against `HEAD:sampler.py` record-for-record — identical
  train/val/test for seeds 41–44 in both question regimes, 25/25/25 throughout.
  (depends: T006)

## Phase 2 — GEPA (one approach)

- [x] T008 In `src/experiments/text2sql/train_gepa.py`, pass `train_size=TRAIN_SIZE`
  to the `split_dataset(...)` call (line ~180) and import `TRAIN_SIZE` alongside
  `split_dataset`. Nothing else in the CLI changes. Done; the only other `src/` file
  in the diff is `sampler.py` (Phase 1), so TextGrad/SkillOpt still split 25/25/25.
  (depends: T007)
- [x] T009 Update the `train_gepa.py` module docstring: GEPA now reflects on
  minibatches from the 20-record train split and Pareto-scores candidates on a val
  split that **is** that same train split, so `initial_eval_score` /
  `final_eval_score` are training scores (C4); the seed full-val pass that the
  `BudgetStopper` first-call snapshot moves to the excluded bucket (D3 of the
  cost-budget spec) is now 20 records, not 25; `test_quality_{before,after}` on 55
  held-out records is the only generalization signal. (depends: T008)
- [x] T010 Cheap smoke: start `just text2sql-train-qwen36-qwen36-qwen36-gepa-1`
  (seed 41) and confirm the pre-LLM echo reads
  `Split 75 samples (seed=41): train=20, val=20, test=55`, then interrupt before the
  test-before phase begins. Repeat for seed 42 via `sampler-seed=42`. No LLM spend.
  Both confirmed: seed 41 → `train=20, val=20, test=55`, seed 42 → same. Notes from
  running it:
  - `common.just` currently points `questions_path` at the 5-record
    `data/text2sql/to_test.json`; both smokes were run with
    `just --set questions_path data/text2sql/deflated_75_sqls_prod.json ...`. Under
    `train_size=20` that 5-record smoke file now yields train=5 / val=5 / **test=0**
    (deficit `5 - 20 < 0`, so every group lands in train) — the local smoke config
    needs a `--train-size`-aware dataset or it will hand the test phases an empty set.
  - `text2sql-...-gepa-2` had a long-standing recipe bug: `questions-path="questions_path"`
    (quoted → literal string, not the variable), so it always failed with
    `Path 'questions_path' does not exist`. Unquoted to match its sibling recipes.
  - The seed-41 attempt overshot the interrupt and ran ~4 of 55 `test-before` records
    before being killed (small unmetered spend); its two RUNNING MLflow runs
    (`e800ab90…` parent, `6eb76696…` test-before) were set to KILLED. The parent had
    already logged `train_size=20`, `test_size=55`. Seed 42 was killed exactly at the
    split echo via `scratchpad/run_until_split.py` — no run created, no spend.
  (depends: T009)
- [x] T011 Full regression run: one complete `...-gepa-1` run to MLflow. Confirm on
  the run page: `train_size=20`, `val_size=20`, `test_size=55`; the test-before /
  test-after nested runs each evaluate 55 records; `optimization_stop_reason` and the
  `cost_*` metrics are still populated; the optimized prompt URI is registered.
  All confirmed on run `53896e96d7e44267b1c106892457d30e` (seed 41, `--budget 1`,
  19:05 → 02:04, ~7 h wall clock, log `logs/gepa-1-2055-0806-1905.log`):
  - params `train_size=20`, `val_size=20`, `test_size=55`, `sampler_seed=41`;
  - nested `test-before` / `test-after` runs each echoed "on 55 samples" and their
    `sql_is_correct/mean` are exact 55ths (30/55 = 54.55% → 40/55 = 72.73%);
  - `optimization_stop_reason=budget_exhausted`; `cost_total=1.110`,
    `cost_task=0.741`, `cost_judge=0.411`, `cost_optimizer=0.084`,
    `cost_excluded=0.126` (the seed full-val pass over 20 records, D3);
  - optimized prompt registered as `prompts:/text2sql_system/19`.
  Result under the new split: val (= train) 65.00% → 80.00%, test 54.55% → 72.73%.
  Note the val numbers are training scores now (C4). (depends: T010)

## Phase 3 — TextGrad (one approach)

- [x] T012 In `src/experiments/text2sql/train_textgrad.py`, pass
  `train_size=TRAIN_SIZE` to the `split_dataset(...)` call (line ~198) and import
  `TRAIN_SIZE`. Nothing else in the CLI changes. Done; the `src/` files in the diff are
  now `sampler.py` (Phase 1), `train_gepa.py` (Phase 2) and the two TextGrad files, so
  SkillOpt still splits 25/25/25. Started while T011's GEPA run was still in flight —
  the edits are trainer-local and cannot affect a running GEPA process, but T011's
  MLflow verification is still owed. (depends: T011)
- [x] T013 Update the `train_textgrad.py` module docstring for the new split and
  record the `val_gate_size` implication (C5): with a 20-record val set the configured
  `textgrad_val_gate_size := "12"` (`common.just`) gates on 60% of the train split,
  and `--val-gate-size 0` / any value `>= 20` degenerates to the full set via
  `TextGradPromptOptimizer._build_gate_set`. (The task text assumed the committed
  default 16 → 80%; the working tree carries 12, kept per T014.) (depends: T012)
- [x] T014 Decide and record the `val_gate_size` value for the new regime — keep 16,
  or lower it to restore the old ~64% ratio (13), or set 0 for a full-val gate. Write
  the decision + reasoning into `spec.md` under "Decisions" and apply it to
  `textgrad_val_gate_size` in `common.just` if it changes. **Decision: keep the current
  12** (spec.md D5, C5 amended to match) — at n=20 it already rises from 48% to 60% of
  val, and gate evals are billable while the full-val baseline is not, so widening the
  gate buys marginal accept/revert precision at the cost of gradient steps under the
  money cap. `common.just` unchanged (already 12). (depends: T013)
- [x] T015 Verify the guardrail text in
  `src/experiments/text2sql/textgrad_optimizer.py:552-557` is still accurate: with
  `MIN_SPLIT_SIZE = 1` a 20/20 train/val pair passes, and the message still describes
  "the same seeded `split_dataset` split GEPA uses" — amend the wording to note val
  is now the train split. No logic change. Done: both the comment and the raised
  message now say val is a copy of train under the 20 / 0 / 55 scheme. (depends: T012)
- [x] T016 Cheap smoke: start `just text2sql-train-qwen36-qwen36-qwen36-textgrad-1`,
  confirm `train=20, val=20, test=55` in the echo, interrupt. Confirmed for seed 41:
  `Split 75 samples (seed=41): train=20, val=20, test=55`, with `--val-gate-size 12`
  on the command line. Run via `just --set questions_path
  data/text2sql/deflated_75_sqls_prod.json ...` (the checked-in `questions_path` still
  points at the 5-record `to_test.json`, see T010) and killed at the echo by a
  scratchpad `run_until_split.py`, so no MLflow run and no spend. (depends: T015)
- [x] T017 Full regression run: one complete `...-textgrad-1` run. Confirm the logged
  split params (20/20/55), that the excluded full-val baseline (`cost_excluded`, SC4)
  is present and now covers 20 records, that the per-step `eval_score` series is
  logged, and that both test phases score 55 records.
  All confirmed on run `3d0842d4769e4704a89d09079a07697b` (seed 41, `--budget 1`,
  02:06 → 06:11, ~4 h, log `logs/textgrad-1-2055-0807-0206.log`), run after T011's
  GEPA run finished so the two never shared endpoint quota:
  - params `train_size=20`, `val_size=20`, `test_size=55`, `val_gate_size=12`,
    `batch_size=2`, `optimization_stop_reason=budget_exhausted`;
  - `cost_excluded=0.258` (the full-val baseline, now 20 records), `cost_total=1.004`,
    task 0.695 / judge 0.395 / optimizer 0.171;
  - `eval_score` series logged for 9 points (step 0 gate baseline + 8 gradient steps);
    values are exact twelfths (7/12, 9/12, …), confirming the n=12 gate of D5;
  - nested `test-before` / `test-after` each echoed "on 55 samples", means 29/55 =
    52.73% and 27/55 = 49.09%.
  Result: val (= train) 65.00% → 60.00% — TextGrad did **not** beat its baseline at
  this budget, so it kept the seed prompt (registered unchanged as
  `prompts:/text2sql_system/21`, byte-identical to GEPA's seed v18 and to
  `SYSTEM_PROMPT_TEMPLATE`). Two noise observations that matter for reading the
  campaign (C1/C2 context):
  - test-before and test-after here scored the **same** prompt (nothing was accepted)
    and still differ by 2 records (29 vs 27 of 55);
  - GEPA's test-before at the same seed, same split and same seed prompt scored 30/55
    (54.55%) vs TextGrad's 29/55 (52.73%).
  So the judge/model run-to-run noise floor on the 55-record test set is ~1–2 records
  (≈2–4 pp); per-technique deltas below that are not signal. (depends: T016)

## Phase 4 — SkillOpt (one approach)

- [x] T018 In `src/experiments/text2sql/train_skillopt.py`, pass
  `train_size=TRAIN_SIZE` to the `split_dataset(...)` call (line ~177) and import
  `TRAIN_SIZE`. Nothing else in the CLI changes. Done; all three trainers now split
  20/20/55, so the `src/` diff is `sampler.py` (Phase 1) plus the three trainers and the
  two optimizer guardrails. Started while T011's GEPA run was still in flight (same
  rationale as T012: the edits are trainer-local and cannot affect a running GEPA
  process); T011's and T017's MLflow verifications are still owed. (depends: T017)
- [x] T019 Update the `train_skillopt.py` module docstring for the new split, noting
  the two cfg values that follow from it in
  `SkillOptPromptOptimizer._build_cfg`: the full-pass epoch (`train_size` /
  `batch_size` = 20, was 25) and the hard val gate `sel_env_num = len(val_set)` = 20,
  now over the same records the round trained on. `minibatch_size` (3) and
  `edit_budget` (2) are unchanged structural knobs — both confirmed against
  `common.just` (`skillopt_edit_budget := "2"`, `skillopt_minibatch_size := "3"`). The
  new paragraph also records C4: the gate and the excluded baseline both score train
  data, so `{initial,final}_eval_score` are training scores. (depends: T018)
- [x] T020 Verify the guardrail in
  `src/experiments/text2sql/skillopt_optimizer.py:613-619` still holds (20/20 passes
  `MIN_SPLIT_SIZE`) and amend its message wording the same way as T015. No logic
  change. Done: `MIN_SPLIT_SIZE = 1` (`prompt_skill.py:37`), so 20/20 passes with room
  to spare; the comment and the raised message now both say val is a copy of train
  under the 20 / 0 / 55 scheme. (depends: T018)
- [x] T021 Cheap smoke: start `just text2sql-train-qwen36-qwen36-qwen36-skillopt-1`,
  confirm `train=20, val=20, test=55` in the echo, interrupt. Confirmed for seed 41:
  `Split 75 samples (seed=41): train=20, val=20, test=55`. Run via `just --set
  questions_path data/text2sql/deflated_75_sqls_prod.json ...` (the checked-in
  `questions_path` still points at the 5-record `to_test.json`, see T010) and killed at
  the echo by a scratchpad `run_until_split.py`, so no MLflow run and no spend. The
  smoke also proves both edited files import cleanly. (depends: T020)
- [ ] T022 Full regression run: one complete `...-skillopt-1` run. Confirm the logged
  split params (20/20/55), that `history.json` / `best_skill.md` are written and the
  gate series is logged, that the budget-stop read-back path still resolves
  `baseline_gate_score`, and that both test phases score 55 records. **Queued behind
  T011 and T017** (shared endpoints/quota, one full run at a time): as of 2026-08-06
  ~19:50 the T011 GEPA run was still in its `test-before` phase (37/55 after ~46 min),
  with T017's TextGrad run next in line. Then run with `--budget 1` (`--set train_budget
  1`) against the 75-record dataset, same as the GEPA run. (depends: T021)

## Phase 5 — Make 20/0/55 the default; retire the ratio path

Implemented ahead of T017/T022 (the two outstanding full regression runs) so that the
re-run campaign records `split_scheme` from its first run; the flip is source-only and
cannot disturb an in-flight trainer, which has already imported the module.

- [x] T023 In `sampler.py`, change `split_dataset`'s `train_size` default from `None`
  to `TRAIN_SIZE`, delete the 3-way `split_indices` and the `ratios` parameter, and
  make `split_indices_train_test` the only splitter (rename it back to
  `split_indices` and update the module docstring). Done: one splitter
  `split_indices(group_ids, seed, train_size=TRAIN_SIZE) -> (train, test)`, and the
  docstring now describes a single regime and points at `spec.md` for C1.
  (depends: T022)
- [x] T024 In `sampler.py`, change the `text2sql-sample` CLI `--train-size` default
  from `None` to `TRAIN_SIZE` and drop the legacy branch; it always writes
  train/val/test JSON with `val.json` a copy of `train.json`. Done (`show_default`
  added). Verified end-to-end: `uv run text2sql-sample --seed 41` on the 75-record set
  wrote 20 / 20 / 55, `val.json == train.json` byte-for-byte, train ∩ test = ∅.
  (depends: T023)
- [x] T025 Drop the now-redundant explicit `train_size=TRAIN_SIZE` arguments from
  `train_gepa.py`, `train_textgrad.py` and `train_skillopt.py` (and the now-unused
  `TRAIN_SIZE` imports). Done; the three module docstrings now say `split_dataset`
  rather than `split_dataset(..., train_size=TRAIN_SIZE)`. [P] with T026.
  (depends: T023)
- [x] T026 In `src/experiments/text2sql/train_common.py::_run_optimization`, add a
  `split_scheme` MLflow param next to `sampler_seed` / `train_size` / `val_size` /
  `test_size` (value: `"20-0-55-val-eq-train"`), so pre- and post-refactor runs are
  distinguishable in the UI without reading split sizes (C1, C4). Document in the
  docstring that `initial/final_eval_score` are training scores under this scheme.
  Done as a module constant `SPLIT_SCHEME` (commented with the C1 caveat) logged in the
  same `log_params` block. Note T011's GEPA run predates this and therefore carries no
  `split_scheme` — the campaign re-runs it anyway. [P] with T025. (depends: T023)
- [x] T027 Re-run `uv run python scripts/verify_sampling_2055.py` against the new
  defaults (call `split_dataset` with no `train_size`) — same assertions, same
  reference numbers as T006. Done via `--default-train-size`: `SAMPLING VERIFIED`, both
  question regimes, 20/20/55 for seeds 41–44, and the overlap matrices reproduce T006
  exactly (clean 7,7,7,4,5,5; prod 2,4,10,4,2,6). (depends: T024, T025)
- [x] T028 One smoke start per technique (`gepa-1`, `textgrad-1`, `skillopt-1`),
  confirming each still echoes `train=20, val=20, test=55` after the default flip;
  interrupt each. All three confirmed at seed 41 via the scratchpad
  `run_until_split.py` (killed at the echo → no MLflow run, no spend), run with
  `--set questions_path data/text2sql/deflated_75_sqls_prod.json` as in T010/T016/T021.
  `ruff check` is clean apart from two F401s that already exist at `HEAD`
  (`scripts/count_tokens.py`, and `validate_teacher_model` in `train_gepa.py`, whose
  call site is commented out at `HEAD`). (depends: T027)

## Phase 6 — Downstream, docs, and the re-run campaign

- [x] T029 `scripts/regrade_traces.py`: no code change expected (it reads whatever
  test-before/after traces a run logged), but confirm it handles 55 traces per phase
  and add a note to its module docstring that paired-at-seed deltas must not mix
  pre-refactor (25-record test) with post-refactor (55-record test) runs (C1).
  Confirmed size-agnostic: `assessed_items` pulls with `max_results=2000` (a 55-record
  phase logs ~110 traces) and every aggregate is per-item — no code change made. The
  docstring gained a "Split regimes" paragraph naming the concrete hazard:
  `after[technique][seed]` averages *every* run found at a seed, so a pre- and a
  post-refactor run at seed 41 pool into one arm silently, and the seeds index
  different test sets across regimes. Nothing enforces the separation — select a regime
  via `--experiment-ids` and read the `n` column (25 vs 55) as the tell; post-refactor
  parents also carry `split_scheme`. Two stale comments fixed while there: the
  quantisation figure (4 pp at 25 records → 1.8 pp at 55) and the cluster-bootstrap
  rationale (a record is in test with p=1/3 under thirds, p=55/75 now — recurrence
  across seeds is the common case, so the cluster bootstrap matters *more*).
  (depends: T028)
- [x] T030 [P] Update the split description in the existing specs that assert
  "identical seed and split across techniques" so they are not left stating the old
  thirds: `specs/textgrad-mlflow-integration/{spec,plan}.md`,
  `specs/skillopt-mlflow-integration/plan.md`,
  `specs/fapo-mlflow-integration/{spec,plan}.md`. One-line amendment each, pointing
  at `specs/sampling_refactoring/spec.md`; do not rewrite their history. Done — one
  italic *Amended:* line appended to each file's split bullet (the two spec files at
  their EC3 "too-small split" bullet, the three plan files at their "Train/val/test
  split" data-model bullet). None of the five stated the old sizes numerically, so
  nothing had to be rewritten, only pinned. (depends: T028)
- [x] T031 [P] Note in `common.just` (comment near `questions_path` /
  `textgrad_val_gate_size`) that the harness splits 20 train / 55 test and that val
  is the train split. Done in both places. The `questions_path` comment also records
  the trap T010 hit: a dataset of ≤ 20 records yields an **empty test split**, so the
  checked-in 5-record `to_test.json` is a train-path smoke file only.
  (depends: T028)
- [ ] T032 Re-run the full comparison campaign under the new split: 3 techniques ×
  seeds 41–44 (12 runs). Use `setsid` for anything over ~20 min so the runs survive
  the harness, and stagger the KISSKI-key recipes against the 429 quota — the test
  phases are now 2.2× longer (C2). Record the new run IDs. Not started — awaiting
  go-ahead. Scale to plan for: T011's single GEPA run took **~7 h wall clock** at
  `--budget 1` (19:05 → 02:04), so 12 sequential runs is on the order of 3–4 days;
  parallelism is bounded by the shared OpenRouter/KISSKI quota, not by CPU.
  T017 (TextGrad) and T022 (SkillOpt) are still owed and should either run first as
  single-run regressions or be folded in as the seed-41 arms of the campaign.
  (depends: T029)
- [ ] T033 Sanity-check the campaign in MLflow: all 12 runs carry
  `split_scheme=20-0-55-val-eq-train`, `train_size=20`, `val_size=20`,
  `test_size=55`; per-seed test sets differ across seeds but are identical across
  techniques at the same seed (the fairness property this refactor must preserve).
  Blocked on T032. The `split_scheme` check is now satisfiable (T026); it will fail for
  any run started before that landed, T011's `53896e96…` included. (depends: T032)

---

## Verification summary

| Check | Where | Expected |
|---|---|---|
| Split sizes | `scripts/verify_sampling_2055.py` | 20 / 20 / 55, val == train, val is not train |
| Disjointness + coverage | same | `train ∩ test = ∅`, `train ∪ test` = 75 |
| Per-seed variation | same | pairwise train overlap 4–7 of 20 for seeds 41–44 |
| Determinism | same | two calls at one seed give identical splits |
| Trainer echo | each `just` recipe, pre-LLM | `train=20, val=20, test=55` |
| MLflow params | run page | `train_size=20`, `val_size=20`, `test_size=55`, `split_scheme` |
| Cross-technique fairness | T033 | same seed ⇒ same test set across all three techniques |
