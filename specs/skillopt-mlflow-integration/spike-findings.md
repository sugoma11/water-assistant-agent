# Phase-1 spike findings — SkillOpt as an MLflow prompt-optimization technique

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Tasks: [`tasks.md`](./tasks.md) ·
Notebook: [`../../notebooks/skillopt_prompt_opt.ipynb`](../../notebooks/skillopt_prompt_opt.ipynb)

Recorded here (per F-008) rather than editing the approved `plan.md`. Every claim is
grounded in the installed source (`skillopt==0.1.0`) and the notebook cells; items marked
**[source]** are read from the package, **[offline-run]** were confirmed by running the
non-network notebook cells, and **[live-run]** require the gated `train()` cell against
`blablador`/`kisski` (the headline numbers; the mechanism is already pinned).

Pinned upstream commit for prompt vendoring: `6940e46f4e0e537a1d7ca8432f24248d0d3550f5`.

---

## T001 — dependency resolution

- **`skillopt==0.1.0`** added to `[project.dependencies]` (`skillopt>=0.1.0`). Only `0.1.0`
  is published. **[offline-run]**
- Resolved on **Python 3.13** with `uv sync`, **no conflict** with `gepa` / `textgrad` /
  `mlflow` / `litellm`. Base deps are light: `openai`, `pyyaml`, `numpy`, `openpyxl`,
  `azure-identity`, `azure-core`, `httpx`; `requires-python>=3.10`. No pin/isolation beyond
  the version floor (R2 closed). **[offline-run]**
