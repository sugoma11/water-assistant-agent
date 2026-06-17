# Analysis Report: TextGrad as a Prompt-Optimization Technique in MLflow

Artifacts analyzed:
- `specs/textgrad-mlflow-integration/spec.md`
- `specs/textgrad-mlflow-integration/plan.md`
- `specs/textgrad-mlflow-integration/tasks.md`

Cross-checked against the live codebase (`src/experiments/text2sql/train.py`,
`harness.py`, `pyproject.toml`, `justfile`, `.example.env`,
`notebooks/textgrad_prompt_opt.ipynb`).

---

## Coverage Matrix (requirement × artifact)

| Req | Spec | Plan | Tasks | Notes / Findings |
|---|---|---|---|---|
| FR1 (TextGrad selectable) | ✓ | ✓ (separate CLI) | ✓ T014,T016,T017 | OK |
| FR2 (revised prompt from train) | ✓ | ✓ | ✓ T011 | OK |
| FR3 (same data/split/judge) | ✓ | ✓ | ✓ T005,T015 | OK |
| FR4 (technique recorded) | ✓ | ✓ | ✓ T006,T015 | OK |
| FR5 (record name/models/sizes/effort/before-after/prompt) | ✓ | ✓ | ✓ T015 | metric-name issue → F-001/F-002 |
| FR6 (prompt saved/versioned same way) | ✓ | ✓ | ✓ T012,T020 | OK |
| FR7 (clear failure, no fake result) | ✓ | ✓ | ✓ T019 | OK |
| FR8 (no change to existing technique) | ✓ | ✓ (Phase 2) | ✓ T004,T007 | **regression-by-construction → F-001, F-008** |
| FR9 (3 model roles, shared judge) | ✓ | ✓ | ✓ T010,T011,T015 | OK |
| FR10 (epochs human-set, batch/steps recorded) | ✓ | ✓ | ✓ T014,T015 | OK |
| FR11 (rationale = edit signal; pass/fail = metric) | ✓ | ✓ | ✓ T011 | OK |
| FR12 (per-epoch val keep-best/revert) | ✓ | ✓ | ✓ T011,T003 | val-metric source ambiguous → F-002 |
| NFR1 (comparability / same metric names) | ✓ | ✓ | ✓ | **contradicted by GEPA names → F-001/F-002** |
| NFR2 (reproducibility / seeds) | ✓ | ✓ | ✓ T008,T009 | OK |
| NFR3 (cost transparency / effort recorded) | ✓ | ✓ | ✓ T013,T015 | OK |
| NFR4 (isolation) | ✓ | ✓ (separate module/cmd) | ✓ | OK |
| EC1 (optimizer unreachable) | ✓ | ✓ | ✓ T019 | OK |
| EC2 (no improvement) | ✓ | ✓ | ✓ T020 | OK |
| EC3 (empty/small split) | ✓ (train only) | ✓ (train+val) | ✓ T018 (train+val) | spec/impl scope mismatch → F-006 |
| EC4 (judge unavailable) | ✓ | ✓ | ✓ T019 | OK |
| EC5 (prompt save fails) | ✓ | ✓ | ✓ T020 | OK |
| SC4 (no regression) | ✓ | ✓ | ✓ T004,T007 | **→ F-001, F-008** |

Reference-impl dependency: tasks.md cites `notebooks/textgrad_prompt_opt.ipynb`
as the "working train loop," but its engine/gradient approach diverges from
plan.md → F-003, F-004.

---

## Findings

### 🔴 HIGH

