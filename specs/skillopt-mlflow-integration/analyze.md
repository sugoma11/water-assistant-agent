# Analysis Report: SkillOpt as a Prompt-Optimization Technique in MLflow

Scope: cross-artifact consistency, coverage, and ambiguity review of
[`spec.md`](./spec.md), [`plan.md`](./plan.md), [`tasks.md`](./tasks.md).
Read-only review per the `analyze` skill — the source artifacts were not modified.

## Inventory

- **Functional requirements:** FR1–FR13 (13)
- **Non-functional requirements:** NFR1–NFR4 (4)
- **User stories:** US1–US4 (4)
- **Edge cases:** EC1–EC6 (6)
- **Success criteria:** SC1, SC2, SC2a, SC3–SC6 (7)
- **Clarifications:** Q1–Q4 (4)
- **Assumptions:** A1–A6 (6)
- **Tasks:** T001–T026 (26), 6 phases

## Coverage Matrix

| Req | spec.md | plan.md | tasks.md | Status |
|---|---|---|---|---|
| FR1 | L62 | L95–98, L330 | T016, T018 | ✅ |
| FR2 | L63 | L88, L331 | T013, T015 | ✅ |
| FR3 | L64 | L146–148, L332 | T012, T025 | ✅ |
| FR4 | L70 | L98, L330 | T016, T017 | ✅ |
| FR5 | L71 | L162–165, L333 | T017 | ✅ |
| FR6 | L76 | L87–88, L149–153, L331 | T015 | ✅ |
| FR7 | L81 | L285–291, L334 | T021, T022 | ✅ |
| FR8 | L84 | L224–230, L335 | T007–T009, T017 | ✅ |
| FR9 | L85 | L74–83, L158, L337 | T012, T017 | ✅ |
| FR10 | L94 | L184–219, L333 | T012, T016, T017 | ✅ |
| FR11 | L99 | L74–83, L132, L332 | T011, T012 | ✅ |
| FR12 | L105 | L85–86, L216, L336 | T012, T015 | ⚠️ F-001 |
| FR13 | L109 | L50, L178, L210–212 | T012 | ⚠️ F-002 |
| NFR1 | L115 | L332 | T025 | ✅ |
| NFR2 | L117 | L146, L218, L338 | T012 | ✅ |
| NFR3 | L121 | L207–219, L333 | T017 | ✅ |
| NFR4 | L126 | L335 | T007–T009 | ✅ |
| US1 | L48 | L95–124 | T010–T018 | ✅ |
| US2 | L50 | L146–148 | T012, T017, T025 | ✅ |
| US3 | L53 | L162–165 | T014, T017, T025 | ✅ |
| US4 | L56 | L285–291 | T020–T023 | ✅ |
| EC1 | L196 | L285–291 | T021 | ✅ |
| EC2 | L198 | L121, L204 | T015 | ⚠️ F-004 |
| EC3 | L200 | L196–198 | T020 | ✅ |
| EC4 | L205 | L132 | T011 | ✅ |
| EC5 | L207 | L290 | T022 | ✅ |
| EC6 | L210 | L290 | T022 | ✅ |
| SC1 | L171 | L95–98 | T016 | ✅ |
| SC2 | L173 | L119, L162–165 | T014, T025 | ⚠️ F-001, F-003 |
| SC2a | L182 | L85–86 | T015 | ⚠️ F-001 |
| SC3 | L184 | L332 | T025 | ✅ |
| SC4 | L186 | L335 | T009 | ✅ |
| SC5 | L189 | L285–291 | T023 | ✅ |
| SC6 | L191 | L149–153 | T015 | ✅ |

**Every requirement, story, edge case, and success criterion is traced to at least one
task.** No orphaned requirements and no orphaned tasks. The findings below concern
consistency and under-specification within otherwise-covered items, not missing coverage.

## Findings

### 🔴 HIGH

**F-001 — Two divergent validation-evaluation paths logged under one metric name.**
- **Location:** plan.md L116, L119–120 (`_val_score(eval_fn, …)` for
  `initial/final_eval_score` **and** `history.json → eval_score` series); spec.md SC2
  L176–180, FR12 L105–108, SC2a L182.
