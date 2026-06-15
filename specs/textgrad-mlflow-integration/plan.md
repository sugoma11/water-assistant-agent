# Implementation Plan: TextGrad as a Prompt-Optimization Technique in MLflow

Spec: [`spec.md`](./spec.md)

## Technical Context

The harness already optimizes the text-to-SQL system prompt with GEPA through
MLflow's `mlflow.genai.optimize_prompts(...)` extension point and records before/after
quality + the optimized prompt in MLflow (`src/experiments/text2sql/train.py`). The
generic building blocks (dataset loader, optimizable `predict_fn`, the FLEX-style
LLM-as-Judge scorer, MLflow setup/param logging, the group-aware sampler) live in
`src/experiments/text2sql/harness.py` and `src/experiments/text2sql/sampler.py`.

The optimizer is a **pluggable object**: `optimize_prompts(..., optimizer=<BasePromptOptimizer>)`.
`GepaPromptOptimizer` is one subclass. The MLflow driver (`mlflow/genai/optimize/optimize.py`)
builds the `eval_fn` from `predict_fn` + scorers, patches `PromptVersion.template` so
candidate prompts reach `predict_fn`, registers the returned optimized template, and
owns the autolog run. This confirms spec assumption **A1** (documented extension point).

### Key architectural decision

Implement **`TextGradPromptOptimizer(mlflow.genai.optimize.optimizers.BasePromptOptimizer)`**
and pass it as `optimizer=` to the **same** `optimize_prompts` call GEPA uses. TextGrad
does **not** run its own `BlackboxLLM` forward pass or task engine. Instead:

- The **task model** runs through MLflow's `eval_fn` (our existing `create_optimizable_predict_fn`).
- The **judge** is our existing `sql_is_correct` scorer; `eval_fn` returns its `score`
  (pass/fail metric, FR11) and `rationales["sql_is_correct"]` (the textual feedback that
  drives edits, FR11). Same judge, same metric names as GEPA (FR9, A3, NFR1) — for free.
- The **optimizer model** is TextGrad's TGD backward engine; it turns the judge rationale
  (attached as a textual gradient on the prompt Variable) into a revised prompt.

