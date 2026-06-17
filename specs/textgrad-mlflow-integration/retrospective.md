# Retrospective: TextGrad as a Prompt-Optimization Technique in MLflow

Spec: [`spec.md`](./spec.md) · Plan: [`plan.md`](./plan.md) · Tasks: [`tasks.md`](./tasks.md)

Scope reviewed: all 27 tasks across Phases 1–7 as implemented in
`src/experiments/text2sql/textgrad_optimizer.py` (new), `train_textgrad.py` (new),
`train.py` (shared-routine extraction), `harness.py` (`OPTIMIZER_*` helper), plus
`pyproject.toml`, `justfile`, `.example.env`, `README.md`. The reference notebook
(`notebooks/textgrad_prompt_opt.ipynb`) is the Phase-1 spike baseline.

## Outcome

The feature is **complete and internally consistent**. All four modules compile; the
entry point (`text2sql-train-textgrad`), the `justfile` recipe, and the `OPTIMIZER_*`
defaults are wired. Every functional requirement traces to concrete code:

- FR1/FR4 — separate CLI + `technique` param logged on both runs.
- FR2/FR6 — TGD rewrites the instruction block, `_recombine` restores the full
  `SYSTEM_PROMPT_TEMPLATE` shape, `optimize_prompts` registers it.
- FR3/FR9/FR11/A3 — the *same* `build_sql_judge_scorer` is the gradient signal (verdict
  + rationale → `tg.TextLoss`) and the val/test metric, with GEPA's metric names.
- FR8/SC4 — the strongest part: `_run_optimization` was a pure extraction; T007 verified
  GEPA's logged params/metrics are byte-identical except the one intentional `technique`
  addition. `OPTIMIZER_*` params are logged on the TextGrad run only (never in
  `log_global_params`), so GEPA runs are untouched.
- FR12/SC2a — per-epoch keep-best on val, `set_value`-revert; a stub run confirmed the
  early val peak is returned, not the last epoch.
- EC1–EC5 — degenerate split refused up front; judge/optimizer failures propagate to a
  FAILED run; honest no-improvement returns the seed template byte-for-byte so
  `register_prompt_if_changed` registers no spurious version.

Code quality is high: thorough docstrings traceable to FR/EC numbers, consistent typing,
naming that mirrors the `train.py` sibling, defensive guards before any LLM call.

## Key architectural decision (and a plan/implementation divergence)

The most important finding is a **deliberate, spike-driven deviation from the plan that
was never written back into `plan.md`.**

`plan.md` §"Key architectural decision" states: *"TextGrad does **not** run its own
`BlackboxLLM` forward pass or task engine. Instead the task model runs through MLflow's
`eval_fn`."* The shipped implementation does the **opposite for the training step**: it
builds a `SchemaInjectingEngine` task engine and runs `tg.BlackboxLLM(task_engine,
system_prompt)` so that `loss.backward()` can reach the prompt Variable. `eval_fn` is
used only for baseline/per-epoch **validation** scoring.

This is the correct call — TextGrad's autograd graph cannot connect a prompt edit to a
loss computed outside it, as discovered in spike T002 and documented in
`textgrad_optimizer.py` and `tasks.md` (T011). The decision is sound and well-commented
**at the code site**, but `plan.md` still asserts the discarded design. Anyone reading
the plan as the source of truth will be misled.

→ **Follow-up:** update `plan.md`'s "Key architectural decision" and the Architecture
diagram to reflect the two-path design (BlackboxLLM forward for the gradient step;
`eval_fn` for val/test scoring).

## Findings / follow-ups

1. **[RESOLVED] Task model sampled differently across the two client stacks (NFR2).**
   The gradient-step forward goes through `ChatExternalClient` / `SchemaInjectingEngine`
   (a raw `openai.OpenAI` client), while the val/test forward goes through
   `create_optimizable_predict_fn` / litellm. The litellm path applies the `LLM_*`
   sampling params; the `ChatExternalClient` path previously used TextGrad's own defaults
   (`temperature=0`, `top_p=0.99`), so the same task model was sampled differently when
   generating the gradient signal vs. when being scored.
   **Fix:** `train_textgrad` now passes `read_sampling_params("LLM")` into
   `TextGradPromptOptimizer(task_sampling_params=...)`; `SchemaInjectingEngine` forwards
   them to the completion call (`_ENGINE_GEN_PARAMS = temperature/top_p/max_tokens`), so
   the gradient-step forward samples the same way the eval path does. **Residual
   limitation:** textgrad's `ChatExternalClient.generate` has no hook for `seed`/`top_k`,
   so those two `LLM_*` keys are dropped (explicitly, not silently) on the gradient-step
   forward — the eval path still seeds them. Full per-call seed parity would require
   overriding textgrad's `_generate_from_single_prompt`. *(Resolved bar a documented
   engine limitation.)*

2. **`plan.md` not updated** for the architecture change above. *(Medium — doc drift.)*

3. **Two judge-scorer instances / two DuckDB connections.** `train_textgrad` builds a
   `judge_scorer` to hand to the optimizer (training step), and `_run_optimization`
   builds its **own** `judge_scorer` for the eval phases and `optimize_prompts(scorers=)`.
   Same configuration, so FR9/A3 ("same judge") holds functionally, but it's two objects
   each opening a read-only DuckDB connection. Harmless; a small cleanup would thread one
   scorer through. *(Low.)*

4. **`make_client` raises a raw `KeyError`** if the resolved env var (`os.environ[base_var]`)
   is unset — the endpoint-name lookup has a friendly `ValueError`, but the env-var read
   does not. A missing `*_API_BASE`/`*_API_KEY` will surface as a bare `KeyError`. *(Low.)*

5. **Keep-best tie-breaking prefers the later epoch** (`val >= best_val`). On an exact
   tie the later candidate replaces `best_prompt`. This is spec-compliant — SC2a only
   requires reverting a strictly-worse later epoch — and the final
   `best_val > initial_eval_score` guard still returns the seed byte-for-byte on a
   no-net-gain run, so no spurious version is registered. Noted as an intentional
   judgment call, not a bug. *(Informational.)*

## Verdict

Ship-ready. The implementation meets the spec, preserves GEPA byte-for-byte, and is
unusually well documented at the code level. The one item worth doing before considering
the spec "closed" is reconciling `plan.md` with the as-built two-path architecture
(finding 2). Finding 1 (task-model sampling consistency) has been **fixed** — the
gradient-step forward now uses the `LLM_*` params, bar the documented `seed`/`top_k`
limitation of textgrad's engine.
