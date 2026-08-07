# Feature Specification: FAPO as a Prompt-Optimization Technique in MLflow

## Overview / Context

This project evaluates different automated prompt-optimization techniques for a
water-management text-to-SQL assistant. The experiment harness can already optimize
the assistant's system prompt with three families of techniques — the GEPA reflection
optimizer, TextGrad, and SkillOpt — and records every run (baseline scores, optimized
scores, and the resulting prompt) in MLflow so techniques can be compared side by side.
Each technique is plugged into the *same* `mlflow.genai.optimize_prompts` call as a
`BasePromptOptimizer` subclass, scored by the same shared FLEX SQL judge, on the same
seeded train/validation/test split (see `train_gepa.py`, `train_textgrad.py`,
`train_skillopt.py` and the shared `train_common._run_optimization`).

This feature adds **FAPO** (Fully Automated Prompt Optimization,
https://github.com/cisco-foundation-ai/fully-automated-prompt-optimization) as an
additional, selectable prompt-optimization technique that runs inside the same harness,
on the same data split, judged by the same quality measure, and tracked the same way.
FAPO is itself built **on top of GEPA as its baseline** — it keeps GEPA's reflective,
keep-best-on-validation loop but replaces GEPA's evolutionary search with an
**attribution-driven, prompt-first scoped-edit loop with an independent review step**.
The purpose of this feature is to obtain a fair, apples-to-apples comparison of FAPO
against the techniques already available ("use FAPO as GEPA, with MLflow integration"),
not to change how the assistant itself answers questions.

### Why FAPO is a larger integration than the prior three

GEPA, TextGrad, and SkillOpt are Python libraries whose optimization loop can be driven
in-process and wrapped in a `BasePromptOptimizer.optimize()` method. **FAPO is not.** It
is an *agentic* system ("hephaestus"): its optimization loop is orchestrated by a coding
agent (Claude Code / Codex) executing prompt-defined commands
(`.claude/agents/optimization.md`, `step-attribution.md`, `variant-reviewer.md`), it
organizes work into file-based **tenants** (`tenants/<id>/` with prompts, datasets,
scorers, chains, and iteration memory), it targets **multi-step LangGraph pipelines**,
and it exposes **no public Python API for the full optimize loop** — only a single
*Evaluate* stage via `python -m hephaestus.cli eval --config eval.json`. The remaining
stages (attribute → propose → review → compare → iterate/escalate) are agent-driven.

This impedance mismatch was the central design question for this feature. It is
**resolved (Q1, 2026-06-28): strategy (A)** — FAPO's loop is **ported into a
`FapoPromptOptimizer(BasePromptOptimizer)`** that runs in-process inside the same
`mlflow.genai.optimize_prompts` call the other three techniques use, reusing the proven
`_run_optimization` plumbing, per-round `eval_score` logging, and keep-best semantics.
The remaining clarifications (Q2–Q6) are scoped to that decision and are listed below.

## Goals

- Make FAPO available as a prompt-optimization technique that produces an improved
  version of the assistant's system prompt from training examples, using FAPO's
  attribution-driven, prompt-first, review-gated loop that keeps the best prompt seen on
  the validation split.
- Let an experimenter choose which technique to run (FAPO, SkillOpt, TextGrad, or GEPA)
  for a given optimization run.
- Reuse the existing dataset, train/validation/test split, and quality judge so results
  are directly comparable across techniques.
- Record each FAPO run in MLflow with the same before/after quality metrics and the same
  saved-prompt artifact as existing techniques, so a reviewer can compare techniques from
  the tracking UI alone.

## Non-Goals

- Changing the assistant's runtime behavior, the question dataset, or the quality judge.
- Improving or tuning FAPO's internal algorithm itself.
- Optimizing anything other than the assistant's system prompt. In FAPO's terms this
  rules out its **parameter-level** edits (e.g. `retrieval_k`, temperature) and its
  **structural-level** edits (chain topology / adding nodes); only FAPO's
  **prompt-level** optimization is in scope (see A4, FR12).
- Adopting FAPO's multi-step pipeline / chain-structure optimization, its step-level
  failure attribution *across multiple steps*, its tenant web UI, its synthetic-sample
  generation/pruning, or its Kubernetes/deploy tooling. The text-to-SQL task is a single
  step, so cross-step attribution collapses to single-step reflection (A4).
- Adding optimization techniques other than FAPO.
- Building a user-facing UI; the audience is experimenters running the harness.
- Forking FAPO to maintain a long-lived divergent copy; any FAPO code used MUST be a
  pinned, declared dependency or a thin, clearly-scoped adapter (see Clarifications).

## User Stories / Scenarios

1. **As an experimenter**, I can start an optimization run and select FAPO as the
   technique, so that it improves the system prompt using the training split.
2. **As an experimenter**, I can run FAPO and an already-available technique (GEPA,
   TextGrad, or SkillOpt) on the same dataset and split, so that I can compare their
   results fairly.
3. **As a reviewer**, I can open the tracking UI and see, for a FAPO run, the technique
   used, the quality before and after optimization on validation and test, and the
   optimized prompt, so that I can judge whether the technique helped.
4. **As an experimenter**, if FAPO fails partway through a run, I receive a clear error
   and the run is recorded as failed rather than silently producing a misleading result.

## Functional Requirements

- FR1. The harness MUST offer FAPO as a selectable prompt-optimization technique
  alongside the existing technique(s).
- FR2. Selecting FAPO MUST produce a revised version of the assistant's system prompt
  derived from the training examples.
- FR3. A FAPO run MUST use the same dataset, the same seeded train/validation/test
  split, and the same quality judge as the existing technique(s) when given the same
  inputs.
- FR4. The technique selection MUST be explicit and recorded with the run (technique
  name `fapo`), so a reviewer can tell which technique produced a given prompt.
- FR5. Each FAPO run MUST record, at minimum: the technique name, all three model names
  involved (see FR9), the dataset and split sizes, the configured effort (see FR10), the
  quality score before optimization, the quality score after optimization (on both
  validation and test), and the resulting optimized prompt as a retrievable artifact.
- FR6. The optimized prompt MUST be saved/versioned the same way existing techniques
  save their result (the `text2sql_system` registered prompt), and MUST be a complete,
  reusable system prompt of the same shape as the original, so downstream comparison and
  reuse work identically. FAPO edits only the isolated, editable instruction block; the
  unedited fixed context (the database schema) MUST be preserved unchanged in the saved
  result (reuse `prompt_skill.instruction_block` / `recombine`, as TextGrad and SkillOpt
  do).
- FR7. If FAPO cannot complete (e.g. one of its models is unreachable, its agent loop
  errors out, no usable variant is produced, or the result cannot be read back), the run
  MUST surface a clear, actionable error and MUST NOT report an optimized result as if it
  succeeded.
- FR8. Adding FAPO MUST NOT change the behavior or results of the existing technique(s)
  (GEPA, TextGrad, SkillOpt). FAPO-specific params/artifacts MUST be logged only on the
  FAPO run (via `extra_params`/`extra_artifacts`), never added to the shared
  `log_global_params`.
- FR9. A FAPO run MUST allow three model roles to be configured independently: the
  **task model** that answers using the prompt being optimized, the **optimizer model**
  that drives FAPO's attribution/proposal/review (the model the FAPO loop uses to reflect
  and rewrite), and the **judge model** that scores a candidate. Each MUST be recorded
  with the run. All three roles MUST be pointed at the project's own blablador / kisski
  endpoints, not any external or library-default endpoint (FAPO's OpenAI provider
  hardcodes `api.openai.com`; see Q3 and A2). The judge role MUST be the same FLEX SQL
  judge already used by the existing techniques, so all techniques are scored by an
  identical measure.
