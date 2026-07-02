# Phase-1 spike findings — FAPO as an MLflow prompt-optimization technique

Spec: [`spec.md`](./spec.md) (Q1 resolved = strategy A; Q2–Q6 below).
Sibling deliverable this mirrors: [`../skillopt-mlflow-integration/spike-findings.md`](../skillopt-mlflow-integration/spike-findings.md).

This spike de-risks **strategy (A)**: porting FAPO's loop primitives into a
`FapoPromptOptimizer(BasePromptOptimizer)` that runs in-process inside the same
`mlflow.genai.optimize_prompts` call GEPA / TextGrad / SkillOpt use. Every claim is
grounded in upstream source or a live test; items marked **[upstream]** are read from the
pinned FAPO checkout, **[sdk]** from the installed `openai` package, and **[live]** were
confirmed against a project endpoint.

**Pinned upstream commit (FAPO / "hephaestus"):**
`2ed526a7b74908f1ca51a6d453c1837f404b39ef` — *"Add Agent Skill Optimization"*,
2026-06-29, `cisco-foundation-ai/fully-automated-prompt-optimization`. All file
paths/line numbers below are at this SHA. **[upstream]**

**Go / no-go for strategy A: GO.** The two headline risks both cleared. (1) The endpoint
seam works via the `openai` SDK's `OPENAI_BASE_URL` env fallback — **live-confirmed**. (2)
The loop primitives worth porting are small, mostly prompt text plus two short Python
modules (`compare.py`, `scoring/runtime.py`), and FAPO's headline features collapse to
nothing on a single-step text-to-SQL task. The crucial caveat: FAPO's *actual* iterate /
propose / review / keep-best loop **does not exist as Python** — it lives entirely in the
`.claude/agents/*.md` agent prompts. Strategy A therefore reimplements that loop in
Python against the project's split/judge/endpoints (exactly as planned), porting the
guardrail *policies* from those prompts rather than calling upstream code. This is the
same shape as `SkillOptPromptOptimizer` / `TextGradPromptOptimizer`, so it is tractable.

---

## Q3 (the #1 risk) — endpoint seam: FAPO's OpenAI provider via `OPENAI_BASE_URL`

**Question.** FAPO's OpenAI provider builds `OpenAI(api_key=..., timeout=...)` with **no
`base_url`** and reads only `OPENAI_API_KEY`. Does the `openai` SDK fall back to the
`OPENAI_BASE_URL` env var so we can point that provider at blablador/kisski, the same
env-fallback trick GEPA uses (`configure_teacher_env`)?

**Evidence (a) — FAPO's provider source. [upstream]**
`src/hephaestus/providers/openai.py:44-53`:

```python
def _create_client(self) -> Any:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=api_key, timeout=self.timeout_seconds)
```

Confirmed: no `base_url` passed, hard-codes the OpenAI default unless the SDK overrides
it. `DEFAULT_TIMEOUT_SECONDS = 300`, `DEFAULT_MAX_RETRIES = 10` with a 5 s backoff
(`generate()` loops `max_retries + 1` times). It special-cases reasoning models by name
prefix (`o1/o3/o4`, `gpt-5`) — irrelevant to our models but harmless.

**Evidence (b) — installed SDK fallback. [sdk]** `openai==2.36.0` in `.venv`.
`OpenAI.__init__` resolves `base_url` as:

```python
if base_url is None:
    base_url = os.environ.get("OPENAI_BASE_URL")
if base_url is None:
    base_url = f"https://api.openai.com/v1"
```

