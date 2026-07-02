# Implementation Plan: Cost-Budget-Based Stopping for Prompt-Optimization Runs

Spec: [`spec.md`](./spec.md)

## Technical Context

All three techniques already run through one technique-agnostic routine,
`_run_optimization(...)` in `src/experiments/text2sql/train_common.py`, which opens the
MLflow run, logs global params + `technique` + `extra_params`, brackets
`mlflow.genai.optimize_prompts(..., optimizer=<BasePromptOptimizer>)` with the
test-before/after eval phases, and logs `test_quality_{before,after}`. The pluggable
optimizers are `GepaPromptOptimizer` (MLflow's, driven from
`src/experiments/text2sql/train_gepa.py`), `TextGradPromptOptimizer`
(`src/experiments/text2sql/textgrad_optimizer.py`) and `SkillOptPromptOptimizer`
(`src/experiments/text2sql/skillopt_optimizer.py`).

**Where every optimization-phase LLM call flows today** (the metering surface, FR4):

| Role | GEPA | TextGrad | SkillOpt |
|---|---|---|---|
| task/student | `eval_fn` → `create_optimizable_predict_fn` → `harness.completion_with_retry` (litellm) | gradient-step forwards via `SchemaInjectingEngine` (raw `openai` client from `prompt_skill.make_client`); gate/val evals via `eval_fn` → litellm | adapter `rollout` → `Text2SqlEnvAdapter._task_sql` → `completion_with_retry` (litellm) |
| judge | `build_sql_judge_scorer` → `completion_with_retry` (litellm) | same shared scorer (litellm), called directly per gradient step and via `eval_fn` | same shared scorer (litellm), called inside `rollout` |
| optimizer/teacher | GEPA reflection: `litellm.completion` **inside the gepa library** (OPENAI_API_BASE fallback, no project wrapper) | backward/proposal via `ReflectionEngine` (raw `openai` client from `make_client`) | reflection/edit inside `ReflACTTrainer` on SkillOpt's own model layer (`skillopt.model`) |

