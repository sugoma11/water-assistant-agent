# Implementation Plan: FAPO as a Prompt-Optimization Technique in MLflow

Spec: [`spec.md`](./spec.md) · Spike: [`spike-findings.md`](./spike-findings.md)
(Q1 resolved = strategy A; Q2–Q6 resolved by the Phase-1 spike).

## Technical Context

The harness already optimizes the text-to-SQL system prompt with **three** pluggable
optimizers through MLflow's
`mlflow.genai.optimize_prompts(..., optimizer=<BasePromptOptimizer>)` extension point:
`GepaPromptOptimizer` (`train_gepa.py`), `TextGradPromptOptimizer`
(`textgrad_optimizer.py` / `train_textgrad.py`), and `SkillOptPromptOptimizer`
(`skillopt_optimizer.py` / `train_skillopt.py`). All three share one technique-agnostic
routine, `_run_optimization(...)` in `src/experiments/text2sql/train_common.py`, which
opens the MLflow run, logs the global params + a `technique` tag + technique-specific
`extra_params` / `extra_artifacts`, runs the test-before / test-after eval phases around
`optimize_prompts`, and logs `test_quality_{before,after}`. The optimizer itself logs the
validation progression (`eval_score` / `eval_score.<scorer>` steps) plus
`initial_eval_score` / `final_eval_score` on the same run. Shared building blocks live in
`harness.py` (`build_sql_judge_scorer`, `create_predict_fn`,
`create_optimizable_predict_fn`, `build_completion_kwargs`, `completion_with_retry`,
`render_system_prompt`, `log_global_params`, `read_optimizer_params_for_logging`,
`make_run_name`, `register_prompt_if_changed`, `setup_mlflow`), `sampler.py`
(`split_dataset`), and `prompt_skill.py` (`instruction_block`, `recombine`,
`SCHEMA_MARKER`, `make_client`, `MIN_SPLIT_SIZE`).

**FAPO** ("hephaestus", `cisco-foundation-ai/fully-automated-prompt-optimization`) is
structurally unlike the prior three. The spike established (and this plan is built on)
the central fact: **FAPO's optimize loop is not Python — it lives in `.claude/agents/*.md`
agent prompts**; the only public Python entry point is an *Evaluate* stage
(`python -m hephaestus.cli eval`). Strategy A (resolved Q1) therefore **reimplements
FAPO's loop in Python** against the project's split / judge / endpoints, **porting the
guardrail and attribution *policies*** from the pinned agent prompts rather than calling
upstream loop code (there is none). This is the same shape as
`TextGradPromptOptimizer` — which likewise reimplements its loop in Python around
`eval_fn`, a `metric_call_budget` (GEPA parity), per-candidate keep-best on a val gate,
and honest no-improvement handling. **TextGrad is the closest structural sibling and the
primary reference implementation for this plan.**

FAPO's loop, ported to a single-step text-to-SQL task, reduces to:

> **attribute** (partition judged train cases into pass/fail) → **propose** (the optimizer
> model rewrites the instruction block from the failure cluster, producing a numbered
> `variant-NNN.md`) → **review** (deterministic guardrail checks on the variant text alone —
> no dataset cases shown to the reviewer: placeholder/`{schema}` integrity, no example-specific
> content (intrinsic leakage heuristic), instruction-block-only scope, output-rule retention)
> → **compare / keep-best** (score the variant on the val gate; keep iff the
> mean composite strictly improves, else reject/revert) → **iterate** until `variant_budget`,
> `metric_call_budget`, or a 3-consecutive-no-improvement plateau is hit.

Everything FAPO is famous for collapses to nothing on a single step: cross-step
attribution → one bucket; parameter-level edits (`retrieval_k`/temperature) and
structural edits (chain topology) → out of scope (FR12); retrieval/tool attribution,
skill files, synthetic-sample generation, tenant web UI, k8s tooling → not used
(Non-Goals). FAPO's micro/meso/macro granularities reduce to **micro (prompt text) only**.