So when `base_url` is not passed (FAPO's exact case), the SDK honors `OPENAI_BASE_URL`.

**Evidence (c) — live test. [live]** Replicated FAPO's construction byte-for-byte
(`OpenAI(timeout=60)` with only `OPENAI_BASE_URL` + `OPENAI_API_KEY` set from
`LLM_API_BASE_BLABLADOR` / `LLM_API_KEY_BLABLADOR`):

```
client.base_url -> https://api.blablador.fz-juelich.de/v1/
models.list OK, sample: ['alias-apertus', '15 - Apertus-8B-Instruct-2509 ...', 'alias-eve']
completion OK -> 'ok'
```

A real chat completion came back from blablador through the env-only path. (One trivial
1-token call, no budget spent, no secrets printed/committed.)

**Resolution / recommendation for Q3.**
- **A2 holds and is live-verified.** FAPO's OpenAI provider reaches a project endpoint
  via `OPENAI_BASE_URL` + `OPENAI_API_KEY` with no provider patch — no custom adapter
  needed for the optimizer role.
- **Per-role endpoint constraint (EC9) is real.** The fallback is a single *global*
  `OPENAI_BASE_URL`, so FAPO's provider can only point at one endpoint per process. The
  clean answer is exactly the one the spec floats: **route task + judge through the
  project's existing litellm path (per-role endpoints already supported via
  `build_completion_kwargs` / `make_client`), and route only the optimizer role through
  FAPO's OpenAI provider via the global env var.** This mirrors how
  `SkillOptPromptOptimizer` already works — task model + judge run inside the project's
  inference paths (`rollout`/`_judge`), and only the reflection model runs on the
  technique's own model layer. Under strategy A the port doesn't even need FAPO's
  provider class; `FapoPromptOptimizer` can build the optimizer client with the project's
  own `make_client(optimizer_endpoint)` (which passes `base_url` explicitly), giving
  per-role endpoints for free and sidestepping the global-env limitation entirely. Keep
  the `OPENAI_BASE_URL` env trick documented only as the fallback if a vendored FAPO
  provider object is used verbatim.
- **EC9 handling:** because the port resolves each role's endpoint explicitly, three
  distinct per-role endpoints are supportable. If a future variant insists on the
  vendored `OpenAIClient` (global env only), reject a 3-distinct-endpoint request up
  front with a clear message rather than silently routing two roles to the wrong base.

## Q6 — exactly which FAPO primitives to port

**Question.** Strategy A ports FAPO's loop in-process. Which upstream
algorithms/prompts go into `FapoPromptOptimizer`, and what collapses to nothing for a
single-step text-to-SQL task?

**Key structural finding. [upstream]** FAPO's optimize *loop* is **not Python**. The
attribute → propose → review → compare → iterate/escalate orchestration lives entirely in
the agent prompt files (`.claude/agents/optimization.md`, `step-attribution.md`,
`variant-reviewer.md`); the Python under `src/hephaestus/` only provides an **Evaluate**
stage (`eval_runner.py`) plus helper modules. `AGENTS.md` / `CLAUDE.md` confirm the only
public Python entry point is `python -m hephaestus.cli eval`. So strategy A reimplements
the loop in Python (as the spec intends) and **ports the *policies* encoded in those
prompts**, not upstream loop code (there is none). Three things are worth porting verbatim
as Python helpers; the rest is policy text to reproduce.

**Port list (concrete).**

1. **Compare / keep-best-on-validation — port the algorithm.** `runs/compare.py`
   `compare_runs()` + `_score_delta()` compute the candidate-vs-baseline composite-score
   delta from two `results.jsonl` files and decide improvement on **mean composite score**
   (`_score_delta`: `mean_delta = c_mean - b_mean`, lines 82-97). This is FAPO's keep-best
   signal. Reproduce as: score the candidate's instruction block on the val split with the
   shared judge, compare mean pass-rate to the running best, keep the better (FR13, SC2a).
   The per-check / per-step-timing / regression machinery (`_check_deltas`,
   `_timing_deltas`, `_case_changes`) is multi-step/multi-check and **collapses** for a
   single binary check — keep only the composite mean delta. `compare.py:202`'s
   `threshold` semantics (a case is "failed" when `composite_score < 100.0`) is exactly our
   pass/fail gate (see Q4).