- **Issue:** SkillOpt selects `best_skill.md` using its **own** internal validation gate,
  whose per-round scores are read back from `history.json` and logged as the `eval_score`
  progression (plan L119). But `initial_eval_score` / `final_eval_score` are independently
  recomputed by the optimizer via `eval_fn` (plan L116, L120) — a different evaluation
  path (MLflow scorer vs. adapter `rollout`). The plan asserts both are "the same metric"
  but never establishes that the adapter `rollout` (task `predict_fn` + judge, scoring
  `hard`) and `eval_fn` sample/score identically. If they diverge, the logged series and
  its endpoints sit on different axes under one name, and the prompt SkillOpt selected as
  "best on validation" (FR12/SC2a) may not be the one `final_eval_score` reflects.
- **Impact:** Undermines SC2 ("same metric names"), SC2a (keep-best fidelity), and FR12.
  This is precisely the NFR2 sampling-divergence class the plan cites TextGrad's
  retrospective for (plan L78) — but only the task-model-in-rollout side is mitigated; the
  rollout-vs-`eval_fn` consistency at the gate is not.
- **Recommendation:** Add an explicit spike check (extend T004/T005) that the
  `history.json` val score and an `eval_fn` call on the same candidate + same val split
  agree within tolerance. If they cannot be reconciled, decide one canonical source for the
  logged `eval_score` series and state that the progression and endpoints share it. Record
  the decision in T006.

### 🟡 MEDIUM

**F-002 — FR13 "edit budget" → `learning_rate` mapping is asserted but never spike-verified.**
- **Location:** plan.md L50 ("`learning_rate` (4, the edit budget = textual learning
  rate)"), L178, L210–212; tasks.md T012 L73–74; spec.md FR13 L109–111.
- **Issue:** The whole of FR13 (bounded, recorded per-round edits) rests on the claim that
  SkillOpt's `learning_rate` is the maximum number of edits applied per round. The Phase-1
  spike pins endpoints (T003), rollout (T004), and the success/history seams (T005), but
  **no task verifies that `learning_rate` actually caps edit count** rather than acting as
  some other "textual learning rate" weighting. If the semantics differ, FR13 and the
  recorded `edit_budget` are meaningless.
- **Impact:** A core recorded effort knob (FR10/NFR3) could be mislabeled; cross-technique
  effort comparison (R4) is built on it.
- **Recommendation:** Add a spike seam (or extend T005) to observe the number of edits
  applied per round under a fixed `learning_rate` and confirm the cap. Record in T006.

**F-003 — `history.json` step granularity vs. logged `step=epoch` is internally inconsistent.**
- **Location:** plan.md L35 ("persists a **per-step** `history.json`"), L119 / tasks.md
  T014 L88 ("at `step=epoch`"); plan.md L318 (R6 "depends on `history.json` granularity").
- **Issue:** The plan describes `history.json` as per-step, then logs the `eval_score`
  series at `step=epoch`. If a step is a minibatch update rather than an epoch, the mapping
  from history rows to epoch steps is unspecified (aggregate? last-per-epoch? validation
  only runs per epoch?). T014's fallback ("initial/final only if granularity insufficient")
  acknowledges the risk but the epoch-mapping rule itself is undefined.
- **Impact:** The SC2 `eval_score` progression may be mis-stepped or non-monotonic relative
  to GEPA/TextGrad, weakening side-by-side comparison (SC3).
- **Recommendation:** In T005/T006 pin what one `history.json` row represents and state the
  explicit row→step mapping rule used in T014.

**F-004 — EC2/FR12 interaction can silently discard a SkillOpt-selected improvement.**
- **Location:** plan.md L121 ("if final<=initial: return seed_template byte-for-byte"),
  L204; spec.md EC2 L198–199, FR12 L105–108.
- **Issue:** SkillOpt may produce a `best_skill.md` that beat baseline on **its** gate, yet
  the optimizer's `eval_fn`-based `final_eval_score` (F-001's second path) may compute
  `final <= initial` and return the seed byte-for-byte. That is honest per EC2, but it
  means a candidate SkillOpt judged an improvement is reported as "no improvement,"
  contradicting the spirit of FR12 keep-best. The two judgments use different paths
  (F-001), so this is not a rare tie — it is a structural possibility.
- **Impact:** Confusing/contradictory run records; reviewer (US3) sees "no improvement"
  while `history.json` shows gains.