### Key architectural decision

Implement **`FapoPromptOptimizer(mlflow.genai.optimize.optimizers.BasePromptOptimizer)`**
(new, `src/experiments/text2sql/fapo_optimizer.py`) and pass it as `optimizer=` to the
**same** `optimize_prompts` call the other three use, through the **same**
`_run_optimization` routine — so the FAPO run records the same metric names, the same
judge, and the same registered prompt as the others (FR1, FR3, FR8, FR9, A1, A3, NFR1).

The integration follows the proven **two-path** model-role design (Q3, A2, FR9):

- **Task (target) model + judge run through the project's own inference paths.** The
  attribution rollout and the val-gate scoring run the task model via the project litellm
  path (`build_completion_kwargs` + `completion_with_retry`, exactly as
  `Text2SqlEnvAdapter.rollout` does) and score with the **shared** `build_sql_judge_scorer`
  FLEX judge — so the task model is sampled identically to the val/test eval path and the
  judge verdict that drives attribution is the *same* pass/fail signal that produces the
  reported metric (FR9, FR11, A3, NFR2). Val-gate / endpoint scoring goes through `eval_fn`
  (the same predict_fn + judge GEPA scores with), keeping the reported numbers on one axis.
- **Optimizer (proposal/reflection) model runs through the project's own
  `make_client(optimizer_endpoint)`** (explicit `base_url`, Q3). This gives true per-role
  endpoints and **sidesteps FAPO's single-global-`OPENAI_BASE_URL` limitation entirely** —
  the port does **not** instantiate FAPO's `OpenAIClient`. The `OPENAI_BASE_URL` +
  `OPENAI_API_KEY` env trick (live-confirmed in the spike) is documented only as the
  fallback if a vendored FAPO provider object is ever used verbatim (EC9).

The optimizable unit is the prompt's **instruction block** (`SYSTEM_PROMPT_TEMPLATE`
before `Schema:`, via `instruction_block`); the DB schema is fixed context injected per
call by `render_system_prompt` and is never inside a variant. The accepted instruction
block is `recombine`d into the full template before the result is returned, so the
registered `text2sql_system` artifact stays a complete, reusable template with parity to
the other three (FR6, A6, SC6). This reuses `prompt_skill.py` **unchanged** — no new
extraction is needed (SkillOpt already lifted those helpers out of `textgrad_optimizer.py`).

## Architecture / Components

