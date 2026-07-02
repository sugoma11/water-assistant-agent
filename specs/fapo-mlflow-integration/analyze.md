# Analysis Report: FAPO as a Prompt-Optimization Technique in MLflow

Scope: cross-artifact consistency, coverage, and ambiguity review of
[`spec.md`](./spec.md), [`plan.md`](./plan.md), [`tasks.md`](./tasks.md), grounded in the
sibling implementations the plan declares it mirrors (`textgrad_optimizer.py`,
`skillopt_optimizer.py`) and the shared harness (`train_common.py`). Read-only per the
`analyze` skill — the source artifacts were not modified.

## Inventory

- **Functional requirements:** FR1–FR14 (14)
- **Non-functional requirements:** NFR1–NFR5 (5)
- **User stories:** US1–US4 (4)
- **Edge cases:** EC1–EC9 (9)
- **Success criteria:** SC1, SC2, SC2a, SC3–SC7 (8)
- **Clarifications:** Q1–Q6 (6, all resolved)
- **Assumptions:** A1–A7 (7)
- **Tasks:** T001–T025 (25), 6 phases

## Coverage Matrix

| Req | spec.md | plan.md | tasks.md | Status |
|---|---|---|---|---|
| FR1 | L92 | L58–62, L362 | T014, T016 | ✅ |
| FR2 | L94 | L81–88, L363 | T012 | ✅ |
| FR3 | L96 | L149–155, L364 | T010, T018, T024 | ✅ |
| FR4 | L99 | L362 | T014, T015 | ✅ |
| FR5 | L101 | L175–180, L365 | T013, T015 | ✅ |
| FR6 | L105 | L81–88, L128, L363 | T005, T012 | ✅ |
| FR7 | L111 | L311–319, L366 | T019, T022 | ✅ |
| FR8 | L116 | L140–145, L367 | T015, T023 | ⚠️ F-008 |
| FR9 | L119 | L64–79, L371 | T007, T008, T015 | ⚠️ F-006, F-007 |
| FR10 | L129 | L239–249, L365 | T014, T015 | ⚠️ F-003 |
| FR11 | L136 | L72, L233, L364 | T008 | ⚠️ F-007 |
| FR12 | L143 | L50–54, L368 | T005, T015 | ✅ |
| FR13 | L147 | L122, L237, L369 | T011 | ⚠️ F-005 |
| FR14 | L151 | L119, L236, L370 | T005, T020 | 🔴 F-001 |
| NFR1 | L160 | L261–276, L364 | T010, T012, T024 | ✅ |
| NFR2 | L165 | L332–335, L372 | T009, T010, T018 | ⚠️ F-007 |
| NFR3 | L172 | L239–249, L365 | T015 | ⚠️ F-003 |
| NFR4 | L178 | L367, L374 | T001, T023 | ✅ |
| NFR5 | L182 | L126, L217–218, L373 | T013, T015 | ⚠️ F-002 |
| US1 | L79 | L58–88 | T007–T017 | ✅ |
| US2 | L81 | L149–155 | T010, T015, T024 | ✅ |
| US3 | L84 | L261–276 | T013, T015, T024 | ⚠️ F-002 |
| US4 | L87 | L311–319 | T018–T022 | ✅ |
| EC1 | L274 | L258, L311 | T019 | ✅ |
| EC2 | L276 | L127, L223–227 | T012 | ✅ |
| EC3 | L281 | L110, L216 | T018 | ✅ |
| EC4 | L284 | L233, L276–279 | T008 | ✅ |
| EC5 | L287 | L120, L316 | T011, T020 | ✅ |
| EC6 | L290 | L119–120, L316 | T005, T020 | 🔴 F-001 |
| EC7 | L292 | L316 | T020 | ✅ |
| EC8 | L294 | L219–222, L243 | T011, T022 | ⚠️ F-003, F-005 |
| EC9 | L298 | L74–79, L344, L371 | T021 | ✅ |
| SC1 | L248 | L58–62 | T014 | ✅ |
| SC2 | L249 | L261–276 | T011, T012, T024 | ✅ |
| SC2a | L257 | L122, L237 | T011, T024 | ⚠️ F-005 |
| SC3 | L260 | L261–276 | T024 | ✅ |
| SC4 | L262 | L140–145, L367 | T024 | ⚠️ F-008 |
| SC5 | L264 | L311–319 | T022 | ✅ |
| SC6 | L267 | L128, L363 | T005, T012 | ✅ |
| SC7 | L270 | L274–276, L369 | T005, T024 | 🔴 F-001 |

