# Tasks: Cost-Budget-Based Stopping for Prompt-Optimization Runs

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md)

Conventions: `[P]` = parallelizable with sibling `[P]` tasks (different files, no
shared edits). Each task names a concrete file/artifact target. Phases are ordered so
earlier tasks unblock later ones; do not start a phase until its predecessor is green.
Phase 3 (TextGrad) intentionally precedes 4–5: it is fully project-owned and proves the
meter end-to-end before the library-seam techniques build on it.

User stories (from spec): **US1** start any technique with a money budget · **US2** run
all three techniques at the same budget for an equal-cost comparison · **US3** reviewer
sees budget, spend (tokens + money, per role) and stop reason in the tracking UI ·
**US4** budget stop keeps the best-so-far prompt, saved/versioned as a natural finish ·
**US5** missing price config refuses to start with a clear message.

---

## Phase 1 — Meter core, no LLM (blocks everything)

- [x] T001 Create `src/experiments/text2sql/cost_meter.py` with `PriceConfig.from_env()`
  reading the six env vars `PRICE_{TASK,JUDGE,OPTIMIZER}_{INPUT,OUTPUT}` (EUR per 1M
  tokens); refuse absent, negative or non-numeric values with a message naming the
  offending env var (EC5, SC6, US5).
- [ ] T002 Implement `CostMeter` in `cost_meter.py`: thread-safe
  `record(role, input_tokens, output_tokens)` accumulating tokens and cost
  (`tokens/1e6 * price`) into billable/excluded buckets; contextvar-based `active()`,
  `role(name)`, `excluded()` contexts; `exhausted()` (`billable_cost >= budget`, sets
  `stop_reason="budget_exhausted"` on first firing, default `"completed"`; a third
  value `"failed"` is recorded by the runner when the phase raises — see T006);
  `record_unmetered(role)` with a prominent warning (EC2); `check_unmetered()` raising
  past `max(5, 5% of all calls metered while active(), excluded bucket included)`
  (OQ3); `spend_summary()` returning the FR9 metric
  dict (`tokens_{task,judge,optimizer}_{input,output}`, `cost_{task,judge,optimizer}`,
  `cost_total`, `cost_excluded`, `unmetered_calls`). Assert a role is set when active;
  fall back loudly rather than misattributing — role-missing calls count toward the
  same unmetered counter and threshold as missing-usage calls (R6). (depends: T001)
- [ ] T003 Add the stop/attachment primitives to `cost_meter.py`:
  `BudgetStopper(meter)` implementing GEPA's `StopperProtocol` (`(gepa_state) -> bool`)
  with the first-call snapshot that reclassifies seed-pass spend as excluded (D3);
  `BudgetExhaustedStop(Exception)` for the SkillOpt checkpoint;
  `litellm_reflection_callback(meter)` recording litellm calls *without* the
  `cost_meter_role` metadata tag to the `optimizer` role (GEPA reflection seam, D1-3).
  (depends: T002)
- [x] T004 Wire the litellm seam in `src/experiments/text2sql/harness.py`:
  `completion_with_retry` records usage to the active meter under the role contextvar
  (no-op when no meter is active — FR12/NFR4); a response missing usage data triggers
  `record_unmetered` + loud warning (EC2, FR11); `build_completion_kwargs` injects
  `metadata={"cost_meter_role": <role>}` so the reflection callback can dedupe tagged
  calls. (depends: T002)
- [x] T005 Set role contexts at the three litellm call sites: `role("task")` in
  `_make_predict_fn` (`harness.py`) and `Text2SqlEnvAdapter._task_sql`
  (`skillopt_optimizer.py`); `role("judge")` in `build_sql_judge_scorer`. (depends: T004)

## Phase 2 — Shared plumbing (US1, US3, US5) — depends on Phase 1

- [x] T006 Extend `_run_optimization(..., cost_meter)` in
  `src/experiments/text2sql/train_common.py`: log `budget` + the six lowercased price
  params up front under identical names for every technique (FR10, NFR1); wrap
  `mlflow.genai.optimize_prompts(...)` in `meter.active()` (test-before/after eval
  phases stay outside — FR5, EC3); log `spend_summary()` metrics +
  `optimization_stop_reason` param in a `finally`, recording `"failed"` when the
  optimization phase raised, so FAILED runs still carry their spend and their true
  stop reason (FR9, EC4, EC6). (depends: T003, T005)