```
text2sql-train-gepa      ─┐
text2sql-train-textgrad   ├─►  _run_optimization(optimizer, technique, extra_params, …)  [shared, unchanged]
text2sql-train-skillopt   │         │
text2sql-train-fapo ──────┘         ├─ log_global_params + technique="fapo" + extra_params/extra_artifacts
   (new CLI)                        ├─ test-before eval phase (run_eval_phase)
                                    ├─ mlflow.genai.optimize_prompts(predict_fn, train, prompt_uris,
                                    │        optimizer=<Fapo>, scorers=[judge])
                                    │        └─ optimizer.optimize(eval_fn, train, prompts, tracking)
                                    ├─ test-after eval phase
                                    └─ log_metric(test_quality_{before,after})

FapoPromptOptimizer(BasePromptOptimizer)   [new, src/experiments/text2sql/fapo_optimizer.py]
   __init__(task_model, task_endpoint, optimizer_model, optimizer_endpoint,
            judge_scorer, schema_text, val_set,
            variant_budget=8, metric_call_budget=100, val_gate_size=8,
            plateau_patience=3, reflect_on_success=False,
            task_sampling_params, optimizer_sampling_params, reasoning_effort, seed=42)
   optimize(eval_fn, train_data, target_prompts, enable_tracking) -> PromptOptimizerOutput
       assert exactly one target prompt; refuse empty/too-small train or val split (EC3, MIN_SPLIT_SIZE)
       seed_instr = instruction_block(seed_template)
       initial_eval_score = _val_score(eval_fn, seed_instr, FULL val)      # reserved pass, outside budget
       best_gate = _gate_score(eval_fn, seed_instr, gate_set)              # cheap per-variant axis
       best_instr = seed_instr ; log eval_score(step=0)=best_gate
       for variant_idx in 1..variant_budget while budget left and not plateau:
           results   = _attribute(best_instr, train)        # cached on best_instr: re-rolls train only
                                                            #   on baseline + each acceptance (F-003)
           proposal  = _propose(best_instr, failures[, successes])  # optimizer client rewrites instruction
           variant   = persist variant-NNN.md in run-scoped tmp tenant dir (NFR5)
           review    = _review(variant)                     # variant text only, no dataset cases (F-001, FR14)
           if review rejected: log, plateau++ , continue (EC5/EC6)
           gate      = _gate_score(eval_fn, variant, gate_set) ; log eval_score(step=variant_idx)
           if gate > best_gate: best_gate, best_instr = gate, variant ; plateau=0   # strict keep-best (FR13, SC2a)
           else:                plateau++                                            # tie or worse -> revert (F-005)
           if plateau >= plateau_patience: break                                     # FAPO native stop
       final_eval_score = _val_score(eval_fn, best_instr, FULL val)        # reserved pass, outside budget
       mlflow.log_artifacts(tenant_dir) from inside optimize()  # variants + iteration-memory (NFR5, F-002)
       if final<=initial: return seed_template byte-for-byte (EC2)
       else: optimized = recombine(best_instr)  ; assert "{schema}" present (FR6, SC6)
       return PromptOptimizerOutput(optimized_prompts={name: optimized},
                                    initial_eval_score, final_eval_score, *_per_scorer)

fapo_vendor/   [new, vendored & pinned to 2ed526a7b74908f1ca51a6d453c1837f404b39ef, Apache-2.0 provenance]
   compare.py        -> mean-composite-delta keep-best  (port of runs/compare._score_delta)
   score.py          -> validate_score_payload-style 0–100 wrapper (port of scoring/runtime)
   review.py         -> deterministic guardrails on variant text only, no dataset cases (port of
                        .claude/agents/variant-reviewer.md; intrinsic leakage heuristic, F-001)
   policy.py / *.md  -> single-step failure-reflection proposal prompt (port of optimization.md /
                        step-attribution.md), used to build the optimizer-model proposal call
```

Reused **unchanged**: `_run_optimization`, `run_eval_phase`, `PROMPT_NAME`,
`split_dataset`, `load_dataset`, `create_predict_fn`, `create_optimizable_predict_fn`,
`build_sql_judge_scorer`, `setup_mlflow`, `log_global_params`,
`read_optimizer_params_for_logging`, `register_prompt_if_changed`, the FLEX judge, and
all of `prompt_skill.py` (`instruction_block`, `recombine`, `make_client`,
`MIN_SPLIT_SIZE`). No change to the other three optimizers (FR8, SC4).

## Data Model / Entities

- **Train/val/test split** — `split_dataset(data, seed, cache_path)`, identical seed and
  embedding cache as the other three (FR3, NFR2, EC3). FAPO attributes on train, gates on
  a fixed val subset, reports endpoints on full val; test is the after-metric only,
  evaluated by `_run_optimization`.
  *Amended:* the split is now 20 train / 55 test with val a copy of train, not equal
  thirds — see [`specs/sampling_refactoring/spec.md`](../sampling_refactoring/spec.md). Native dataset adapter: the project's
  `{inputs:{question}, expectations:{sql, argilla_link}}` shape is used directly by the
  task rollout + judge; FAPO's JSONL `case_id/context/expected` shape is **not** persisted
  (we replace `eval_runner`/`datasets` per Q6).
