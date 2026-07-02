# Reflection on Analysis: Cost-Budget-Based Stopping

Analysis: [`analyze.md`](./analyze.md) · Spec: [`spec.md`](./spec.md) ·
Plan: [`plan.md`](./plan.md) · Tasks: [`tasks.md`](./tasks.md)
Date: 2026-07-02

Experimenter decisions taken as input for the four leading findings:
F-001 — no dedicated tests; F-002 — the true stop reason must be shown;
F-003 — `--edit-budget` is preserved; F-004 — budgets will always be sized to
cover the GEPA seed pass plus several candidate evaluations. The four LOW
findings were resolved editorially (clarifications only, no scope change).

---

## F-001 — Plan's Phase-1 unit tests have no task

### Research

The finding is valid as stated: plan.md committed to Phase-1 unit tests in three
places (Phase 1 bullet, Testing Strategy "Unit" bullet, Generated Artifacts
"its unit tests") while tasks.md Phase 1 (T001–T005) contains no test task, and
the Testing Strategy's Regression bullet leaned on "the inactive-meter unit
tests" for half of the FR12 argument.

### Decision

**Experimenter decision: no unit tests.** The inconsistency is resolved in the
opposite direction from the analysis recommendation — instead of adding a test
task, the plan's unit-test commitments are removed so plan and tasks agree.
Verification weight shifts entirely to what tasks.md already carries: the
per-technique tiny-budget smoke runs (T013, T016, T020) exercise pricing,
exclusion and stopping end-to-end, and the FR12/NFR4 regression guard becomes
the T023 eval-run diff alone.

### Artifact Changes

- plan.md Phase 1: drop the "Unit-test the meter …" sentence.
- plan.md Testing Strategy: drop the "Unit (Phase 1, no LLM)" bullet; reword the
  Regression bullet to rest on the eval-run diff only.
- plan.md Generated Artifacts: drop "its unit tests".
- tasks.md: no change (it already had no test task).

---

## F-002 — Stop-reason on the failure path contradicts FR9/EC4

### Research

Valid. plan.md defined a closed two-value enum (`budget_exhausted` |
`completed`) with `completed` as the default, while the `finally` in
`_run_optimization` logs the param even when the optimizer raises and MLflow
marks the run FAILED — so a crashed run would self-describe as "completed",
contradicting spec FR9 ("…an optimizer that converges or errors") and EC4
("the true stop reason").

### Decision

**Experimenter decision: show the true reason.** A third value `failed` is
added. The meter keeps its `completed` default; `_run_optimization` (which owns
the `try/finally` and therefore knows whether the phase raised) records
`optimization_stop_reason="failed"` on the exception path. No change to failure
semantics otherwise — the exception still propagates and the run is still
marked FAILED with its spend logged.

### Artifact Changes

- plan.md Data Model: enum becomes `budget_exhausted` | `completed` | `failed`.
- plan.md `CostMeter` contract: note the runner records `failed` when the phase
  raises.
- plan.md Stop-reason semantics: state that the `finally` logs
  `optimization_stop_reason="failed"` on the failure path.
- tasks.md T002: mention the third value and where it is set.
- tasks.md T006: the `finally` records `failed` when the optimization phase
  raised.

---

## F-003 — Spec and plan disagree on SkillOpt's edit budget

### Research

Valid, and the plan's side is the factually correct one — verified against the
installed trainer: `.venv/…/skillopt/engine/trainer.py:743`
(`max_lr=cfg["edit_budget"]`) and `:1178` (`max_edits=edit_budget`). The edit
budget bounds edits per update round (a textual learning rate) and never
terminates a run; the spec mischaracterized it as a stopping cap in the
Overview, FR2 and (implicitly) C3.

### Decision

**Experimenter decision: `--edit-budget` shall be preserved.** This confirms
the plan/tasks treatment (T019 keeps it as a structural knob). The corrective
edit is therefore to the **spec**: strike "edit budget" from the list of
stopping caps and name it explicitly in FR2's structural-knob keep-list and in
C3, so a future FR2/C3 compliance audit does not misread the shipped CLI.

### Artifact Changes

- spec.md Overview: "SkillOpt on epochs plus an edit budget" → "SkillOpt on a
  number of epochs".
- spec.md FR2: remove "and edit budget" from the caps list; add the per-round
  edit budget to the structural-knobs list.
- spec.md C3: note explicitly that SkillOpt's per-round edit budget is
  structural and preserved.
- plan.md / tasks.md: no change (already correct).

---

## F-004 — EC1 verified only for TextGrad

### Research