2. **Score contract — port `scoring/runtime.validate_score_payload` semantics.**
   `score_case` must return `{"composite_score": 0-100, "score_breakdown": {...}}`;
   `_coerce_score` enforces `0.0 <= composite_score <= 100.0` and **rejects bool**
   (`runtime.py:59-70`). Our shared FLEX judge → `composite_score ∈ {100.0, 0.0}` and
   `score_breakdown = {"sql_is_correct": 100|0}`, plus the judge rationale carried in the
   optional `diagnostics` field (`extract_score_diagnostics`, `runtime.py:111`) so the
   attribution step has a reason string. This is a thin wrapper, not a dependency.

3. **Attribution / proposal policy — reproduce, don't import.** `step_attribution.py`
   `attribute_failures()` + `summarize()` partition failures into `prompt_addressable` /
   `structural_addressable` / `tool_addressable` / `skill_addressable` using **multi-step
   heuristics** (`_is_retrieval_step`, `_detect_cascading_failures`,
   `_detect_tool_failures`, retrieval-overlap tiers). On a **single-step** chain with no
   retrieval/tools, every one of those heuristics is inert: there is exactly one step, no
   `tool_call_history`, no intermediate `step_outputs`, so attribution always lands on
   `final_step_fallback` → `prompt_addressable` (`summarize`, lines 404-407). **Do not port
   the module.** Port the *behaviour* it feeds the optimizer (`optimization.md` principle
   4): partition judged cases into pass/fail, then drive a scoped prompt edit from the
   **failure** cluster (and optionally successes, see Q5). That is single-step reflection —
   the same partition SkillOpt's `failure_only` reflect stage uses.

4. **Variant-reviewer guardrails — reproduce as deterministic checks.**
   `.claude/agents/variant-reviewer.md` is the analogue of TextGrad's `TGD_CONSTRAINTS` /
   SkillOpt's bounded edits (FR14). The checks that map onto our single-prompt task:
   - **Placeholder Integrity** (check 3): no added/removed/renamed placeholders. Ours:
     the recombined template must still contain `{schema}` and must not introduce other
     placeholders — exactly the `"{schema}" not in optimized_template` guard SkillOpt
     already does before registering.
   - **No Example-Specific Hints / No Train-Example Leakage** (checks 5, 6): reject a
     variant that pastes train/val/test cases or case-specific clauses into the prompt
     (FR14c, EC6, SC7).
   - **Scope** (Pre-Variant Scope Check + check 12): only the instruction block may
     change; the fixed schema must not re-enter it (FR6, FR14a, EC6) — i.e. the edit is
     applied to `instruction_block(...)` and the schema lives outside it by construction.
   - **Scorer Compatibility** (check 1): the prompt must keep the strict output rules
     (return only one DuckDB SQL query) so the judge can still parse it (FR14b) — same as
     `TGD_CONSTRAINTS[0]`.
   A variant failing any **block**-severity check is rejected, not applied (FR14, EC5).
   The chain-specific checks (8-11: ChainState protocol, node-factory, import safety,
   chain conventions) and skill-frontmatter checks (7a-7c) are structural/agentic and
   **collapse** — not applicable to a single prompt edit.

5. **Prompt-renderer (`engine/prompt_renderer.py`) — do NOT port.** It renders
   `${placeholder}` / `System:`/`User:` FAPO-tenant templates. Our prompts use the
   project's `render_system_prompt` (`{schema}` via `str.replace`) and
   `instruction_block`/`recombine`. The only transferable idea is its placeholder-diff
   check, already covered by the reviewer guardrail above.

6. **`eval_runner.py`, `cli.py`, providers, chains, mcp, webui, datasets,
   stratified_split — out of scope.** These are the tenant/LangGraph/agentic footprint
   the spec explicitly excludes (Non-Goals, NFR4). The project's `_run_optimization` +
   `eval_fn` + `split_dataset` replace them.

