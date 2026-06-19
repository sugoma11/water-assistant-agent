# Reflection on Analysis: SkillOpt MLflow Integration

Response to [`analyze.md`](./analyze.md). Each finding was researched against actual
source files; decisions are evidence-based and corrective edits applied to `plan.md` /
`tasks.md`. Findings are not deleted from `analyze.md` — this document records the
response.

Primary evidence base: `src/experiments/text2sql/textgrad_optimizer.py` (the existing
`BasePromptOptimizer` whose contract SkillOpt mirrors). SkillOpt is **not yet installed**
(`uv run python -c "import skillopt"` → `ModuleNotFoundError`; T001 pending), so findings
about SkillOpt internals remain genuine spike-gaps rather than verifiable-now facts.

---

## F-001 — Two divergent validation-evaluation paths under one metric name (HIGH)

### Research
`textgrad_optimizer.py` shows the project's deliberate design rule. The docstring (L18–24)
states validation scoring goes through MLflow's `eval_fn` ("the canonical optimize path"),
explicitly *not* the training forward path. Concretely:
- `_val_score(eval_fn, …)` computes the val mean via `eval_fn` (L263–270).
- Keep-best/revert (FR12, SC2a) is driven by that `eval_fn` val mean (L386–397).
- The logged `eval_score` series is the **same** `eval_fn` `val` value used for keep-best
  (L386–387, `_log_eval_score` L288–296).
- `initial/final_eval_score` and the EC2 decision use the same axis (`best_val >
  initial_eval_score`, L407, L425).

So TextGrad keeps **selection, the logged series, and the endpoints all on one
`eval_fn` axis**. The SkillOpt plan instead delegates keep-best to SkillOpt's *native*
rollout/`history.json` gate (plan L85–90) while logging `initial/final_eval_score` from
`eval_fn` (plan L116, L120) and the series from `history.json` (plan L119). That is two
axes under one metric name — the exact thing TextGrad's design avoids.

### Decision
**Valid — strengthened.** The fix preserves SkillOpt's native gate (FR12 stays
SkillOpt-owned) but requires the logged `eval_score` series to be reconciled with the
`eval_fn` endpoints: the spike must confirm `history.json` val agrees with an `eval_fn`
val call on the same candidate/split; if they diverge, source the series from per-epoch
`eval_fn` `_val_score` (TextGrad parity) so series and endpoints share one axis.

### Artifact Changes
- `plan.md` seam 3: add the history-val ↔ `eval_fn`-val agreement requirement and the
  fallback to per-epoch `eval_fn` sourcing.
- `tasks.md` T005: add the agreement check. `tasks.md` T014: require the series source to
  match the `eval_fn` endpoint axis.

---

## F-002 — `edit_budget` → `learning_rate` mapping unverified (MEDIUM)

### Research
SkillOpt not installed, so `learning_rate` semantics cannot be confirmed from source. The
plan asserts the mapping (L50, L210–212) but the spike tasks T003–T005 pin endpoints,
rollout, and the success/history seams — none observes the per-round applied-edit count.
FR13 (bounded, recorded edits) and the recorded `edit_budget` depend entirely on this.

### Decision
**Valid.** Add a concrete spike verification that `learning_rate` caps the number of edits
applied per round.

### Artifact Changes
- `tasks.md` T002: add confirmation that `learning_rate` bounds applied edits per round.

---

## F-003 — `history.json` step granularity vs. logged `step=epoch` (MEDIUM)

### Research
Plan calls `history.json` "per-step" (L35) yet logs at `step=epoch` (L119, T014 L88). No
row→step mapping rule is stated. Whether a row is a minibatch update or an epoch is
unconfirmed (SkillOpt not installed). TextGrad sidesteps this by computing val once per
epoch and logging at `step=epoch` directly (L386–387) — there is no history-file
re-mapping to reconcile.

### Decision
**Valid.** Pin what one `history.json` row represents and the explicit row→step rule in the
spike, and reference it from T014.

### Artifact Changes
- `tasks.md` T005: record what one history row represents and the row→epoch mapping.
- `plan.md` seam 3: state the row→step rule must be pinned (folded into the F-001 edit).

---

## F-004 — EC2/FR12 interaction can discard a SkillOpt-selected improvement (MEDIUM)

