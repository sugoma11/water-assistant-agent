# Analysis Report: Cost-Budget-Based Stopping

Artifacts analyzed: [`spec.md`](./spec.md) · [`plan.md`](./plan.md) · [`tasks.md`](./tasks.md)
Date: 2026-07-02

Claims that were verifiable against the codebase were checked, not assumed. All
load-bearing seams named by the plan exist as described: `_make_predict_fn`
(`src/experiments/text2sql/harness.py:409`), `build_sql_judge_scorer`
(`harness.py:517`), `completion_with_retry` call sites are exactly the three the
plan lists (`harness.py:418`, `harness.py:626`, `skillopt_optimizer.py:219`),
`make_client` (`prompt_skill.py:31`), gepa `stop_callbacks`
(`.venv/.../gepa/api.py:68`), SkillOpt `get_token_summary`/`reset_token_tracker`
(`.venv/.../skillopt/model/router.py:173-178`). The plan's Phase-6 claim that
optimizer-side roles are traceable is substantiated: `mlflow.openai.autolog()` is
already enabled for SkillOpt runs (`train_skillopt.py:171`) and SkillOpt's backend
uses the `openai` SDK; GEPA reflection rides litellm autolog
(`scripts/count_tokens.py:34-38`).

## Coverage Matrix

| Requirement | spec.md | plan.md | tasks.md (impl) | tasks.md (verify) | Status |
|---|---|---|---|---|---|
| FR1 budget param | :79 | D4, :167-168 | T007 | T013/T016/T020/T022 | ✅ |
| FR2 sole stop, caps removed | :83-89 | Data Model :179-186 | T011, T012, T014, T018, T019 | T013/T016/T020 | ⚠️ F-003 (edit-budget wording) |
| FR3 per-role prices, refuse | :90-93 | D4, :169-173 | T001, T007, T008 | — (unit tests missing) | ⚠️ F-001, F-005 |
| FR4 meter every opt call | :94-104 | D1, call-flow table :19-29 | T004, T005, T009, T010, T014, T017 | T013, T016, T020 | ✅ |
| FR5 exclusions | :105-108 | D3 | T006, T011, T015, T017 | T013/T016/T020 (SC4) | ✅ |
| FR6 live metering | :109-111 | D1 (in-process meter) | T002 | T021 (SC5) | ✅ |
| FR7 checkpoint stop | :112-115 | D2 | T003, T011, T014, T017 | T013/T016/T020 (SC1) | ✅ |
| FR8 keep-best | :116-119 | D2, :186-189 | T011, T018 | T013/T016/T020/T021 (SC3) | ✅ |
| FR9 record spend + reason | :120-123 | :174-179, :260-266 | T002, T006 | T022 | ⚠️ F-002 (error path) |
| FR10 identical names | :124-126 | D4 | T006 | T022 | ✅ |
| FR11 unmetered surfaced | :127-129 | :214-217 | T002, T004 | — (unit tests missing) | ⚠️ F-001, F-007 |
| FR12 eval paths unchanged | :130-132 | D1-1 | T004 (no-op design) | T023 | ⚠️ F-001 (unit half missing) |
| NFR1 comparability | :136-138 | D4 | T006 | T022 | ✅ |
| NFR2 accounting accuracy | :139-142 | Phase 6 | T002-T005 | T021 | ⚠️ F-006 (role mapping) |
| NFR3 reproducibility | :143-146 | Data Model | T006, T007 | — (params logged; inherent variance documented) | ✅ |
| NFR4 isolation | :147-149 | D1-1, per-technique seams | T004 | T023 | ✅ |
| EC1 budget < one step | :217-221 | D2, :256-258 | T011, T018 | T013 only | ⚠️ F-004 |
| EC2 missing usage | :222-226 | OQ3 | T002, T004 | — (unit tests missing) | ⚠️ F-001 |
| EC3 excluded-phase crossing | :227-229 | D3 | T006 | T013/T016/T020 (SC4) | ✅ |
| EC4 early natural end | :230-232 | :260-266 | T002, T006 | — (defensive path) | ⚠️ F-002 |
| EC5 bad price config | :233-235 | D4 | T001, T007, T008 | — | ⚠️ F-001, F-005 |
| EC6 model unreachable | :236-238 | :264-266 | T006 (`finally`) | — (existing semantics) | ✅ |
| US1–US5 | :62-76 | throughout | mapped in tasks.md:177-181 | T013–T022 | ✅ |

## Findings

### 🔴 HIGH