`EvaluationResultRecord` (`mlflow/genai/optimize/types.py`) carries `inputs`, `outputs`,
`score`, `rationales`, `individual_scores` — everything TextGrad needs as its gradient
signal and everything the harness needs for metrics. The `optimizer.optimize()` signature
only receives `train_data`, so (mirroring GEPA's `valset` via `gepa_kwargs`) the **val set
is passed into `TextGradPromptOptimizer.__init__`** for per-epoch keep-best (FR12).

## Architecture / Components

```
text2sql-train  (GEPA CLI)  ─┐
                              ├─►  _run_optimization(optimizer, technique, extra_params, …)   [shared]
text2sql-train-textgrad (new)─┘         │
                                        ├─ log_global_params + technique-specific params
                                        ├─ test-before eval phase (run_eval_phase)
                                        ├─ mlflow.genai.optimize_prompts(predict_fn, train, prompt_uris,
                                        │        optimizer=<Gepa|TextGrad>, scorers=[judge])
                                        │        └─ optimizer.optimize(eval_fn, train, prompts, tracking)
                                        ├─ val_before  = result.initial_eval_score
                                        ├─ val/test-after eval phases
                                        └─ log_metrics(val/test_quality_{before,after})

TextGradPromptOptimizer(BasePromptOptimizer)   [new, src/experiments/text2sql/textgrad_optimizer.py]
   __init__(optimizer_model, optimizer_endpoint, val_set, epochs, batch_size,
            max_steps_per_epoch, seed, display_progress_bar)
   optimize(eval_fn, train_data, target_prompts, enable_tracking) -> PromptOptimizerOutput
       seed prompt -> tg.Variable(requires_grad=True)
       baseline val score via eval_fn(seed, val_set)         (initial_eval_score)
       for epoch in epochs:
         for batch in batches(train_data)[:max_steps_per_epoch]:
            results = eval_fn({name: prompt.value}, batch)   # task model + judge
            guard: judge-call failure -> raise (EC4)
            attach per-example textual gradients from rationales -> TGD.step()
         val_score = mean(eval_fn({name: prompt.value}, val_set))   (FR12)
         keep-best / revert to best on no improvement
       return PromptOptimizerOutput(optimized_prompts={name: best}, initial/final scores)

CustomLiteLLMEngine(textgrad EngineLM)   [new, in textgrad_optimizer.py]
   wraps litellm.completion via build_completion_kwargs(optimizer_model, endpoint, "OPTIMIZER")
```

Reused unchanged: `sampler.split_dataset`, `load_dataset`, `create_optimizable_predict_fn`,
`create_predict_fn`, `setup_mlflow`, `log_global_params`, `register_prompt_if_changed`,
`run_eval_phase`, the FLEX judge.

## Data Model / Entities

- **Train/val/test split** — `sampler.split_dataset(data, seed, cache_path)`, identical seed
  and cache as GEPA (FR3, NFR2). TextGrad uses train for steps, val for keep-best, test for
  the after metric only.
- **Optimizable prompt** — single prompt `text2sql_system` (`PROMPT_NAME`), seeded from
  `SYSTEM_PROMPT_TEMPLATE` (with the `{schema}` placeholder). The TGD `tg.Variable` value is
  this template text; `render_system_prompt` substitutes the schema at task-call time inside
  `predict_fn` (the placeholder is preserved across edits via a TGD constraint, mirroring how
  GEPA mutates the same template). Saved verbatim by `optimize_prompts`' `register_prompt`
  (FR6).
- **Model roles (FR9)** — task model (`--model`/`--endpoint`), judge model
  (`--judge-model`/`--judge-endpoint`, the shared SQL judge), optimizer model
  (`--optimizer-model`/`--optimizer-endpoint`). All three logged as run params.
- **Run record** — extends the existing run with `technique`, the three model roles, `epochs`,
  `batch_size`, `max_steps_per_epoch`, `sampler_seed`, and split sizes; carries the same
  `{val,test}_quality_{before,after}` metrics and the registered optimized prompt (FR5, FR10).

## Interfaces / Contracts

### CLI — `text2sql-train-textgrad` (new entry point, separate command per the selection decision)

Options mirror `text2sql-train` for the shared roles, replacing GEPA's `--teacher-*` with:

| Option | Required | Notes |
|---|---|---|
| `--questions-path` / `--schema-path` / `--db-path` | yes | same as GEPA |
| `--model` / `--endpoint` | yes | task model (answers using the prompt) |
| `--judge-model` / `--judge-endpoint` | yes | shared SQL judge (FR9, A3) |
| `--optimizer-model` / `--optimizer-endpoint` | yes | proposes prompt edits (FR9) |
| `--epochs` | yes (no default) | full passes over train (FR10, C5) |
| `--batch-size` | yes/with-default | per-step batch; recorded (FR10, NFR3) |
| `--max-steps-per-epoch` | optional | per-epoch step cap; recorded (FR10, NFR3) |
| `--sampler-seed` | default 42 | split + TextGrad seed where supported (NFR2) |
| `--use-prod-questions` | flag | same as GEPA |

`pyproject.toml` `[project.scripts]`: `text2sql-train-textgrad = "experiments.text2sql.train_textgrad:train_textgrad"`.
`justfile`: a `text2sql-train-textgrad` recipe parallel to `text2sql-train`.

### Shared routine (extracted from current `train.py`)

`_run_optimization(*, optimizer, technique, extra_params, model, endpoint, judge_model,
judge_endpoint, schema_text, train_set, val_set, test_set, prompt_version, db_path, …)`:
opens the run, logs global + `technique` + `extra_params`, runs test-before, calls
`optimize_prompts`, logs `val_quality_before = result.initial_eval_score`, runs val/test-after,
logs the four metrics. GEPA's `train()` becomes a thin wrapper that builds `GepaPromptOptimizer`
and calls it; `train_textgrad()` builds `TextGradPromptOptimizer`. **A `technique` param is also
added to the GEPA run** (logging-only; no behavior change) so both are filterable (FR4, NFR1).

### `TextGradPromptOptimizer.optimize` contract

- Input: `eval_fn(candidate: dict[str,str], data: list[dict]) -> list[EvaluationResultRecord]`,
  `train_data`, `target_prompts` (single entry), `enable_tracking`.
- Asserts exactly one target prompt; refuses empty/too-small train or val split (EC3).
- Returns `PromptOptimizerOutput(optimized_prompts={name: best_template}, initial_eval_score,
  final_eval_score, *_per_scorer)`. Best = highest val mean (FR12, SC2a). `optimize_prompts`
  registers `best_template` (FR6).

### Optimizer-model engine / sampling params

New env family `OPTIMIZER_*` (`OPTIMIZER_TEMPERATURE/TOP_P/SEED`, optional `TOP_K`) read by
`read_sampling_params("OPTIMIZER")`, logged like `LLM_*`/`JUDGE_*`. Add defaults to
`.example.env`. Endpoint resolution reuses `ENDPOINTS` + `build_completion_kwargs`.

## TextGrad-internal mechanism (the one piece needing a spike)

Because the forward pass + judge come from `eval_fn` (outside TextGrad's autograd graph), the
judge rationale must be injected into TGD as a textual gradient on the prompt Variable.

- **Primary approach:** create `system_prompt = tg.Variable(seed, requires_grad=True, role_description=…)`
  and `optimizer = tg.TGD(parameters=[system_prompt], engine=CustomLiteLLMEngine(...), constraints=[…])`.
  Per batch, for each result build a feedback Variable embedding `(question, generated_sql,
  pass/fail, rationale)` and attach it to `system_prompt.gradients`, then `optimizer.step()`
  rewrites `system_prompt.value`. (TGD "reads textual gradients, constructs an optimization
  prompt, calls the backward engine, and updates `.value`.")
- **Fallback if direct gradient attachment isn't supported by the installed version:** wrap each
  example with a minimal TextGrad loss op (subclass the autograd `Function`) whose `backward`
  returns the rationale as the gradient on `system_prompt`, then `tg.sum(losses).backward()` +
  `optimizer.step()`.
- **TGD constraints** keep the `{schema}` placeholder intact and require "output a single system
  prompt only" so edits stay a valid, reusable template (FR6, parity with GEPA).

This is the first deliverable (Phase 1 spike) so the exact API is pinned before the rest is built.

## Phases / Dependencies

1. **Spike: TextGrad wiring + dependency.** Add `textgrad` to `pyproject.toml`; confirm it
   installs on Python 3.13. In a scratch script, build `CustomLiteLLMEngine` against a real
   endpoint, attach a hand-written gradient to a `tg.Variable`, and confirm `TGD.step()` rewrites
   it. Lock in the gradient-injection API (primary vs. fallback). *Blocks everything.*
2. **Refactor train.py into a shared routine** with GEPA still passing. Pure extraction; verify
   no behavior change (Testing Strategy regression check) — guards FR8/SC4.
3. **Implement `TextGradPromptOptimizer`** (`textgrad_optimizer.py`): engine, epoch/batch loop,
   gradient injection, per-epoch keep-best/revert (FR12), `PromptOptimizerOutput`. Per-epoch
   `eval_score` logged with `step=epoch`.
4. **Implement `train_textgrad.py` CLI** + entry point + justfile recipe + `OPTIMIZER_*` env
   defaults; wire to the shared routine. Logs `technique`, three roles, epochs/batch params (FR5).
5. **Error handling (EC1–EC5):** optimizer/judge model unreachable → exception propagates and the
   `mlflow.start_run` context marks the run FAILED with no optimized prompt (EC1, EC4, SC5);
   empty/small split refused up front (EC3); no-improvement reported honestly, best == baseline,
   no spurious new version (EC2, via `register_prompt_if_changed` semantics); prompt-save failure
   surfaced as a failed run (EC5).
6. **Docs:** brief note in the harness/README and `.example.env`.

## Risks & Open Questions

- **R1 (highest): gradient-injection API.** Exact `tg.Variable.gradients` / `TGD.step` surface
  varies by version. Mitigated by the Phase 1 spike and the documented fallback.
- **R2: Python 3.13 + textgrad compatibility.** Resolve in Phase 1; if unsupported, pin a
  compatible version or isolate the dep.
- **R3: optimizer-model endpoint routing.** TextGrad's stock engines assume `OPENAI_API_KEY`-style
  env. Mitigated by the `CustomLiteLLMEngine` using `build_completion_kwargs` (api_base/key per
  `ENDPOINTS`), parallel to GEPA's `configure_teacher_env`.
- **R4: comparability of effort units.** GEPA uses `max_metric_calls`; TextGrad uses epochs/batch.
  Both are recorded; report effort in both unit systems for side-by-side reasoning (NFR3).
- **R5: cost.** Each epoch adds a full val eval + per-batch task+judge calls. Keep `--epochs`/
  `--max-steps-per-epoch` small for first runs; all are recorded (NFR3).
- **OQ1: reverting in TextGrad.** Confirm `Variable.set_value`/equivalent for the keep-best revert
  during the spike; otherwise track best template in Python and rebuild the Variable.

## Testing Strategy

- **GEPA regression (SC4/FR8):** run `text2sql-train` on a tiny fixed split before and after the
  Phase 2 refactor; assert identical logged params/metrics and optimized template.
- **TextGrad happy path (SC1, SC2, FR2):** small split, `--epochs 1–2`; assert the run logs
  `technique`, three model roles, epochs/batch params, `{val,test}_quality_{before,after}` (same
  names as GEPA), and a retrievable registered prompt artifact.
- **Keep-best (SC2a/FR12):** unit-test `TextGradPromptOptimizer` with a stub `eval_fn` whose val
  score peaks at an early epoch then drops; assert the returned template is the early best, not
  the last.
- **Shared judge (FR9/A3/NFR1):** assert the metric name (`sql_is_correct/mean` →
  `*_quality_*`) and judge model recorded match the GEPA path on identical inputs.
- **Failure (SC5/EC1,EC4):** point `--optimizer-model` (and separately the judge) at an
  unreachable endpoint; assert a clear error, run marked FAILED, and no optimized prompt
  registered.
- **Guards (EC2/EC3):** stub a no-improvement run (report best==baseline, no new version) and an
  empty/too-small train split (refused up front).

## Traceability

| Requirement | Where addressed |
|---|---|
| FR1, FR4 | separate `text2sql-train-textgrad` command; `technique` param on both runs |
| FR2, FR6 | TGD rewrites `text2sql_system`; `optimize_prompts` registers it |
| FR3, FR9, FR11, A3, NFR1 | shared `eval_fn`/scorer → same judge, rationale + pass/fail, same metric names |
| FR5, FR10, NFR3 | run params: roles, epochs, batch_size, max_steps_per_epoch, split sizes |
| FR7, EC1–EC5, SC5 | Phase 5 error handling; `start_run` FAILED on exception |
| FR8, SC4, NFR4 | Phase 2 extraction with regression check; separate command/module |
| FR12, SC2a | per-epoch val keep-best / revert in `optimize()` |
| NFR2 | shared `sampler_seed`; `OPTIMIZER_*`/`LLM_*`/`JUDGE_*` seeds |

## Generated Artifacts

- `specs/textgrad-mlflow-integration/plan.md` (this file)
- To be created during implementation: `src/experiments/text2sql/textgrad_optimizer.py`,
  `src/experiments/text2sql/train_textgrad.py`; edits to `train.py` (shared routine),
  `harness.py` (`OPTIMIZER_*` params), `pyproject.toml`, `justfile`, `.example.env`.

Ready for task breakdown.