So there are exactly **three call-path families**: the project litellm path
(`completion_with_retry` — every task and judge call in all techniques, plus nothing
else), project-owned raw-OpenAI clients (TextGrad's two engines, built by us), and two
library-internal paths (GEPA reflection via litellm; SkillOpt reflection/edit via
SkillOpt's model layer).

**Library seams, confirmed from installed source** (not assumptions):

- **GEPA stop conditions (A2).** `gepa.optimize(..., stop_callbacks=StopperProtocol |
  Sequence[StopperProtocol])` exists (`gepa/api.py`); a stopper is any callable
  `(gepa_state) -> bool`, checked by the engine at its per-iteration boundary
  (`gepa/core/engine.py:672`). Multiple stoppers combine with "any". MLflow's
  `GepaPromptOptimizer` merges `self.gepa_kwargs | {..., "max_metric_calls": ...}`
  (`mlflow/genai/optimize/optimizers/gepa_optimizer.py:349`), so `stop_callbacks`
  passes through `gepa_kwargs` untouched while `max_metric_calls` is always set by
  MLflow — it becomes a huge sentinel and the spend stopper governs.
- **SkillOpt token observation (A4).** `skillopt.model.azure_openai` keeps a
  module-level, thread-safe `tracker = TokenTracker()` that records
  prompt/completion tokens for **every** optimizer-side chat call, exposed via
  `skillopt.model.router.get_token_summary()` / `reset_token_tracker()`. The trainer
  imports `reset_token_tracker` but never calls it, so the tracker accumulates
  monotonically → optimizer-role spend is read as **deltas at checkpoints**, no fork,
  no monkeypatch. Our adapter's rollout never touches SkillOpt's model layer, so the
  entire tracker content is optimizer-role by construction.
- **SkillOpt keep-best survives a mid-run stop.** `ReflACTTrainer` persists
  `best_skill.md` and `history.json` incrementally at each step
  (`skillopt/engine/trainer.py:1455-1459`), so aborting between steps leaves the
  best-validated skill readable on disk (FR8).
- **TextGrad engines cache to disk.** `ChatOpenAI` (base of `ChatExternalClient`) is a
  `CachedEngine` with a persistent per-model disk cache; a cache hit skips the API
  call entirely. Under a money budget this makes repeat runs artificially free and
  breaks equal-cost comparability — the cache must be disabled for metered runs
  (NFR1/NFR2 consequence, decision D3 below).
- **Usage availability (A1).** The litellm path already extracts `resp.usage`
  (`harness.extract_usage`); TextGrad's clients and SkillOpt's tracker surface the
  same OpenAI-style `usage` object.
- **MLflow eval threading (verified 2026-07-02, during T013).** `mlflow.genai.evaluate`
  executes every real dataset row on `MlflowGenAIEvalPredict_N` worker threads that do
  **not** inherit the caller's `ContextVar`s — only the first-sample trace-validation
  call runs on the main thread (offline probe: 6/6 worker rows saw
  `_active_meter=None, _current_role=None, _excluded=False`). Any metering
  gate/attribution/exclusion carried by a contextvar set around `evaluate` therefore
  misses every `eval_fn`-flowing call: GEPA's candidate evaluations (most of its
  spend), TextGrad's per-step gate and baseline/final passes. Thread identity — not
  concurrency — is what breaks inheritance, so pinning workers to 1 would not help.

Post-hoc trace accounting (`scripts/count_tokens.py`) stays as the independent audit
axis for SC5/NFR2; nothing in this feature replaces it.

## Key architectural decisions

**D1 — one meter object, three attachment seams.** A new
`src/experiments/text2sql/cost_meter.py` owns all pricing/stopping state
(`CostMeter`). It attaches to the three call-path families where they already
converge, rather than per-technique ad-hoc counting:

1. **litellm path**: `completion_with_retry` records to the active meter after each
   call. **Revised 2026-07-03 (eval-thread finding above):** the master gate is
   process-global meter state set by `meter.active()` — not a contextvar — and the
   role is **construction-bound**: the call-site builders inject
   `cost_meter_role="task"` (`_make_predict_fn`, `create_predict_fn`,
   `create_optimizable_predict_fn`, `Text2SqlEnvAdapter._task_sql`) or `"judge"`
   (`build_sql_judge_scorer`) into their completion kwargs at build time, so the tag
   is present regardless of which thread executes the call. When no meter is active —
   the standalone eval CLI, the test-before/after phases — this is a no-op, so
   evaluation paths are byte-identical to today (FR12, NFR4).
2. **project-owned OpenAI clients** (TextGrad): `make_client` grows a metered variant
   that wraps `chat.completions.create` and records usage under a role bound at
   construction (`task` for `SchemaInjectingEngine`, `optimizer` for
   `ReflectionEngine`).
3. **library-internal paths**: GEPA reflection via a litellm success-callback that
   records only calls *not* tagged by seam 1 (tag = `metadata={"cost_meter_role":
   ...}` injected in `build_completion_kwargs`), attributing them to `optimizer`;
   SkillOpt reflection/edit via `get_token_summary()` deltas read at every adapter
   checkpoint.

**D2 — stop at each technique's existing natural checkpoint (FR7), never mid-call:**
GEPA: a `BudgetStopper(meter)` (StopperProtocol) via `gepa_kwargs["stop_callbacks"]`.
TextGrad: the existing per-gradient-step budget check, now testing money instead of
metric calls. SkillOpt: `meter` checked at the start of each non-excluded adapter
`rollout`; exhaustion raises a `BudgetExhaustedStop` exception that
`SkillOptPromptOptimizer.optimize` catches, then reads the incrementally persisted
`best_skill.md`/`history.json` (or falls back to the seed, EC1). Keep-best is in every
case the technique's existing mechanism (FR8): GEPA's engine returns its best
candidate, TextGrad's per-step gate/revert already holds `best_prompt`, SkillOpt's
gate wrote `best_skill.md`.

**D3 — exclusions are recorded, not invisible (FR5, SC4).** The meter has an
`excluded` bucket: calls made under `meter.excluded()` are still counted (visible as
`cost_excluded` for audit) but never charge the budget. TextGrad wraps its two
reserved full-val passes (baseline + final `_val_score`); SkillOpt's adapter wraps the
baseline val-gate rollout (the first eval-split rollout); GEPA's seed full-val pass
happens inside the engine before the first stopper invocation, so `BudgetStopper`
snapshots billable spend on its first call and moves that snapshot to the excluded
bucket (primary mechanism; ordering verified in Phase 4 with a documented fallback).
The test-before/after phases sit entirely outside `meter.active()` and are never
metered (EC3). TextGrad's engine cache is disabled so every metered run pays real
tokens.

**Revised 2026-07-03:** the excluded flag is process-global meter state rather than a
contextvar, for the same eval-worker-thread reason as D1's gate. This is safe because
the reserved passes are sequential — nothing else is in flight while a bracketing
full-val pass runs. Exclusion semantics re-confirmed with the experimenter: *only* the
bracketing passes (test-before/after, baseline/final full-val) are excluded; every
optimization-internal call is billable, **including full evaluations the optimizer
runs as part of its search** (GEPA's candidate evals, TextGrad's per-step gate).

**D4 — prices and budget follow the repo's existing configuration split.** Prices are
env-family config like the `LLM_*`/`JUDGE_*`/`OPTIMIZER_*` sampling params:
`PRICE_{TASK,JUDGE,OPTIMIZER}_{INPUT,OUTPUT}` in **EUR per million tokens** (A5
decision: EUR, per-1M convention). The budget is a required per-run CLI option
`--budget` (EUR), like the other human-set, no-default effort knobs. All are validated
before the run starts (EC5) and logged as identically named params on every technique
(FR3, FR10).

## Architecture / Components

```
text2sql-train-{gepa,textgrad,skillopt}                      [all three CLIs change]
    read_price_config()  -> PriceConfig   (EC5: refuse absent/negative/non-numeric)
    meter = CostMeter(budget, prices)
    optimizer = <technique optimizer>(..., cost_meter=meter)   # effort caps removed
    _run_optimization(..., cost_meter=meter)

_run_optimization (train_common.py)                          [extended, shared]
    log budget + price params (identical names, FR10)
    test-before eval phase                                   # meter inactive (FR5/EC3)
    with meter.active():                                     # optimization phase only
        try:     result = optimize_prompts(..., optimizer=...)
        finally: log spend metrics + optimization_stop_reason  (FR9; also on FAILED runs)
    test-after eval phase                                    # meter inactive

cost_meter.py  [new]
    PriceConfig.from_env()          # PRICE_*_{INPUT,OUTPUT}, EUR / 1M tokens
    CostMeter                       # thread-safe
        record(role, in_tok, out_tok)   / record_unmetered(role)     (EC2)
        active() ctx   -> gates all metering (process-global state, NOT a contextvar
                          -- must survive mlflow's eval worker threads)
        excluded() ctx -> counts to the excluded bucket, not the budget (FR5;
                          process-global state, same reason)
        (role attribution is construction-bound at the call-site builders; the
         role(name) contextvar is retired -- eval workers don't inherit it)
        exhausted() -> billable_cost >= budget ; stop_reason ; spend_summary()
        check_unmetered() -> raise if unmetered calls make spend meaningless (EC2)
    BudgetStopper(meter)            # gepa StopperProtocol; 1st-call baseline snapshot
    BudgetExhaustedStop(Exception)  # SkillOpt checkpoint signal
    litellm_reflection_callback(meter)  # untagged litellm calls -> optimizer role

harness.py: completion_with_retry records to the active meter, reading the role from
            the construction-bound cost_meter_role in its own kwargs (usage missing ->
            record_unmetered + loud warning); build_completion_kwargs takes the role as
            an explicit build-time argument -- the same tag doubles as the reflection-
            callback dedupe marker.
prompt_skill.py: make_client(endpoint, meter=None, role=None) -> metered client wrapper.

GEPA:     GepaPromptOptimizer(max_metric_calls=SENTINEL,
              gepa_kwargs={..., "stop_callbacks": [BudgetStopper(meter)]})
TextGrad: loop = for epoch in itertools.count(1): ... per-step `meter.exhausted()`
          (replaces `spent >= metric_call_budget`); baseline/final _val_score in
          meter.excluded(); engine cache disabled; engines built with metered clients.
SkillOpt: cfg num_epochs=SENTINEL; adapter checks meter at each train-rollout start
          (raise BudgetExhaustedStop); baseline gate rollout in meter.excluded();
          optimizer tokens = get_token_summary() deltas at every checkpoint +
          reset_token_tracker() at optimize() start; budget-stop read-back path.
```

## Data Model / Entities

- **Money budget** — `--budget` (float, EUR), required on all three train CLIs, no
  default; logged as param `budget`. Identical semantics everywhere (FR1).
- **Price configuration** — six env vars `PRICE_TASK_INPUT`, `PRICE_TASK_OUTPUT`,
  `PRICE_JUDGE_INPUT`, `PRICE_JUDGE_OUTPUT`, `PRICE_OPTIMIZER_INPUT`,
  `PRICE_OPTIMIZER_OUTPUT` (EUR per 1M tokens). All three techniques use all three
  roles, so all six are always required (FR3, EC5). Logged as params under the same
  names, lowercased (`price_task_input`, ...).
- **Spend record (FR9, FR10)** — metrics logged by `_run_optimization` in a
  `finally`, so failed runs still carry their spend (EC4/EC6):
  `tokens_{task,judge,optimizer}_{input,output}`, `cost_{task,judge,optimizer}`,
  `cost_total`, `cost_excluded` (reserved passes, audit/SC4), `unmetered_calls`
  (EC2). Param `optimization_stop_reason`: `budget_exhausted` | `completed` |
  `failed` — the last set by `_run_optimization` when the optimization phase
  raises, so a FAILED run carries its true reason (FR9, EC4; analyze F-002).
- **Removed-as-stops params (FR2, C3)** — GEPA `max_metric_calls`/
  `total_metric_calls`; TextGrad `epochs`, `metric_call_budget`,
  `max_steps_per_epoch`; SkillOpt `epochs`. No longer CLI options, no longer logged.
  Structural knobs stay configurable and logged: TextGrad `batch_size`,
  `val_gate_size`; SkillOpt `edit_budget` (a per-round textual learning rate — it
  bounds edits per round and never terminates a run, so it survives as structure),
  `minibatch_size`, `reflect_on_success`, `reasoning_effort`; GEPA reflection
  settings.
- **Best prompt** — unchanged (FR8): each technique's existing keep-best object/file
  is what a budget stop returns; the honest no-improvement seed-return (EC2 of the
  earlier specs) is preserved on top.

## Interfaces / Contracts

### CLI changes (all three trainers)

| Option | GEPA | TextGrad | SkillOpt |
|---|---|---|---|
| `--budget` (EUR, required, > 0) | new | new | new |
| removed | — (`MAX_METRIC_CALLS` const gone) | `--epochs`, `--metric-call-budget`, `--max-steps-per-epoch` | `--epochs` |
| kept (structural) | teacher role opts | `--batch-size`, `--val-gate-size` | `--edit-budget`, `--minibatch-size`, `--reflect-on-success`, `--reasoning-effort` |

Prices come from the environment (D4), validated by `read_price_config()` before
`setup_mlflow` so a misconfigured run refuses to start with the offending env var
named (EC5, SC6). `experiments.just` train recipes gain a `budget=` parameter and drop
the removed knobs.

### `CostMeter` contract

- `record(role, input_tokens, output_tokens)`: accumulates tokens and cost
  (`tokens/1e6 * price`) into the billable or excluded bucket per the `excluded()`
  contextvar; thread-safe (litellm callback may fire off-thread).
- `exhausted()`: `billable_cost >= budget`; setting `stop_reason="budget_exhausted"`
  the first time it fires. Techniques that return without exhaustion leave
  `stop_reason="completed"` (EC4); `_run_optimization` records `"failed"` instead
  when the optimization phase raises (F-002).
- `record_unmetered(role)`: increments the unmetered counter and logs a prominent
  warning (EC2). `check_unmetered()`, called at each stop checkpoint: raise when
  unmetered calls exceed `max(5, 5% of all calls metered while active(), excluded
  bucket included)` — frequent enough to make the spend meaningless fails the run
  rather than reporting a misleading cost.
- `spend_summary()`: the FR9 metric dict logged by `_run_optimization`.
- Contexts: `active()` (master gate — everything outside is unmetered by design) and
  `excluded()` (FR5 bucket) keep their context-manager API but are backed by
  **process-global meter state, not contextvars** (revised 2026-07-03):
  `mlflow.genai.evaluate` runs every row on `MlflowGenAIEvalPredict_N` worker threads
  that don't inherit contextvars, and thread identity — not concurrency — is what
  breaks inheritance. Global state is safe because exactly one meter is active per
  process and the excluded bracketing passes are sequential. Role attribution is
  **construction-bound** (an explicit role at predict-fn/judge-scorer build time,
  carried in the completion kwargs); the `role(name)` contextvar is retired. The
  GEPA-reflection callback needs none of this (it captures the meter and always
  records role=`optimizer`, never excluded).

### Per-technique stop/exclusion wiring

- **GEPA** (`train_gepa.py` only; no optimizer subclass needed):
  `max_metric_calls=10**9` sentinel; `gepa_kwargs["stop_callbacks"] =
  [BudgetStopper(meter)]`. `BudgetStopper.__call__` ignores the GEPA state; on its
  first invocation it snapshots billable spend as the seed-pass cost and reclassifies
  it excluded (D3); afterwards it returns `meter.exhausted()` and runs
  `meter.check_unmetered()`. The litellm reflection callback is registered when the
  meter activates and deregistered after (scoped to the GEPA run; TextGrad/SkillOpt
  never produce untagged litellm calls, so registering it uniformly is harmless but
  it is GEPA that needs it).
- **TextGrad** (`textgrad_optimizer.py`): `epochs`/`metric_call_budget`/
  `max_steps_per_epoch` params and the `spent` bookkeeping are deleted; the loop
  becomes `for epoch in itertools.count(1)` with the existing per-batch checkpoint
  now `if self.cost_meter.exhausted(): break` (+ `check_unmetered()`). The two
  full-val `_val_score` calls (baseline, final) run inside `meter.excluded()`; the
  per-step gate evals stay billable (FR4 explicitly includes gate evaluations).
  `_DefaultGenKwargsEngine` disables the inherited disk cache; both engines receive
  metered clients (`make_client(endpoint, meter, role)`).
- **SkillOpt** (`skillopt_optimizer.py`): cfg `num_epochs=10**6` sentinel (safe with
  the constant LR scheduler already pinned). `Text2SqlEnvAdapter` gains the meter:
  at the start of every rollout it folds in the optimizer-token delta
  (`get_token_summary()["_total"]` minus last snapshot), runs `check_unmetered()`,
  and — for non-excluded rollouts — raises `BudgetExhaustedStop` when exhausted. The
  first eval-split rollout (`env_manager is self._val`, first occurrence) is the
  baseline gate and runs under `meter.excluded()`. `optimize()` calls
  `reset_token_tracker()` up front, catches `BudgetExhaustedStop` around
  `trainer.train()`, folds the final tracker delta, and takes the budget-stop
  read-back path: `best_skill.md` + `history.json` if present (initial score from the
  baseline history row, final from the best row — the same selection axis as today);
  if no step completed (EC1), return the seed template byte-for-byte with
  `initial == final` from the baseline gate score. The existing EC6 "artifacts
  unreadable" failure applies only to natural completion, where `summary` is
  available as today.

### Stop-reason semantics (FR9, EC4)

With the sentinel caps none of the three techniques has a remaining self-stop, so
`budget_exhausted` is the expected reason; `completed` covers a technique returning
early (defensive); an optimizer error keeps today's semantics — the exception
propagates, `mlflow.start_run` marks the run FAILED, and the `finally` in
`_run_optimization` has already logged the spend accumulated up to the failure with
`optimization_stop_reason="failed"`, so the FAILED run carries its true reason
(EC6, FR9).

## Phases / Dependencies

1. **Meter core (no LLM).** `cost_meter.py`: `PriceConfig.from_env` (EC5 messages),
   `CostMeter` (buckets, contexts, thresholds, thread-safety), `BudgetStopper`,
   `BudgetExhaustedStop`, the litellm callback. `harness.py`: record inside
   `completion_with_retry`, role tag in `build_completion_kwargs`, role contexts at
   the three litellm call sites. No dedicated unit tests (experimenter decision,
   analyze F-001): verification rides the per-technique tiny-budget smoke runs and
   the T023 eval-run diff. *Blocks everything.*
2. **Shared plumbing.** `_run_optimization(..., cost_meter)`: budget/price params up
   front, `meter.active()` around `optimize_prompts`, `finally`-logged spend +
   `optimization_stop_reason`. `read_price_config()` + `--budget` in all three CLIs
   (still passing the old caps to the optimizers until phases 3–5 land per technique,
   so each phase stays independently runnable). `.example.env`: the six `PRICE_*`
   entries with a "set your endpoint's real prices" comment.
3. **TextGrad** (fully project-owned — proves the meter end-to-end first): metered
   clients, cache off, loop rewrite, exclusions, CLI/knob removal, param cleanup.
   Verify with a tiny-budget run: stops within one step of exhaustion (SC1), best-not-
   last prompt (SC3), spend metrics (SC2), `cost_excluded` covers exactly the two
   full-val passes (SC4).
3.5. **Thread-safe metering seam rework (added 2026-07-03).** The Phase-3 tiny-budget
   run proved the stop/keep-best/spend-logging path end-to-end but exposed the
   eval-thread contextvar loss: `cost_excluded` was 0 and every `eval_fn`-flowing
   call went unmetered. Rework `cost_meter.py` (process-global gate/excluded state,
   retire the role contextvar) and `harness.py` + call sites (construction-bound
   roles), then re-verify TextGrad (T013: SC3, SC4, EC1). *Blocks Phases 4–5* — GEPA's
   spend is mostly `eval_fn` calls, so without this rework its meter would see almost
   nothing but the reflection callback.
4. **GEPA**: sentinel + `BudgetStopper` via `gepa_kwargs`, reflection callback,
   seed-pass snapshot. Runtime verifications: (a) first stopper invocation happens
   *after* the seed full-val eval (engine-loop ordering) — fallback if not: charge the
   seed pass and record it as a named param-documented deviation, or derive the
   snapshot at the second invocation; (b) callback threading — counters may lag one
   in-flight logging thread, bounded and harmless at iteration-boundary checks; (c)
   the huge `max_metric_calls` only affects GEPA's progress-bar denominator
   (cosmetic).
5. **SkillOpt**: tracker deltas, checkpoint stop + budget-stop read-back (incl. EC1
   seed path), baseline-gate exclusion, sentinel epochs, CLI cleanup. Runtime
   verifications: baseline gate is the first eval-split rollout and precedes the first
   train rollout; `history.json` baseline row exists after the gate; a
   `BudgetExhaustedStop` raised mid-training leaves `best_skill.md` consistent with
   the last gated best.
6. **End-to-end validation + docs.** Same-budget triple run (SC1/SC2/NFR1: identical
   param/metric names side by side); `scripts/verify_budget_stop.py` (pattern:
   `verify_skillopt_sc5.py`) asserting SC3 (returned prompt == best-on-val in the
   logged progression) and SC5 (meter totals vs `scripts/count_tokens.py` per role
   within tolerance — the optimizer roles are now traced via the existing autolog
   setup, so the audit no longer has untraced roles). Update `README.md`,
   `experiments.just` recipes (add `budget=`, drop removed knobs).

## Risks & Open Questions

- **R1: litellm success-callback delivery** (thread/async timing, metadata
  visibility). Bounded impact — it only meters GEPA reflection, checked at iteration
  boundaries. Fallback: a litellm `CustomLogger` with its sync hook, or (worst case)
  wrapping GEPA's reflection through a proxied `OPENAI_API_BASE`; flagged so it is a
  conscious choice.
- **R2: GEPA seed-pass exclusion ordering** (Phase 4a). Both fallbacks keep spend
  honest; the only variance is whether the seed pass charges the budget, which is
  identical across GEPA runs and therefore does not distort GEPA-internal
  comparisons.
- **R3: checkpoint granularity differs per technique** — GEPA's iteration (minibatch
  reflection + candidate full-val pass) overshoots more than TextGrad's gradient step
  or SkillOpt's rollout round. FR7/C4 tolerate this explicitly; actual spend is
  recorded, and the overshoot is visible as `cost_total - budget`.