**F-001 — Plan's Phase-1 unit tests have no task.**
- **Location:** plan.md:274-276 ("Unit-test the meter with fake usage objects and a
  monkeypatched `litellm.completion` … *Blocks everything*"), plan.md:346-350
  (Testing Strategy "Unit" bullet: meter math, context gating, EC2 threshold, EC5
  messages, `BudgetStopper` first-call snapshot), plan.md:383-384 (Generated
  Artifacts: "`cost_meter.py`, **its unit tests**") — vs. tasks.md:19-50 (Phase 1,
  T001–T005), which contains no test-authoring task at all.
- **Issue:** The plan repeatedly commits to Phase-1 unit tests and even declares
  them blocking, but no task creates them. T023 (tasks.md:158-160) leans on them:
  plan.md:356-358 says FR12 regression is "asserted by the inactive-meter unit
  tests **plus** one eval run" — half of that assertion has no task.
- **Impact:** Implementation can proceed to completion with the meter (the
  correctness core of the whole feature: pricing math, exhaustion boundary,
  bucket/context gating, EC2/EC5 behavior) never tested in isolation, and the
  FR12/NFR4 regression guard reduced to a single manual diff.
- **Recommendation:** Add a Phase-1 task (e.g. T005a, depends T003+T004) creating
  the unit tests enumerated at plan.md:346-350, and make T006 depend on it.

### 🟡 MEDIUM

**F-002 — Stop-reason on the failure path contradicts FR9/EC4.**
- **Location:** plan.md:178-179 (`optimization_stop_reason`: `budget_exhausted` |
  `completed` — a closed two-value set), plan.md:211-213 (default `"completed"`),
  plan.md:133-134 + plan.md:263-266 (the `finally` logs stop_reason even when the
  optimizer raises and the run is marked FAILED) — vs. spec.md:120-123 (FR9: record
  "the reason the optimization ended (budget exhausted vs. technique finished on
  its own, e.g. an optimizer that **converges or errors**)") and spec.md:230-232
  (EC4: "the run records the actual spend and **the true stop reason**").
- **Issue:** A run that errors mid-optimization gets `stop_reason="completed"` (the
  default, never flipped), logged by the `finally` on a FAILED run.
- **Impact:** A reviewer filtering the tracking UI sees a FAILED run self-describing
  as "completed" — precisely the misleading record FR9 exists to prevent; EC4's
  "true stop reason" is unsatisfiable with a two-value enum.
- **Recommendation:** Add a third value (e.g. `failed`/`error`) set in the `except`
  path, or omit the param when the phase raises and document that FAILED status is
  the reason. Update T002/T006 wording accordingly.

**F-003 — Spec and plan disagree on whether SkillOpt's edit budget is a stopping cap.**
- **Location:** spec.md:10-13 (Overview: "SkillOpt on epochs **plus an edit
  budget**" as its stopping cap), spec.md:84-86 (FR2 lists "SkillOpt's epochs and
  edit budget" among caps that "MUST no longer stop a run"), spec.md:245-247 (C3:
  "the old effort caps are **removed**") — vs. plan.md:183-186 and tasks.md:135-136
  (T019 keeps `--edit-budget` as a structural knob).
- **Issue:** The plan's justification is factually correct — verified in
  `.venv/.../skillopt/engine/trainer.py:743` (`max_lr=cfg["edit_budget"]`) and
  `:1178` (`max_edits=edit_budget`): it bounds edits per round and never terminates
  a run. It is the spec that mischaracterizes it as a stopping cap; the two
  artifacts currently contradict each other.
- **Impact:** Anyone auditing FR2/C3 compliance against the finished code will find
  `--edit-budget` still present and conclude FR2 was violated; the correction lives
  only in a plan aside.
- **Recommendation:** Amend spec.md Overview/FR2/C3 to strike "edit budget" from
  the list of stopping caps (or footnote that it was misclassified and is
  structural). This is a spec correction, not a plan change.

**F-004 — EC1 ("budget smaller than one step") is only verified for TextGrad.**
- **Location:** spec.md:217-221 (EC1, technique-agnostic), spec.md:211-213 (SC6
  requires it to "end honestly" — unqualified, all techniques). Verification:
  T013 covers EC1 for TextGrad (tasks.md:94-95); T018 *implements* the SkillOpt
  seed path (tasks.md:130-132) but T020's verification list (tasks.md:137-142)
  omits EC1; T014–T016 (GEPA) neither implement nor verify it.
- **Issue:** For GEPA the EC1 interaction is the least understood: the first
  `BudgetStopper` call doubles as the seed-snapshot-to-excluded (plan.md:229-233),
  so with a sub-step budget the stopper returns False on that first call (billable
  just got zeroed) and GEPA runs one full iteration before stopping — plausible
  under FR7's in-flight allowance, but whether the engine then returns the seed
  candidate cleanly as "best" is unexamined anywhere.
- **Impact:** The technique with the most fragile EC1 story ships with zero EC1
  coverage; SkillOpt's implemented seed fallback is never exercised.
- **Recommendation:** Add EC1 assertions to T016 and T020 (a near-zero-budget run
  ends as a valid run, seed prompt, `budget_exhausted`), mirroring T013's wording.

### 🟢 LOW

**F-005 — No end-to-end verification of the EC5/SC6 refusal.**
- **Location:** spec.md:211-213 (SC6), tasks.md:61-66 (T007 requires validation
  "*before* `setup_mlflow`"); Phase 6 (tasks.md:144-167) checks everything except
  the refusal path.
- **Issue:** Unit tests (once F-001 is fixed) cover the messages, but nothing
  verifies the ordering property T007 promises — that a misconfigured run refuses
  before creating any MLflow run.
- **Recommendation:** One line in T022 or T023: run one trainer with a `PRICE_*`
  var unset and confirm it exits with the named var and no MLflow run is created.

**F-006 — Role-name mapping between meter and audit tooling is unspecified.**
- **Location:** plan.md:174-176 (meter metrics use roles `task/judge/optimizer`) vs.
  `scripts/count_tokens.py:48` (audit roles are `("task", "judge", "reflection",
  "other")`); T021 (tasks.md:146-151) says "reconcile … per role … within a small
  tolerance" without defining the `optimizer`↔`reflection` mapping, the fate of the
  audit's `other` bucket, or the tolerance value.
- **Impact:** SC5's pass/fail criterion is left to be invented inside T021.
- **Recommendation:** State the mapping and tolerance in T021 (e.g. meter
  `optimizer` ≡ audit `reflection`; `other` must be ~0 or explained; tolerance X%).

**F-007 — R6's "unmetered-role" fallback conflates two failure kinds.**
- **Location:** plan.md:333-335 (role missing while active → "recorded as
  unmetered-role") vs. plan.md:214-217 (`record_unmetered(role)` exists for
  missing *usage*, EC2, and feeds the `check_unmetered()` failure threshold);
  tasks.md:33-34 repeats the R6 fallback without resolving it.
- **Issue:** A call with perfectly good usage data but no role attribution would
  increment the same counter as a call with no usage, and can therefore trip the
  EC2 "spend is meaningless" run-failure — a different problem (attribution)
  reported as another (measurement).
- **Recommendation:** Specify in T002 whether role-missing shares the EC2 counter
  (and threshold) or gets its own counter/metric.

**F-008 — Unmetered-threshold denominator drifts between plan and task.**
- **Location:** plan.md:216-217 ("5% of metered **optimization** calls") vs.
  tasks.md:30-31 (T002: "5% of metered calls"); neither says whether
  excluded-bucket calls count in the denominator.
- **Impact:** Minor, but the constant gates a run-failure decision (EC2), so the
  denominator should be exact.
- **Recommendation:** Pin the denominator in T002 (suggest: all metered calls while
  `active()`, excluded bucket included).

## Metrics Summary

- **Functional requirements:** 12/12 addressed in plan; 12/12 have implementation
  tasks; 3 have verification gaps (FR3/EC5 e2e, FR11/EC2 unit, EC1 beyond TextGrad).
- **Non-functional requirements:** 4/4 addressed; NFR2 verification underspecified
  (F-006).
- **User stories:** 5/5 mapped to tasks (tasks.md:177-181), mapping spot-checked
  and accurate.
- **Edge cases:** 6/6 designed for; EC1 verified for 1/3 techniques, EC4's error
  arm contradicted by the two-value stop-reason enum, EC5 lacks an e2e check.
- **Assumptions:** A1–A5 all verified against installed sources by the plan (and
  re-verified here); none outstanding.
- **Findings:** 1 🔴 HIGH · 3 🟡 MEDIUM · 4 🟢 LOW. No duplication findings; the
  three artifacts are otherwise tightly cross-referenced (traceability table in
  plan.md:360-377 is accurate).

## Suggested Next Actions (by priority)

1. **F-001:** Add the Phase-1 unit-test task to tasks.md before starting T001 —
   everything downstream (including T023's regression argument) assumes it exists.
2. **F-002:** Decide the failure-path stop-reason semantics now (third enum value
   vs. omit-on-failure) and fold into T002/T006 wording — cheap before Phase 2,
   confusing after runs exist.
3. **F-003:** Correct spec.md's characterization of SkillOpt's edit budget so the
   spec and the shipped CLI don't contradict each other for future auditors.
4. **F-004:** Extend T016/T020 with EC1 assertions (GEPA especially).
5. **F-005/F-006/F-007/F-008:** One-line clarifications to T002, T021, T022 —
   batch them in a single tasks.md edit.
