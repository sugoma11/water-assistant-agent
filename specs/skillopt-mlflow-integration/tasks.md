# Tasks: SkillOpt as a Prompt-Optimization Technique in MLflow

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md)

Conventions: `[P]` = parallelizable with sibling `[P]` tasks (different files, no
shared edits). Each task names a concrete file/artifact target. Phases are ordered so
earlier tasks unblock later ones; do not start a phase until its predecessor is green.

User stories (from spec): **US1** select SkillOpt & optimize the prompt · **US2** run
SkillOpt and GEPA/TextGrad on the same split for fair comparison · **US3** reviewer
reads technique/before-after/prompt from tracking UI · **US4** clear failure on error.

---

## Phase 1 — Setup & Spike (blocks everything)

- [x] T001 Add `skillopt` (microsoft/SkillOpt, Python ≥3.10) to `[project.dependencies]`
  in `pyproject.toml`; resolve it on Python 3.13 alongside `gepa`, `textgrad`, `mlflow`,
  `litellm` (`uv sync`). Pin a compatible version or isolate on conflict (R2). Record the
  resolved version in the spike notebook.
- [x] T002 Create the Phase-1 spike notebook `notebooks/skillopt_prompt_opt.ipynb`: build
  a minimal `cfg: dict` + a throwaway `Text2SqlEnvAdapter`, construct
  `skillopt.engine.trainer.ReflACTTrainer(cfg, adapter)` and run `.train()` **in-process**
  on a handful of records against a real blablador/kisski endpoint. Confirms the in-process
  path of R1. While running, observe and record two cfg-semantics assumptions: that
  `learning_rate` **caps the number of edits applied per round** (FR13, F-002) and that
  `batch_size`/`train_size` yields a **full-pass epoch** (Q1, F-006). (depends: T001)
- [x] T003 Spike seam 1 — pin **per-role OpenAI-compatible endpoint config** in
  `notebooks/skillopt_prompt_opt.ipynb`: confirm `optimizer` and `target` backends
  (`openai_chat`) accept **independent** base_url/api_key/model via the per-role endpoint
  overrides against the project endpoints. Record the exact cfg keys; if overrides are not
  honored, record the fallback (optimizer-only on SkillOpt's layer, task role via adapter
  rollout). (depends: T002)