- FR10. The optimization effort MUST be expressed and recorded in FAPO's own terms — at
  minimum an **iteration/variant budget** (the maximum number of candidate variants
  proposed-and-evaluated) plus the **per-variant evaluation set size** (how many training
  cases each variant is scored on per round). These MUST be human-set run parameters (no
  implicit default is assumed correct) and MUST be recorded with the run. Because FAPO
  expresses effort differently from GEPA's metric-call budget, the recorded knobs MUST be
  enough to reason about cost comparability (NFR3).
- FR11. The judge's per-example pass/fail outcome MUST be the optimization signal: it
  partitions graded training examples into successes and failures that FAPO's attribution
  step reflects on to propose edits, AND it produces the comparable before/after quality
  metrics, so the optimization signal and the reported metric come from one shared judge.
  Where FAPO expects a 0–100 `composite_score`, the shared boolean judge verdict MUST be
  mapped deterministically (pass → 100, fail → 0) so FAPO's internal compare and the
  reported MLflow metric stay in agreement (see Q4).
- FR12. FAPO MUST be restricted to its **prompt-level** optimization for this run: only
  the editable instruction block of the system prompt may change. Parameter-level and
  structural-level edits MUST be disabled or unused (the assistant is a single-step
  chain), and this restriction MUST be recorded with the run.
