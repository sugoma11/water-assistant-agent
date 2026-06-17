# Reflection on analyze.md — TextGrad MLflow Integration

Each finding from `analyze.md` is researched against source, decided (with user
direction where given), and turned into surgical edits to spec/plan/tasks.

User direction received:
1. (F-001) Yes — rely on GEPA's internal evals; the TextGrad adapter must do the same.
2. (F-002) Rename metrics to be coherent; **GEPA is the target**.
3. (Medium, F-003/F-004) Use the notebook as the working example.
4. (Low, F-007) train/val/test split must be the same as GEPA — same seed, same val — for comparability.

---

## F-001 — Shared routine would change GEPA's logged metrics (HIGH)

**Research.** `train.py:248-262` shows GEPA logs only `test_quality_{before,after}`; it reads
`result.initial_eval_score`/`final_eval_score` but does **not** re-log them. The GEPA optimizer
itself logs per-iteration `eval_score` (`gepa_optimizer.py:267-271`, `step=iteration`) and returns
`initial/final_eval_score` in `PromptOptimizerOutput` (`gepa_optimizer.py:415-418`); `optimize.py:250-253`
surfaces those on the result. `optimize.py` does **not** log `val_quality_*` — that name was invented
by the plan. Valid finding.

**Decision (per user #1).** GEPA stays the target. The shared `_run_optimization` does **not** log
`val_quality_*` and runs **no** val-after eval phase. It reads `result.initial_eval_score`/
`final_eval_score` for the summary only; the per-epoch val progression is logged by the optimizer as
`eval_score` (step=epoch), exactly as GEPA does. `TextGradPromptOptimizer.optimize()` returns
`initial/final_eval_score` so the same machinery applies. This keeps T004/T007's "identical logged
params/metrics" achievable.

**Artifact changes.** plan §Architecture diagram, §"Shared routine"; tasks T005.

## F-002 — "Same metric names" contradicted by GEPA's actual names (HIGH)

**Research.** Same evidence as F-001. GEPA's comparable metrics are `eval_score` (+ `eval_score.<scorer>`,
step series), scalar `initial_eval_score`/`final_eval_score`, and the hand-logged
`test_quality_{before,after}`. There is no `val_quality_*`. Valid finding.

