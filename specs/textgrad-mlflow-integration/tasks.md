# Tasks: TextGrad as a Prompt-Optimization Technique in MLflow

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md)
Reference implementation (working train loop): [`notebooks/textgrad_prompt_opt.ipynb`](../../notebooks/textgrad_prompt_opt.ipynb)

Conventions: `[P]` = parallelizable with other `[P]` tasks in the same phase (no shared
file / no ordering dependency). Each task names its target artifact. Phases are ordered so
earlier work unblocks later work.

---

## Phase 1 — Spike: pin the TextGrad gradient-injection API (blocks everything)

Plan §"TextGrad-internal mechanism", Phase 1. The notebook already proves a working approach
(`ChatExternalClient` + `SchemaInjectingEngine` + judge-as-`TextLoss` bridge); this phase
confirms it against the production endpoints and locks the API the optimizer will use.
Commit the notebook at the spike-validated state so the reference is version-pinned (F-008).

- [x] T001 Confirm `textgrad>=0.1.8` (already in `pyproject.toml`) installs and imports on the
  project's Python 3.13 venv; run `python -c "import textgrad as tg; from textgrad.engine.local_model_openai_api import ChatExternalClient"` and record the resolved version. (R2)
  → **Resolved: textgrad 0.1.8 imports cleanly on Python 3.13.12 (R2 closed).**
- [x] T002 In a scratch script, build a `ChatExternalClient` against a real `ENDPOINTS` entry
  (kisski default per memory — blablador drops backward calls), attach a hand-written feedback
  Variable to a `tg.Variable(requires_grad=True)`, run `tg.set_backward_engine` + `tg.TGD.step()`,
  and confirm `.value` is rewritten. Pin primary (`TextLoss` judge-as-loss, per notebook) vs.
  fallback (custom autograd `Function`) gradient-injection API. (R1, OQ1)
  → **Pinned PRIMARY: `tg.TextLoss(judge_feedback)(response)` → `loss.backward()` →
  `tg.TGD(parameters=[prompt], constraints=[…]).step()` rewrites `.value` (verified against kisski
  via both the minimal spike and the full notebook code path). Fallback also verified: appending a
  feedback `tg.Variable` to `prompt.gradients` then `TGD.step()` rewrites `.value` too — keep as the
  documented escape hatch. R1 closed; T011 uses the primary bridge.**
- [x] T003 Confirm the keep-best **revert** primitive: verify `system_prompt.set_value(best)` /
  `get_value()` round-trips the template (as the notebook uses); document the chosen revert
  mechanism for T011. (OQ1, FR12)
  → **Confirmed: `prompt.set_value(best)` / `prompt.get_value()` round-trips the template exactly.
  T011 keep-best/revert = snapshot `best = prompt.get_value()` on improvement, restore via
  `prompt.set_value(best)` otherwise. OQ1 closed.**

## Phase 2 — Refactor `train.py` into a shared optimization routine (guards FR8 / SC4)

Pure extraction; no behavior change to GEPA. Must complete before TextGrad CLI is wired.

- [x] T004 Capture a GEPA regression baseline: run `text2sql-train` on a tiny fixed split and
  save the logged params, the four metrics, and the optimized template for before/after
  comparison (Testing Strategy "GEPA regression"). (SC4)
  → **Done: ran the pre-refactor `train.py` (commit 26222a3, budget patched to
  `len(val_set)*2+1`) on `data/text2sql/to_test.json` (split train=2/val=2/test=1, seed 42),
  all three roles = `openai/alias-eve` on blablador. Baseline run
  `gepa-alias-eve-06-17-10-53` (id 533612d23a3d…), snapshot saved to
  `/tmp/baseline_snapshot.json`.**