- **System prompt / instruction block** — single prompt `text2sql_system` (`PROMPT_NAME`).
  Only the instruction block is editable; the schema is fixed context (`render_system_prompt`)
  and never enters a variant or the proposal/attribution prompts (A6, FR6, FR12). The
  accepted block is `recombine`d into the full template before return (SC6).
- **Variant** — FAPO's unit of change: a numbered, immutable `variant-NNN.md` file holding
  one proposed instruction block, written into a run-scoped tmp tenant dir, reviewed, and
  accepted-on-val or rejected. Count bounded by `variant_budget` (FR10).
- **Attribution record** — per-train-case judge pass/fail + rationale, partitioned into a
  failure cluster (always) and optionally a success cluster (`reflect_on_success`); the
  signal the proposal step reflects on. Single-step ⇒ one bucket (Q5).
- **Score** — the boolean FLEX judge mapped deterministically **pass→100.0 / fail→0.0**
  via the vendored `score.py` wrapper for FAPO-internal compatibility, but the **reported**
  metric and the keep-best comparison run on the equivalent `eval_fn` mean pass-rate in
  `[0,1]` (mean composite / 100), so FAPO's keep-best axis and the headline MLflow metric
  are the *same* number (Q4).
- **Model roles (FR9)** — task model (`--model`/`--endpoint`, project litellm path), judge
  model (`--judge-model`/`--judge-endpoint`, shared FLEX judge), optimizer model
  (`--optimizer-model`/`--optimizer-endpoint`, `make_client` explicit `base_url`). All
  three + endpoints recorded.
- **Run record** — extends the run with `technique="fapo"`, the three roles, the effort
  knobs (`variant_budget`, `per_variant_eval_size`/`val_gate_size`, `metric_call_budget`,
  `plateau_patience`), `reflect_on_success`, the prompt-only restriction marker (FR12),
  `reasoning_effort`, `OPTIMIZER_*` sampling params, `sampler_seed`, split sizes; carries
  the same metrics as the others and the registered optimized prompt (FR5, FR10, NFR3).
  The tmp tenant dir (variants + iteration-memory) is attached as run artifacts (NFR5).

## Interfaces / Contracts

### CLI — `text2sql-train-fapo` (new entry point, mirrors `train_skillopt`)

| Option | Required | Notes |
|---|---|---|
| `--questions-path` / `--schema-path` / `--db-path` | yes | same as the other three |
| `--model` / `--endpoint` | yes | task (target) model — project litellm path (A2) |
| `--judge-model` / `--judge-endpoint` | yes | shared FLEX SQL judge (FR9, A3) |
| `--optimizer-model` / `--optimizer-endpoint` | yes | FAPO proposal/reflection model via `make_client` (FR9, Q3) |
| `--variant-budget` | yes (no default) | max proposed-and-evaluated variants (FR10, Q2) |
| `--val-gate-size` | yes (no default) | per-variant keep-best subset size = `per_variant_eval_size` (FR10, Q2) |
| `--metric-call-budget` | default 100 | task+judge call cap for GEPA cost parity (FR10, NFR3, Q2) |
| `--plateau-patience` | default 3 | consecutive non-improving variants before early stop (Q2, EC8) |
| `--reflect-on-success` / `--no-reflect-on-success` | default off | success-reflection toggle, recorded (FR11, Q5) |
| `--reasoning-effort` | default high | reasoning effort for the optimizer model (mirrors SkillOpt) |
| `--sampler-seed` | default 42 | split seed + optimizer/gate seed (NFR2) |
| `--use-prod-questions` | flag | same as the other three |

No implicit effort default is assumed correct: `--variant-budget` and `--val-gate-size`
are required, human-set, and recorded (FR10, NFR3). `metric_call_budget` defaults to 100
for GEPA parity but is overridable and recorded.

