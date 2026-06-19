# Implementation Plan: SkillOpt as a Prompt-Optimization Technique in MLflow

Spec: [`spec.md`](./spec.md)

## Technical Context

The harness already optimizes the text-to-SQL system prompt with **two** pluggable
optimizers through MLflow's `mlflow.genai.optimize_prompts(..., optimizer=<BasePromptOptimizer>)`
extension point: `GepaPromptOptimizer` (`src/experiments/text2sql/train_gepa.py`) and
`TextGradPromptOptimizer` (`src/experiments/text2sql/textgrad_optimizer.py`,
`train_textgrad.py`). Both share one technique-agnostic routine,
`_run_optimization(...)` in `src/experiments/text2sql/train_common.py`, which opens the
MLflow run, logs the global params + a `technique` tag + technique-specific
`extra_params`, runs the test-before / test-after eval phases around
`optimize_prompts`, and logs `test_quality_{before,after}`. The optimizer itself logs the
validation progression (`eval_score` / `eval_score.<scorer>` steps) plus
`initial_eval_score` / `final_eval_score` on the same run. Generic building blocks
(dataset loader, the seeded group-aware `split_dataset`, the FLEX-style SQL judge
`build_sql_judge_scorer`, litellm `create_predict_fn` / `create_optimizable_predict_fn`,
`setup_mlflow`, `log_global_params`, `register_prompt_if_changed`) live in
`src/experiments/text2sql/harness.py` and `sampler.py`.

**SkillOpt** (microsoft/SkillOpt, `pip install skillopt`, Python ≥3.10) is structurally
different from GEPA/TextGrad: it is a config-driven training framework, not a library you
drive variable-by-variable. Its loop is **rollout → reflect → aggregate/select → update →
validate (hard gate) → keep-best**, treating a Markdown *skill document* as the trainable
state. The pieces relevant to us, confirmed from source
(`github.com/microsoft/SkillOpt`):

- **`skillopt.engine.trainer.ReflACTTrainer(cfg: dict, adapter: EnvAdapter)`** with
  **`train() -> dict`** — constructible and runnable **fully in-process** (no argparse).
  It writes the best validated skill to `<out_root>/best_skill.md` and persists a
  per-step `history.json` carrying `current_score`, `best_score`, `selection_hard`,
  `selection_soft`, and the gate decisions. There is **no per-epoch callback hook**; the
  score series is read back from `history.json`.
- **`skillopt.envs.base.EnvAdapter`** — the benchmark seam. A new task implements
  `build_train_env`, `build_eval_env`, `rollout(env_manager, skill_content, out_dir) ->
  list[dict]`, and `get_task_types`. Each result dict carries `hard` (0/1 or [0,1]
  correctness) and `soft` ([0,1]); **"whatever you put in `hard`/`soft` is what the
  optimizer reads"** — both the success/failure reflection partition and the validation
  gate run off `hard`. There is **no separate judge interface**; scoring lives inside
  `rollout`. This is exactly where we inject the project's FLEX judge.
- **Model roles** via `skillopt/model/` + `config.py`: `target` (rollout/task) and
  `optimizer` (reflection/edit) backends, each selectable independently
  (`OPTIMIZER_BACKEND` / `TARGET_BACKEND`, default `openai_chat`) with per-role endpoint
  overrides (`optimizer_azure_openai_endpoint` / `target_azure_openai_endpoint`) and an
  `azure_openai_auth_mode` that accepts plain OpenAI-compatible auth.
- **Effort knobs** in `configs/_base_/default.yaml`: `num_epochs` (4), `minibatch_size`
  (8, the reflection minibatch), `learning_rate` (4, the edit budget = textual learning
  rate) with `lr_scheduler: cosine` + `min_learning_rate` (2), `batch_size` (40, rollout
  batch), `use_gate: true` (hard validation gate), `seed`/`split_seed` (42),
  `skill_update_mode: patch`, `env.skill_init` (path to a seed skill).

This confirms spec assumption **A1**: SkillOpt plugs in behind the *same*
`optimize_prompts(..., optimizer=...)` extension point used for TextGrad, by wrapping the
in-process `ReflACTTrainer` inside a `BasePromptOptimizer` subclass.

### Key architectural decision