**Every requirement, story, edge case, and success criterion is traced to at least one
task.** No orphaned requirements and no orphaned tasks. The findings below concern
consistency, feasibility, and under-specification within otherwise-covered items — one of
which (F-001) is a direct internal contradiction, not merely a caveat.

## Findings

### 🔴 HIGH

**F-001 — The review leakage check requires the test split, which the optimizer never
receives and SC7 forbids it from inspecting.**
- **Location:** plan.md L119 (`review = _review(variant, seed_template, train+val+test)`);
  tasks.md T005 L51 and T020 L149 ("no train/val/**test** case leakage"); spec.md FR14 L151–156
  ("leak validation/test cases"), EC6 L290, SC7 L270–271 ("never selecting a variant by
  inspecting the test split … variant selection uses only train … and validation").
- **Issue:** The plan's `_review` signature feeds it `train+val+test` cases so it can reject a
  variant that pasted eval cases into the prompt. But `FapoPromptOptimizer` is only ever handed
  `train_data` (the `optimize()` arg) and `val_set` (the constructor) — the **test split is
  deliberately withheld** from every optimizer in this harness. Confirmed by the sibling CLI:
  `train_skillopt.py:186–193` passes `val_set=val_set` to the optimizer and `train_skillopt.py:223,
  236–237` passes `test_set` only to `_run_optimization`, never to the optimizer. So `review.py`
  *cannot* obtain the test cases to check against — and obtaining them would itself violate SC7,
  which states the run must never inspect the test split for variant selection.
- **Impact:** A literal implementation of T005/T020/plan-L119 is impossible (no test set in
  scope) or, if someone wires the test set into the optimizer to satisfy it, it breaks SC7 and
  the apples-to-apples comparison guarantee. The contradiction sits on the feature's headline
  guardrail (FR14) and its no-leakage success criterion (SC7).
- **Recommendation:** ~~Scope the review leakage check to train + val only.~~
- **RESOLVED (2026-07-01): intrinsic check, no dataset shown to the reviewer.** `review.py` is a
  **pure function of the variant text alone** — it is given no case sets (train, val, or test).
  Case leakage is detected *intrinsically*: deterministic, network-free heuristics that flag
  example-specific content that has no place in a general instruction block — fully-formed
  `SELECT … FROM … WHERE …` statements, hardcoded string/numeric literals and IDs, and long
  verbatim question-like sentences. This resolves the contradiction outright: no test split is
  needed or touched (SC7 honored), and `review.py` stays a pure function so the T006 checks are
  data-free. It is also sound because leakage is nearly impossible by construction — the proposal
  prompt is schema-free and built only from the train failure cluster, so the optimizer model
  never sees val/test text. **Action:** reword the `_review` input in plan.md (L45, L119) and
  tasks T005/T020 to drop "train/val/test case leakage" in favor of "no example-specific content
  (intrinsic check; reviewer is given no dataset cases)." (spec.md FR14/EC6 wording is the user's
  to adjust.)

### 🟡 MEDIUM

**F-002 — Tenant-directory capture (NFR5) specifies an incompatible mechanism: `extra_artifacts`
cannot carry the temp tenant dir.**
- **Location:** plan.md L310 ("attaches the tenant dir via `extra_artifacts`"), L218, L126;
  tasks.md T015 L128–129 ("attach the tmp tenant dir via `extra_artifacts`") vs. T013 L107–110
  ("capture the tenant dir as MLflow artifacts on the active run"); evidence
  `train_common.py:70` (`extra_artifacts: dict[str, str]`), L111–112 (`for artifact_path, text
  in (extra_artifacts or {}).items(): mlflow.log_text(text, artifact_path)`).
- **Issue:** `extra_artifacts` is a `dict[str, str]` of *path → text* logged with
  `mlflow.log_text`, and it is logged in `_run_optimization` **before** `optimize_prompts` runs
  (L111 precedes L119). The FAPO tenant dir is a *directory tree of files* created inside
  `FapoPromptOptimizer.optimize()`'s `tempfile.TemporaryDirectory()` (plan L118, L217) and torn
  down when `optimize()` returns. It does not exist at the time `_run_optimization` logs
  `extra_artifacts`, and a directory of variant files is not a single text string. So T015's
  "attach the tmp tenant dir via `extra_artifacts`" is both mistimed and type-incompatible.
- **Impact:** Following T015 literally drops the NFR5 audit artifacts (or tempts an unnecessary
  edit to the shared `_run_optimization`, risking FR8/SC4). The two tasks describe two different
  mechanisms for the same requirement.