- FR13. At the end of each round FAPO MUST evaluate the candidate prompt on the
  validation split and keep the best-scoring prompt, rejecting (reverting) a candidate
  that does not improve on validation — never selecting a variant by inspecting the test
  split — so the reported result is the best prompt found rather than the last one tried.
- FR14. FAPO's review/guardrail step (variant-reviewer: scope, leakage, placeholder
  drift) MUST be preserved or faithfully reproduced so that an accepted variant cannot
  (a) re-introduce the fixed schema into the instruction block, (b) drop the required
  output rules, or (c) contain example-specific content (a memorized case) rather than
  general instructions. The leakage check MUST be an **intrinsic heuristic on the variant
  text alone** — the review step is given no dataset cases (train, val, or test) — flagging
  hallmarks of a pasted case (fully-formed `SELECT … WHERE …` statements, hardcoded
  literals/IDs, verbatim question strings). This both honors SC7 (the run never inspects
  the test split) and is sufficient because leakage is prevented by construction: the
  schema-free proposal prompt is built only from train failures, so the optimizer model
  never sees val/test text. A variant that fails review MUST be rejected, not applied
  (this is FAPO's analogue of TextGrad's `TGD_CONSTRAINTS` and SkillOpt's bounded edits).

## Non-Functional Requirements

- NFR1. **Comparability** — When run on identical inputs, the recorded inputs and metric
  names MUST line up across techniques so results can be compared directly. The FAPO run
  MUST emit the same headline metric names the other techniques do (see SC2): the
  per-round validation progression under `eval_score` / `eval_score.sql_is_correct`, the
  run's `initial_eval_score` / `final_eval_score`, and `test_quality_{before,after}`. No
  new `val_quality_*` metric is introduced.
- NFR2. **Reproducibility** — Given the same dataset, split seed, and models, a FAPO
  run's setup (data, split, judge) MUST be reproducible; any inherent randomness in the
  technique MUST be controllable via a configurable seed where the technique supports it.
  Where FAPO's agent-driven loop is inherently non-deterministic, that MUST be called out
  and the seed/temperature of the optimizer role recorded so runs are at least
  *describable*, not silently irreproducible.
- NFR3. **Cost transparency** — The amount of optimization effort (for FAPO, the
  iteration/variant budget plus per-variant eval size, FR10) MUST be configurable and
  recorded, so comparisons account for the work each technique was given. Because
  techniques express effort differently (variants/iterations here vs. evaluation-call
  budgets for GEPA vs. epochs for TextGrad/SkillOpt), the recorded effort SHOULD be
  enough to reason about comparability even though the units differ.
- NFR4. **Isolation** — A failure or change in FAPO MUST NOT degrade or block the
  existing techniques. FAPO's heavier footprint (file-based tenants, an agent subprocess,
  or a vendored dependency) MUST be isolated so it cannot perturb a GEPA/TextGrad/SkillOpt
  run on the same machine.
- NFR5. **Containment** — FAPO's file-based artifacts (tenant directory, numbered variant
  files, iteration-memory) MUST live under a run-scoped working directory and be captured
  as MLflow artifacts where useful for audit, not scattered into the repo tree.

## Data / Entities

- **Training/validation/test examples** — existing question-and-reference-SQL records,
  reused unchanged from `split_dataset`. FAPO's native dataset is JSONL with
  `case_id` / `context` / `expected`; an adapter MUST map the project's
  `{inputs: {question}, expectations: {sql, argilla_link}}` records to/from that shape
  without altering the split.
- **System prompt** — the existing optimizable prompt; FAPO produces a new version of it
  by editing the instruction block as a numbered prompt variant and recombining it into
  the full `SYSTEM_PROMPT_TEMPLATE` shape.
- **Variant** — FAPO's unit of change: a numbered, immutable prompt file
  (`variant-NNN.md`) proposed by the loop, reviewed, and either accepted on validation or
  rejected. Multiple variants per run are bounded by the iteration/variant budget (FR10).
- **Attribution record** — FAPO's per-case failure classification used to decide the
  next scoped edit; for the single-step SQL task this is single-step reflection on judged
  failures (and optionally successes).