`pyproject.toml` `[project.scripts]`:
`text2sql-train-fapo = "experiments.text2sql.train_fapo:train_fapo"`.
`experiments.just`: a `text2sql-train-…-fapo-N` recipe family parallel to the SkillOpt one
(student-judge-optimizer-technique-seed naming), passing `--optimizer-model`/`--optimizer-endpoint`
from `teacher-*`, plus `--variant-budget` / `--val-gate-size` / `--metric-call-budget`.

### `FapoPromptOptimizer.optimize` contract

- Input: `eval_fn(candidate: dict[str,str], data) -> list[EvaluationResultRecord]`,
  `train_data`, `target_prompts` (single entry), `enable_tracking`.
- Asserts exactly one target prompt; refuses empty/too-small train **or** val split up
  front, before any LLM call (EC3), reusing the `MIN_SPLIT_SIZE` guard.
- Runs the loop in a `tempfile.TemporaryDirectory()` run-scoped tenant dir; never pollutes
  the repo tree (NFR5). Captures the tenant dir as MLflow artifacts on the active run.
- Budget: whichever of `variant_budget` / `metric_call_budget` / `plateau_patience` is hit
  first stops the loop (EC8). The two reported full-val passes (baseline + best) sit
  **outside** `metric_call_budget`, mirroring GEPA's `2*len(val) + MAX_METRIC_CALLS` and
  TextGrad's accounting.
- Returns `PromptOptimizerOutput(optimized_prompts={name: best_template}, initial_eval_score,
  final_eval_score, *_per_scorer)`. `best_template = recombine(best_instr)` only on a
  **strict full-val gain** over baseline; else the seed `target_prompts[name]`
  byte-for-byte so `register_prompt_if_changed` dedups it and no spurious version is
  registered (EC2).

### Loop primitive contracts (the ported policies, Q6)

| Step | Helper | Ported from (pinned `2ed526a`) | Behaviour |
|---|---|---|---|
| attribute | `_attribute` | `optimization.md` principle 4, `step_attribution.md` | task rollout + shared judge → pass/fail + rationale; failures always, successes iff `reflect_on_success`; **cached on `best_instr`** (re-rolls only on baseline + each acceptance, F-003); **EC4: re-raise on judge error** |
| score wrap | `fapo_vendor/score.py` | `scoring/runtime.validate_score_payload` | pass→100/fail→0, `score_breakdown={"sql_is_correct": composite}`; rejects bool/out-of-range |
| propose | `_propose` | `optimization.md`, `step-attribution.md` | optimizer client rewrites instruction block from the failure cluster; schema-free prompt; strip `openai/` prefix for the raw `make_client` call (F-006) |
| review | `fapo_vendor/review.py` | `.claude/agents/variant-reviewer.md` | block-severity checks on the **variant text alone, no dataset cases** (F-001) → reject (FR14): `{schema}`/placeholder integrity, **no example-specific content (intrinsic leakage heuristic)**, instruction-only scope, keep the single-DuckDB-query output rule |
| compare | `fapo_vendor/compare.py` | `runs/compare._score_delta` | mean-composite delta vs. running best → keep-best (FR13, SC2a) |

### Effort → FAPO loop mapping (FR10, NFR3, Q2)

| Spec knob (recorded) | Loop variable | Notes |
|---|---|---|
| variant_budget (default 8) | `variant_budget` | hard cap on proposed-and-evaluated variants; numeric stand-in for FAPO's plateau |
| per_variant_eval_size (val) | `val_gate_size` (default 8) | fixed val subset for the per-variant keep-best gate (same knob TextGrad uses) |
| per_variant_eval_size (train) | full train split | attribution rollout scores every train case (FAPO has no per-variant subsample), but the pass is **cached on `best_instr`** so it recurs only on baseline + each acceptance, not per rejected variant (F-003) |
| metric_call_budget (default 100) | `metric_call_budget` | task+judge cap for GEPA parity; counts the attribution passes actually run (baseline + acceptances) + each gate pass; 2×full-val passes reserved outside it |
| plateau_patience (default 3) | `plateau_patience` | FAPO's native "3 consecutive no-improvement variants" stop |
| reflect_on_success (default off) | `reflect_on_success` | `failure_only = not reflect_on_success` (Q5) |
| seed | `seed` | val-gate subset draw + per-variant proposal seed where supported (NFR2) |