### Research
A consequence of F-001's two axes. In TextGrad the EC2 decision and keep-best are the same
`eval_fn` axis (L407), so no contradiction arises. For SkillOpt, EC2 uses `eval_fn` `final`
(plan L121) while SkillOpt selected `best_skill.md` on its rollout gate — they can disagree.

### Decision
**Valid, resolved by F-001.** Once the series/endpoints share one axis (F-001), the EC2
basis is unambiguous. Add a one-line note that the EC2 keep/return decision is made on the
`eval_fn` `final_eval_score`, consistent with the logged endpoints.

### Artifact Changes
- `tasks.md` T015: note the EC2 decision basis is the `eval_fn` `final_eval_score` (F-001
  axis).

---

## F-005 — `val_set` enters out-of-band, not via `optimize_prompts` (LOW)

### Research
`textgrad_optimizer.py` takes `val_set` in `__init__` (L203, L218) and receives only
`train_data` in `optimize()` (L300–301), using `self.val_set` for `_val_score` (L268). This
is the **established, working convention** the SkillOpt plan mirrors exactly — not a
deviation.

### Decision
**False positive.** No artifact change. The SkillOpt plan correctly follows the existing
contract; documenting it as a "deviation" would be inaccurate.

### Artifact Changes
- None.

---

## F-006 — `batch_size`/`train_size` full-pass semantics guessed (LOW)

### Research
SkillOpt not installed; the `train_size=0`/full-pass convention (plan L214) is inferred
from source-reading, not confirmed. Q1's "epoch = full minibatch sweep" depends on it.

### Decision
**Valid.** Confirm full-pass semantics during the in-process spike run (T002).

### Artifact Changes
- `tasks.md` T002: add confirmation that `batch_size`/`train_size` yields a full-pass epoch.

---

## F-007 — Subprocess fallback (R1 worst case) unticketed (LOW)

### Research
Plan L302–304 describes a subprocess `scripts/train.py` + YAML fallback if in-process
`train()` is impossible. No task captures it; T002 is the decision point.

### Decision
**Valid.** Annotate T006 to record the subprocess-fallback decision if the in-process path
fails, rather than spawning a speculative task for a contingency that may never trigger.

### Artifact Changes
- `tasks.md` T006: record the subprocess-fallback decision (R1) if in-process fails.

---

## F-008 — T006 records spike decisions by editing `plan.md` (LOW)

### Research
T006 (L43–44) offers plan.md or a `spike-findings.md`. Editing the approved plan post-hoc
mixes spec baseline with results; the `analyze` skill's own convention keeps generated
artifacts separate.

### Decision
**Valid.** Make `spike-findings.md` the primary target; leave plan.md as the approved
baseline.

### Artifact Changes
- `tasks.md` T006: prefer `spike-findings.md`.

---

## F-009 — Naming / version notes (LOW)

### Research
The `specs.md` reference was the invoking command's typo; `plan.md` L3 and `tasks.md` L3
correctly cite `spec.md`. Python ≥3.10 (plan L23) vs the project's 3.13 (L266, T001) is a
compatibility floor, not a conflict.

### Decision
**Informational, no action.**

### Artifact Changes
- None.

---

## Summary of Artifact Changes

| Finding | Verdict | File | Change |
|---|---|---|---|
| F-001 | Valid (strengthened) | plan.md, tasks.md | Seam 3 + T005 history-val↔eval_fn agreement; T014 series source matches endpoint axis |
| F-002 | Valid | tasks.md | T002 verify `learning_rate` caps applied edits/round |
| F-003 | Valid | plan.md, tasks.md | Seam 3 + T005 pin row meaning & row→epoch mapping |
| F-004 | Valid (via F-001) | tasks.md | T015 note EC2 basis = `eval_fn` `final_eval_score` |
| F-005 | False positive | — | None (established TextGrad convention) |
| F-006 | Valid | tasks.md | T002 confirm full-pass `batch_size`/`train_size` |
| F-007 | Valid | tasks.md | T006 record subprocess-fallback decision if in-process fails |
| F-008 | Valid | tasks.md | T006 prefer `spike-findings.md` |
| F-009 | Informational | — | None |