- **Model roles** — three independently configurable models per FAPO run: task model,
  optimizer model, and judge model.
- **Run record** — the existing per-run tracking entry, extended to identify the
  technique (`fapo`), the three model roles (each with its blablador / kisski endpoint),
  the configured effort (variant budget, per-variant eval size), the prompt-only
  restriction (FR12), and to carry the same before/after metrics and optimized-prompt
  artifact. FAPO's iteration-memory / change-log SHOULD be attached as run artifacts
  (NFR5).

## Assumptions

- A1. The tracking platform's prompt-optimization entry point supports plugging in an
  additional technique without forking or replacing the platform. (Confirmed: the same
  documented `BasePromptOptimizer` extension point used for TextGrad and SkillOpt is
  reused. Per the resolved Q1 (strategy A), FAPO's loop runs **in-process inside** that
  `optimize()` call — `FapoPromptOptimizer.optimize()` — not as an external result
  importer, so it shares the exact harness path of the other three techniques.)
- A2. FAPO's three model roles (task, optimizer, judge) MUST use the project's own
  blablador / kisski endpoints, not any external or library-default endpoint. FAPO's
  OpenAI provider constructs `OpenAI(api_key=..., timeout=...)` with **no `base_url`** and
  reads only `OPENAI_API_KEY`; however the `openai` SDK still honors the `OPENAI_BASE_URL`
  environment variable when `base_url` is not passed, so pointing FAPO's OpenAI provider
  at a project endpoint is expected to work via `OPENAI_BASE_URL` + `OPENAI_API_KEY` — the
  same env-fallback trick GEPA uses (`configure_teacher_env`). This MUST be validated by a
  spike (Q3); if it does not hold, a small custom OpenAI-compatible provider/adapter for
  FAPO is required. A single global `OPENAI_BASE_URL` only supports one endpoint at a
  time, so per-role endpoints may be constrained (Q3).
- A3. FAPO's judge role is the project's existing FLEX SQL judge (the same one used by
  GEPA, TextGrad, and SkillOpt), wrapped as a FAPO `Scorer` whose `composite_score` is the
  boolean verdict mapped to 100/0 (FR11). Its pass/fail outcome both drives FAPO's
  attribution/compare and produces the comparable before/after quality metrics, so the
  optimization signal and the reported metric share one judge across techniques.
- A4. Optimizing a single system prompt (treated as the one prompt module of a
  single-step chain) is in scope and matches the assistant's single-step SQL-generation
  task; FAPO's multi-step pipeline rollouts, cross-step attribution, parameter edits, and
  structural edits are NOT used (FR12). Each training example is a single
  question-to-SQL exchange.
- A5. Comparison runs are initiated manually by an experimenter, not automatically.
- A6. Only the editable instruction portion of the prompt is changed; the fixed database
  schema is preserved as-is and is not subject to FAPO's edits (FR6, FR14).
- A7. Any FAPO code used is pinned to a specific commit and either declared as a
  dependency or vendored behind a thin adapter, so the integration is reproducible and
  does not drift with upstream `main`.

## Success Criteria

- SC1. An experimenter can start a run, select FAPO, and obtain a revised system prompt
  without editing harness code for that run.
- SC2. For a FAPO run, the tracking UI shows the technique name, the three model roles,
  the configured effort (variant budget, per-variant eval size), before/after quality on
  validation and test, and the optimized prompt is retrievable as an artifact — with the
  same metric names used by existing techniques. Concretely, those names match the GEPA /
  TextGrad / SkillOpt runs: validation via the per-round logged `eval_score` progression
  plus the run's `initial_eval_score` / `final_eval_score`, and test via
  `test_quality_{before,after}`. (No `val_quality_*` metric is introduced.)
- SC2a. The run keeps the best validation-scoring prompt: if a later variant scores worse
  on validation than an earlier one, the reported optimized prompt is the earlier
  (better) one, not the last variant tried (FR13).
- SC3. Running FAPO and an existing technique on the same dataset, split seed, and judge
  yields records that can be placed side by side and compared on identical metric names.
- SC4. Existing technique runs (GEPA, TextGrad, SkillOpt) produce the same results after
  this feature is added as before (no regression).
- SC5. A deliberately induced FAPO failure (e.g. unreachable optimizer model, or the
  FAPO loop produces no acceptable variant) produces a clear error and a run marked
  failed, with no optimized result reported.