### Optimizer-model engine / sampling params

Reuse the existing `OPTIMIZER_*` env family (`read_optimizer_params_for_logging()`) —
logged **on the FAPO run only** via `extra_params`, never in `log_global_params`, so the
other three runs stay byte-identical (FR8). The optimizer client is built with
`make_client(optimizer_endpoint)` (project endpoint only, never a library default; Q3, A2);
`make_client` already raises an actionable `ValueError` on an unknown endpoint or unset
`*_API_BASE`/`*_API_KEY` (EC1, the friendly-error pattern). A leading `openai/` is stripped from
`optimizer_model` before the raw `make_client` call (the client speaks the OpenAI-compatible API
directly and 404s on a litellm prefix), mirroring `skillopt_optimizer.py:383`; the task role keeps
its prefix on the litellm path (F-006). `reasoning_effort` is forwarded
to the optimizer call as the OpenAI `reasoning_effort` param, mirroring SkillOpt.

## Metric / comparability discipline (NFR1, SC2, SC3)

The FAPO run emits the **same headline metric names** as the other three — no new
`val_quality_*` metric:

- per-variant validation progression under `eval_score` / `eval_score.sql_is_correct`
  (step = variant index; step 0 = baseline gate score), logged by the optimizer;
- `initial_eval_score` / `final_eval_score` (full-val baseline + best), returned in
  `PromptOptimizerOutput` and logged by `optimize_prompts` on the run;
- `test_quality_{before,after}`, logged by `_run_optimization`.

The keep-best axis (mean composite / 100 over the gate) and the reported `eval_score`
are the **same number** (Q4), so the series, the endpoints, and the gate decision all sit
on one axis — the single-axis discipline TextGrad and SkillOpt already follow. The run
never scores or selects on the test split (SC7): attribution uses train, keep-best uses
val; test is only the after-metric in `_run_optimization`.

## Phases / Dependencies

The Phase-1 **spike is complete** ([`spike-findings.md`](./spike-findings.md), GO for
strategy A): endpoint seam live-confirmed, primitives identified, Q2–Q6 resolved. The
remaining phases build on those resolutions.

1. **Vendor pinned FAPO primitives** into `src/experiments/text2sql/fapo_vendor/`,
   pinned to `2ed526a7b74908f1ca51a6d453c1837f404b39ef` with Apache-2.0 provenance headers
   citing the upstream `.py` / `.md` files (A7, Q6): the `compare` mean-composite-delta
   keep-best, the `validate_score_payload`-style 0–100 score wrapper, the
   `variant-reviewer.md` guardrails as deterministic checks, and the single-step
   failure-reflection proposal-policy prompt text from `optimization.md` /
   `step-attribution.md`. **Do NOT** vendor `step_attribution.py`,
   `engine/prompt_renderer.py`, `eval_runner.py`, `cli.py`, providers, chains, mcp, webui,
   datasets/stratified_split (Q6, Non-Goals, NFR4). *Blocks 2–3.*
2. **Implement the review guardrails + score wrapper** as a self-contained, deterministic,
   network-free module (block-severity → reject): `{schema}`/placeholder integrity, no
   example-specific content (intrinsic leakage heuristic on the variant text — the reviewer is
   given **no** dataset cases, F-001), instruction-block-only scope, output-rule retention (FR14,
   EC6, SC6, SC7). These are pure functions of the variant text. (No isolated tests — dropped per
   F-004; exercised end-to-end in phases 5–6.)