**What collapses to nothing for our task (confirm A4 / FR12).** Cross-step attribution
(single step ⇒ one bucket), parameter-level edits (`retrieval_k`/temperature, no chain
params to tune), structural edits (no chain topology), retrieval/tool attribution (no
retrieval, no MCP tools), skill files (agentic-only; `eval_runner._validate_eval_paths`
hard-requires an MCP section for `optimization_target` ∈ {skill, both}), the strategy
ladder's "web research / chain pattern" rungs, and synthetic-sample generation. FAPO's
"micro/meso/macro" granularities reduce to **micro (prompt text) only** — i.e. the
prompt-first scoped-edit + review + keep-best loop, which is precisely the in-scope
subset.

## Q2 — effort budget: how FAPO bounds work, and recommended params

**Question.** How does FAPO decide to stop (iteration count, escalation triggers, success
criteria)? What run params should we expose?

**Evidence. [upstream]** FAPO's stopping logic is **entirely agent-prompt-driven** — there
is no numeric iteration cap anywhere in `src/hephaestus/`. From `optimization.md`:
- **Plateau / exhaustion** (Level-Transition Workflow step 4): iterate within a level
  until "All addressable failures resolved", OR "**3 consecutive variants show no
  improvement** (plateau) **and** the strategy ladder has been fully exhausted", OR
  "Performance reaches the estimated ceiling".
- **Strategy ladder** (principle 3): "**Require at least 3 distinct techniques** tried on
  the best variant before declaring plateau."
- **Escalation**: move to the next *allowed* optimization level when one is exhausted —
  but for us only the prompt level is allowed (FR12), so there is no escalation; the run
  ends at prompt-level plateau.
- **Success criteria**: optional `composite_score` targets supplied by the user (Inputs
  section), checked against per-case `composite_score < threshold` where `threshold`
  defaults to **100.0** (`step_attribution.attribute_failures`, `compare.compare_runs`).
- **Keep-best**: "Always branch from the current best variant (never diverge to an older
  or parallel variant)" + compare-on-validation — the FR13 semantics.
- **Eval cost**: every eval is a full pass over the dataset (`eval_runner.run_evaluation`
  scores every case); FAPO has no per-variant subsampling knob — that is a project add.

So FAPO's native "budget" is qualitative (plateau after ≥3 no-improvement variants, ≥3
techniques). The spec's quantitative reframing is the right call.