Implement **`SkillOptPromptOptimizer(mlflow.genai.optimize.optimizers.BasePromptOptimizer)`**
(new, `src/experiments/text2sql/skillopt_optimizer.py`) and pass it as `optimizer=` to the
**same** `optimize_prompts` call GEPA and TextGrad use, through the **same**
`_run_optimization` routine — so the SkillOpt run records the same metric names, the same
judge, and the same registered prompt as the others (FR1, FR3, FR8, FR9, A3, NFR1).

Inside `optimize()` the optimizer builds a `cfg: dict` and a custom
**`Text2SqlEnvAdapter(EnvAdapter)`**, constructs `ReflACTTrainer(cfg, adapter)`, runs
`train()` in a temp `out_root`, reads `best_skill.md` + `history.json`, and returns a
`PromptOptimizerOutput`. The integration follows a deliberate **two-path** design (the
same shape the TextGrad implementation settled on, per its retrospective):

- **Task (target) model + judge run through the project's own inference paths** (A2, Q4,
  FR9): `Text2SqlEnvAdapter.rollout` calls the project's litellm `create_predict_fn`
  (task model) and the **shared** `build_sql_judge_scorer` (FLEX judge), setting
  `hard = 1.0/0.0` from the judge verdict and stashing the judge rationale on the result
  dict. SkillOpt's own `chat_target` is **not** used for the task model — this keeps the
  task model sampled identically to the val/test eval path (avoiding the NFR2 divergence
  TextGrad's retrospective finding #1 had to fix) and makes `hard` the *same* pass/fail
  signal that produces the reported metric (FR11, A3).
- **Optimizer (reflection/edit) model runs through SkillOpt's own model layer** (Q4, A2),
  `openai_chat` backend with OpenAI-compatible auth pointed at a project blablador/kisski
  endpoint via the per-role endpoint override. Its endpoint + model are recorded on the
  run so the differing client path is visible (Q4, FR9).

Because SkillOpt evaluates the candidate skill on the **validation** split at the gate and
keeps the best (its native behavior), FR12 / SC2a / Q2 are satisfied by SkillOpt itself,
not re-implemented. The seed skill is the prompt's **instruction block**; `best_skill.md`
is recombined into the full `SYSTEM_PROMPT_TEMPLATE` shape before registration (FR6, A6,
SC6), reusing the exact `_instruction_block` / `_recombine` pattern already proven in
`textgrad_optimizer.py`.

## Architecture / Components

```
text2sql-train-gepa      ─┐
text2sql-train-textgrad   ├─►  _run_optimization(optimizer, technique, extra_params, …)   [shared, unchanged]
text2sql-train-skillopt ──┘         │
   (new CLI)                        ├─ log_global_params + technique="skillopt" + extra_params
                                    ├─ test-before eval phase (run_eval_phase)
                                    ├─ mlflow.genai.optimize_prompts(predict_fn, train, prompt_uris,
                                    │        optimizer=<Skillopt>, scorers=[judge])
                                    │        └─ optimizer.optimize(eval_fn, train, prompts, tracking)
                                    ├─ test-after eval phase
                                    └─ log_metric(test_quality_{before,after})

SkillOptPromptOptimizer(BasePromptOptimizer)   [new, src/experiments/text2sql/skillopt_optimizer.py]
   __init__(task_model, task_endpoint, optimizer_model, optimizer_endpoint,
            judge_scorer, schema_text, val_set, epochs, edit_budget, minibatch_size,
            reflect_on_success, seed, ...)
   optimize(eval_fn, train_data, target_prompts, enable_tracking) -> PromptOptimizerOutput
       assert exactly one target prompt; refuse empty/too-small train or val split (EC3)
       seed_skill   = _instruction_block(seed_template)            -> write to tmp skill_init.md
       cfg          = build cfg dict (roles+endpoints, epochs, lr=edit_budget, minibatch,
                      use_gate=True, success-reflection toggle, seed, train/val splits)
       adapter      = Text2SqlEnvAdapter(task predict_fn, shared judge, schema, splits)
       initial_eval_score = _val_score(eval_fn, seed_skill)        # baseline, same metric as GEPA
       result       = ReflACTTrainer(cfg, adapter).train()         # rollout/reflect/edit/gate/keep-best
       best_skill   = read best_skill.md ; history = read history.json
       log per-epoch val series -> eval_score / eval_score.<scorer>  (step=epoch, from history.json)
       final_eval_score = _val_score(eval_fn, best_skill)          # candidate, same metric path
       if final<=initial: return seed_template byte-for-byte (EC2, EC5)
       else: optimized = _recombine(best_skill)
       return PromptOptimizerOutput(optimized_prompts={name: optimized},
                                    initial_eval_score, final_eval_score, *_per_scorer)

Text2SqlEnvAdapter(skillopt.envs.base.EnvAdapter)   [new, same module]
   build_train_env / build_eval_env  -> wrap our train/val records (SkillOpt env_manager shape)
   rollout(env_manager, skill_content, out_dir) -> list[{id, hard, soft, rationale, ...}]
       for each item: sql = task_predict_fn(question, system=_recombine(skill_content))
                      fb  = shared_judge(question, ref_sql, sql)        # FLEX judge
                      guard: fb.error -> raise (EC4, never default to fail/pass)
                      hard = 1.0 if fb.value else 0.0 ; soft = hard ; rationale = fb.rationale
   get_task_types -> ["text2sql"]
```

Reused unchanged: `_run_optimization`, `run_eval_phase`, `PROMPT_NAME`,
`sampler.split_dataset`, `load_dataset`, `create_predict_fn`,
`create_optimizable_predict_fn`, `build_sql_judge_scorer`, `setup_mlflow`,
`log_global_params`, `register_prompt_if_changed`, the FLEX judge. The shared
`_instruction_block` / `_recombine` / `SCHEMA_MARKER` / `make_client` helpers currently
in `textgrad_optimizer.py` are **lifted into a small shared module**
(`prompt_skill.py`) so both optimizers import one copy (see Phase 2).

## Data Model / Entities

- **Train/val/test split** — `sampler.split_dataset(data, seed, cache_path)`, identical
  seed and embedding cache as GEPA/TextGrad (FR3, NFR2, EC3). SkillOpt rolls out on train,
  gates on val; test is the after-metric only, evaluated by `_run_optimization`.
- **Optimizable prompt / skill document** — single prompt `text2sql_system` (`PROMPT_NAME`).
  Only the **instruction block** (`SYSTEM_PROMPT_TEMPLATE` before `Schema:`) is the SkillOpt
  skill document; the DB schema is fixed context the task `predict_fn` injects per call
  (`render_system_prompt`) and never enters the skill doc or the reflection prompts (A6,
  FR6). `best_skill.md` is recombined into the full template before registration (SC6).
- **Edit** — SkillOpt's add/insert/replace/delete operations, merged and ranked under the
  per-round **edit budget** (`learning_rate`). Bounded per round (FR13).
- **Model roles (FR9)** — task model (`--model`/`--endpoint`, project litellm path), judge
  model (`--judge-model`/`--judge-endpoint`, the shared FLEX judge), optimizer model
  (`--optimizer-model`/`--optimizer-endpoint`, SkillOpt's own model layer). All three +
  their endpoints logged as run params.
- **Run record** — extends the existing run with `technique="skillopt"`, the three model
  roles, `epochs`, `edit_budget`, `minibatch_size`, `reflect_on_success`, `sampler_seed`,
  the `OPTIMIZER_*` sampling params, and split sizes; carries the same metrics as GEPA/
  TextGrad (`test_quality_{before,after}` + the optimizer's `eval_score` /
  `initial_eval_score` / `final_eval_score`) and the registered optimized prompt (FR5,
  FR10, NFR3).

## Interfaces / Contracts

### CLI — `text2sql-train-skillopt` (new entry point, separate command, mirrors TextGrad)

| Option | Required | Notes |
|---|---|---|
| `--questions-path` / `--schema-path` / `--db-path` | yes | same as GEPA/TextGrad |
| `--model` / `--endpoint` | yes | task (target) model — project litellm path (A2) |
| `--judge-model` / `--judge-endpoint` | yes | shared FLEX SQL judge (FR9, A3) |
| `--optimizer-model` / `--optimizer-endpoint` | yes | SkillOpt reflection/edit model (FR9, Q4) |
| `--epochs` | yes (no default) | passes over the train split (FR10, Q1) |
| `--edit-budget` | yes (no default) | max edits per round = SkillOpt `learning_rate` (FR10, FR13) |
| `--minibatch-size` | yes (no default) | reflection minibatch = `minibatch_size` (FR10) |
| `--reflect-on-success` / `--no-reflect-on-success` | default off | success-reflection toggle, recorded (FR11, Q3) |
| `--sampler-seed` | default 42 | split seed + SkillOpt `seed`/`split_seed` (NFR2) |
| `--use-prod-questions` | flag | same as GEPA/TextGrad |

No implicit effort default is assumed correct: `--epochs`, `--edit-budget`,
`--minibatch-size` are required, human-set, and recorded (FR10, NFR3).

`pyproject.toml` `[project.scripts]`:
`text2sql-train-skillopt = "experiments.text2sql.train_skillopt:train_skillopt"`.
`experiments.just`: a `text2sql-train-...-skillopt` recipe parallel to the GEPA one
(student-judge-optimizer-technique naming).

### `SkillOptPromptOptimizer.optimize` contract

- Input: `eval_fn(candidate: dict[str,str], data: list[dict]) -> list[EvaluationResultRecord]`,
  `train_data`, `target_prompts` (single entry), `enable_tracking`.
- Asserts exactly one target prompt; refuses empty/too-small train **or** val split up
  front, before any LLM call or trainer construction (EC3), reusing the `MIN_SPLIT_SIZE`
  guard pattern.
- Drives `ReflACTTrainer` in a `tempfile.TemporaryDirectory()` `out_root`; never pollutes
  the repo with `outputs/`.
- Returns `PromptOptimizerOutput(optimized_prompts={name: best_template}, initial_eval_score,
  final_eval_score, *_per_scorer)`. `best_template = _recombine(best_skill)` when it beats
  baseline on val, else the seed `target_prompts[name]` byte-for-byte so
  `register_prompt_if_changed` dedups it and no spurious version is registered (EC2).
  `optimize_prompts` registers the result (FR6).

### Effort → SkillOpt config mapping (FR10, NFR3, Q1)

| Spec knob (recorded) | SkillOpt cfg key | Notes |
|---|---|---|
| epochs | `num_epochs` | full passes over train; one epoch = one minibatch sweep (Q1) |
| edit budget | `learning_rate` | max edits/round; **constant**, see decision below |
| minibatch size | `minibatch_size` | reflection minibatch |
| — | `batch_size` | set to full train size (`train_size=0` / ≥len(train)) so an epoch is a full pass, not a 40-item rollout cap (Q1) |
| — | `lr_scheduler` | set **constant** + `min_learning_rate == learning_rate` so the recorded edit budget is one stable number (Q1) — decision OQ1 |
| — | `use_gate` | `true` (hard val gate = FR12/Q2) |
| — | `eval_test` | `false`; the test metric comes from `_run_optimization`'s test-after phase, not SkillOpt, to keep one metric path across techniques |
| seed | `seed` / `split_seed` | `sampler_seed` (NFR2) |
| reflect_on_success | (success-reflection cfg key, pinned in spike T-spike) | failure reflection always on (FR11, Q3) |

### Optimizer-model engine / sampling params

Reuse the existing `OPTIMIZER_*` env family (`read_optimizer_params_for_logging()` in
`harness.py`) — logged **on the SkillOpt run only** via `extra_params`, never in
`log_global_params`, so GEPA/TextGrad runs stay byte-identical (FR8). SkillOpt's
`openai_chat` backend reads base_url/api_key from its own config/env; we set them from
`ENDPOINTS[optimizer_endpoint]` (the `make_client` resolution already used by TextGrad),
pointed at the project endpoint — never a library default (Q4, A2). A friendly error (not
a bare `KeyError`) is raised when a role's `*_API_BASE` / `*_API_KEY` env var is unset
(addresses TextGrad retrospective finding #4 for the new module).

## SkillOpt-internal mechanism (the pieces needing a spike)

SkillOpt is heavier and less library-shaped than TextGrad, so the spike confirms three
seams before the rest is built:

1. **Programmatic run + per-role OpenAI-compatible endpoints.** Confirm
   `ReflACTTrainer(cfg, adapter).train()` runs in-process against blablador/kisski with
   the `openai_chat` backend in OpenAI-compatible auth mode, and that
   `optimizer`/`target` roles take **independent** base_url/api_key/model (per-role
   endpoint overrides). *Fallback:* if per-role overrides are not honored, run the
   optimizer role through SkillOpt's model layer and **route the task role entirely
   through our adapter's `rollout`** (which already calls our litellm `predict_fn`), so
   only one endpoint (the optimizer's) is configured on SkillOpt's side.
2. **`EnvAdapter` rollout fully under our control.** Confirm our `rollout` can run the
   task model via our litellm `predict_fn` and score with the shared judge (not SkillOpt's
   `chat_target`/built-in scorers), and that `hard`/`soft` drive both the success/failure
   partition and the gate. *Fallback:* if the ABC forces `chat_target` for rollout, keep
   the task model on SkillOpt's model layer pointed at the project endpoint and apply the
   `LLM_*` sampling params to it (accept the documented seed/top_k caveat as TextGrad did);
   the judge still scores via our shared scorer set as the adapter's reward.
3. **Success-reflection toggle + val-score series.** Pin the config key that turns success
   reflection on/off (FR11, Q3), and confirm `history.json` exposes the per-step/epoch val
   `best_score`/`current_score` so we can log the `eval_score` progression under GEPA's
   metric names (SC2). Pin **what one `history.json` row represents** (minibatch update vs.
   epoch) and the explicit **row→step mapping** used when logging at `step=epoch`. Confirm
   the `history.json` val score **agrees with an `eval_fn` val call** on the same candidate
   + same val split, so the logged series and the `initial/final_eval_score` endpoints sit
   on one axis under one metric name — the single-axis discipline `textgrad_optimizer.py`
   already follows (keep-best, the series, and the endpoints all run through `eval_fn`).
   *Fallback for the series:* if `history.json` granularity is insufficient **or its val
   disagrees with `eval_fn`**, source the `eval_score` series from per-epoch `_val_score`
   (`eval_fn`) calls — or log only `initial_eval_score` / `final_eval_score` — so the
   reported numbers stay on the `eval_fn` axis (still satisfies SC2's headline numbers) and
   note the limitation.

This is the first deliverable (Phase 1 spike) so the API is pinned before the rest is
built — the same spike-first discipline the TextGrad plan used for its gradient-injection
unknown.

## Phases / Dependencies

1. **Spike: SkillOpt dependency + the three seams above.** Add `skillopt` to
   `pyproject.toml`; confirm it installs alongside the existing deps on Python 3.13. In a
   scratch script/notebook (`notebooks/skillopt_prompt_opt.ipynb`), build the `cfg` +
   `Text2SqlEnvAdapter`, run `ReflACTTrainer(...).train()` against a real endpoint on a
   handful of records, and lock in: per-role OpenAI-compatible endpoint config, the
   rollout-owns-task-and-judge wiring, the success-reflection key, and the `history.json`
   val series. Also confirm two cfg-semantics assumptions used downstream: that
   `learning_rate` **caps the number of edits applied per round** (FR13) and that
   `batch_size`/`train_size` yields a **full-pass epoch** (Q1). *Blocks everything.*
2. **Lift shared prompt-skill helpers** (`_instruction_block`, `_recombine`,
   `SCHEMA_MARKER`, `make_client`, `MIN_SPLIT_SIZE`) from `textgrad_optimizer.py` into a
   new `src/experiments/text2sql/prompt_skill.py`; import them from both optimizers. Pure
   extraction; verify TextGrad still behaves identically (guards FR8 for TextGrad).
3. **Implement `Text2SqlEnvAdapter` + `SkillOptPromptOptimizer`**
   (`skillopt_optimizer.py`): adapter rollout (task `predict_fn` + shared judge → `hard`),
   cfg builder, in-process `ReflACTTrainer` run in a temp dir, `best_skill.md` recombine,
   `history.json` → `eval_score` series (step=epoch), baseline/best `_val_score` via
   `eval_fn`, `PromptOptimizerOutput`, EC2/EC4 honesty.
4. **Implement `train_skillopt.py` CLI** + entry point + `experiments.just` recipe; wire to
   the shared `_run_optimization`. Logs `technique="skillopt"`, the three roles, `epochs`,
   `edit_budget`, `minibatch_size`, `reflect_on_success`, split sizes, `OPTIMIZER_*` (FR5,
   FR10). A `technique` param already exists on GEPA/TextGrad runs (no change there).
5. **Error handling (EC1–EC6):** optimizer/target endpoint unreachable or judge failure →
   exception propagates and `mlflow.start_run` marks the run FAILED with no optimized
   prompt (EC1, EC4, SC5); empty/small split refused up front (EC3); no-improvement
   returns the seed byte-for-byte, no spurious version (EC2); a round with no usable edits
   is handled by SkillOpt's own keep-best (EC5); `best_skill.md` missing/unreadable or
   prompt-save failure → failed run with a clear message (EC6). Friendly endpoint-env-var
   errors (retrospective finding #4).
6. **Docs + env defaults:** note the new technique in `README.md`; the `OPTIMIZER_*`
   defaults already exist in `.example.env` (shared with TextGrad) — extend only if the
   spike needs new SkillOpt-specific env vars.

## Risks & Open Questions

- **R1 (highest): SkillOpt API surface is config/CLI-shaped, not library-shaped.** The
  in-process `ReflACTTrainer(cfg, adapter).train()` path is confirmed to exist, but the
  exact `cfg` keys, `EnvAdapter` method contracts, and rollout override freedom must be
  pinned in the Phase-1 spike. Mitigated by spike-first + the documented per-seam
  fallbacks. *If `train()` cannot be driven in-process at all, fall back to a subprocess
  `scripts/train.py` run with a generated YAML + `best_skill.md`/`history.json` parsing —
  a larger divergence, flagged here so it is a conscious choice, not a surprise.*
- **R2: Python 3.13 + skillopt + existing deps (gepa, textgrad, mlflow, litellm)
  compatibility.** Resolve in Phase 1; pin a compatible version or isolate if a conflict
  surfaces.
- **R3: per-role endpoint routing on SkillOpt's model layer.** Mitigated as in R1 seam 1;
  worst case only the optimizer role uses SkillOpt's layer and the task role stays on our
  litellm path.
- **R4: comparability of effort units (NFR3).** GEPA uses `max_metric_calls`; TextGrad uses
  epochs/batch/max-steps; SkillOpt uses epochs × minibatch × edit budget (Q1). All three
  are recorded; report SkillOpt's three knobs so effort is reasoned about side by side even
  though units differ.
- **R5: cost.** Each epoch rolls out the full train split + reflection + a val gate pass;
  keep `--epochs`/`--minibatch-size` small for first runs. All recorded (NFR3).
- **R6: `eval_score` progression fidelity (SC2).** Depends on `history.json` granularity
  (spike seam 3); fallback logs only initial/final, with the limitation noted.
- **OQ1 (decision taken): LR scheduler.** SkillOpt defaults to cosine LR decay (4→2),
  which would make "the edit budget" a moving number across epochs and muddy Q1's
  single-knob recording. **Decision:** run with a constant edit budget
  (`min_learning_rate == learning_rate`, decay off) so the recorded `edit_budget` is one
  stable, comparable number. Revisit only if the spike shows decay materially changes
  behavior; either way the effective schedule is recorded.

## Traceability

| Requirement | Where addressed |
|---|---|
| FR1, FR4 | separate `text2sql-train-skillopt` command; `technique="skillopt"` param |
| FR2, FR6, A6, SC6 | SkillOpt rewrites the instruction-block skill doc; `_recombine` restores the full template; `optimize_prompts` registers it |
| FR3, FR9, FR11, A3, NFR1 | shared `eval_fn`/scorer → same judge; adapter `hard` = judge pass/fail drives both reflection partition and the metric; same metric names |
| FR5, FR10, FR13, NFR3, Q1 | run params: three roles, epochs, edit_budget, minibatch_size, reflect_on_success, split sizes; bounded per-round edits |
| FR7, EC1–EC6, SC5 | Phase 5 error handling; `start_run` FAILED on exception; honest no-improvement |
| FR8, SC4, NFR4 | new module + separate command; `_run_optimization` untouched; `OPTIMIZER_*` on SkillOpt run only; Phase-2 extraction regression-checked |
| FR12, SC2a, Q2 | SkillOpt's native hard val gate + keep-best |
| FR9, Q4, A2 | optimizer role on SkillOpt's model layer at a project endpoint (recorded); task + judge on project inference paths |
| NFR2 | shared `sampler_seed` → split + SkillOpt `seed`/`split_seed`; `OPTIMIZER_*`/`LLM_*`/`JUDGE_*` seeds |
| Q3 | `--reflect-on-success` toggle, recorded |

## Generated Artifacts

- `specs/skillopt-mlflow-integration/plan.md` (this file)
- To be created during implementation:
  `src/experiments/text2sql/skillopt_optimizer.py`,
  `src/experiments/text2sql/train_skillopt.py`,
  `src/experiments/text2sql/prompt_skill.py` (lifted shared helpers),
  `notebooks/skillopt_prompt_opt.ipynb` (Phase-1 spike); edits to
  `textgrad_optimizer.py` (import lifted helpers), `pyproject.toml`, `experiments.just`,
  `README.md`, and `.example.env` if the spike adds SkillOpt-specific env vars.

Ready for task breakdown.