- **R4: SkillOpt budget-stop read-back edge cases** (no baseline history row, stop
  between update and gate). Mitigated by the Phase-5 verifications and the EC1 seed
  fallback; a stop before the gate discards the ungated in-flight update by design
  (best-on-val is whatever last passed the gate — FR8's "best so far").
- **R5: disabling TextGrad's cache changes its cost profile vs. past runs** (past
  runs partially rode the disk cache). Intended: a money budget requires every call
  to be a real, paid call. Noted in README; past runs remain comparable via the
  post-hoc trace accounting.
- **R6 (materialized, superseded by the Phase-3.5 rework): contextvar attribution
  does not survive even today's threading.** `mlflow.genai.evaluate` executes rows on
  worker threads with fresh contexts, so the original mitigation ("workers pinned to
  1") was moot — thread identity, not concurrency, breaks contextvar inheritance. The
  rework makes the gate/exclusion process-global and the role construction-bound. The
  meter still refuses to guess: a call whose role cannot be determined counts toward
  the same unmetered counter and threshold as missing-usage calls (F-007).
- **OQ1 (decision taken): currency/units** — EUR, prices per 1M tokens (A5). The
  param names carry no unit suffix; units are documented in `.example.env` and README.
- **OQ2 (decision taken): `--max-steps-per-epoch` removed** — it was an effort cap on
  pass length, not a structural knob in FR2's keep-list, and unbounded epochs make it
  redundant.
- **OQ3 (decision taken): unmetered-call failure threshold** — `max(5, 5%)` at
  checkpoint granularity (EC2's "frequent enough to make the spend meaningless").
  Constant in `cost_meter.py`, revisitable without spec impact.

## Testing Strategy

- **Per-technique tiny-budget smoke runs (Phases 3–5):** EC1 on TextGrad only
  (budget smaller than one step → seed result, `budget_exhausted`, valid run;
  GEPA/SkillOpt rely on A7 budget sizing), SC1 (stop within one
  checkpoint), SC3 (best-not-last), SC4 (`cost_excluded` == reserved passes only).
  No dedicated unit tests (experimenter decision, analyze F-001).
- **Cross-technique (Phase 6):** same budget/prices/seed on all three (NFR1 name
  parity in the MLflow UI), `scripts/verify_budget_stop.py` reconciling meter totals
  against `count_tokens.py` per role (SC5, NFR2).
- **Regression:** the standalone eval CLI and test phases produce identical traces
  and params with the feature merged (FR12) — asserted by one eval run diffed
  against a pre-change run (the meter's inactive no-op path, FR12/NFR4).

## Traceability

| Requirement | Where addressed |
|---|---|
| FR1, FR10, NFR1 | `--budget` + `PRICE_*` identically named on all three CLIs; params/metrics logged by shared `_run_optimization` (D4) |
| FR2, C3 | caps removed as stops (sentinels + CLI removals); structural knobs kept & logged (Data Model) |
| FR3, EC5, SC6 | `read_price_config()` validates all six prices pre-run with named-env-var errors |
| FR4 | three metering seams cover every optimization-phase call path (D1, call-flow table) |
| FR5, EC3, SC4 | `meter.active()` scope + `excluded()` bucket for reserved passes (D3); `cost_excluded` metric |
| FR6 | live in-process meter is the stop input; traces remain audit-only (SC5) |
| FR7, C4 | per-technique natural checkpoints (D2); overshoot visible as recorded actual spend |
| FR8, SC3, EC1 | keep-best unchanged per technique; SkillOpt budget-stop read-back + seed fallback |
| FR9, EC4, EC6 | `finally`-logged spend + `optimization_stop_reason`; failures keep FAILED semantics with spend recorded |
| FR11, EC2 | `record_unmetered` + warning + `unmetered_calls` metric + `check_unmetered` failure threshold (OQ3) |
| FR12, NFR4 | meter inactive outside optimization → eval paths byte-identical; per-technique metering isolated to its own modules |
| NFR2, SC5 | Phase-6 reconciliation vs `count_tokens.py`; SkillOpt optimizer role observable via its native tracker |
| NFR3 | budget + prices are recorded params; spend-dependent stop variance documented as inherent |
| A2, A3, A4 | confirmed seams: gepa `stop_callbacks`, project-owned TextGrad clients, SkillOpt `TokenTracker` (Technical Context) |

## Revision log

- **2026-07-03** — Phase-3 verification (T013, tiny-budget TextGrad run on kisski)
  proved SC1/SC2/FR8 end-to-end (stopped within one gradient step of exhaustion,
  spend + `budget_exhausted` logged, best prompt registered) but exposed that
  `mlflow.genai.evaluate` executes rows on worker threads that do not inherit
  ContextVars, so the contextvar-based gate/role/excluded seams missed every
  `eval_fn`-flowing call (`cost_excluded` = 0; GEPA would be worse — its search *is*
  `eval_fn` calls). D1/D3/CostMeter-contract revised to process-global gate/excluded
  state + construction-bound roles; Phase 3.5 (tasks T026–T027) inserted; T013
  re-verification (SC3/SC4/EC1) deferred until it lands. Exclusion semantics
  re-confirmed with the experimenter: only the bracketing passes are excluded; all
  optimization-internal calls are billable, including optimizer-run full evals.

## Generated Artifacts

- `specs/cost-budget-stopping/plan.md` (this file)
- To be created during implementation:
  `src/experiments/text2sql/cost_meter.py`,
  `scripts/verify_budget_stop.py`; edits to `harness.py`, `prompt_skill.py`,
  `train_common.py`, `train_gepa.py`, `train_textgrad.py`, `textgrad_optimizer.py`,
  `train_skillopt.py`, `skillopt_optimizer.py`, `.example.env`, `experiments.just`,
  `README.md`.

Ready for task breakdown.