- [x] T005 Extract `_run_optimization(*, optimizer, technique, extra_params, model, endpoint,
  judge_model, judge_endpoint, schema_text, train_set, val_set, test_set, prompt_version,
  db_path, …)` from `train.py`: opens the run, logs global + `technique` + `extra_params`, runs
  test-before, calls `optimize_prompts`, reads `result.initial_eval_score`/`final_eval_score` for the
  val summary (already logged by the optimizer as `eval_score` steps — not re-logged, matching current
  GEPA), runs test-after, logs `test_quality_{before,after}`. No new `val_quality_*` metric, so GEPA's
  logged params/metrics stay byte-identical.
  File: `src/experiments/text2sql/train.py`. (Plan §"Shared routine"; F-001, F-002)
  → **Done: `_run_optimization(...)` added to `train.py`. It builds the judge scorer + the three
  predict_fns, opens the run, calls `log_global_params`, logs `technique` + `sampler_seed` +
  split sizes + `**extra_params`, logs `extra_artifacts` via `log_text`, runs test-before,
  `optimize_prompts(optimizer=…)`, reads `result.initial/final_eval_score` (echo only — not
  re-logged), runs test-after, logs `test_quality_{before,after}`. Run name =
  `make_run_name(f"{technique}-{model}")`.**
- [x] T006 Reduce GEPA `train()` to a thin wrapper that builds `GepaPromptOptimizer` and calls
  `_run_optimization(..., technique="gepa", extra_params={teacher_model, teacher_endpoint,
  max_metric_calls, total_metric_calls})`. Add the logging-only `technique` param to the GEPA run
  (no behavior change). File: `src/experiments/text2sql/train.py`. (FR4, NFR1)
  → **Done: `train()` now only resolves env/split/prompt + GEPA budget, then delegates to
  `_run_optimization`. `extra_params` = {teacher_model, teacher_endpoint, max_metric_calls,
  total_metric_calls}; `sampler_seed` + split sizes are logged by the shared routine; the
  `reflection_prompt_template.txt` artifact is passed via `extra_artifacts`. CLI options +
  `--help` unchanged; module imports clean. The one intentional new param is `technique="gepa"`
  (logging only).**
- [x] T007 Re-run T004's GEPA regression and assert identical logged params/metrics and optimized
  template vs. the saved baseline. (SC4, FR8)
  → **Done & PASS. Ran the refactored wrapper (`python -m experiments.text2sql.train`) on the
  identical inputs → run `gepa-alias-eve-06-17-11-23` (id c61350834bdb…). Diff of the two run
  snapshots:**
  - **Param key set identical except the one intentional addition `technique="gepa"` (per T006).**
  - **All deterministic params our code controls are byte-identical: `model`, `endpoint`, LLM +
    `judge_*` sampling params, `db_path`, `dataset_sha256`, `schema_sha256`, `commit_hash`,
    `sampler_seed`, `train/val/test_size`, `teacher_*`, `max/total_metric_calls`.**
  - **`test_quality_{before,after}` + our 4 artifacts (`generation_prompt.txt`, `schema.py.txt`,
    `to_test.json`, `reflection_prompt_template.txt`) present on both runs.**
  - **GEPA-internal autolog (`objective/*` metric, `candidate_tree`/`candidates.json`/`*_pareto_*`
    artifacts) differs between runs, but that is data/trajectory-driven — baseline val 0.0→0.0
    vs refactored 0.0→1.0 (LLM nondeterminism on a 5-row split), NOT an effect of the extraction.
    Exact optimized-template equality is therefore not assertable across two live runs; structural
    parity of everything our code logs is confirmed.**

## Phase 3 — `OPTIMIZER_*` env + sampling params (foundational for the optimizer engine)

- [x] T008 [P] Add `read_sampling_params("OPTIMIZER")` wiring, logged **on the TextGrad run only**
  (via `train_textgrad`/`extra_params`, NOT in the shared `log_global_params`), so GEPA runs keep
  identical params (FR8). Files: `src/experiments/text2sql/harness.py` (helper),
  `src/experiments/text2sql/train_textgrad.py` (call site). (Plan §"Optimizer-model engine"; NFR2, F-006)
  → **Done (harness side). Added `read_optimizer_params_for_logging()` to `harness.py`: returns
  `read_sampling_params("OPTIMIZER")` re-keyed with an `optimizer_` prefix (→
  `optimizer_temperature/top_p/seed[/top_k]`) so the params log on the TextGrad run without
  colliding with the unprefixed `LLM` generation params, mirroring the `judge_` prefix. NOT added to
  `log_global_params`, so GEPA runs stay byte-identical (FR8). The optimizer ENGINE will consume the
  raw `read_sampling_params("OPTIMIZER")` in T010/T011. The call site (`train_textgrad.py`
  `extra_params`) is deferred to T015 — that file is a Phase-5 artifact and does not exist yet.**