- SC6. The saved optimized prompt is a complete, reusable system prompt that still
  contains the unchanged fixed schema context and the required output rules (FR6, FR14,
  A6).
- SC7. The FAPO run never selects or reports a variant based on the test split; variant
  selection uses only train (for attribution) and validation (for keep-best) (FR13).

## Edge Cases / Error Handling

- EC1. **Optimizer/task/judge model unreachable or times out** — fail clearly (FR7); do
  not emit a fabricated optimized prompt.
- EC2. **No improvement found** — record the run and report that the result did not beat
  the baseline on the full validation split rather than presenting an unchanged prompt as
  an improvement (return the seed template byte-for-byte so the registry dedups it, as
  TextGrad/SkillOpt do).
- EC3. **Empty or too-small train or validation split** — refuse to run with a clear
  message rather than producing an unreliable result. The split is the same seeded
  `split_dataset` split the other techniques use (same seed, same validation set), so the
  per-round keep-best stays comparable (FR3, NFR2; reuse `MIN_SPLIT_SIZE`).
  *Amended:* that split is now 20 train / 55 test with val a copy of train, not equal
  thirds — see [`specs/sampling_refactoring/spec.md`](../sampling_refactoring/spec.md).
- EC4. **Quality judge unavailable mid-run** — surface the judge failure; do not score an
  example as passing or failing by default, since a fabricated grade would corrupt both
  the attribution partition and the reported metric (FR11) — mirror SkillOpt/TextGrad
  re-raising on judge error rather than swallowing it.
- EC5. **FAPO proposes no usable / no review-passing variant in a round** — keep the
  current best prompt and proceed or stop cleanly; never apply a variant that failed
  review (FR14) and never present a non-edit as an improvement.
- EC6. **Accepted variant re-introduces the schema, drops the output rules, or contains
  example-specific content** — the review step MUST reject it via the intrinsic
  variant-text check (FR14); such a variant MUST NOT be saved.
- EC7. **Optimized prompt fails to save/version** — treat the run as failed for
  comparison purposes and report why, since an unsaved result is not reusable.
- EC8. **FAPO's agent loop cannot be driven non-interactively / exhausts its budget
  without converging** — the run MUST terminate within the recorded effort budget (FR10)
  and report the best-so-far status; an indefinitely hanging agent loop is a failure
  (FR7), not a silent stall.
- EC9. **Per-role endpoints conflict** — if FAPO can only honor one global
  `OPENAI_BASE_URL`, a run that requests three different endpoints across roles MUST
  either be supported by the adapter or rejected up front with a clear message, not
  silently routed to the wrong endpoint (Q3).

## Clarifications

### 2026-06-28

- Q1. **Integration strategy — port vs. drive-native (the central decision).** Resolved:
  **strategy (A)** — port FAPO's loop into a `FapoPromptOptimizer(BasePromptOptimizer)`
  that runs in-process inside `mlflow.genai.optimize_prompts`, reimplementing FAPO's
  attribution-driven, prompt-first, review-gated, keep-best-on-validation loop in Python
  against the project's endpoints/judge/split — exactly the shape of
  `TextGradPromptOptimizer` and `SkillOptPromptOptimizer`. Chosen over (B) "drive native
  FAPO externally and import the result" because the project goal is an apples-to-apples
  comparison of *techniques* ("use FAPO as GEPA"), the single-step SQL task collapses
  FAPO's headline features (multi-step/cross-step attribution, parameter/structural edits)
  to single-step reflection + scoped-edit + review (A4, FR12), making the port tractable,
  and (A) reuses the proven `_run_optimization` plumbing, per-round `eval_score` logging,
  and keep-best semantics the other three already share. The FAPO code used is a pinned,
  vendored port of the loop primitives behind a thin adapter, not the full hephaestus
  agent program (A7, Q6). (A1, NFR1, NFR2)

### 2026-06-30 (resolved by spike)

Q2–Q6 are resolved by the Phase-1 spike — see [`spike-findings.md`](./spike-findings.md),
grounded in upstream FAPO at pinned commit `2ed526a7b74908f1ca51a6d453c1837f404b39ef`, the
installed `openai==2.36.0` SDK, and a live blablador connectivity test. **Go/no-go for
strategy A: GO.** Headline structural finding: FAPO's optimize loop is **not Python** — it
lives in `.claude/agents/*.md` prompts; strategy A reimplements the loop in Python and
ports the *policies* (not upstream loop code), the same shape as
`SkillOptPromptOptimizer` / `TextGradPromptOptimizer`.