**Decision (per user #2 — GEPA is target).** Drop `val_quality_*` everywhere. Canonical comparable
metrics on both paths: val = optimizer-logged `eval_score` progression + `initial/final_eval_score`;
test = `test_quality_{before,after}`. Spec SC2 clarified to name them; plan/tasks renamed.

**Artifact changes.** spec SC2; plan §Architecture, §"Shared routine", Testing Strategy "Shared judge";
tasks T005, T013, T023.

## F-003 — Conflicting optimizer-engine implementations (MEDIUM)

**Research.** Plan §Architecture/§Interfaces and tasks T010 specify a bespoke `CustomLiteLLMEngine`
using `build_completion_kwargs`. The notebook (cited reference) instead uses
`SchemaInjectingEngine(ChatExternalClient)` for the task role and a plain `ChatExternalClient` for the
backward role, each built from `OpenAI(base_url, api_key)` via `make_client(endpoint)` reading
`ENDPOINTS[endpoint]` (notebook cell `cd09168b`). Only this path actually solves R3 for per-role
endpoints. Valid finding.

**Decision (per user — use the notebook).** Replace `CustomLiteLLMEngine`/`build_completion_kwargs`
with the notebook's `ChatExternalClient` + `SchemaInjectingEngine` + `make_client` wiring. R3 mitigation
restated accordingly.

**Artifact changes.** plan §Architecture, §Interfaces "Optimizer-model engine", §Risks R3; tasks T010.

## F-004 — Plan's "primary" gradient approach isn't the proven one (MEDIUM)

**Research.** Plan §"TextGrad-internal mechanism" lists direct `system_prompt.gradients` attachment as
primary and `TextLoss` as fallback. The notebook proves the **opposite**: a judge-as-`TextLoss` bridge
(`JUDGE_LOSS_TEMPLATE` → `tg.TextLoss(eval_instruction)(response)` → `tg.sum(losses).backward()` →
`optimizer.step()`, cells `907c3516`/`9b11cc85`). Tasks T002 already pins TextLoss as primary, so the
plan is the outlier. Valid finding.

**Decision (per user — use the notebook).** Make the judge-as-`TextLoss` bridge the primary approach in
the plan; demote direct `.gradients` attachment / autograd `Function` to fallback. Note: the notebook
loop is **iteration-based**; the port must adapt it to the **epoch-based** loop FR10/C1 require.

**Artifact changes.** plan §"TextGrad-internal mechanism"; tasks T011 (epoch adaptation + TextLoss).

Also surfaced while researching the notebook: it optimizes **only the instruction block**
(`SYSTEM_PROMPT_TEMPLATE.split("Schema:")[0]`), with the schema injected at call time, not the full
`{schema}`-placeholder template the plan's Data Model assumes. For FR6 parity the optimized instruction
block must be recombined into the full template before registration.
→ plan §"Data Model / Entities" (Optimizable prompt); tasks T012.

## F-005 — No existing test layout to mirror (MEDIUM)

**Research.** No `tests/` tree and no test runner in `pyproject.toml` (verified). "mirror existing test
layout" has no referent. Valid finding.

**Decision.** Create a `tests/` tree using pytest; put the unit tests at
`tests/text2sql/test_textgrad_optimizer.py`; add `pytest` as a dev dependency.

**Artifact changes.** tasks T021, T022.

## F-006 — OPTIMIZER_* would leak onto GEPA runs via shared log_global_params (MEDIUM)

**Research.** `log_global_params` is shared and called by GEPA (`harness.py:702-726`, `train.py:197`).
Adding `read_sampling_params("OPTIMIZER")` there (T008) logs optimizer params on GEPA runs too —
another way T007's identical-params check breaks, and inconsistent with the F-001 "GEPA unchanged"
decision. Valid finding.

**Decision.** Log `OPTIMIZER_*` on the TextGrad run only (via `train_textgrad`/`extra_params`), not in
the shared `log_global_params`.

**Artifact changes.** plan §Interfaces "Optimizer-model engine"; tasks T008, T015.

## F-007 — EC3 scope + split comparability (LOW)

**Research.** spec EC3 names only the training split; plan/tasks already guard train+val (T018). The
notebook splits with the same seeded `split_dataset(data, SEED, cache_path=EMBED_CACHE)` as GEPA
(notebook cell `1c86bdb9`), and plan.md:79-81 already states identical seed/cache. Valid finding.

**Decision (per user #4).** Extend EC3 to the validation split and state explicitly that the
train/val/test split is the same seeded `split_dataset` split GEPA uses (same val), so keep-best stays
comparable.

**Artifact changes.** spec EC3; tasks T018.

## F-008 — Reference notebook uncommitted / in flux (LOW)

**Research.** git status shows `notebooks/textgrad_prompt_opt.ipynb` modified and `tasks.md` untracked.
Tasks depend on the notebook as the "working train loop." Valid finding.

**Decision.** Keep the notebook as the reference (consistent with user direction) and commit it at the
spike-validated state so it is version-pinned.

**Artifact changes.** tasks Phase 1 note.

## F-009 — OPTIMIZER_TOP_K parity (LOW)

**Research.** `.example.env` `JUDGE_*`/`LLM_*` blocks always include `*_TOP_K`; T009 marked it optional.
Valid, trivial.

**Decision.** Include `OPTIMIZER_TOP_K` by default for parity.

**Artifact changes.** plan §Interfaces; tasks T009.

---

## Summary of artifact changes

| Finding | spec.md | plan.md | tasks.md |
|---|---|---|---|
| F-001 | — | Architecture diagram, "Shared routine" | T005 |
| F-002 | SC2 | Architecture, "Shared routine", Testing "Shared judge" | T005, T013, T023 |
| F-003 | — | Architecture, Interfaces "Optimizer-engine", Risks R3 | T010 |
| F-004 | — | "TextGrad-internal mechanism", Data Model | T011, T012 |
| F-005 | — | — | T021, T022 |
| F-006 | — | Interfaces "Optimizer-engine" | T008, T015 |
| F-007 | EC3 | (already states same seed) | T018 |
| F-008 | — | — | Phase 1 note |
| F-009 | — | Interfaces "Optimizer-engine" | T009 |