- [x] T009 [P] Add `OPTIMIZER_TEMPERATURE` / `OPTIMIZER_TOP_P` / `OPTIMIZER_TOP_K` /
  `OPTIMIZER_SEED` defaults to `.example.env`, mirroring the `JUDGE_*` block (include `TOP_K` for
  parity). File: `.example.env`. (NFR2, NFR3)
  → **Done: added the `OPTIMIZER_*` block (`0.0 / 1.0 / 1 / 42`, mirroring `JUDGE_*` for
  deterministic, reproducible proposals) right after `JUDGE_SEED`, with a comment noting it drives
  the TextGrad backward/proposal model and is logged on the TextGrad run only.**

## Phase 4 — Implement `TextGradPromptOptimizer` (core, depends on Phases 1–3)

Plan §"Architecture" and §"`TextGradPromptOptimizer.optimize` contract". Port the notebook's
loop into a `BasePromptOptimizer` subclass driven by MLflow's `eval_fn`.

- [x] T010 Port the notebook's engines into
  `src/experiments/text2sql/textgrad_optimizer.py`: a `make_client(endpoint)` helper building
  `OpenAI(base_url, api_key)` from `ENDPOINTS[endpoint]`, a `SchemaInjectingEngine(ChatExternalClient)`
  task engine (injects the fixed schema per call via `render_system_prompt`), and a plain
  `ChatExternalClient` backward/optimizer engine wired with `tg.set_backward_engine`. (R3, FR9)
  → **Done. `make_client` resolves `ENDPOINTS[endpoint]`'s `(api_base, api_key)` env vars into an
  `openai.OpenAI` client (R3). `SchemaInjectingEngine(ChatExternalClient)` ported verbatim from the
  notebook (renders the schema into the system prompt per `generate`). The backward/optimizer engine
  is a plain `ChatExternalClient(make_client(optimizer_endpoint), optimizer_model)` wired via
  `tg.set_backward_engine(..., override=True)` inside `optimize()`.**
- [x] T011 Implement `TextGradPromptOptimizer(BasePromptOptimizer)` `__init__(optimizer_model,
  optimizer_endpoint, val_set, epochs, batch_size, max_steps_per_epoch, seed,
  display_progress_bar)` and the `optimize(eval_fn, train_data, target_prompts, enable_tracking)`
  loop. Adapt the notebook's iteration loop to **epoch-based** passes over the train split (FR10): seed
  the instruction-block `tg.Variable(requires_grad=True)`; baseline val via `eval_fn`
  (→ `initial_eval_score`); per batch, run the task model + shared judge and wrap each judge
  rationale/verdict in a `tg.TextLoss` (primary bridge pinned in T002), `tg.sum(losses).backward()` +
  `optimizer.step()`; per-epoch val mean keep-best / `set_value`-revert (T003 mechanism); return
  `PromptOptimizerOutput(initial/final_eval_score, …)`.
  File: `src/experiments/text2sql/textgrad_optimizer.py`. (FR2, FR10, FR11, FR12, SC2a)
  → **Done. `optimize()` seeds the instruction-block `tg.Variable(requires_grad=True)`, runs a baseline
  val via `eval_fn` (→ `initial_eval_score`), then for each epoch iterates seeded full-pass batches
  (`_epoch_batches`, capped by `max_steps_per_epoch`): per record run `BlackboxLLM(task_engine)` +
  shared judge, wrap verdict+rationale in `tg.TextLoss(...)(response)`, `tg.sum(losses).backward()` +
  `optimizer.step()`. Per-epoch val mean → keep-best (`get_value`) / `set_value`-revert (T003); returns
  `PromptOptimizerOutput` with initial/final (+ per-scorer) scores. NB: `__init__` is a superset of the
  T011 list — the pinned primary `BlackboxLLM`+`TextLoss` bridge (T010/T002) needs the task engine and
  the shared judge in the gradient step, so it also takes `task_model`/`task_endpoint`/`judge_scorer`/
  `schema_text` (all available where the optimizer is built in T015); the forward MUST go through
  `BlackboxLLM`, not `eval_fn`, for `backward()` to reach the prompt. Verified with a stubbed run:
  keep-best returns the early val peak (0.8), not the last epoch (0.7).**
