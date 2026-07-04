# Retrospective: Cost-Budget-Based Stopping for Prompt-Optimization Runs

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Tasks: [`tasks.md`](./tasks.md)

Reviewed 2026-07-04, after T021–T024 landed and the T022 same-budget triple run
verified all three techniques (GEPA `0cbca2b7`, TextGrad `fb34ae87`, SkillOpt
`ac802bb2`) `SC3+SC5 VERIFIED`, `budget_exhausted`. Feature range `93d7941..HEAD`
(T001–T022; the one unrelated `d3c2c88` package-rename chore is excluded from this
review).

## Summary

**Clean, and unusually well-validated.** The plan's central design — one `CostMeter`
attached at the three call-path families where they already converge (D1) — is
realized faithfully, and the meter's per-role totals reconcile **token-exactly** with
the independent trace audit (`scripts/count_tokens.py`) on every one of the three
techniques (SC5), which is the strongest possible evidence the metering is neither
double-counting nor missing calls. The mid-flight architectural correction (Phase 3.5:
process-global gate/exclusion + construction-bound roles, forced by mlflow's eval
worker threads dropping ContextVars) was the right call and is documented thoroughly
in both code and plan. The budget-stop / keep-best / honest-no-improvement behaviour
is consistent across the three techniques. Findings below are refinements, not
correctness problems — the most actionable is a small DRY consolidation of the
usage→record block that now appears in three seams.

## Code Quality Findings

### 🟡 R-001 — usage→record plumbing duplicated across the three metering seams
The identical "read `usage`; if `None` → `record_unmetered(role)` + warn; else
`record(role, prompt_tokens, completion_tokens)`" block appears in three modules:
- `harness._record_to_active_meter` (`harness.py`, litellm seam),
- `prompt_skill._install_cost_meter.metered_create` (`prompt_skill.py:_install_cost_meter`, TextGrad raw-openai seam),
- `cost_meter.litellm_reflection_callback._callback` (`cost_meter.py:353`, GEPA reflection seam).

**Impact:** three copies of the missing-usage / attribution contract drift
independently — e.g. a future change to how unmetered calls are logged has to be made
in three places, and the reflection callback already diverges subtly (it hard-codes
`role="optimizer"` and checks the dedupe tag). Low risk today (all three are correct
and covered by SC5), but it is real duplication of a contract the spec calls out (EC2,
FR11, R6).

**Recommended fix:** extract a single helper on `CostMeter`, e.g.
`record_completion(role, usage_obj)` that does the `usage is None` branch + warning
once, and have all three seams call it. The reflection callback keeps only its
tag-dedupe/`active_meter()` guard; `prompt_skill` and `harness` keep only their
role-resolution. ~15 lines net removed, one contract.

### 🟢 R-002 — "phantom improvement" final-pass skip duplicated in TextGrad and SkillOpt
Both optimizers carry the same guard (added in the T013 fix): when the best prompt is
still the seed, skip the final excluded full-val pass and pin `final_eval_score =
initial_eval_score`, so judge/sampling noise on a re-scored identical prompt can't
fabricate an improvement (`textgrad_optimizer.py:694`, `skillopt_optimizer.py:699`).
The surrounding control flow differs enough that a shared helper would be awkward, but
the *policy* ("only a strict gain over the full-val baseline counts; identical prompt ⇒
no re-score") is a cross-technique invariant worth naming in one place (a short helper
or a shared comment referencing a single source of truth) so a third technique added
later inherits it deliberately rather than by copy.

### 🟢 R-003 — mixed `Optional[X]` and `X | None` typing style
`cost_meter.py` uses `Optional["CostMeter"]` / `Optional[str]` (4 sites) while the
newer sibling `prompt_skill.py` uses `CostMeter | None`. Cosmetic; pick the `| None`
form for consistency with the rest of the touched modules.

### 🟢 R-004 — no dedicated regression test for the meter core
By explicit experimenter decision (analyze F-001) there are no unit tests; verification
rides the per-technique tiny-budget smoke runs, the offline thread/seam probes, and
`scripts/verify_budget_stop.py`. That is a defensible choice given the LLM-in-the-loop
surface, but the meter's *pure* logic — bucket accounting, the `exhausted()` latch,
`exclude_accumulated_billable()`, the `max(5, 5%)` `check_unmetered` threshold — is
fully deterministic and could carry a handful of fast offline asserts with no endpoint.
Not a defect; a low-cost hardening opportunity if the meter is revisited.

## Architectural Observations

- **Process-global meter state (D1 revised).** `cost_meter._active`/`_excluded` are
  module-level, lock-guarded globals — normally a smell, here the correct answer:
  mlflow's `MlflowGenAIEvalPredict_N` workers don't inherit ContextVars, so a
  contextvar gate would miss most of GEPA's spend. The `active()` context enforces
  exactly one meter per process (raises otherwise), which contains the global-state
  risk. Well-reasoned and heavily documented; no change recommended, only awareness that
  the module is now single-active-meter by contract (fine for a CLI, would need rework
  if ever run in-process concurrently).

- **Construction-bound roles beat the retired contextvar.** Threading the role through
  the completion kwargs at builder time (`build_completion_kwargs(..., role=)`) makes
  attribution independent of which thread executes the call — the tag doubles as the
  GEPA-reflection dedupe marker, which is an elegant reuse.

- **`textgrad_optimizer.py` is large (~730 lines).** It carries the engine subclasses
  (schema injection, cache-off, retry, empty-completion handling) *and* the optimize
  loop. It is cohesive around TextGrad and the empty-completion/retry hardening is
  genuinely needed for the thinking model, but it is the one module where a future
  reader pays for the density. Optional: split the engine classes into a
  `textgrad_engines.py`. Not urgent.

- **Verification tooling is a feature deliverable, not scaffolding.**
  `scripts/verify_budget_stop.py` reconciling the live meter against the trace audit
  (and the SC5 finding that meter `optimizer` ≡ audit `reflection + other`, token-exact)
  is what makes the "honest cost comparison" goal auditable going forward. Good
  investment.

## Metrics

- **Files changed:** 17 (source + config + docs), plus the spec dir.
- **New files:** `src/experiments/text2sql/cost_meter.py` (370 lines),
  `scripts/verify_budget_stop.py` (286 lines).
- **Lines:** +1,765 / −255 across the feature scope.
- **Findings:** 🔴 0 · 🟡 1 (R-001) · 🟢 3 (R-002, R-003, R-004).
- **Verification:** 3/3 techniques `SC3+SC5 VERIFIED`; meter vs trace-audit reconciles
  token-exact per role (task within the named `convert_predict_fn` probe delta,
  ≤1.6%); `unmetered_calls=0` on all three; eval-path meter no-op proven offline
  (FR12/NFR4); price refusal proven (EC5/SC6).