**Resolution / recommendation for Q2.**
- Express effort as **`variant_budget`** (max proposed-and-evaluated variants;
  hard cap on the loop, the numeric stand-in for FAPO's "3-consecutive-no-improvement
  plateau") × **`per_variant_eval_size`** (train/val cases scored per variant per round),
  both recorded run params (FR10, NFR3). No implicit default is assumed correct.
- **Recommended defaults:** `variant_budget = 8`, `per_variant_eval_size = full
  train split for attribution + a fixed val subset (`val_gate_size`, default 8, the same
  knob TextGrad uses) for the per-variant keep-best gate`. A plateau early-stop after **3
  consecutive non-improving variants** preserves FAPO's native stop condition while
  staying inside `variant_budget`.
- **Cost parity (the open part of Q2): YES, add a `metric_call_budget` cap.** GEPA uses
  `MAX_METRIC_CALLS = 100` (`train_gepa.py:51`) and reserves `2 * len(val)` full passes
  *outside* it (`total_metric_calls = len(val_set) * 2 + MAX_METRIC_CALLS`,
  `train_gepa.py:164`); TextGrad already mirrors this (`metric_call_budget=100`,
  `train_textgrad.py`). FAPO should adopt the **same accounting**: cap the optimization
  phase (per-variant attribution scoring + per-variant val-gate scoring) at
  `metric_call_budget` task+judge calls (recommend **100**, GEPA parity), and keep the two
  reported full-val passes (baseline + best, → `initial/final_eval_score`) *outside* the
  budget. This makes FAPO directly cost-comparable to GEPA/TextGrad despite the
  variant/iteration unit. Whichever of `variant_budget` / `metric_call_budget` is hit
  first stops the loop (EC8: terminate within the recorded budget, report best-so-far).

## Q4 — score mapping: confirm the 0–100 / boolean hard gate

**Question.** Confirm FAPO's scorer contract and that the boolean FLEX judge → {100, 0}
"hard gate" is right (matching SkillOpt). Any place FAPO assumes partial credit?

**Evidence. [upstream]**
- **Scorer contract:** `scoring/scorer.py` `Scorer.score_case(...) -> Dict` and
  `runtime.validate_score_payload` (lines 88-108) require `composite_score` (0–100,
  bool rejected) and `score_breakdown` (a dict; values coerced ≥ 0). So FAPO's native
  unit is a 0–100 float per case.
- **How compare uses it:** `compare.compare_runs` averages `composite_score` across cases
  (`_score_delta`, mean/median) and **`_case_changes` treats any non-zero delta as an
  improvement/regression** (`compare.py:178-181`: `if delta < 0 ... elif delta > 0`).
  `attribute_failures` (line 202) and the agents' success-criteria all use the
  `composite_score < 100.0` threshold as the **pass/fail line**.

**Where partial credit *could* leak in (and why it's safe here).** `_score_delta` reports
a *mean over cases* and `_check_deltas` averages per-check sub-scores — both designed for
graded rubrics. Under a binary {100, 0} mapping these simply become **pass-rate** and a
single all-or-nothing check; nothing in `compare.py` requires intermediate values, and
`validate_score_payload` is perfectly happy with exactly 100 or 0 (it only rejects bool
*type* and out-of-range). The one thing to avoid: do **not** let `score_breakdown` carry a
fractional partial-credit value that disagrees with the {100,0} composite, or `_check_deltas`
and the composite would tell different stories. Keeping `score_breakdown = {"sql_is_correct":
composite}` keeps them in lockstep.

**Resolution / recommendation for Q4.** Keep the boolean FLEX judge as the **single
source of truth**, exposed to FAPO only through the deterministic map **pass → 100.0,
fail → 0.0** (FR11). This makes FAPO's keep-best "mean composite over val" identical in
meaning to the headline MLflow metric (mean judge pass-rate), exactly as the spec's
recommendation and SkillOpt's hard-gate decision intend. No partial-credit score is
introduced. On a judge-call error, **re-raise** rather than defaulting a grade (EC4) —
mirror `SkillOptPromptOptimizer.rollout` / `TextGradPromptOptimizer._judge`, which inspect
`Feedback.error` and raise so the run is marked FAILED instead of training/gating on a
fabricated verdict.

## Q5 — reflection scope: does FAPO reflect on successes too?

**Question.** From `step-attribution.md`, does FAPO reflect on successes as well as
failures, confirming the `reflect_on_success` toggle design?

**Evidence. [upstream]** `step-attribution.md` is **failure-only by construction**: it
"analyze[s] eval results to partition **failures**", `attribute_failures` iterates only
`failed = [r for r in results if composite_score < threshold]` (`step_attribution.py:202`)
and returns an empty attribution when there are no failures (lines 204-205). The
`optimization.md` orchestrator likewise drives edits from "addressable **failures**"
(principle 4). There is **no success-reflection path** in FAPO's attribution — successes
are used only as the keep-best reference ("branch from the current best variant") and for
cross-validation, never reflected on to propose edits. This matches SkillOpt's 0.1.0
default (`failure_only=True`).

**Resolution / recommendation for Q5.** Confirm the toggle: **failures always on; success
reflection off by default, recorded as a run param** (`reflect_on_success`, default
`False`). This is faithful to FAPO's failure-only attribution as the default, while
leaving the optional success-reflection extension available and auditable — identical to
SkillOpt's `reflect_on_success` (which maps to `failure_only = not reflect_on_success`).

---

## Risks / surprises

- **The loop is prompts, not code (biggest surprise).** FAPO ships no Python `optimize()`
  loop — attribute/propose/review/iterate live in `.claude/agents/*.md`. Strategy A's
  "port the loop" therefore means *reimplement the loop in Python and port the
  guardrail/attribution policies from prompt text*. This is exactly what the spec
  resolved (A) to do and is why (B) "drive native FAPO" would mean driving a coding agent
  — but it means the port is "FAPO-as-GEPA in spirit", not a line-for-line code lift.
  Provenance for the policies must cite the pinned agent `.md` files, not just `.py`.
- **NFR2 non-determinism.** With no Python loop and the proposal step being an LLM rewrite
  on the optimizer model, the run is inherently non-deterministic (as the spec's NFR2
  already flags). Record optimizer model + seed/temperature so runs are *describable*; the
  data/split/judge remain reproducible.
- **Global `OPENAI_BASE_URL` if FAPO's provider is used verbatim (EC9).** Sidestepped by
  building the optimizer client with the project's `make_client(...)` (explicit
  `base_url`) instead of the vendored provider — recommended. If the vendored provider is
  kept, three-distinct-endpoint runs must be rejected up front.
- **`score_breakdown` partial-credit trap (Q4).** Keep `score_breakdown` equal to the
  {100,0} composite; a fractional sub-score would desync `compare`'s per-check averaging
  from the composite.
- **Provider retry vs. project retry.** FAPO's `OpenAIClient.generate` has its own
  10×/5 s retry. If the port uses the project's litellm path / `make_client` for all three
  roles (recommended), the project's patient retry policy applies and FAPO's is unused;
  if the vendored provider is kept for the optimizer role, its retry is weaker than the
  litellm path — prefer the project path.
- **Licensing/provenance.** FAPO is Apache-2.0 (file headers). Vendored prompt/policy text
  and any ported helper (`compare`-style mean-delta, `validate_score_payload`-style
  coercion) must carry provenance to the pinned SHA (A7).

## Go / no-go

**GO for strategy A.** Endpoint seam proven live; the in-scope primitives are small and
clearly identified; FAPO's headline features collapse cleanly to single-step prompt-only
reflection; and the result fits the existing `BasePromptOptimizer` shape shared by
SkillOpt/TextGrad. Q2–Q6 resolutions above are ready to feed `plan.md` / `tasks.md`.

### Updated answers to feed plan.md / tasks.md

| Q | Resolution |
|---|---|
| Q2 | `variant_budget=8` × `per_variant_eval_size` (full train for attribution + `val_gate_size=8` for keep-best); **add `metric_call_budget=100`** for GEPA cost parity (2×full-val passes outside it); plateau early-stop after 3 non-improving variants. |
| Q3 | `OPENAI_BASE_URL`+`OPENAI_API_KEY` fallback **works (live-confirmed on blablador)**. Route task+judge through the project litellm path (per-role endpoints), optimizer role through `make_client(optimizer_endpoint)` (explicit `base_url`) — no FAPO provider patch needed; reject 3-distinct-endpoint runs only if the vendored global-env provider is used (EC9). |
| Q4 | Boolean judge → **pass=100.0 / fail=0.0**, single source of truth; `score_breakdown={"sql_is_correct": composite}`; re-raise on judge error (EC4). No partial credit. |
| Q5 | **failures always on; `reflect_on_success=False` by default**, recorded — matches FAPO's failure-only attribution and SkillOpt's toggle. |
| Q6 | Port: `compare` mean-composite-delta keep-best, `validate_score_payload`-style 0–100 score wrapper, variant-reviewer guardrails (placeholder/`{schema}`-integrity, no-leakage, scope, scorer-compat) as deterministic checks, single-step failure-reflection proposal policy. **Do not** port `step_attribution.py`, `prompt_renderer.py`, `eval_runner`, providers, chains, mcp, webui. Pin `2ed526a7b74908f1ca51a6d453c1837f404b39ef`. |