- **Recommendation:** Capture the tenant dir from **inside `optimize()`** via
  `mlflow.log_artifacts(tenant_dir, artifact_path="fapo_tenant")` while the temp dir still exists
  and the run is active (T013, the correct path). Drop the `extra_artifacts` claim from T015 and
  plan L310; reserve `extra_params` (T015) for the scalar run params only.
- **RESOLVED (2026-07-01):** accepted as recommended. T013 uses `mlflow.log_artifacts` inside
  `optimize()`; strike the tenant-dir-via-`extra_artifacts` wording from T015 and plan L310.

**F-003 — The three default effort knobs are mutually inconsistent: `metric_call_budget=100`
with full-train attribution per variant makes `variant_budget=8` effectively unreachable.**
- **Location:** plan.md L107–108 (defaults `variant_budget=8`, `metric_call_budget=100`,
  `val_gate_size=8`), L245 ("per_variant_eval_size (train) → full train split; attribution
  rollout scores every train case"), L219–222, L343–344 (R5); tasks.md T011 L97–99; spec.md FR10
  L129–135, NFR3 L172–177, EC8 L294.
- **Issue:** Each variant costs roughly `len(train)` task+judge calls for attribution **plus**
  `val_gate_size` calls for the gate, and the plan states all of these count against
  `metric_call_budget` (Q2 / spike-findings: "cap … per-variant attribution scoring + per-variant
  val-gate scoring at `metric_call_budget`"). For any realistic train split (tens of cases),
  `len(train) + 8` exhausts a 100-call budget after ~2–3 variants, so the loop is almost always
  metric-call-bound and `variant_budget=8` is dead — yet the spike frames `variant_budget` as the
  *primary* effort knob and numeric stand-in for FAPO's plateau (plan L243). Additionally, the
  loop re-runs `_attribute(best_instr, train)` every iteration (plan L116), but `best_instr` only
  changes when a variant is *accepted*; across rejected variants the failure cluster is identical,
  so attribution is recomputed needlessly, multiplying the budget pressure. No caching is
  specified.
- **Impact:** The recorded effort knobs (FR10/NFR3) misrepresent what actually bounds the run;
  cross-technique cost comparability (R4) is built on numbers that don't bind. First runs may stop
  after 2 variants regardless of `--variant-budget`.
- **Recommendation:** Reconcile the defaults — e.g. lower the per-variant attribution cost
  (subsample attribution like TextGrad's batch, or cache attribution while `best_instr` is
  unchanged so only accepted variants re-roll), or raise/relabel `metric_call_budget` so the two
  knobs can both be reached, or document explicitly that `metric_call_budget` is the binding knob
  and `variant_budget` is a safety cap. State the attribution-caching decision in T011.
- **RESOLVED (2026-07-01): cache attribution across rejected variants.** `_attribute` is memoized
  on `best_instr`: it re-rolls the full train split only after a variant is *accepted* (best
  changed); rejected variants reuse the cached failure cluster, since `best_instr` is unchanged and
  attribution would be identical. This drops per-variant cost to ~`val_gate_size` (the gate) once
  the initial/accepted attribution passes are paid, so `variant_budget=8` becomes reachable within
  `metric_call_budget=100`. `metric_call_budget` remains the hard stop (EC8); the two full-val
  passes stay outside it. **Action:** specify the memo-on-`best_instr` behavior and its budget
  accounting in T011 (attribution calls counted only on baseline + each acceptance).

**F-004 — T006 prescribes unit tests but the repo has no test infrastructure or convention.**
- **Location:** tasks.md T006 L54–59 ("Unit tests … `tests/.../test_fapo_review.py` or repo test
  convention"); plan.md L293–297 ("a … module with unit tests … the cheapest thing to test
  first").
- **Issue:** There is no `tests/` directory, no `test_*.py`, and no `[tool.pytest]` / pytest
  dependency anywhere in the repo (verified). The sibling SkillOpt work did **not** add a
  unit-test task; it verified behavior with a script (`scripts/verify_skillopt_sc5.py`, cf.
  SkillOpt T023). T006 as written ("or repo test convention") points at a convention that does not
  exist, so it is not actionable and risks scope creep (standing up pytest infra) that no other
  phase accounts for.
- **Impact:** The one place the plan asks for tests cannot be executed against an established
  harness; an implementer must either invent infra (unbudgeted) or silently skip the task.
- **Recommendation:** Decide one convention and state it: either (a) add a small explicit setup
  task (pytest dev-dependency + `tests/` + `[tool.pytest.ini_options]`) that T006 depends on, or
  (b) follow the existing `scripts/verify_*.py` pattern the harness already uses, and reword T006
  to "a deterministic `scripts/verify_fapo_review.py` exercising each violation class." The pure,
  network-free nature of `review.py`/`score.py`/`compare.py` (T002–T005) makes either viable.
- **RESOLVED (2026-07-01): drop the tests.** T006 is removed; no unit-test or verify-script task
  for the vendored primitives. Consequence to accept: `review.py`/`score.py`/`compare.py` get no
  isolated verification — their correctness is only exercised end-to-end via the SC5 failure run
  (T022) and the SC2/SC2a comparison run (T024). Given they are small pure functions this is a
  deliberate, low-cost-of-error trade; if a review-guardrail regression later slips through, revisit.
  **Action:** delete T006 and renumber (or mark it dropped); Phase 2 becomes review + score wrapper
  only.

### 🟢 LOW

**F-005 — Keep-best tie handling is unspecified and diverges from the TextGrad sibling, with
plateau consequences.**
- **Location:** plan.md L122 (`if gate > best_gate: … keep … else: plateau++`); evidence
  `textgrad_optimizer.py:646` (`if gate >= best_gate:` — keeps on a tie); spec.md FR13 L147–150,
  SC2a L257, EC8 L294.
- **Issue:** The plan's strict `>` means a variant that *ties* the current best is treated as a
  non-improvement and increments the plateau counter, so three consecutive ties trigger the
  `plateau_patience` early stop (EC8) even though nothing regressed. TextGrad uses `>=`. The
  divergence is plausibly deliberate (FAPO's "3 consecutive no-improvement" is literally
  no-*improvement*), but it is unstated, and tie-frequency is high under a binary {100,0}-derived
  pass-rate gate on a small `val_gate_size`.
- **Recommendation:** State the tie rule explicitly in T011 and the plan loop, and confirm it is
  intended that ties count toward plateau. Note the final return decision uses strict
  `final_eval_score > initial_eval_score` (plan L127, EC2) consistently — only the per-variant gate
  comparison is in question.
- **RESOLVED (2026-07-01):** keep strict `>` (a tie is a non-improvement and counts toward
  plateau), faithful to FAPO's native "3 consecutive no-improvement" stop; state it in T011.

**F-006 — Optimizer model string convention for the `make_client` (raw OpenAI) call is
unspecified; a `openai/` prefix would break `_propose`.**
- **Location:** tasks.md T007 L69–71, T009 L78–83; plan.md L74–79, L256–259; evidence
  `skillopt_optimizer.py:383` (`self.optimizer_model.removeprefix("openai/")` before sending to
  the OpenAI-compatible endpoint), `textgrad_optimizer.py:543–545` (passes the raw model string to
  `ChatExternalClient`).
- **Issue:** `_propose` calls the optimizer via `make_client(optimizer_endpoint)`, which returns a
  raw `openai.OpenAI` client; the model name is sent as `model=` to `chat.completions.create`. A
  litellm-style `--optimizer-model openai/<name>` (the prefix the *task* role keeps for the
  litellm path) would 404 on the raw client. SkillOpt strips it; FAPO's tasks/plan do not say
  whether the optimizer model carries a prefix.
- **Recommendation:** Specify in T009/T014 the model-string convention for the make_client path
  (strip `openai/`, mirroring `skillopt_optimizer.py:383`), so the task role (litellm, prefix
  kept) and the optimizer role (raw client, prefix stripped) are handled consistently.
- **RESOLVED (2026-07-01):** accepted — strip a leading `openai/` on the optimizer model before
  the raw `make_client` call, per `skillopt_optimizer.py:383`; note it in T009.

**F-007 — Two task-model invocation paths (attribution vs. gate) must be pinned to the same
model/sampling to keep FR11/NFR2 on one axis.**
- **Location:** tasks.md T008 L72–77 (attribution via `build_completion_kwargs` +
  `completion_with_retry`), T010 L84–89 (gate/endpoints via `eval_fn`); plan.md L66–73; spec.md
  FR11 L136–142, NFR2 L165–171; cf. the analogous SkillOpt finding (`skillopt …/analyze.md`
  F-001).
- **Issue:** Attribution rolls out the task model through the project litellm path directly,
  while `_val_score`/`_gate_score` score through `eval_fn` (the `create_optimizable_predict_fn`
  the CLI passes to `optimize_prompts`). Both use the *shared judge object*, so the judge is
  identical (good, FR11). But the **task model + sampling** now flow through two construction
  sites — `FapoPromptOptimizer(task_model, task_endpoint, task_sampling_params)` and the separate
  `predict_fn` built in `train_common.py:120`. If the CLI does not source both from the same
  `--model`/`--endpoint` and pass the same sampling params, the failure partition (attribution)
  and the reported metric (gate) sample differently.
- **Recommendation:** In T014, source the optimizer's `task_model`/`task_endpoint`/
  `task_sampling_params` from the same `--model`/`--endpoint` (and `read_sampling_params("LLM")`)
  that back `optimize_prompts`'s `predict_fn`, and note the single-axis invariant in the plan
  (as TextGrad relies on implicitly).
- **RESOLVED (2026-07-01):** accepted — T014 sources both task paths from one `--model`/`--endpoint`
  + shared sampling params; note the single-axis invariant in T014/plan.

**F-008 — SC4 (no regression) has no explicit regression-run task; it rests on "no shared edits."**
- **Location:** tasks.md T024 L169–177 ("confirm the existing three techniques are unchanged"),
  no analogue of SkillOpt's dedicated regression task (SkillOpt T009); plan.md L140–145, L367;
  spec.md SC4 L262–263, FR8 L116–118.
- **Issue:** Unlike the SkillOpt feature (which *extracted* shared helpers and therefore needed a
  TextGrad regression run), FAPO is purely additive (new module + new CLI; `_run_optimization`,
  `prompt_skill.py`, and the other optimizers reused unchanged). SC4 is thus largely structural —
  but T024 only validates it "by inspection," and F-002 shows a latent temptation to edit the
  shared `_run_optimization`. There is no task asserting the shared files are byte-unchanged.
- **Recommendation:** Add a one-line check (or fold into T023/T024) that `train_common.py`,
  `prompt_skill.py`, `harness.py`, and the three existing optimizers have no FAPO-driven diff
  (`git diff --stat` over those paths empty for this feature), making FR8/SC4 a verified gate
  rather than an assumption.
- **RESOLVED (2026-07-01):** accepted — fold a `git diff --stat` no-shared-edit check into T024.

## Metrics Summary

- **Requirements covered (FR+NFR):** 19/19 traced to tasks. 8 carry caveats — FR14/SC7/EC6 the
  HIGH contradiction (F-001); FR8/SC4 (F-008); FR9/FR11/NFR2 (F-006/F-007); FR10/NFR3/EC8 (F-003);
  NFR5/US3 (F-002); FR13/SC2a (F-005).
- **User stories covered:** 4/4.
- **Edge cases covered:** 9/9 (EC6 carries the F-001 contradiction; EC8 carries F-003/F-005).
- **Success criteria covered:** 8/8 (SC7 carries F-001; SC4 carries F-008; SC2a carries F-005).
- **Clarifications:** Q1–Q6 all resolved (spike-findings.md); resolutions flow into the plan.
- **Findings by severity:** 🔴 1 · 🟡 3 · 🟢 4 (8 total).
- **Coverage gaps (missing tasks):** 0. All findings are contradiction / feasibility /
  under-specification, not absent coverage.

## Suggested Next Actions (priority order)

**Status (2026-07-01): all 8 findings resolved; resolutions applied to `plan.md` and `tasks.md`
(spec.md wording left to the user for FR14/EC6, F-001).** The decisions:

1. **F-001 (resolved):** intrinsic leakage check — `review.py` is a pure function of the variant
   text, given no dataset cases; SC7 contradiction removed. Applied to T005/T020, plan L45/L119/L138/L239/L301.
2. **F-002 (resolved):** tenant dir captured via `mlflow.log_artifacts` inside `optimize()` (T013);
   `extra_artifacts` tenant claim struck from T014/T015 and plan L316/L320.
3. **F-003 (resolved):** attribution cached on `best_instr` (re-rolls only on baseline + each
   acceptance); `metric_call_budget` is the hard cap. Applied to T011 and plan L117/L236/L248/L353.
4. **F-004 (resolved):** tests dropped; T006 retired (IDs kept stable). Applied to T006, Phase-2
   heading, summary, plan phase-2 item.
5. **LOW (resolved):** strict-`>` tie rule (F-005, T011/plan L123); strip `openai/` on the
   optimizer model (F-006, T009/plan L262); single task-model axis (F-007, T014/plan phase 4);
   `git diff --stat` no-shared-edit gate for SC4 (F-008, T024).

No open items remain; ready for implementation starting at T001.