- [x] T004 Spike seam 2 — pin **rollout-owns-task-and-judge** in the spike notebook:
  confirm `EnvAdapter.rollout` can run the task model via the project litellm `predict_fn`
  and score via the shared FLEX judge (not SkillOpt's `chat_target`/built-in scorers), and
  that `hard`/`soft` drive both the success/failure partition and the val gate. Record the
  `rollout` result-dict shape (`id`, `hard`, `soft`, rationale). (depends: T002)
- [x] T005 Spike seam 3 — pin the **success-reflection toggle cfg key** and the
  **`history.json` val-score series** in the spike notebook: identify the config key that
  turns success reflection on/off (FR11, Q3) and confirm `history.json` exposes per-step/
  epoch `current_score`/`best_score` so the `eval_score` progression can be logged under
  GEPA's metric names. Record **what one `history.json` row represents** and the explicit
  **row→epoch mapping** (F-003). Confirm the `history.json` val score **agrees with an
  `eval_fn` val call** on the same candidate + same val split so the series and the
  `initial/final_eval_score` endpoints share one axis (F-001); if it disagrees, source the
  series from per-epoch `eval_fn` `_val_score` (TextGrad parity). Record the fallback
  (initial/final only) if granularity is poor (R6). (depends: T002)
- [x] T006 Record spike decisions in a short `spike-findings.md` in the spec dir (preferred
  over editing the approved `plan.md`, F-008): the confirmed cfg keys for roles/endpoints, the
  rollout wiring, the success-reflection key, the `history.json` series + row→epoch mapping and
  the history-val↔`eval_fn` agreement result (F-001/F-003), the `learning_rate` edit-cap and
  full-pass `batch_size` findings (F-002/F-006), the constant-LR decision (OQ1:
  `min_learning_rate == learning_rate`, `batch_size` = full train size, `eval_test=false`,
  `use_gate=true`), and which fallbacks (if any) are taken — including the subprocess-fallback
  decision if the in-process `train()` path proves impossible (R1, F-007).
  (depends: T003, T004, T005)

## Phase 2 — Shared helper extraction (guards FR8 for TextGrad)

- [x] T007 Create `src/experiments/text2sql/prompt_skill.py` and move `_instruction_block`,
  `_recombine`, `SCHEMA_MARKER`, `make_client`, and `MIN_SPLIT_SIZE` out of
  `textgrad_optimizer.py` into it (pure extraction, no behavior change). (depends: T001)
- [x] T008 Update `src/experiments/text2sql/textgrad_optimizer.py` to import the lifted
  helpers from `prompt_skill.py`; delete the now-duplicated definitions. (depends: T007)
- [x] T009 Regression-check TextGrad after extraction: run a small `text2sql-train-textgrad`
  optimization (or its existing smoke path) and confirm the run records and registered
  prompt are unchanged vs. before extraction (FR8, SC4, NFR4). (depends: T008)

## Phase 3 — Core: adapter + optimizer (US1, US2, US3) — depends on Phases 1–2

- [ ] T010 Implement `Text2SqlEnvAdapter(skillopt.envs.base.EnvAdapter)` in new
  `src/experiments/text2sql/skillopt_optimizer.py`: `build_train_env` / `build_eval_env`
  wrapping our train/val records into SkillOpt's `env_manager` shape, and `get_task_types`
  → `["text2sql"]`. Use the seams pinned in T003–T005. (depends: T006, T008)
- [ ] T011 Implement `Text2SqlEnvAdapter.rollout(env_manager, skill_content, out_dir)` in
  `skillopt_optimizer.py`: for each item call the project litellm `predict_fn` with
  `system=_recombine(skill_content)`, score with the shared `build_sql_judge_scorer`, set
  `hard = 1.0/0.0` from the judge verdict, `soft = hard`, stash the judge rationale; guard
  EC4 — on judge error **raise**, never default a grade (FR11, A3). (depends: T010)
- [ ] T012 Implement the `cfg` builder in `SkillOptPromptOptimizer.__init__`/`optimize`
  (`skillopt_optimizer.py`) mapping spec knobs → SkillOpt cfg: `epochs→num_epochs`,
  `edit_budget→learning_rate` (constant: `lr_scheduler` const + `min_learning_rate ==
  learning_rate`, OQ1), `minibatch_size→minibatch_size`, `batch_size`=full train size,
  `use_gate=true`, `eval_test=false`, `seed`/`split_seed`=`sampler_seed`, success-reflection
  toggle (key from T005). Roles/endpoints set from `ENDPOINTS[...]` via `make_client`
  resolution — project endpoints only, never a library default (Q4, A2). (depends: T010)
- [ ] T013 Implement `SkillOptPromptOptimizer(BasePromptOptimizer).optimize(eval_fn,
  train_data, target_prompts, enable_tracking)` in `skillopt_optimizer.py`: assert exactly
  one target prompt; seed skill = `_instruction_block(seed_template)` written to a tmp
  `skill_init.md`; compute `initial_eval_score` via `eval_fn` on the val set; run
  `ReflACTTrainer(cfg, adapter).train()` inside a `tempfile.TemporaryDirectory()` `out_root`
  (no repo `outputs/`); read `best_skill.md`; compute `final_eval_score` via `eval_fn`;
  return `PromptOptimizerOutput(optimized_prompts={name: best}, initial_eval_score,
  final_eval_score, *_per_scorer)`. (depends: T011, T012)
- [ ] T014 Log the per-epoch val series in `optimize()`: read `history.json`, emit the
  `eval_score` / `eval_score.<scorer>` progression at `step=epoch` under GEPA's metric names
  plus `initial_eval_score` / `final_eval_score` on the same run (SC2). The series source MUST
  sit on the same `eval_fn` axis as the `initial/final_eval_score` endpoints per the T005
  agreement check (F-001). Apply the T005 fallback (per-epoch `eval_fn` series, or
  initial/final only) if granularity is insufficient or the axes disagree, noting the
  limitation. (depends: T013)
- [ ] T015 Recombine + keep-best honesty in `optimize()`: when `final_eval_score >
  initial_eval_score`, return `_recombine(best_skill)`; otherwise return
  `target_prompts[name]` **byte-for-byte** so `register_prompt_if_changed` dedups and no
  spurious version is registered (EC2). The keep/return decision is made on the `eval_fn`
  `final_eval_score` (the same axis as the logged endpoints, F-001/F-004). Verify the fixed
  schema context survives in the recombined template (FR6, A6, SC6). (depends: T013)

## Phase 4 — CLI & MLflow wiring (US1, US3) — depends on Phase 3

- [ ] T016 Implement `src/experiments/text2sql/train_skillopt.py` with `train_skillopt`
  (click command) mirroring `train_textgrad.py`: options `--questions-path`/`--schema-path`/
  `--db-path`, `--model`/`--endpoint` (task), `--judge-model`/`--judge-endpoint` (shared
  judge), `--optimizer-model`/`--optimizer-endpoint` (SkillOpt layer), required-no-default
  `--epochs`/`--edit-budget`/`--minibatch-size`, `--reflect-on-success/--no-reflect-on-success`
  (default off), `--sampler-seed` (default 42), `--use-prod-questions`. Build the optimizer,
  call the shared `_run_optimization(optimizer, technique="skillopt", extra_params=…)`.
  (depends: T015)
- [ ] T017 Populate `extra_params` in `train_skillopt.py` so the run records: the three model
  roles + their endpoints, `epochs`, `edit_budget`, `minibatch_size`, `reflect_on_success`,
  `sampler_seed`, split sizes, and the `OPTIMIZER_*` sampling params via
  `read_optimizer_params_for_logging()` — logged **on the SkillOpt run only** (not in
  `log_global_params`), so GEPA/TextGrad runs stay byte-identical (FR5, FR10, NFR3, FR8).
  (depends: T016)
- [ ] T018 [P] Add the entry point `text2sql-train-skillopt =
  "experiments.text2sql.train_skillopt:train_skillopt"` to `[project.scripts]` in
  `pyproject.toml`. (depends: T016)
- [ ] T019 [P] Add a `text2sql-train-…-skillopt` recipe to `experiments.just` parallel to
  the GEPA one (student-judge-optimizer-technique naming), forwarding all required options.
  (depends: T016)

## Phase 5 — Error handling (US4, EC1–EC6) — depends on Phase 4

- [ ] T020 EC3 split guard in `SkillOptPromptOptimizer.optimize`: refuse empty/too-small
  train **or** val split up front (before any LLM call or trainer construction) with a clear
  message, reusing the `MIN_SPLIT_SIZE` pattern from `prompt_skill.py`. (depends: T013)
- [ ] T021 EC1/EC4/SC5 failure propagation: ensure an unreachable optimizer/target endpoint
  or a judge failure raises so `mlflow.start_run` marks the run **FAILED** with no optimized
  prompt reported; add friendly errors (not bare `KeyError`) when a role's `*_API_BASE` /
  `*_API_KEY` env var is unset (TextGrad retrospective finding #4). (depends: T017, T011)
- [ ] T022 EC5/EC6 robustness in `optimize()`: a round with no usable edits is left to
  SkillOpt's native keep-best (no malformed edit applied); if `best_skill.md` is missing/
  unreadable or the prompt fails to save/version, fail the run with a clear, actionable
  message (EC6). (depends: T015)
- [ ] T023 Verify SC5 end-to-end: deliberately induce a SkillOpt failure (e.g. unreachable
  optimizer endpoint) via the CLI and confirm a clear error + a run marked failed with no
  optimized result. (depends: T021, T022)

## Phase 6 — Docs, env defaults & comparison validation

- [ ] T024 [P] Document the new technique in `README.md` (how to select SkillOpt, the three
  roles, the effort knobs); extend `.example.env` only if the spike (T006) added
  SkillOpt-specific env vars beyond the shared `OPTIMIZER_*`. (depends: T017)
- [ ] T025 Validate US2/US3/SC2/SC3 end-to-end: run `text2sql-train-skillopt` and an existing
  technique (GEPA or TextGrad) on the same dataset, split seed, and judge; confirm the
  tracking UI shows for SkillOpt the technique name, three roles, effort knobs, before/after
  quality on validation (`eval_score` progression + `initial/final_eval_score`) and test
  (`test_quality_{before,after}`), the retrievable optimized-prompt artifact, and that the
  records sit side-by-side on identical metric names with no `val_quality_*` introduced.
  (depends: T023, T024)
- [ ] T026 Run the retrospective skill to review all implemented changes for code quality and
  architectural decisions (`specs/skillopt-mlflow-integration/retrospective.md`).
  (depends: T025)

---

## Summary

- **Total tasks:** 26
- **Phases:** Setup/Spike (T001–T006) → Helper extraction (T007–T009) → Core adapter/
  optimizer (T010–T015) → CLI & MLflow wiring (T016–T019) → Error handling (T020–T023) →
  Docs & comparison validation (T024–T026).
- **Per-story coverage:** US1 (select & optimize) T010–T018; US2 (fair comparison) T012,
  T017, T025; US3 (reviewer reads tracking) T014, T017, T025; US4 (clear failure) T020–T023.
- **Parallel opportunities:** spike seams T003/T004/T005 after T002; T018/T019 after T016;
  T024 alongside Phase-5 hardening. Phase 2 (T007–T009) can run alongside the Phase-1 spike
  since it only touches TextGrad/shared helpers.
- **Critical path:** T001 → T002 → (T003–T005) → T006 → T010 → T011/T012 → T013 → T015 →
  T016 → T017 → T021/T022 → T023 → T025 → T026.