3. **Implement `FapoPromptOptimizer`** (`fapo_optimizer.py`): split guard (EC3); baseline
   full-val + gate; the attribute → propose → review → compare/keep-best loop with the
   triple stop condition (`variant_budget` / `metric_call_budget` / `plateau_patience`,
   EC8); task rollout via `build_completion_kwargs` + `completion_with_retry` + shared
   judge (EC4 re-raise), **attribution cached on `best_instr`** (F-003); proposal via
   `make_client(optimizer_endpoint)`; **strict** keep-best (tie → plateau, F-005); `eval_score`
   series + endpoints via `_val_score`/`_gate_score`; honest no-improvement (EC2);
   `recombine` + `{schema}` assertion (FR6, SC6); run-scoped tmp tenant dir captured via
   `mlflow.log_artifacts` from inside `optimize()` (NFR5, F-002).
4. **Implement `train_fapo.py` CLI** + `text2sql-train-fapo` entry point + `experiments.just`
   recipe; wire to the shared `_run_optimization`. Source the optimizer's task role from the same
   `--model`/`--endpoint` + sampling params that back `predict_fn`, so attribution and the gate
   share one task-model axis (F-007). Logs `technique="fapo"`, the three
   roles, the effort knobs, `reflect_on_success`, the prompt-only marker (FR12),
   `reasoning_effort`, split sizes, and `OPTIMIZER_*` via `extra_params` (FR5, FR10, NFR3). The
   tenant dir is captured inside `optimize()` (phase 3), **not** via `extra_artifacts` (F-002).
5. **Error handling (EC1–EC9):** task/optimizer/judge unreachable or judge failure →
   exception propagates and `mlflow.start_run` marks the run FAILED with no optimized
   prompt (EC1, EC4, SC5); empty/small split refused up front (EC3); no-improvement returns
   the seed byte-for-byte (EC2); a round with no review-passing variant keeps the current
   best and counts toward plateau, never applies a failed variant (EC5, EC6); budget
   exhaustion terminates with best-so-far, never hangs (EC8); `{schema}`-loss or
   prompt-save failure → failed run with a clear message (EC6/EC7); per-role endpoints
   resolved explicitly so EC9 cannot mis-route (the global-`OPENAI_BASE_URL` reject only
   applies if the vendored provider is ever used verbatim).