- Q2. **Effort budget shape.** Resolved: express effort as `variant_budget` (default 8;
  the numeric stand-in for FAPO's "3 consecutive no-improvement variants" plateau) ×
  `per_variant_eval_size` (full train split for attribution + a fixed `val_gate_size`
  subset, default 8, for the per-variant keep-best gate — the same knob TextGrad uses),
  **plus a `metric_call_budget` cap (default 100) for GEPA cost parity** (GEPA's
  `MAX_METRIC_CALLS=100` with `2*len(val)` full-val passes reserved *outside* it; TextGrad
  already mirrors this). A plateau early-stop after 3 consecutive non-improving variants
  preserves FAPO's native stop condition; whichever of `variant_budget` /
  `metric_call_budget` is hit first stops the loop (EC8). All recorded as run params
  (FR10, NFR3).
- Q3. **Endpoint path for FAPO's models.** Resolved (live-confirmed). FAPO's OpenAI
  provider reaches blablador/kisski via the `openai` SDK's `OPENAI_BASE_URL` +
  `OPENAI_API_KEY` env fallback — proven with one trivial live completion against
  blablador. But the port does **not** need FAPO's provider class: route the **task and
  judge** roles through the project's existing litellm path (per-role endpoints already
  supported via `build_completion_kwargs`/`make_client`) and build the **optimizer** role
  client with the project's own `make_client(optimizer_endpoint)` (explicit `base_url`).
  This gives true per-role endpoints and sidesteps the single-global-`OPENAI_BASE_URL`
  limitation entirely (EC9). The env trick is documented only as the fallback if a
  vendored FAPO provider object is ever used verbatim — in which case a 3-distinct-endpoint
  run must be rejected up front (FR9, A2, EC9).
- Q4. **Score mapping.** Resolved: keep the boolean FLEX judge as the single source of
  truth, exposed to FAPO only through the deterministic map **pass → 100.0, fail → 0.0**,
  with `score_breakdown={"sql_is_correct": composite}` kept equal to the composite (a
  fractional sub-score would desync FAPO's per-check averaging from the composite). FAPO's
  `validate_score_payload` accepts exactly 100/0 (it only rejects the `bool` type and
  out-of-range). This makes FAPO's keep-best "mean composite over val" identical to the
  headline MLflow metric (mean judge pass-rate) — the same "hard gate" as SkillOpt. On a
  judge-call error, **re-raise** rather than defaulting a grade (EC4), mirroring
  `SkillOptPromptOptimizer` / `TextGradPromptOptimizer` (FR11).
- Q5. **Reflection scope.** Resolved: FAPO's attribution is failure-only by construction
  (`step_attribution.attribute_failures` iterates only `composite_score < threshold`
  cases; no success-reflection path). Confirm the toggle: **failures always on;
  `reflect_on_success=False` by default, recorded** — faithful to FAPO and identical to
  SkillOpt's `reflect_on_success` (`failure_only = not reflect_on_success`) (FR11).
- Q6. **Dependency vs. vendor / which primitives to port.** Resolved: vendor the minimal
  primitives, pinned to `2ed526a7b74908f1ca51a6d453c1837f404b39ef`, with provenance to the
  upstream `.py`/`.md` files (A7). **Port:** the `runs/compare.py` mean-composite-delta
  keep-best algorithm; a `scoring/runtime.validate_score_payload`-style 0–100 score
  wrapper; the `variant-reviewer.md` guardrails as deterministic checks on the variant text
  alone (placeholder / `{schema}` integrity, no example-specific content via an intrinsic
  leakage heuristic — the reviewer is given no dataset cases, scope = instruction-block-only,
  scorer compatibility = keep the strict single-DuckDB-query output rule); and the single-step
  failure-reflection proposal policy from `optimization.md` / `step-attribution.md`. **Do
  NOT port:** `step_attribution.py` (multi-step heuristics all inert on one step),
  `engine/prompt_renderer.py`, `runs/eval_runner.py`, `cli.py`, providers, chains, mcp,
  webui, datasets/stratified_split — the project's `_run_optimization` + `eval_fn` +
  `split_dataset` + `render_system_prompt`/`instruction_block`/`recombine` replace them
  (Non-Goals, NFR4). Cross-step attribution, parameter-level and structural-level edits,
  retrieval/tool attribution, and skill files all collapse to nothing on the single-step
  task (A4, FR12).