Valid as an observation: EC1 assertions exist only in T013 (TextGrad); T018
implements SkillOpt's seed fallback but T020 does not verify it; GEPA
(T014–T016) has no EC1 handling or verification, and its first
`BudgetStopper` call doubles as the seed-pass exclusion snapshot, making a
sub-step budget its least-understood path.

### Decision

**Experimenter decision: budgets will always be set to fully cover the GEPA
seed pass plus several candidate evaluations**, so the sub-step-budget regime
is out of operational scope for GEPA (and, by the same sizing practice, for
SkillOpt). EC1 stays in the spec as designed defensive behavior — it is real
code on the TextGrad and SkillOpt paths — but end-to-end verification is
scoped to TextGrad only (T013). This is recorded as a new assumption (A7)
rather than silently narrowing SC6.

### Artifact Changes

- spec.md Assumptions: add A7 (budget sizing covers the seed pass plus several
  evaluations; EC1 verified end-to-end on TextGrad only).
- plan.md Testing Strategy: scope the EC1 smoke bullet to TextGrad, citing A7.
- tasks.md: no change (T016/T020 stay as written).

---

## F-005 — No end-to-end verification of the EC5/SC6 refusal

### Research

Valid: T007 promises validation *before* `setup_mlflow`, and with F-001 removing
unit tests entirely, nothing anywhere would exercise the refusal path.

### Decision

Apply the one-line fix. With unit tests gone (F-001), this manual check is the
only remaining verification of US5/SC6, so it earns its line in T023 (a manual
runtime check inside an existing verification task — consistent with the
no-test-infrastructure decision).

### Artifact Changes

- tasks.md T023: add a check that one trainer started with a missing `PRICE_*`
  var refuses before any MLflow run is created.

---

## F-006 — Meter↔audit role-name mapping unspecified

### Research

Valid: meter roles are `task/judge/optimizer` (plan Data Model) while
`scripts/count_tokens.py:48` uses `("task", "judge", "reflection", "other")`;
T021 said "within a small tolerance" with no mapping or number.

### Decision

Pin it in T021: meter `optimizer` ≡ audit `reflection`; the audit `other`
bucket must be ≈0 or explained; tolerance 5% per role (matches the order of
magnitude already used for the unmetered threshold; revisitable in the script
without spec impact).

### Artifact Changes

- tasks.md T021: state the mapping and the 5% per-role tolerance.

---

## F-007 — R6 "unmetered-role" fallback conflates two failure kinds

### Research

Valid ambiguity: plan R6 routes role-missing calls into "unmetered-role" while
`record_unmetered` exists for missing usage (EC2) and feeds the
`check_unmetered()` failure threshold; neither artifact said whether the
counter is shared.

### Decision

Share the counter and threshold, deliberately: an unattributed call corrupts
per-role spend just as a usage-less call corrupts total spend — both are
meter-integrity failures, and either kind occurring frequently should fail the
run the same way. One counter also keeps the meter simple (in the spirit of
F-001's minimalism). The wording is clarified in both artifacts.

### Artifact Changes

- plan.md R6: state the counter/threshold is shared with missing-usage calls.
- tasks.md T002: same clarification.

---

## F-008 — Unmetered-threshold denominator drift

### Research

Valid minor drift: plan said "5% of metered **optimization** calls", T002 said
"5% of metered calls"; neither defined whether excluded-bucket calls count.

### Decision

Pin one phrasing in both places: **all calls metered while `active()`,
excluded bucket included**. Rationale: excluded calls are still successfully
metered calls — they attest that the meter is working — so they belong in the
denominator of a meter-integrity ratio.

### Artifact Changes

- plan.md `CostMeter` contract: pin the denominator wording.
- tasks.md T002: identical wording.

---

## Summary of Artifact Changes

| Finding | spec.md | plan.md | tasks.md |
|---|---|---|---|
| F-001 no tests (user) | — | Phase 1 unit-test sentence removed; Testing Strategy "Unit" bullet removed, Regression bullet reworded; Generated Artifacts "its unit tests" removed | — |
| F-002 true stop reason (user) | — | stop-reason enum + contract + semantics gain `failed` | T002, T006 mention `failed` on the raise path |
| F-003 keep `--edit-budget` (user) | Overview, FR2, C3 corrected: edit budget is structural, not a stop | — | — |
| F-004 budget sizing (user) | new assumption A7 | Testing Strategy EC1 bullet scoped to TextGrad (A7) | — |
| F-005 refusal check | — | — | T023 gains the missing-price refusal check |
| F-006 role mapping | — | — | T021 pins mapping + 5% tolerance |
| F-007 shared counter | — | R6 wording: shared counter/threshold | T002 wording: shared counter/threshold |
| F-008 denominator | — | contract wording pinned | T002 wording pinned |

analyze.md is left untouched, per skill rules — this document records the
response to it.