6. **Docs + env:** document FAPO as the fourth technique in `README.md` (mirroring the
   SkillOpt write-up), noting the strategy-A port and the pinned vendored commit; the
   `OPTIMIZER_*` defaults already exist in `.example.env` (shared) — extend only if a new
   FAPO-specific env var is introduced (none expected, since the port avoids FAPO's provider).

## Risks & Open Questions

- **R1: the loop is policy, not code (resolved into the design).** FAPO ships no Python
  `optimize()`; the port reimplements the loop and reproduces the guardrail/attribution
  *policies* from the pinned agent `.md` files. Provenance must cite those `.md` files, not
  just `.py` (spike "biggest surprise"). Mitigated: TextGrad already proves a Python-loop
  port against `eval_fn` + budget + keep-best works in this harness.
- **R2: NFR2 non-determinism.** The proposal step is an LLM rewrite on the optimizer model,
  so the run is inherently non-deterministic. Record optimizer model + seed + temperature /
  `reasoning_effort` so a run is *describable*; data/split/judge stay reproducible. Called
  out per NFR2.
- **R3: `score_breakdown` partial-credit trap (Q4).** Keep `score_breakdown` equal to the
  {100,0} composite; a fractional sub-score would desync `compare`'s per-check averaging
  from the composite. Enforced in the vendored `score.py` wrapper.
- **R4: effort-unit comparability (NFR3).** FAPO uses variants/iterations vs. GEPA's
  metric-call budget vs. TextGrad/SkillOpt epochs. The `metric_call_budget=100` GEPA-parity
  cap plus the three recorded knobs make cost reasoned-about side by side even though units
  differ.
- **R5: cost.** Attribution rolls out the full train split, but is **cached on `best_instr`** so
  it recurs only on baseline + each acceptance, not per rejected variant (F-003); the recurring
  per-variant cost is the val-gate pass. Keep `--variant-budget` and `--val-gate-size` small for
  first runs. All recorded, and `metric_call_budget` is the hard cap (EC8).
- **R6: provider retry path.** Routing all three roles through the project paths
  (`completion_with_retry` / `make_client`) means the project's patient retry policy
  applies and FAPO's weaker 10×/5 s built-in retry is unused — the recommended path
  (spike). The vendored FAPO provider is *not* instantiated.
- **OQ1 (decision taken): keep-best axis.** Compare on the `eval_fn` mean pass-rate `[0,1]`
  (== mean composite / 100), not a separately maintained 0–100 mean, so the gate decision
  and the logged `eval_score` are byte-identical numbers. The 0–100 `score.py` wrapper
  exists only for FAPO-contract fidelity where a reason string feeds attribution.
- **OQ2 (open, low-risk): step granularity of the `eval_score` series.** One variant = one
  logged step (step 0 = baseline). If a rejected-on-review variant should still occupy a
  step is a cosmetic choice; default — log a step only for review-passing variants that
  reach the gate, matching TextGrad's "one logged step per scored candidate".

## Traceability

| Requirement | Where addressed |
|---|---|
| FR1, FR4 | separate `text2sql-train-fapo` command; `technique="fapo"` param |
| FR2, FR6, A6, SC6 | FAPO rewrites the instruction block; `recombine` + `{schema}` assertion restores the full template; `optimize_prompts` registers it |
| FR3, FR9, FR11, A3, NFR1 | shared `eval_fn`/scorer → same judge; attribution pass/fail = judge verdict drives both the partition and the metric; same metric names |
| FR5, FR10, NFR3, Q2 | run params: three roles, `variant_budget`, `val_gate_size`, `metric_call_budget`, `plateau_patience`, `reflect_on_success`, prompt-only marker, split sizes |
| FR7, EC1–EC9, SC5 | Phase 5; `start_run` FAILED on exception; honest no-improvement; budget-bounded termination |
| FR8, SC4, NFR4 | new module + separate command; `_run_optimization` untouched; `OPTIMIZER_*`/tenant on FAPO run only; vendored primitives isolated |
| FR12, A4 | only `instruction_block` is editable; review scope check rejects schema re-entry; parameter/structural edits unused; recorded |
| FR13, SC2a, SC7, Q4 | per-variant keep-best on val gate (mean composite delta); train=attribution, val=keep-best, test never inspected |
| FR14, EC5, EC6 | vendored deterministic review guardrails reject scope/leakage/placeholder/output-rule violations before apply |
| FR9, Q3, A2, EC9 | task+judge on project inference paths; optimizer via `make_client` explicit `base_url`; per-role endpoints; FAPO provider not used |
| NFR2 | shared `sampler_seed` → split + gate draw; optimizer seed/temperature/`reasoning_effort` recorded; non-determinism called out |
| NFR5 | run-scoped tmp tenant dir; variants + iteration-memory captured as MLflow artifacts |
| A7, Q6 | `fapo_vendor/` pinned to `2ed526a…` with provenance; minimal primitives ported, heavy footprint excluded |

## Generated Artifacts

- `specs/fapo-mlflow-integration/plan.md` (this file)
- To be created during implementation:
  `src/experiments/text2sql/fapo_optimizer.py`,
  `src/experiments/text2sql/train_fapo.py`,
  `src/experiments/text2sql/fapo_vendor/` (pinned, provenance-headed: `compare.py`,
  `score.py`, `review.py`, proposal-policy text); edits to `pyproject.toml`,
  `experiments.just`, `README.md`, and `.example.env` only if a new FAPO env var is added
  (none expected). `prompt_skill.py` and the other three optimizers are reused **unchanged**.

Ready for task breakdown.
</content>
</invoke>