- Side effect: `uv sync` pruned an ad-hoc Jupyter stack (`ipykernel`, `jupyter-client`,
  `ipython`, …) that was in the venv but not in the lockfile. Notebooks here are not run via
  locked deps; re-add a kernel however you ran the existing notebooks (e.g.
  `uv run --with jupyter ...` or the IDE's Python extension).

## R1 — in-process `train()` path (no subprocess fallback)

- **`skillopt.engine.trainer.ReflACTTrainer(cfg: dict, adapter: EnvAdapter)`** with
  **`train() -> dict`** is constructible and driveable fully in-process — no argparse, no
  YAML file required. `cfg` is a **flat** dict; `train()` reads `cfg["edit_budget"]`,
  `cfg["out_root"]`, … directly. **The subprocess fallback (R1/F-007) is NOT needed.**
- **[live-run] confirmed end-to-end** against `blablador`/`kisski` (task `alias-eve`@blablador,
  judge `qwen3.6-35b-a3b`@kisski, optimizer `glm-4.7`@kisski; 4 train + 3 val records,
  2 epochs, `edit_budget=2`, `minibatch_size=4`). The full 6-stage loop ran
  (rollout→reflect→aggregate→select→update→gate), produced `history.json` (2 rows),
  `best_skill.md`, and a summary dict. Selected results:

  ```
  [config] epochs=2 steps/epoch=1 batch_size=4 train_size=4
  [config] lr_scheduler=constant edit_budget=2 min_edit_budget=2  gate metric=hard
  BASELINE selection hard=0.0000
  STEP 1 (epoch 1): rollout hard=0.0000 → 1 edit → ACCEPT (new best) selection hard=0.3333
  STEP 2 (epoch 2): rollout hard=0.5000 → 2 edits (budget cap) → REJECT (0.3333 <= 0.3333)
  done: best skill from step 1, score=0.3333
  history rows: 2   step=1 epoch=1 (accept_new_best)   step=2 epoch=2 (reject)
  F-001 check: direct val judge pass-rate=0.3333  ==  history best_score=0.3333  → axes agree
  ```

  `train()` summary keys: `baseline_selection_hard`, `best_selection_hard`, `best_step`,
  `best_origin`, `current_origin`, `config`, `epoch_stats`, `test_hard`/`test_soft`/
  `test_delta_hard`, `baseline_test_*` (the test keys are inert here — `eval_test=False`).
- **Cost/latency (R5):** the reflection (optimizer) model dominates wall time. In this run
  step-1 reflect took ~7500s on `glm-4.7` (cold/queued; `rewrite_reasoning_effort` defaults
  to `high`), step-2 reflect only ~9s. Keep `--epochs`/`--minibatch-size` small for first
  runs; consider lowering the optimizer reasoning effort. Rollout (~100s) and gate (~100s)
  are modest. Total tokens for the 2-step run: ~9k.

## BLOCKER (and its workaround): the wheel ships no prompt assets

- `skillopt==0.1.0`'s wheel contains **zero `.md` prompt files**. The reflection / merge /
  ranking / meta prompts live in the repo at `skillopt/prompts/*.md`, but the repo builds
  with setuptools and declares **no `package-data`/MANIFEST for `*.md`**, so neither the
  PyPI wheel **nor a `git+https://…` install** includes them. **[source]**
- Effect: the default reflect path → `run_minibatch_reflect` → `load_prompt("analyst_error")`
  raises `FileNotFoundError`; the aggregate/select stages need `merge_failure`,
  `merge_success`, `merge_final`, `ranking` too. `load_prompt("analyst_error")` raising was
  **[offline-run]** reproduced.
- **Workaround (non-destructive, used in the notebook):** seed the loader cache —
  `skillopt.prompts._cache[<generic_path>] = content` — so `load_prompt(name)` returns the
  vendored text without the file existing. Confirmed working **[offline-run]**. The notebook
  fetches the generic prompts from the pinned commit at runtime.
- **Phase-3 recommendation:** vendor the needed `.md` files into the repo (e.g.
  `src/experiments/text2sql/skillopt_prompts/`) and seed the cache at optimizer import time —
  no runtime network, reproducible. Needed for `skill_update_mode="patch"`: `analyst_error`,
  `analyst_success`, `merge_failure`, `merge_success`, `merge_final`, `ranking`. (Vendor
  `meta_skill`/`slow_update`/`rewrite_skill`/`lr_autonomous` too only if those features are
  enabled — the spike keeps them off.)

## 0.1.0 vs. GitHub `main` divergence (affects T010)

- In **0.1.0**, `EnvAdapter.reflect` is `@abstractmethod` with **no body**; `main` has since
  given it a default. So `Text2SqlEnvAdapter` **must implement `reflect`** in 0.1.0. Mirror
  the built-in `DocVQAAdapter`: delegate to `run_minibatch_reflect(...)` passing
  `self.{analyst_workers,failure_only,minibatch_size,edit_budget}`, the two analyst prompts,
  and `update_mode`. **[source + offline-run]**
- Abstract methods to implement in 0.1.0: `build_train_env`, `build_eval_env`, `rollout`,
  `reflect`, `get_task_types`. **[offline-run]**

## cfg semantics — flat keys and the flatten map (T012)

Structured YAML is flattened by `skillopt.config.flatten_config`. Key renames that matter
(**[source]**):

| structured | flat cfg key (what `train()` reads) |
|---|---|
| `optimizer.learning_rate` | **`edit_budget`** (not `learning_rate`) |
| `optimizer.min_learning_rate` | **`min_edit_budget`** |
| `train.num_epochs` | `num_epochs` |
| `train.batch_size` / `train.train_size` / `train.accumulation` / `train.seed` | `batch_size` / `train_size` / `accumulation` / `seed` |
| `gradient.minibatch_size` / `gradient.failure_only` / `gradient.merge_batch_size` / `gradient.analyst_workers` | same names |
| `optimizer.lr_scheduler` / `optimizer.skill_update_mode` | same names |
| `evaluation.use_gate` / `evaluation.eval_test` / `evaluation.sel_env_num` | same names |
| `env.name` / `env.skill_init` / `env.out_root` | `env` / `skill_init` / `out_root` |

- `flatten_config` **raises** if `evaluation.use_gate is False` — the hard val gate is
  mandatory (satisfies FR12/Q2 for free). **[source]**
- **Required flat keys** (no default; `KeyError`/`ValueError` if missing): `out_root`,
  `optimizer_model`, `target_model`, `skill_init`, `batch_size`, `num_epochs`,
  `accumulation`, `seed`, `merge_batch_size`, `edit_budget`, `sel_env_num`. With **no
  SkillOpt dataloader** (`get_dataloader()->None`), `train_size` must also be set explicitly
  (`_resolve_train_size` can't infer it). **[source + offline-run]**

## Seam 1 (T003) — per-role OpenAI-compatible endpoints

- The `openai_chat` backend is served by `skillopt/model/azure_openai.py`. `_make_client`
  returns a **plain `openai.OpenAI(base_url=endpoint, api_key=…)`** when
  `auth_mode ∈ {"openai_compatible","compat","openai"}` (otherwise `AzureOpenAI`). **[source]**
- Optimizer role flat cfg keys (consumed by `configure_azure_openai` inside `train()`):
  `optimizer_backend="openai_chat"`, `optimizer_model=<id without openai/ prefix>`,
  `optimizer_azure_openai_endpoint=<base_url, e.g. …/v1>`,
  `optimizer_azure_openai_api_key=<key>`, **`optimizer_azure_openai_auth_mode="openai_compatible"`**.
- **Confirmed [offline-run]:** with those set, `get_optimizer_client()` returns an `OpenAI`
  client whose `base_url` is our endpoint. Per-role overrides **are honored** → the seam-1
  fallback (optimizer-only on SkillOpt's layer) is **not needed**.
- Env-var equivalents also exist (`OPTIMIZER_AZURE_OPENAI_ENDPOINT` /
  `OPTIMIZER_AZURE_OPENAI_API_KEY` / `OPTIMIZER_AZURE_OPENAI_AUTH_MODE`, and `TARGET_…`),
  but the project resolves endpoints from `ENDPOINTS[...]` and passes them via cfg keys
  (never a library default — Q4/A2). Friendly errors when a role's `*_API_BASE`/`*_API_KEY`
  is unset → Phase 5 (EC1, retrospective finding #4).
- The **task (target) role is never built**: the task model runs inside our `rollout`
  (seam 2), so SkillOpt's `target` client is not constructed. `target_model`/`target_backend`
  are still set because `train()` calls `set_target_deployment(cfg["target_model"])`.

## Seam 2 (T004) — `rollout` owns the task model + the shared judge

- `rollout(self, env_manager, skill_content, out_dir, **kwargs)`. The trainer calls it on
  train batches as `rollout(train_env, current_skill, dir, use_eval_feedback=True)` and on
  the val/selection set as `rollout(sel_env, candidate_skill, dir)` — so **one `rollout`
  drives both the reflection partition and the validation gate**. **[source]**
- `env_manager` is **opaque to the trainer** (only `len()` is read). We return the split's
  **record list** from `build_train_env`/`build_eval_env`; `rollout` iterates it. **[source + offline-run]**
- Scoring: `compute_score(results)` averages `hard`/`soft`; the gate metric defaults to
  `hard` (`gate_metric="hard"` → `select_gate_score(..., "hard")`). Set `hard = 1.0/0.0`
  from the judge verdict, `soft = hard`. **[source]**
- **EC4:** on a judge `error` we **raise** — never default a grade. **[in notebook]**
- **rollout must persist a trajectory file** per item:
  `<out_dir>/predictions/<id>/conversation.json` (a list of `{role,content}` messages).
  `fmt_minibatch_trajectories` reads it for reflection; returning `{id,hard,soft}` alone is
  insufficient. It also reads optional result-dict fields: `task_description`, `task_type`,
  `fail_reason`, `reference_text`, `n_turns`. **[source]**
- **Result-dict shape used:** `{id, hard, soft, task_description (question), task_type
  ("text2sql"), reference_text (ref SQL), fail_reason (judge rationale when failed),
  n_turns}`.
- The default/0.1.0 `reflect` reads **attributes** `self.analyst_workers`,
  `self.failure_only`, `self.minibatch_size`, `self.edit_budget` — set them in `setup(cfg)`.
  Analyst prompts come from `get_error_minibatch_prompt()` / `get_success_minibatch_prompt()`
  (override to return the vendored generic prompts, since no env prompt dir exists). **[source + offline-run]**
- Fallback (task on SkillOpt's `chat_target`) is **not needed**: the ABC does not force
  `chat_target` for rollout.

## Seam 3 (T005) — success-reflection toggle + `history.json` series

- **Success-reflection toggle = `gradient.failure_only` (flat `failure_only`).**
  `failure_only=True` → failure reflection only (success reflection **off**, our CLI
  default-off state); `failure_only=False` → success reflection **on**. So
  `failure_only = not reflect_on_success`. Failure reflection is always on. **[source +
  live-run]:** with `failure_only=True` the run logged `success=0→0 groups` every step (no
  success reflection), only `failure` minibatches.
- **`history.json` is a JSON list, one row per training *step*.** A row carries: `step`,
  `epoch`, `step_in_epoch`, `action` (`accept` / `accept_new_best` / `reject` /
  `skip_no_patches` / `skip_no_rewrite`), `selection_hard`, `selection_soft`,
  `current_score`, `best_score`, `best_step`, `skill_len`, timings/tokens. **[source]**
- **Row → epoch mapping (F-003):** `steps_per_epoch = ceil(train_size / (batch_size *
  accumulation))`. With the chosen full-pass cfg (`batch_size == train_size`,
  `accumulation = 1`) → `steps_per_epoch = 1`, so **one row per epoch and `row["step"] ==
  row["epoch"]`**. Log the `eval_score` series at `step = row["epoch"]`. **[live-run] confirmed:**
  the run produced exactly 2 rows with `step==epoch` (1/1, 2/2).
- **Val score on the `eval_fn` axis (F-001):** the gate scores the candidate by calling our
  **same `rollout`** on the val split, so `selection_hard`/`current_score`/`best_score` are
  the **mean judge `hard` over val** — identical in definition to what `eval_fn` computes
  (same task `predict_fn` + same shared judge). The series and the
  `initial/final_eval_score` endpoints therefore share **one axis by construction**, modulo
  task-model sampling nondeterminism (temperature>0). **[live-run] confirmed:** the notebook
  cell compared `history` `best_score` to a fresh val judge pass-rate — both `0.3333`, exact
  agreement.
- **Fallback (R6) — not needed:** the live run showed exact history-val ↔ `eval_fn`
  agreement, so the `history.json` series is usable as-is. If a future run shows the
  `history.json` val disagrees with `eval_fn` (e.g. sampling drift) or granularity is poor,
  source the `eval_score` series from per-epoch
  `eval_fn` `_val_score` calls (TextGrad parity), or log only `initial/final_eval_score`.
- `best_skill.md` is (re)written every step with the running best-on-val skill — what Phase 3
  reads, recombines via `_recombine`, and registers. **[source]**

## Q1 / FR13 / OQ1 decisions

- **Q1 / F-006 (full-pass epoch):** confirmed via `steps_per_epoch = ceil(train_size /
  (batch_size*accumulation))`. Setting `batch_size = train_size`, `accumulation = 1` makes an
  epoch a single full-pass rollout (not the default 40-item cap). **[source]**
- **FR13 / F-002 (edit budget caps edits/step):** `edit_budget` is the scheduler's `max_lr`
  (`build_scheduler(max_lr=cfg["edit_budget"], …)`) and `rank_and_select` clips the merged
  edit pool to that many edits per step. **[source]**
- **OQ1 (constant LR):** `lr_scheduler="constant"` + **`min_edit_budget == edit_budget`** so
  the recorded edit budget is one stable number. **[source/decision]**
- `use_gate=True` (forced by `flatten_config`), `eval_test=False` (the test metric comes from
  `_run_optimization`'s test-after phase, one metric path across techniques). `seed` /
  `split_seed` = `sampler_seed`. **[decision]**

## Fallbacks taken

None of the documented per-seam fallbacks are required: in-process `train()` works (no
subprocess), per-role endpoints are honored (no optimizer-only routing), `rollout` owns the
task + judge (no `chat_target`), and `history.json` gives a usable per-epoch val series on the
`eval_fn` axis. The **only** workaround is vendoring the missing prompt assets (packaging gap
above), which Phase 3 should bake into the repo.