- [x] T007 Add `read_price_config()` validation + required `--budget` (float, EUR, > 0,
  no default) to all three CLIs — `train_gepa.py`, `train_textgrad.py`,
  `train_skillopt.py` — invoked *before* `setup_mlflow` so a misconfigured run refuses
  to start (EC5, SC6). Construct the `CostMeter` and pass it to `_run_optimization`;
  keep the old effort caps passing through to the optimizers for now so each of
  Phases 3–5 stays independently runnable. (depends: T006)
- [x] T008 [P] Add the six `PRICE_*` entries to `.example.env` with an "EUR per 1M
  tokens — set your endpoint's real prices" comment (D4, OQ1). (depends: T001)

## Phase 3 — TextGrad, fully project-owned (US1–US4) — depends on Phase 2

- [x] T009 Extend `make_client(endpoint, meter=None, role=None)` in
  `src/experiments/text2sql/prompt_skill.py` with a metered variant wrapping
  `chat.completions.create` that records usage under the construction-bound role
  (D1-2). (depends: T002)
- [ ] T010 In `src/experiments/text2sql/textgrad_optimizer.py`: build
  `SchemaInjectingEngine` with a metered `task` client and `ReflectionEngine` with a
  metered `optimizer` client; disable the inherited `CachedEngine` disk cache in
  `_DefaultGenKwargsEngine` so every metered call pays real tokens (D3, R5).
  (depends: T009)
- [ ] T011 Rewrite the TextGrad loop in `textgrad_optimizer.py`: delete the
  `epochs`/`metric_call_budget`/`max_steps_per_epoch` params and `spent` bookkeeping;
  `for epoch in itertools.count(1)` with the existing per-gradient-step checkpoint now
  `if self.cost_meter.exhausted(): break` + `check_unmetered()` (FR2, FR7); run the
  baseline and final full-val `_val_score` calls inside `meter.excluded()` while
  per-step gate evals stay billable (FR4, FR5). Keep-best/revert logic untouched (FR8).
  (depends: T010)
- [ ] T012 Clean up `src/experiments/text2sql/train_textgrad.py`: remove `--epochs`,
  `--metric-call-budget`, `--max-steps-per-epoch` options and their logged params; keep
  `--batch-size`, `--val-gate-size` as structural knobs (FR2, C3). (depends: T007, T011)
- [ ] T013 Verify TextGrad with a tiny-budget run: stops within one gradient step of
  exhaustion (SC1); returned prompt is best-not-last per the logged progression (SC3);
  spend metrics + stop reason present (SC2); `cost_excluded` covers exactly the two
  reserved full-val passes (SC4); a budget smaller than one step still ends honestly
  with the seed prompt and `budget_exhausted` (EC1). (depends: T012)

## Phase 4 — GEPA (US1–US4) — depends on Phase 2; [P] with Phase 5

- [ ] T014 Wire GEPA in `src/experiments/text2sql/train_gepa.py`: drop the
  `MAX_METRIC_CALLS` constant and its logged param; pass `max_metric_calls=10**9`
  sentinel and `gepa_kwargs={..., "stop_callbacks": [BudgetStopper(meter)]}`; register
  the litellm reflection callback when the meter activates and deregister after
  (scoped to the GEPA run, D1-3). (depends: T007)
- [ ] T015 Runtime-verify the GEPA seams: (a) the first `BudgetStopper` invocation
  happens *after* the seed full-val pass so the snapshot-to-excluded works — if not,
  apply a documented fallback (charge the seed pass with a named param, or snapshot at
  the second invocation) (R2, D3); (b) reflection-callback threading lags are bounded
  and harmless at iteration boundaries (R1); (c) the huge `max_metric_calls` is only a
  cosmetic progress-bar denominator. Record outcomes in code comments or the spec dir.
  (depends: T014)
- [ ] T016 Verify GEPA with a tiny-budget run: stops within one GEPA iteration (SC1,
  R3 overshoot visible as `cost_total - budget`); best candidate returned by the engine
  (SC3, FR8); reflection tokens appear under the `optimizer` role (FR4);
  `cost_excluded` covers the seed pass (SC4). (depends: T015)

## Phase 5 — SkillOpt (US1–US4) — depends on Phase 2; [P] with Phase 4

- [ ] T017 Meter the SkillOpt adapter in
  `src/experiments/text2sql/skillopt_optimizer.py`: `Text2SqlEnvAdapter` takes the
  meter; at the start of every rollout fold in the optimizer-token delta
  (`skillopt.model.router.get_token_summary()` minus last snapshot → `optimizer` role),
  run `check_unmetered()`, and for non-excluded rollouts raise `BudgetExhaustedStop`
  when exhausted (FR7, D2); run the first eval-split rollout (baseline gate) under
  `meter.excluded()` (FR5, D3). (depends: T007)