**F-001 — The "shared routine" will change GEPA's logged metrics, contradicting the no-regression guarantee it is meant to protect.**
- Location: `plan.md:50-52`, `plan.md:118-124`; `tasks.md:36-47` (T005, T007); vs. `src/experiments/text2sql/train.py:248-262`.
- Issue: The plan's `_run_optimization` "logs `val_quality_before = result.initial_eval_score`, runs val/test-after, logs the four `{val,test}_quality_{before,after}` metrics" (plan.md:122). But the **existing GEPA run deliberately does not log val metrics** — `train.py:248-254` keeps `initial_eval_score`/`final_eval_score` only as local values because `optimize_prompts` already logs them under those names, and **GEPA runs no separate val-after eval phase** (train.py comment, lines 248-252). Routing GEPA through this shared routine therefore (a) adds new `val_quality_before/after` metrics GEPA never emitted and (b) adds a full extra valset pass (cost + an extra nested run).
- Impact: T007 asserts "identical logged params/metrics … vs. the saved baseline" (tasks.md:46-47). As designed, that assertion **cannot pass** — the refactor adds metrics by construction. Directly threatens FR8 / SC4 (no regression) and NFR4 (isolation).
- Recommendation: Decide one model and make spec/plan/tasks agree: either (i) the shared routine does **not** re-log val metrics for GEPA (preserve `initial_eval_score`/`final_eval_score`, derive TextGrad's val numbers the same way), or (ii) accept new `val_quality_*` names and **update T004/T007** so the GEPA baseline is re-captured with the new names (i.e. regression check is "stable across re-runs," not "byte-identical to pre-refactor"). Option (i) better preserves SC4.

**F-002 — "Same metric names across techniques" is asserted but the two techniques' validation metrics have different names.**
- Location: `spec.md:99-100` (NFR1), `spec.md:147-154` (SC2/SC3), `plan.md:207` (traceability "`sql_is_correct/mean` → `*_quality_*`"); vs. `train.py:14-18,251-254` and `harness.py:76` (`sql_is_correct/mean`).
- Issue: GEPA's before/after **validation** quality is recorded as `initial_eval_score` / `final_eval_score` (auto-logged by `optimize_prompts`), while only the **test** phase uses `test_quality_{before,after}` (train.py:224,262). The plan/tasks describe TextGrad's val numbers as `val_quality_{before,after}` (plan.md:52,93,122). Nothing pins whether the canonical comparable val metric is `initial/final_eval_score` (GEPA's) or `val_quality_*` (plan's), nor whether `optimize_prompts` auto-logs `initial/final_eval_score` for a `TextGradPromptOptimizer` too (it does for GEPA via the result object).
- Impact: Side-by-side comparison in the tracking UI (SC2/SC3, the core deliverable) may compare differently-named metrics, defeating NFR1.
- Recommendation: Explicitly name the comparable metrics in the spec and align both code paths to them. Confirm whether `optimize_prompts` auto-logs `initial/final_eval_score` for any optimizer; if so, prefer reusing those for val on both paths and drop `val_quality_*`, or vice-versa — but use one name set.

### 🟡 MEDIUM

**F-003 — Conflicting optimizer-engine implementations between plan and tasks/notebook.**
- Location: `plan.md:54-71,141-166` and `tasks.md:63-66` (T010) specify a bespoke `CustomLiteLLMEngine` wrapping `litellm.completion` via `build_completion_kwargs`; but `tasks.md:13-26` (T001/T002 header) and the cited reference `notebooks/textgrad_prompt_opt.ipynb` use TextGrad's built-in `ChatExternalClient` + `SchemaInjectingEngine` (verified: 5× `ChatExternalClient`, 4× `SchemaInjectingEngine` in the notebook, 0× `CustomLiteLLMEngine`).
- Impact: Implementers get two different engine designs for the same role (T002 vs T010). `build_completion_kwargs` exists (harness.py:240) and routes endpoints/keys via `ENDPOINTS`; `ChatExternalClient` does not, so R3 (endpoint routing) is only solved by the custom-engine path. Picking the notebook path silently drops the R3 mitigation.
- Recommendation: Reconcile. If `ChatExternalClient` is the proven path, document how it gets `api_base`/`api_key` per `ENDPOINTS` (R3) and rename T010 accordingly; otherwise keep `CustomLiteLLMEngine` and update T001/T002 and the notebook reference.

**F-004 — The "primary" gradient-injection approach in the plan is not the one the notebook/tasks actually prove.**
- Location: `plan.md:148-157` lists **primary** = direct attachment to `system_prompt.gradients`, **fallback** = custom autograd `Function`. `tasks.md:22-26` (T002) pins **primary** = `TextLoss` judge-as-loss "per notebook," **fallback** = custom autograd `Function`. The notebook uses `TextLoss` (verified 2× `TextLoss`), which plan.md never mentions.
- Impact: R1 (the highest risk, plan.md:183) is "mitigated by the spike," but the spike (T002) validates a different primary approach than the plan's architecture section describes. The pinned API may not match what Phase 4 (T011) is written against.
- Recommendation: Update plan.md §"TextGrad-internal mechanism" so its primary approach is the `TextLoss` bridge already working in the notebook; demote direct-`.gradients` attachment to an alternative.

**F-005 — Tests are told to "mirror existing test layout," but no test layout exists.**
- Location: `tasks.md:116-120` (T021/T022) "File: `tests/` (mirror existing test layout)". Verified: there is **no `tests/` directory** and no first-party test files in the repo; `pyproject.toml` declares no test runner.
- Impact: T021/T022 are underspecified — no framework, directory convention, fixtures, or invocation command. Risk that the keep-best (SC2a/FR12) and guard (EC2/EC3) unit tests — the only automated checks in the plan — are skipped or done ad-hoc.
- Recommendation: Specify the test runner (e.g. pytest), the target path, and how it's invoked (justfile recipe / CI). Add the dev dependency if missing.

**F-006 — Shared `log_global_params` would log `OPTIMIZER_*` params on GEPA runs too.**
- Location: `tasks.md:51-53` (T008) adds `read_sampling_params("OPTIMIZER")` logging "in `log_global_params`"; `harness.py:702-726` shows `log_global_params` is **shared** and called by GEPA (train.py:197-206).
- Impact: Every GEPA run would gain `OPTIMIZER_*` params for a model role GEPA does not use — extra noise and another way T007's "identical params" assertion (tasks.md:46-47) breaks. Compounds F-001.
- Recommendation: Log `OPTIMIZER_*` only on the TextGrad path (e.g. via `extra_params` in `_run_optimization`, not in shared `log_global_params`), or explicitly accept and re-baseline (see F-001 recommendation).

### 🟢 LOW

**F-007 — EC3 scope mismatch (spec mentions only the training split).**
- Location: `spec.md:166-167` (EC3, "empty or too-small **training** split") vs. `plan.md:130` and `tasks.md:103` (T018) which also guard the **val** split.
- Impact: Minor; plan/tasks are stricter than spec. A too-small val split breaks keep-best (FR12) but has no spec requirement.
- Recommendation: Extend EC3 to mention the val split so the guard is traceable.

**F-008 — Reference notebook and tasks.md are uncommitted / in flux.**
- Location: git status — `notebooks/textgrad_prompt_opt.ipynb` is modified (unstaged) and `specs/textgrad-mlflow-integration/tasks.md` is untracked.
- Impact: Tasks lean heavily on the notebook as the "working train loop" (tasks.md:4), but it is not version-pinned; the proven API (F-003/F-004) could drift before Phase 4.
- Recommendation: Commit the notebook state the spike (T001-T003) validates, and reference that commit from tasks.md.

**F-009 — `OPTIMIZER_TOP_K` optionality unstated in `.example.env` block.**
- Location: `tasks.md:54-56` (T009) marks `OPTIMIZER_TOP_K` optional; the existing `.example.env` `JUDGE_*`/`LLM_*` blocks always include `*_TOP_K` (verified). Mild inconsistency in "mirroring the JUDGE_ block."
- Recommendation: Decide whether to include `OPTIMIZER_TOP_K` by default for parity; trivial.

---

## Metrics Summary

- Functional requirements (FR1–FR12): **12/12** present across all three artifacts; **2** carry HIGH consistency defects (FR5/FR8 via F-001/F-002).
- Non-functional (NFR1–NFR4): **4/4** present; NFR1 undermined by F-002, NFR4/SC4 by F-001/F-006.
- User stories (US1–US4): **4/4** covered (tasks Summary, tasks.md:144-150).
- Edge cases (EC1–EC5): **5/5** addressed; 1 spec/plan scope mismatch (F-007).
- Success criteria (SC1–SC5, incl. SC2a): **6/6** traced; SC3/SC4 at risk (F-001/F-002).
- Findings: **2 HIGH, 4 MEDIUM, 3 LOW** (9 total).
- Codebase grounding: all plan-referenced symbols verified to exist (`build_completion_kwargs`, `read_sampling_params`, `log_global_params`, `configure_teacher_env`, `create_optimizable_predict_fn`, `build_sql_judge_scorer`, `register_prompt_if_changed`); `textgrad>=0.1.8` present in `pyproject.toml:21`; `.example.env` present.

---

## Suggested Next Actions (priority order)

1. **Resolve the metric-logging model (F-001, F-002, F-006).** Decide: does the shared routine re-log val metrics as `val_quality_*`, or keep GEPA's `initial/final_eval_score`? Update plan §"Shared routine", T005/T007, and the metric names in spec FR5/SC2/SC3 to one consistent scheme. This unblocks the Phase 2 regression check and the whole comparability goal.
2. **Pin the engine and gradient-injection API in one place (F-003, F-004).** Make plan.md §"TextGrad-internal mechanism"/§Architecture match the notebook+T002 (`ChatExternalClient`/`SchemaInjectingEngine` + `TextLoss`), and document how R3 endpoint routing is satisfied on the chosen engine.
3. **Define the test setup (F-005).** Add runner, path, and invocation for T021/T022 before Phase 7.
4. **Commit the reference notebook (F-008)** at the state the spike validates and cite the commit.
5. **Tidy spec scope and env parity (F-007, F-009).** Extend EC3 to the val split; settle `OPTIMIZER_TOP_K`.