- **Recommendation:** Resolve via F-001 (single canonical val measure). If retained,
  document that the EC2 decision is made on the `eval_fn` measure and ensure the logged
  series and the keep/return decision are consistent.

### 🟢 LOW

**F-005 — Validation split enters the optimizer out-of-band, not through the
`optimize_prompts` contract.**
- **Location:** plan.md L109–110 (`__init__(… val_set …)`), L116, L194 (`optimize(eval_fn,
  train_data, target_prompts, enable_tracking)`).
- **Issue:** `optimize()` receives only `train_data`; the val split is injected via the
  constructor and used by both `_val_score` and the adapter's `build_eval_env`. This is a
  reasonable design but undocumented as a deviation from the GEPA/TextGrad data-flow; the
  CLI's responsibility to split and pass `val_set` consistently to both seams is implicit.
- **Recommendation:** State in the plan that the CLI splits and supplies `val_set` to the
  constructor, and that the same `val_set` object backs `eval_fn` calls and the adapter
  eval env (guards F-001/EC3 consistency).

**F-006 — `batch_size = "train_size=0 / ≥len(train)"` semantics are guessed, not pinned.**
- **Location:** plan.md L214; tasks.md T012 L75.
- **Issue:** Making an epoch a full train pass (Q1) depends on a specific SkillOpt
  `batch_size`/`train_size` convention (`train_size=0` meaning "all") that is inferred from
  source reading, not confirmed in the spike. T002–T005 do not explicitly verify it.
- **Recommendation:** Confirm the full-pass semantics during T002 and record in T006.

**F-007 — R1 worst-case subprocess fallback has no corresponding task.**
- **Location:** plan.md L302–304 (subprocess `scripts/train.py` + YAML fallback if
  in-process `train()` is impossible); tasks.md (none).
- **Issue:** The largest contingency in the plan is unticketed. If the T002 spike fails the
  in-process path, there is no task to fall back to and the critical path stalls.
- **Recommendation:** Add a conditional task (or an explicit note on T002) describing the
  subprocess fallback trigger and scope.

**F-008 — T006 records spike decisions by editing `plan.md`.**
- **Location:** tasks.md T006 L43–44 ("Record spike decisions in … plan.md (or … a short
  `spike-findings.md`)").
- **Issue:** Mutating the approved plan post-hoc mixes spec history with results. The
  parenthetical `spike-findings.md` alternative is cleaner.
- **Recommendation:** Prefer a separate `spike-findings.md`; leave plan.md as the approved
  baseline.

**F-009 — Minor naming/version mismatches.**
- **Location:** invoking command referenced `specs.md`; the file is `spec.md` (plan.md L3,
  tasks.md L3 correctly say `spec.md`). plan.md L23 cites SkillOpt "Python ≥3.10" while
  L266 and T001 target Python 3.13 (compatible, not a conflict — just note the project pins
  3.13).
- **Recommendation:** No change required; noted for clarity.

## Metrics Summary

- **Requirements covered (FR+NFR):** 17/17 traced to tasks (4 carry consistency caveats:
  FR12, FR13, plus SC2/SC2a via F-001/F-003).
- **User stories covered:** 4/4.
- **Edge cases covered:** 6/6 (EC2 carries F-004 caveat).
- **Success criteria covered:** 7/7 (SC2, SC2a carry caveats).
- **Findings by severity:** 🔴 1 · 🟡 3 · 🟢 5 (9 total).
- **Coverage gaps (missing tasks):** 0. All findings are consistency / under-specification,
  not absent coverage.

## Suggested Next Actions (priority order)

1. **Resolve F-001** before Phase 3: decide a single canonical validation measure shared by
   the logged `eval_score` series and `initial/final_eval_score`, and add the
   rollout-vs-`eval_fn` agreement check to the spike (T004/T005). This is the highest-risk
   item and F-003/F-004 partly dissolve once it is fixed.
2. **Add spike verification for F-002** (`learning_rate` = per-round edit cap) and **F-006**
   (full-pass `batch_size`) to T002–T005; record both in T006.
3. **Pin the `history.json` row→`step` mapping (F-003)** and document the EC2 decision basis
   (F-004) so run records cannot show contradictory improvement signals.
4. **Document the `val_set` data-flow (F-005)** and add/annotate the subprocess fallback
   (F-007).
5. **Adopt `spike-findings.md` instead of editing plan.md (F-008)**; no action needed for
   F-009.