- [ ] T018 Budget-stop lifecycle in `SkillOptPromptOptimizer.optimize`
  (`skillopt_optimizer.py`): call `reset_token_tracker()` up front; set cfg
  `num_epochs=10**6` sentinel (safe with the pinned constant-LR scheduler); catch
  `BudgetExhaustedStop` around `trainer.train()`, fold the final tracker delta, and
  take the budget-stop read-back path — `best_skill.md` + `history.json` (initial score
  from the baseline row, final from the best row); if no step completed, return the
  seed template byte-for-byte with `initial == final` from the baseline gate (EC1,
  FR8, SC3). Existing EC6 artifacts-unreadable failure stays natural-completion-only.
  (depends: T017)
- [ ] T019 Clean up `src/experiments/text2sql/train_skillopt.py`: remove `--epochs` and
  its logged param; keep `--edit-budget`, `--minibatch-size`, `--reflect-on-success`,
  `--reasoning-effort` as structural knobs (FR2, C3). (depends: T018)
- [ ] T020 Runtime-verify the SkillOpt seams and run a tiny-budget smoke: the baseline
  gate is the first eval-split rollout and precedes the first train rollout; the
  `history.json` baseline row exists after the gate; a `BudgetExhaustedStop` raised
  mid-training leaves `best_skill.md` consistent with the last gated best (R4); the
  run stops within one rollout round (SC1) with best-not-last read-back (SC3) and the
  baseline gate in `cost_excluded` (SC4). (depends: T019)

## Phase 6 — End-to-end validation & docs — depends on Phases 3–5

- [ ] T021 Create `scripts/verify_budget_stop.py` (pattern:
  `scripts/verify_skillopt_sc5.py`) asserting, for a given run: SC3 — the returned
  prompt equals the best-on-validation prompt in the logged score progression; SC5 —
  the meter's per-role totals reconcile with `scripts/count_tokens.py` within 5% per
  role (mapping: meter `optimizer` ≡ audit `reflection`; the audit `other` bucket
  must be ≈0 or explained), including the optimizer-side roles previously flagged as
  untraced (NFR2). (depends: T013, T016, T020)
- [ ] T022 Run the same-budget triple comparison: GEPA, TextGrad and SkillOpt with one
  budget, one price config, same split seed and judge; confirm in the MLflow UI that
  budget, prices, per-role token/cost metrics and stop reason line up under identical
  names across the three runs (SC1, SC2, NFR1, US2, US3), and that recorded totals
  reflect optimization-phase calls only (SC4). Run `scripts/verify_budget_stop.py`
  against each run. (depends: T021)
- [ ] T023 [P] Regression-check evaluation-only paths (FR12): run the standalone eval
  CLI and confirm traces and params are identical to a pre-change run (meter inactive
  → byte-identical behavior, NFR4). Also start one trainer with a `PRICE_*` env var
  unset and confirm it refuses with the offending var named before any MLflow run is
  created (EC5, SC6, US5). (depends: T013, T016, T020)
- [ ] T024 [P] Update docs and recipes: `README.md` (budget mechanism, EUR-per-1M price
  convention, removed effort caps, TextGrad cache-off cost-profile note — R5, OQ1);
  `experiments.just` train recipes gain a `budget=` parameter and drop the removed
  knobs. (depends: T012, T014, T019)
- [ ] T025 Run the retrospective skill to review all implemented changes for code
  quality and architectural decisions (`specs/cost-budget-stopping/retrospective.md`).
  (depends: T022, T023, T024)

---

## Summary

- **Total tasks:** 25
- **Phases:** Meter core (T001–T005) → Shared plumbing (T006–T008) → TextGrad
  (T009–T013) → GEPA (T014–T016) ∥ SkillOpt (T017–T020) → E2E validation & docs
  (T021–T025).
- **Per-story coverage:** US1 (budgeted runs) T006–T007, T011–T012, T014, T018–T019;
  US2 (equal-cost comparison) T006, T022; US3 (reviewer sees spend) T002, T006, T021–
  T022; US4 (budget stop keeps best) T003, T011, T013, T015–T016, T018, T020–T021;
  US5 (price-config refusal) T001, T007–T008.
- **Parallel opportunities:** T008 alongside anything after T001; Phases 4 and 5 touch
  disjoint files and can run in parallel once Phase 2 is green (Phase 3 first is a
  de-risking preference, not a hard dependency — only T012/T013 need T011); T023/T024
  alongside T021–T022.
- **Critical path:** T001 → T002 → T003/T004 → T006 → T007 → T009 → T010 → T011 →
  T012 → T013 → T021 → T022 → T025.