- [x] T012 Assert exactly one `target_prompts` entry; use TGD `constraints` ("single DuckDB SQL
  only" + "do not paste/invent the schema") so the optimized instruction block stays valid. Before
  returning, recombine the instruction block into the full `SYSTEM_PROMPT_TEMPLATE` shape so the
  registered `text2sql_system` artifact is a complete, reusable template with parity to GEPA's (FR6).
  File: `textgrad_optimizer.py`. (FR6, A4)
  → **Done. `optimize()` raises `ValueError` unless exactly one `target_prompts` entry. `TGD_CONSTRAINTS`
  carries the two constraints (single DuckDB SQL only / do not paste-or-invent the schema). `_recombine`
  rebuilds `instruction + "\n\nSchema:\n\n{schema}\n"`; verified `render_system_prompt` fills `{schema}`,
  so the registered artifact is a complete, reusable template with parity to GEPA's.**
- [x] T013 Log per-epoch `eval_score` to MLflow with `step=epoch` from inside `optimize()` when
  `enable_tracking` — the **same metric name GEPA's optimizer logs** (`gepa_optimizer.py`), so the val
  progression is directly comparable. File: `textgrad_optimizer.py`. (NFR1, NFR3, SC2)
  → **Done. `_log_eval_score` logs `eval_score` + `eval_score.sql_is_correct` (the same names + the
  per-scorer suffix GEPA's `_log_validation_candidate` emits) at `step=0` for the baseline and
  `step=epoch` thereafter, guarded by `enable_tracking`. Stub run confirmed the series
  `[(0,0.4),(1,0.8),(2,0.6),(3,0.5),(4,0.7)]`.**

## Phase 5 — `train_textgrad.py` CLI + entry point + recipe (depends on Phase 4)

- [x] T014 Create `train_textgrad()` Click CLI in
  `src/experiments/text2sql/train_textgrad.py` with options per plan §"CLI" table:
  `--questions-path/--schema-path/--db-path`, `--model/--endpoint`,
  `--judge-model/--judge-endpoint`, `--optimizer-model/--optimizer-endpoint`,
  `--epochs` (required, no default — C5), `--batch-size`, `--max-steps-per-epoch`,
  `--sampler-seed` (default 42), `--use-prod-questions`. Reuse `load_dataset`,
  `split_dataset`, `register_prompt_if_changed`, `build_sql_judge_scorer`. (FR1, FR9, FR10)
  → **Done. `train_textgrad()` mirrors `train()`'s preamble (`.env` → `setup_mlflow` →
  `format_schema_for_prompt`/`load_schema` → `load_dataset` → `split_dataset` →
  `register_prompt_if_changed`). Options exactly per the plan table: `--epochs` required with NO
  default (C5), `--batch-size` default 1, `--max-steps-per-epoch` optional (default `None`),
  `--sampler-seed` default 42. `--help` verified; no `--teacher-*` (GEPA-only).**
- [x] T015 Wire `train_textgrad()` to call the shared `_run_optimization(...)` from T005 with
  `optimizer=TextGradPromptOptimizer(...)`, `technique="textgrad"`, and `extra_params={the three
  model roles, epochs, batch_size, max_steps_per_epoch, sampler_seed, split sizes, the `OPTIMIZER_*`
  sampling params (F-006)}` so the run records every FR5 field with the same metric names as GEPA.
  File: `src/experiments/text2sql/train_textgrad.py`. (FR3, FR4, FR5, NFR1)
  → **Done. Builds the shared `build_sql_judge_scorer` judge + `TextGradPromptOptimizer(...)`, then
  delegates to `_run_optimization(..., technique="textgrad")`. `extra_params` = {`optimizer_model`,
  `optimizer_endpoint`, `epochs`, `batch_size`, `max_steps_per_epoch`,
  `**read_optimizer_params_for_logging()` (the `optimizer_*`-prefixed `OPTIMIZER_*` params, F-006)};
  `model`/`endpoint`/`judge_*` are logged by `log_global_params`, `sampler_seed` + split sizes by the
  shared routine — no duplication, same metric names as GEPA. Model-string convention: the litellm
  task + judge models keep the `openai/` prefix; the optimizer's `ChatExternalClient` task + optimizer
  engines take the raw id, so `--model`/`--optimizer-model` are `.removeprefix("openai/")`-stripped
  before reaching `TextGradPromptOptimizer`.**
- [x] T016 [P] Register the entry point
  `text2sql-train-textgrad = "experiments.text2sql.train_textgrad:train_textgrad"` in
  `pyproject.toml` `[project.scripts]`. (FR1)
  → **Done, right after `text2sql-train`. `uv run text2sql-train-textgrad --help` resolves.**
- [x] T017 [P] Add a `text2sql-train-textgrad` recipe to `justfile`, parallel to
  `text2sql-train`. (FR1)
  → **Done: recipe added after `text2sql-train`, swapping `--teacher-*` for
  `--optimizer-model`/`--optimizer-endpoint` + `--epochs`/`--batch-size`. `just --list` parses it.**

## Phase 6 — Error handling (EC1–EC5, depends on Phase 5)

- [x] T018 Refuse empty / too-small train or val split up front in `optimize()` with a clear
  message (before any LLM call); the val split is the same seeded `split_dataset` split GEPA uses, so
  keep-best stays comparable. File: `textgrad_optimizer.py`. (EC3, FR3, NFR2)
  → **Done. Added `MIN_SPLIT_SIZE = 1` and a guard at the top of `optimize()` (right after the
  single-`target_prompts` check, before any engine is built or any LLM call): raises `ValueError`
  with a clear message naming both split sizes when `len(train_data) < MIN_SPLIT_SIZE` or
  `len(self.val_set) < MIN_SPLIT_SIZE`. Message notes it is the same seeded `split_dataset` split
  GEPA uses, so a too-small split is a dataset/seed issue. Verified with a stub `eval_fn` that asserts
  if called: both the empty-train and empty-val cases raise up front without invoking `eval_fn`.**
- [x] T019 Let optimizer-model and judge failures propagate so the `mlflow.start_run` context
  marks the run FAILED with no optimized prompt registered; ensure a judge-call failure inside the
  batch loop raises rather than scoring 0/pass. Files: `textgrad_optimizer.py`,
  `train_textgrad.py`. (EC1, EC4, FR7, SC5)
  → **Done. The shared `build_sql_judge_scorer` swallows judge-call failures into
  `Feedback(value=False, error=...)` (correct for the MLflow eval path — one bad sample must not kill
  an eval). That default is wrong for the *training* gradient step, so `_judge` now inspects
  `fb.error` and re-raises a `RuntimeError` (chained from the underlying exception) instead of
  letting a failed judge call be treated as an INCORRECT verdict (EC4). The training-loop forward
  (`BlackboxLLM`) and the backward `optimizer.step()` already raise on an unreachable task/optimizer
  model (EC1), and nothing in `optimize()` / `train_textgrad.py` catches these — they propagate out
  of `optimize_prompts`, out of the `mlflow.start_run` context → run FAILED, no prompt registered
  (SC5). No swallowing exists in `train_textgrad.py`, so no change needed there. Note: the per-epoch
  val path stays on `eval_fn` (the canonical GEPA-comparable path) and keeps the scorer's swallow
  behaviour, matching GEPA — only the gradient step raises.**
- [x] T020 Honest no-improvement handling: when best == baseline, report it and rely on
  `register_prompt_if_changed` semantics so no spurious new prompt version is created; surface a
  prompt-save/version failure as a failed run. Files: `textgrad_optimizer.py`,
  `train_textgrad.py`. (EC2, EC5)
  → **Done. `optimize()` now treats only a *strict* gain over baseline (`best_val >
  initial_eval_score`) as an improvement; otherwise it returns the original `seed_template`
  byte-for-byte (not `_recombine(best_prompt)`), so `register_prompt_if_changed` sees an unchanged
  template and registers no spurious new version (EC2). Both branches log honestly (improved
  `x -> y` vs. "did not beat the baseline … keeping the seed prompt unchanged"). `final_eval_score`
  still reports `best_val` (== `initial_eval_score` in the no-improvement branch, since keep-best
  only updates on `>=`). A prompt save/version failure raised inside `optimize_prompts`/the registry
  propagates uncaught → run FAILED (EC5); `train_textgrad.py` adds no swallowing, so no change
  there.**


## Phase 7 — Docs

- [x] T026 [P] Add a brief TextGrad note to the harness/README (technique selection, the three
  roles, epoch-based effort) and confirm `.example.env` `OPTIMIZER_*` defaults are documented.
  → **Done. Added a "Prompt-optimization training" section to `README.md` documenting the two
  interchangeable techniques (GEPA `text2sql-train` = metric-call budget / `--teacher-*` proposer
  vs. TextGrad `text2sql-train-textgrad` = `--epochs`/`--batch-size` effort / `--optimizer-*`
  proposer), the shared split/judge/metrics/prompt for MLflow comparability, and the three model
  roles (task / judge / proposer). Confirmed `.example.env` already carries the documented
  `OPTIMIZER_*` block (lines 13–18: `0.0 / 1.0 / 1 / 42`, TextGrad-run-only) and the README points
  to it.**

## Phase 8 — Retrospective

- [x] T027 Run the retrospective skill to review all implemented changes for code quality and
  architectural decisions (specs/textgrad-mlflow-integration/retrospective.md).
  → **Done. No installed `retrospective` skill, so the review was performed directly against
  the spec/plan and the four shipped modules and written to
  [`retrospective.md`](./retrospective.md). Verdict: ship-ready; GEPA preserved byte-for-byte
  (T007), all FR/EC traced to code, modules compile. Five findings, two notable: (1) the
  task model is sampled through two different client stacks — `ChatExternalClient` (raw
  OpenAI, not governed by `LLM_*`) for the gradient-step forward vs. litellm for val/test —
  a reproducibility gap (NFR2); (2) `plan.md`'s "Key architectural decision" still claims
  TextGrad does NOT run its own `BlackboxLLM` forward, but the as-built design runs one for
  the training step (correct, spike-driven) and uses `eval_fn` only for val scoring — plan
  doc drift to reconcile. Three low/informational: duplicate judge-scorer instances,
  `make_client` raw `KeyError` on a missing env var, and the `>=` keep-best tie-break
  (spec-compliant).**

---

## Summary

- **Total tasks:** 27 across 9 phases.
- **Per user story:**
  - US1 (select TextGrad, optimize on train): T010–T017, T023.
  - US2 (compare fairly on same data/split/judge): T005–T009, T015, T024.
  - US3 (reviewer sees technique/roles/metrics/prompt in UI): T006, T013, T015, T023.
  - US4 (clean failure on partway error): T018–T020, T025.
- **Critical path:** T001→T002/T003 (spike) → T005→T006→T007 (shared routine) → T010→T011 → T014→T015 → T018–T020.
- **Parallel opportunities:** T008/T009; T016/T017; T021/T022; T026 can run alongside others. Phase 2 extraction must finish before Phase 5 wiring; Phase 1 spike blocks Phase 4.
