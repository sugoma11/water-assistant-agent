# Implementation Plan: Periodic Test Evaluation Every K EUR (Learning Curves)

Extends: [`spec.md`](./spec.md) · [`plan.md`](./plan.md) · [`retrospective.md`](./retrospective.md)

This is a **new feature on top of the shipped cost-budget-stopping mechanism**. That
feature made money the single comparable stopping criterion; this one makes money the
**x-axis of a quality curve**: every `K` EUR of training spend, each technique is
paused at its natural checkpoint and its best-so-far prompt is scored on the held-out
55-record test split, so GEPA, TextGrad and SkillOpt can be compared *as functions of
spend* rather than only at their end points.

Requirement IDs are prefixed `LC-` throughout so they never collide with the FR/EC/D
numbering of the parent spec.

---

## 1. Feature summary

### Why

Today a run yields exactly two points on the generalization axis:
`test_quality_before` (seed prompt, 0 EUR) and `test_quality_after` (final prompt,
`cost_total` EUR). Everything in between is measured only on val — which under the
20 / 0 / 55 split **is the train split** (`train_common.py:31-35`), so it measures no
generalization at all (C4 of the parent spec). Two runs that end at the same test
quality can therefore have completely different shapes: one that plateaus after 20% of
the budget and one that is still climbing at the stop look identical in the current
records. The comparison this project exists to make ("which technique gives more
quality per EUR") needs the shape, not just the end point.

### Goals

- **LC-FR1** — Every optimization run (GEPA, TextGrad, SkillOpt) MUST accept a probe
  interval `K` in EUR with identical meaning across techniques: after each additional
  `K` EUR of *billable* (budget-charging) spend, evaluate on the test split at the next
  natural checkpoint.
- **LC-FR2** — The prompt evaluated at a probe MUST be the technique's **best-so-far**
  candidate — the prompt the run would return if the budget stopped it right there
  (FR8 semantics of the parent spec) — never the last-tried candidate.
- **LC-FR3** — Probe evaluations MUST be scored on the **same axis** as
  `test_quality_{before,after}`: the same 55-record held-out test split, the same task
  predict path (`create_predict_fn`) and the same FLEX LLM-as-Judge, so all points of
  one curve are directly comparable — and so are two techniques' curves.
- **LC-FR4** — Probe spend MUST NOT charge the money budget: a run with probes buys
  exactly the same amount of optimization work as a run without them, otherwise the
  curves are self-distorting and no longer comparable to budget-only runs.
- **LC-FR5** — Probe spend MUST still be metered and recorded separately (`cost_probe`,
  probe token counts), because it is real money the experimenter pays. `cost_total`
  (billable) and `cost_excluded` (reserved bracketing passes) keep today's meaning
  exactly.
- **LC-FR6** — Each run MUST record the curve as MLflow metrics keyed by spend, plus a
  machine-readable `learning_curve.json` artifact, under identical names/units for all
  three techniques.
- **LC-FR7** — Curve endpoints MUST be the already-measured `test_quality_before`
  (spend 0) and `test_quality_after` (spend `cost_total`); no extra evaluation is paid
  for them.
- **LC-FR8** — Probing MUST be optional and off by default, and when off the run MUST
  behave byte-identically to today (isolation, mirroring FR12/NFR4 of the parent spec).
- **LC-FR9** — A probe failure MUST NOT fail an expensive training run; it is recorded
  as a hole in the curve (`probe_failures`) and the run continues.

### Non-goals

- Changing the split, the judge, the budget mechanism, keep-best, or what a run
  returns. The curve is **instrumentation only** — nothing measured on test ever feeds
  back into optimization or into candidate selection (see LC-R1).
- FAPO (still no training entry point, same as the parent spec).
- Plotting inside the training run; export tooling produces tidy data (Phase 6),
  notebooks/papers do the plotting.
- Val-side curves: `eval_score` per step is already logged by all three optimizers.

### Success criteria

- **LC-SC1** — Each technique, run with the same `--budget` and the same
  `--probe-interval-eur`, produces a curve whose interior points sit within one
  checkpoint of each `k·K` boundary, under identical metric names.
- **LC-SC2** — For every run the MLflow UI shows `curve_test_quality` against a money
  x-axis, and `learning_curve.json` carries `(spend_eur, test_quality, prompt_sha256)`
  rows including both endpoints.
- **LC-SC3** — `cost_total` and the trajectory of a probed run are statistically
  indistinguishable from an unprobed run with the same seed/budget: the probes changed
  what was *measured*, not what was *optimized* (LC-FR4).
- **LC-SC4** — The meter's bucket identity holds exactly:
  `Σ_role cost_role == cost_total + cost_excluded + cost_probe`, and the per-role token
  totals still reconcile with `scripts/count_tokens.py` (SC5 of the parent spec).
- **LC-SC5** — Every probe point states which prompt it measured (`prompt_sha256`) and
  whether that prompt had changed since the previous point, so a flat curve segment can
  be told apart from a "nothing new was accepted" segment.

### Edge cases

- **LC-EC1 — Empty test split.** `to_test.json` (5 records) yields an *empty* test
  split under the 20 / 0 / 55 scheme (`common.just`). The probe disables itself at
  construction with a loud warning rather than logging an all-zero curve.
- **LC-EC2 — `K` larger than the budget.** No interior probes; the curve is just its
  two endpoints. Valid, not an error.
- **LC-EC3 — Checkpoint jumps several intervals.** A GEPA iteration can cost more than
  `K` (it includes a full-val candidate pass). One probe fires, the skipped multiples
  are not back-filled, and the point is recorded at its *actual* spend — the same
  "next natural checkpoint" honesty as FR7/C4 of the parent spec.
- **LC-EC4 — Best-so-far unchanged since the last probe.** The score is reused from the
  content-hash cache: no LLM calls, `curve_prompt_changed=0`, `curve_probe_cost_eur=0`.
  Re-scoring an identical prompt would only buy judge/sampling noise — the same
  reasoning as the phantom-improvement fix already in both optimizers
  (`textgrad_optimizer.py:719-727`, `skillopt_optimizer.py:744-748`; retrospective
  R-002).
- **LC-EC5 — Budget exhausted at this checkpoint.** No probe: the run is about to stop
  and `test_quality_after` measures the same prompt minutes later (LC-FR7).
- **LC-EC6 — Probe raises** (endpoint down, judge unrecoverable). Caught, warned,
  `probe_failures` incremented, optimization continues (LC-FR9).
- **LC-EC7 — Runaway probing.** `--max-probes` (default 20) caps the instrumentation
  bill if `K` is set absurdly small; `probe_cap_reached` is logged when it bites.

---

## 2. Technical Context

### What a probe actually costs — measured, not estimated

From the finished same-budget triple runs in experiment `1`
(`budget=1.75`, `train=20`, `test=55`, qwen3.6-35b in all roles, prices
`0.14 / 1.00` EUR per 1M in/out tokens):

| Run | Technique | `cost_excluded` | What that bucket is | EUR / evaluated record |
|---|---|---|---|---|
| `d3905b50` | gepa | 0.1218 | exactly one 20-record seed full-val pass | 0.0061 |
| `4398dfee` | gepa | 0.1247 | " | 0.0062 |
| `2942cb58` | gepa | 0.1345 | " | 0.0067 |

⇒ **a 55-record probe costs ≈ 0.34–0.37 EUR** (more for a long optimized prompt),
i.e. **≈ 20 % of today's 1.75 EUR training budget, per curve point**.

Wall clock, from the nested `test-before` / `test-after` phases of the same runs
(55 records, sequential — `MLFLOW_GENAI_EVAL_MAX_WORKERS` is pinned to `1` in
`harness.setup_mlflow`): **27.3, 29.9, 31.1, 31.2, 32.3, 38.7, 42.0, 48.8, 53.3, 59.3
minutes** (median ≈ 35 min). Whole runs take 288–440 min.

**Consequences that shape the design:**

1. `K` must be chosen against a ≈ 0.35 EUR probe cost. `K = 0.5` on a 1.75 EUR budget
   ⇒ 3 interior probes ⇒ ≈ +1.0 EUR (+60 % of the training budget) and, sequentially,
   ≈ +1.7 h on a ≈ 5.5 h run. `K = 0.25` would cost *more than the run itself*. The
   plan therefore ships: the cache (LC-EC4, typically kills 1–2 probes per run), a
   optional parallel probe pool (`--probe-workers`, default 1 = sequential, ≈ 35
   min/probe; raise it to trade endpoint concurrency for wall clock), and a
   `probe_cost_ratio` metric so the overhead is visible after every run.
2. `common.just` gets `probe_interval_eur := "0.5"` as the calibrated default for the
   current `train_budget := "3"`.

### Where each technique's checkpoint and best-so-far live

| | Checkpoint (already exists, budget stop uses it) | Best-so-far at that moment |
|---|---|---|
| **GEPA** | `BudgetStopper.__call__(gepa_state)` — engine's per-iteration boundary (`cost_meter.py:333-348`, `gepa/core/engine.py:672`) | `state.program_candidates[best_idx][PROMPT_NAME]`, `best_idx` = argmax mean over `state.prog_candidate_val_subscores` (ties → higher coverage) — the exact rule of `FullEvaluationPolicy.get_best_program` (`gepa/strategies/eval_policy.py:43-53`), which is what GEPA itself returns as `best_candidate` |
| **TextGrad** | per-gradient-step check at the top of the batch loop (`textgrad_optimizer.py:633-646`) | the in-memory `best_prompt` held by the keep-best gate (`textgrad_optimizer.py:690-704`); full template = `recombine(best_prompt)` |
| **SkillOpt** | every non-excluded `Text2SqlEnvAdapter.rollout` start (`skillopt_optimizer.py:309-340`) | `<out_root>/best_skill.md`, written incrementally by `ReflACTTrainer` at each gated step (already relied on by the budget-stop read-back, `skillopt_optimizer.py:689-705`); full template = `recombine(best_skill)` |

All three already fold in library-side token deltas and run `check_unmetered()` at
exactly these points, so a probe hook adds no new synchronization assumptions: nothing
is in flight at a checkpoint (SkillOpt's reflect workers join before the stage returns,
`skillopt_optimizer.py:295-307`).

Candidate templates are full `SYSTEM_PROMPT_TEMPLATE`-shaped strings in all three
cases, and `recombine(instruction_block(T)) == T` byte-for-byte for the seed
(`prompt_skill.py:135-144` vs `core.SYSTEM_PROMPT_TEMPLATE`), so one content hash
identifies "still the seed" uniformly — which is what lets the cache be pre-seeded with
`test_quality_before` (LC-FR7).

### The evaluation axis

`test_quality_{before,after}` are produced by `run_eval_phase` (`train_common.py:50-65`)
= `mlflow.genai.evaluate(data=test_set, predict_fn=create_predict_fn(...),
scorers=[judge_scorer])` in a nested run. A probe must land on that axis but cannot
reuse that call: `mlflow.genai.evaluate` opening a nested run *inside*
`optimize_prompts` (itself inside GEPA's engine / SkillOpt's trainer) mixes two run
contexts and two tracing regimes for no benefit, and it is pinned to one worker.

The probe therefore re-implements exactly the two-line body of that path in-process —
`create_predict_fn(model, endpoint, schema_text, system_prompt_template=candidate)` per
record, then the shared `build_sql_judge_scorer` Feedback, mean of the boolean values —
which is the *same predict path and the same judge*, only without MLflow's evaluate
harness around it. Both are already exercised directly this way by
`TextGradPromptOptimizer._judge` and `Text2SqlEnvAdapter._rollout`.

### Metering surface (unchanged seams)

Probe calls flow through the existing seams and are attributed correctly with no new
plumbing: task calls carry `cost_meter_role="task"` from `build_completion_kwargs`
(`harness.py:408-452`), judge calls carry `"judge"`, and GEPA's untagged-call
reflection callback skips both (`cost_meter.py:351-379`). The only thing missing is a
bucket that says "this was instrumentation, not optimization" — decision LC-D2.

---

## 3. Key architectural decisions

**LC-D1 — One technique-agnostic probe object, three one-line hooks.** A new
`src/experiments/text2sql/learning_curve.py` owns everything: threshold arithmetic,
the best-so-far evaluation, the content-hash cache, MLflow logging and the artifact.
Each technique contributes only *"here is my checkpoint, and here is my best-so-far
prompt"* as a zero-argument callable. This mirrors how `CostMeter` was attached in the
parent feature (one owner, thin seams) and keeps GEPA-, TextGrad- and SkillOpt-specific
knowledge in their own modules.

**LC-D2 — A third meter bucket, not a reuse of `excluded()`.** `CostMeter` grows a
`probing()` context and a `_probe_cost` bucket with precedence *probe > excluded >
billable*. Reusing `excluded()` would have been one line, but it would silently merge
instrumentation into the reserved-bracketing-pass bucket that SC4 of the parent spec
asserts on, and `excluded()` is explicitly documented as non-reentrant/sequential
(`cost_meter.py:313-327`) — a probe nested inside a reserved pass would corrupt it.
Separate bucket ⇒ `cost_total` and `cost_excluded` keep today's exact meaning
(LC-FR5), the identity `Σ_role == cost_total + cost_excluded + cost_probe` becomes an
offline-checkable invariant (LC-SC4), and per-role token totals still cover *every*
metered call, so the `scripts/count_tokens.py` reconciliation (SC5) survives untouched
— probe traces land on the same parent run as probe tokens.

**LC-D3 — Best-so-far, not current candidate (LC-FR2).** The curve answers "what would
I have gotten for X EUR?", and what a run delivers at X EUR is its keep-best object
(FR8). Probing the live candidate would also make TextGrad's curve jitter with every
reverted rewrite, which measures the optimizer's internal churn rather than the
deliverable. Rejected alternative recorded here so it is a conscious choice.

**LC-D4 — x-axis is money, in cents, as the MLflow step.** `curve_test_quality` is
logged with `step = int(round(billable_spend_eur * 100))`. MLflow charts then plot
quality against real spend with no post-processing, and curves from different runs and
different techniques overlay on one shared money axis — the study's whole point. (The
obvious alternative, `step = k`, would silently plot against "probe ordinal", which is
*not* money whenever a checkpoint skips an interval, LC-EC3.) `curve_spend_eur` is
logged at the same step so the exact float survives the cent rounding.

**LC-D5 — Content-hash cache, seeded with `test_quality_before` (LC-EC4, LC-FR7).**
The probe caches `sha256(template) -> score`. Pre-seeding it with the seed template's
`test_quality_before` makes the k=0 endpoint free *and* makes an early probe of a
still-unimproved prompt free. Re-measuring an identical prompt would add nothing but
judge noise — the repo already made this exact call twice (retrospective R-002).

**LC-D6 — Probes never fire when the budget is already exhausted (LC-EC5).** Hook
order at every checkpoint is: fold library deltas → `check_unmetered()` →
`exhausted()?` → stop, else probe. So the last curve point before the stop is
`test_quality_after`, and no run pays for two 55-record evaluations minutes apart.

**LC-D7 — Probes can be parallel, but default to sequential.** The probe evaluates
through its own `ThreadPoolExecutor` (`--probe-workers`), sized 1 by default
(experimenter decision, 2026-08-12): a probe then runs exactly like the bracketing eval
phases whose axis it shares — `harness.setup_mlflow` pins
`MLFLOW_GENAI_EVAL_MAX_WORKERS` to 1 — and the endpoints see no concurrency they do not
already see. Raising it is the lever when 35 min × N probes dominates the run's wall
clock, and it cannot change the scores: sampling params and `LLM_SEED` are per-call
constants, `CostMeter` is lock-guarded, and the judge already opens a per-call DuckDB
cursor on its shared read-only connection (`harness.py:731-738`). What it does change is
peak endpoint concurrency at a checkpoint — hence the knob, with the existing `llm_retry`
backoff as the safety net (LC-R4).

**LC-D8 — Off by default, one shared knob when on (LC-FR8).** `--probe-interval-eur`
defaults to `0.0` = disabled and resolves to a null-object probe, so every existing
recipe and every non-study run is byte-identical to today. `experiments.just` recipes
pass `common.just`'s `probe_interval_eur` so all three techniques of a comparison
inherit the same `K` from one place (NFR1-style comparability).

---

## 4. Architecture / Components

```
text2sql-train-{gepa,textgrad,skillopt}                    [all three CLIs: +3 options]
    meter = CostMeter(budget, read_price_config())
    probe = build_probe(                                   # null object when disabled
        meter=meter, interval_eur=probe_interval_eur, max_probes=..., workers=...,
        test_set=test_set, model=..., endpoint=...,        # same roles as the eval path
        judge_model=..., judge_endpoint=..., schema_text=..., db_path=...)
    optimizer = <technique optimizer>(..., cost_meter=meter, probe=probe)
    _run_optimization(..., cost_meter=meter, probe=probe)

_run_optimization (train_common.py)                        [extended, shared]
    log probe params (interval / test size / workers / selection rule)
    test_before = run_eval_phase(...)                      # unchanged
    probe.seed_curve(test_before, seed_template)           # k=0 point, free (LC-D5)
    with meter.active():
        optimize_prompts(...)                              # probes fire inside, at
        finally: log spend summary (+ cost_probe) & stop reason
    test_after = run_eval_phase(...)                       # unchanged
    probe.close_curve(test_after, optimized.template)      # final point at cost_total
    -> mlflow.log_text(learning_curve.json)                # LC-FR6

learning_curve.py  [new]
    LearningCurveProbe
        maybe_probe(best_template_fn) -> None      # THE hook: threshold, cache, eval, log
        seed_curve(quality, template) / close_curve(quality, template)
        summary() -> {probe_count, cost_probe, probe_failures, probe_cost_ratio, ...}
    NullProbe                                      # all methods no-op (LC-D8, LC-EC1)
    build_probe(...) -> LearningCurveProbe | NullProbe

cost_meter.py  [extended]
    CostMeter.probing() ctx    -> _probe_cost bucket, precedence over excluded (LC-D2)
    CostMeter.billable_cost    -> public read for the threshold test
    spend_summary()            += cost_probe, tokens_probe_{input,output}

GEPA:     BudgetStopper(meter, on_checkpoint=...)  # hook called only when NOT exhausted
          train_gepa passes lambda state: probe.maybe_probe(
              lambda: gepa_best_template(state, PROMPT_NAME))
TextGrad: per-step checkpoint gains probe.maybe_probe(lambda: recombine(best_prompt))
SkillOpt: adapter rollout checkpoint gains probe.maybe_probe(self._best_so_far_template)
          (reads <out_root>/best_skill.md, falls back to the seed skill)
```

---

## 5. Data model

### Run parameters (identical names on all three techniques, LC-FR6)

| Param | Value |
|---|---|
| `probe_interval_eur` | `K` (0.0 = disabled) |
| `probe_test_size` | `len(test_set)` (55) |
| `probe_workers` | probe thread pool size |
| `probe_max` | `--max-probes` cap |
| `probe_prompt_selection` | `best_so_far` (constant; recorded so a later change is visible) |

### Metrics

Per curve point, all logged at `step = round(spend_eur * 100)` (LC-D4):

| Metric | Meaning |
|---|---|
| `curve_test_quality` | judge pass-rate of the best-so-far prompt on the 55 test records |
| `curve_spend_eur` | exact billable spend at that point |
| `curve_nominal_eur` | `k · K` the point was triggered by (0 / `cost_total` for the endpoints) |
| `curve_probe_cost_eur` | what that probe itself cost (0 for endpoints and cache hits) |
| `curve_prompt_changed` | 1 if the best-so-far prompt differs from the previous point (LC-SC5) |

End-of-run scalars: `probe_count`, `probe_cache_hits`, `probe_failures`,
`probe_judge_errors`, `probe_wall_minutes`, `cost_probe`,
`probe_cost_ratio = cost_probe / cost_total`, and `probe_cap_reached` (0/1).

### Artifact `learning_curve.json` (LC-FR6, the study's actual deliverable)

```json
{
  "run_id": "...", "technique": "gepa", "budget": 1.75,
  "probe_interval_eur": 0.5, "test_size": 55, "prompt_selection": "best_so_far",
  "points": [
    {"spend_eur": 0.0,  "nominal_eur": 0.0, "test_quality": 0.600, "probe_cost_eur": 0.0,
     "prompt_sha256": "9f2c…", "prompt_changed": true,  "cache_hit": false,
     "source": "test_before", "wall_seconds": 0},
    {"spend_eur": 0.53, "nominal_eur": 0.5, "test_quality": 0.655, "probe_cost_eur": 0.34,
     "prompt_sha256": "1ab7…", "prompt_changed": true,  "cache_hit": false,
     "source": "probe", "wall_seconds": 552},
    {"spend_eur": 1.85, "nominal_eur": 1.85, "test_quality": 0.709, "probe_cost_eur": 0.0,
     "prompt_sha256": "44de…", "prompt_changed": true,  "cache_hit": false,
     "source": "test_after", "wall_seconds": 0}
  ]
}
```

Written (overwriting) after **every** probe as well as at the end, so a crashed or
killed run still leaves the points it paid for.

---

## 6. Interfaces / Contracts

### `CostMeter` additions (`cost_meter.py`)

- `probing()` — context manager; a module-global `_probing` flag guarded by the
  existing `_state_lock`, for the same eval-worker-thread reason as `_excluded`
  (`cost_meter.py:36-51`). Non-reentrant, and asserted never to nest inside
  `excluded()` (checkpoints are outside reserved passes by construction, LC-D6).
- `record()` gains the bucket precedence `probe > excluded > billable`; per-role token
  and cost counters are unchanged (they keep covering every metered call, which is what
  makes SC5 reconciliation hold).
- `billable_cost` property — the threshold input for the probe.
- `spend_summary()` gains `cost_probe`, `tokens_probe_input`, `tokens_probe_output`.
- Note in the docstring: probe calls also increment `_metered_calls`, so
  `check_unmetered()`'s `max(5, 5 %)` threshold loosens slightly — deliberate and
  harmless.

### `LearningCurveProbe` (`learning_curve.py`, new)

```python
class LearningCurveProbe:
    def __init__(self, *, meter, interval_eur, max_probes, workers,
                 test_set, model, endpoint, judge_model, judge_endpoint,
                 schema_text, db_path): ...

    def seed_curve(self, quality: float, seed_template: str) -> None:
        """k=0 point from test_quality_before; also seeds the hash cache (LC-D5)."""

    def maybe_probe(self, best_template_fn: Callable[[], str | None]) -> None:
        """THE hook. No-op unless billable_cost >= next_threshold. Then:
        resolve best-so-far -> hash -> cache hit? reuse : evaluate under
        meter.probing() -> log point -> advance threshold to the next multiple of K
        strictly above current spend (LC-EC3). Never raises (LC-FR9)."""

    def close_curve(self, quality: float, final_template: str) -> None:
        """Final point at spend == cost_total; writes the artifact + summary metrics."""

    def summary(self) -> dict[str, float]: ...
```

Evaluation body (the LC-FR3 axis): for each test record, `create_predict_fn(model,
endpoint, schema_text, system_prompt_template=candidate)(question)` → judge Feedback →
mean of `bool(fb.value)`. A record whose predict call raises scores 0 (mirroring
`_build_eval_fn._run_single`, which swallows predict errors into a string the scorer
grades False, `mlflow/genai/optimize/optimize.py:294-321`); a judge-errored Feedback
scores 0 **and** increments `probe_judge_errors`, so a degraded point is visible rather
than silently pessimistic. The whole probe is wrapped so any escaping exception becomes
`probe_failures += 1` + warning (LC-EC6).

`build_probe(...)` returns `NullProbe()` when `interval_eur <= 0` **or** the test split
is empty (LC-EC1, with a warning naming the smoke-file cause).

### Per-technique wiring

- **GEPA** — `BudgetStopper.__init__` gains `on_checkpoint: Callable[[Any], None] |
  None`; `__call__` order becomes: first-call excluded snapshot → `check_unmetered()`
  → `if exhausted(): return True` → `on_checkpoint(state)` → `return False` (LC-D6).
  `train_gepa` supplies the callback and owns the GEPA-state knowledge in a small
  module-level helper:

  ```python
  def gepa_best_template(state, prompt_name) -> str | None:
      subscores = state.prog_candidate_val_subscores          # [{val_id: score}, ...]
      best_idx, best_avg, best_cov = -1, float("-inf"), -1
      for idx, scores in enumerate(subscores):
          cov = len(scores)
          avg = sum(scores.values()) / cov if cov else float("-inf")
          if avg > best_avg or (avg == best_avg and cov > best_cov):
              best_idx, best_avg, best_cov = idx, avg, cov
      return state.program_candidates[best_idx].get(prompt_name)
  ```

  This is `FullEvaluationPolicy.get_best_program` inlined against the state object the
  stopper is handed (the policy itself is not reachable from the callback). It is
  version-coupled to `gepa==0.1.1`, hence LC-R2 and its first-call validation.
- **TextGrad** — `TextGradPromptOptimizer.__init__` takes `probe`; one line at the
  existing per-step checkpoint, immediately after the `exhausted()` break:
  `self.probe.maybe_probe(lambda: recombine(best_prompt))`.
- **SkillOpt** — `SkillOptPromptOptimizer` takes `probe` and forwards it plus `out_root`
  and `seed_skill` to `Text2SqlEnvAdapter`; one line in `rollout` after the
  `BudgetExhaustedStop` check: `self._probe.maybe_probe(self._best_so_far_template)`,
  where that method reads `<out_root>/best_skill.md` (recombined) and falls back to the
  recombined seed skill before the first gated step.

### CLI / recipes

| Option | Default | All three trainers |
|---|---|---|
| `--probe-interval-eur` | `0.0` (disabled) | K in EUR, `>= 0` |
| `--probe-workers` | `1` | probe thread pool (1 = sequential, like the eval phases) |
| `--max-probes` | `20` | instrumentation safety cap |

`common.just`: `probe_interval_eur := "0.5"` (calibrated for `train_budget := "3"`;
≈ 5 interior points, ≈ 1.5 EUR of instrumentation). `experiments.just`: every
`text2sql-train-*` recipe forwards `--probe-interval-eur {{probe-interval}}` with
`probe-interval=probe_interval_eur`, so one edit re-times every technique's curve.
`README.md`: a short "learning curves" section stating the cost rule of thumb (one
curve point ≈ 0.35 EUR ≈ 35 min at the default 1 worker) and the
methodological guardrail below.

---

## 7. Phases / Dependencies

1. **Meter bucket + probe core (no LLM).** `cost_meter.probing()`/`billable_cost`/
   summary keys; `learning_curve.py` complete with `NullProbe`. Offline asserts for the
   pure logic: threshold advance incl. interval skipping (LC-EC3), cache hit/miss,
   `max_probes` cap, bucket-identity `Σ_role == billable + excluded + probe`. This is
   also the cheap hardening the retrospective asked for (R-004) applied to new code.
   *Blocks everything.*
2. **Shared plumbing.** `_run_optimization(..., probe)`: probe params, `seed_curve`
   after test-before, `close_curve` + artifact after test-after, `cost_probe` in the
   `finally`-logged summary. The three CLI options + `build_probe` in all three
   trainers (still passing `NullProbe` behaviour when disabled). `common.just` /
   `experiments.just` / `.example.env` / `README.md`.
3. **TextGrad** (fully project-owned, cheapest checkpoint ⇒ fastest feedback). Verify
   on a short run: points land within one gradient step of each `k·K`; a reverted step
   produces a cache hit rather than a new evaluation; `cost_total` still stops at
   `--budget`.
4. **GEPA** (riskiest — library-internal state, LC-R2). Wire the stopper callback and
   `gepa_best_template`. Validate the extraction at the **first** checkpoint: it must
   return a non-`None` template equal to the seed at iteration 1; if not, fail the run
   immediately with an actionable message rather than silently producing a curve of
   `None`s after hours of spend.
5. **SkillOpt** (easiest — a file read). Verify `best_skill.md` is absent before the
   first gated step (seed fallback path) and that probing at a gate rollout does not
   nest inside the excluded baseline rollout.
6. **Export + verification + docs.** `scripts/export_learning_curves.py`: given run ids
   or a technique filter, pull `learning_curve.json` (falling back to the curve metric
   history) and emit a tidy CSV — `run_id, technique, seed, spend_eur, test_quality,
   prompt_changed` — ready for the paper's plot; pandas only, no plotting dependency.
   Extend `scripts/verify_budget_stop.py` with the LC-SC4 bucket identity and a check
   that curve endpoints equal `test_quality_{before,after}`.

---

## 8. Risks & open questions

- **LC-R1 (methodological, the important one): repeated test measurement invites
  test-set selection.** Nothing feeds back into the run — the deliverable prompt is
  still chosen by each technique's own val/gate mechanism — but a human reading five
  test points per run can be tempted to report the best one ("early stopping on test"),
  which is test-set overfitting with extra steps. Mitigation: the curve is documented
  in `README.md` and in the artifact (`prompt_selection: best_so_far`) as *reporting
  only*; the reported result of a run remains `test_quality_after` of the prompt the
  technique itself returned. Worth stating explicitly in the paper's methods section.
- **LC-R2: GEPA best-candidate extraction is coupled to `gepa==0.1.1` internals**
  (`prog_candidate_val_subscores`, `program_candidates`). Mitigated by first-checkpoint
  validation (Phase 4) — it fails loudly at ≈ one full-val pass of spend, not at the
  end — and by the fact that the rule is a 10-line inline of a documented policy class.
  A version bump gets a smoke run before any study run.
- **LC-R3: instrumentation cost is large relative to today's budgets** (≈ 20 % of 1.75
  EUR per point). Mitigated by the cache, the parallel pool, `--max-probes`, and
  `probe_cost_ratio` being logged on every run. Open decision for the experimenter:
  either accept ≈ +60 % run cost at `K = 0.5`, or raise `--budget` so the same `K`
  buys more curve per EUR of instrumentation.
- **LC-R4 (resolved by defaulting to 1 worker): probe concurrency vs. endpoint rate
  limits** (KISSKI quotas are per key; OpenRouter is provider-pinned). A sequential
  probe adds no concurrency the run does not already produce, so the risk moves to wall
  clock instead: ≈ 35 min per point, i.e. ≈ 3 h added to a ≈ 6 h run at `K = 0.5` on a
  3 EUR budget. `probe_wall_minutes` is logged on every run; if that proves too slow,
  raise `K` first (fewer points) and `--probe-workers` second.
- **LC-R5: probe traces land on the parent run** (no nested run, unlike the bracketing
  phases). This keeps the meter ↔ trace-audit reconciliation exact (both include
  probes) but means the parent run's trace list now mixes optimization and probe
  traces. `scripts/count_tokens.py` needs no change; `scripts/regrade_traces.py` /
  Argilla review consumers should be spot-checked once in Phase 6.
- **LC-OQ1 (decision taken): probe on the full 55, not a subsample.** A
  `--probe-test-size` knob would halve the cost but put the interior points on a
  different sample than the endpoints, breaking LC-FR3 within a single curve. Rejected;
  revisit only if LC-R3 forces it, and then also move the endpoints onto the subsample.
- **LC-OQ2 (settled empirically 2026-08-12): judge-errored records score 0 and are
  counted.** Measured rather than assumed: a `mlflow.genai.evaluate` run over 1 pass +
  1 fail + 1 errored Feedback yields `sql_is_correct/mean = 0.333`, so the bracketing
  evaluations put ungradable rows in the denominator and grade them incorrect. The probe
  therefore does the same, and `probe_judge_errors` makes a degraded point visible.
  Note this is **deliberately opposite** to the training-side policy: there an
  ungradable sample must never become a verdict (today the optimizers raise; on
  `fix/judge-error-training-failure` they drop the sample and continue), because a
  fabricated INCORRECT verdict turns into a textual gradient or a gate score and
  misleads the search. Here the only requirement is to match the endpoints' axis
  (LC-FR3), so dropping them in the probe would tilt every interior point upward
  relative to `test_quality_{before,after}`.

---

## 9. Testing strategy

- **Offline (no endpoint), Phase 1:** threshold advance and interval skipping, cache
  hit/miss + pre-seeding, `max_probes`, `NullProbe` equivalence, meter bucket
  precedence and the `Σ_role == billable + excluded + probe` identity, and
  `gepa_best_template` against a hand-built fake state (ties, empty subscores, missing
  prompt key). All deterministic; this is where the parent feature deliberately had no
  tests, and the new logic is pure arithmetic, so it is cheap to cover.
- **Per-technique smoke, Phases 3–5:** one short run each on the real 75-record file
  with a small `--budget` and `K` sized to yield 2–3 points. Assert: points within one
  checkpoint of `k·K` (LC-SC1), `cost_total` unchanged in meaning and still stopping at
  the budget (LC-FR4), endpoints equal `test_quality_{before,after}` (LC-FR7), a
  cache-hit point costs 0 (LC-EC4), artifact well-formed (LC-SC2).
- **Cross-technique, Phase 6:** the same triple-run protocol the parent feature used
  (same budget/prices/seed/K on GEPA + TextGrad + SkillOpt), then
  `export_learning_curves.py` producing one CSV whose three curves share the money
  axis; `verify_budget_stop.py` extended with LC-SC4.
- **Isolation regression (LC-FR8):** one run with `--probe-interval-eur 0` diffed
  against a pre-change run — identical params/metrics apart from the new
  probe params, `cost_probe == 0`, `probe_count == 0`.

---

## 10. Traceability

| Requirement | Where addressed |
|---|---|
| LC-FR1, LC-SC1 | `--probe-interval-eur` on all three CLIs; `maybe_probe` threshold at each technique's existing checkpoint (§6) |
| LC-FR2, LC-D3, LC-SC5 | best-so-far resolvers per technique (§2 table, §6); `prompt_sha256` / `curve_prompt_changed` |
| LC-FR3 | probe reuses `create_predict_fn` + `build_sql_judge_scorer` on the same 55-record split (§2 "evaluation axis") |
| LC-FR4, LC-SC3 | `meter.probing()` bucket — probe spend never charges the budget (LC-D2) |
| LC-FR5, LC-SC4 | `cost_probe` + probe token metrics; bucket identity asserted offline and in `verify_budget_stop.py` |
| LC-FR6, LC-SC2 | identical param/metric names; `curve_*` at cent-steps (LC-D4); `learning_curve.json` |
| LC-FR7 | `seed_curve` / `close_curve` reuse `test_quality_{before,after}`; cache pre-seeding (LC-D5) |
| LC-FR8 | `NullProbe` when `K <= 0`; isolation regression run (§9) |
| LC-FR9, LC-EC6 | probe body fully wrapped; `probe_failures` metric |
| LC-EC1 | `build_probe` disables on an empty test split |
| LC-EC3 | threshold advances to the next multiple strictly above current spend; actual spend recorded |
| LC-EC4 | content-hash cache; `curve_probe_cost_eur == 0` on hits |
| LC-EC5, LC-D6 | checkpoint order: stop test before probe |
| LC-EC7 | `--max-probes` + `probe_cap_reached` |
| LC-R1 | README + artifact record the selection rule; reported result stays the technique's own return |

---

## 11. Revision log

- **2026-08-12 — implemented (Phases 1–6 code + offline verification).** One decision
  changed against the plan as written: Phase 4 said a failed GEPA best-candidate
  extraction at the first checkpoint should *fail the run*. It now logs a prominent
  warning instead and lets the run continue with an endpoint-only curve. Killing an
  hours-long, money-spending training run because its *instrumentation* broke
  contradicts LC-FR9, and the warning arrives at the same time (the first checkpoint,
  minutes in), so it is just as actionable. Everything else landed as planned.
- **2026-08-12 — `--probe-workers` default changed 4 → 1** (experimenter decision). A
  probe now runs exactly as sequentially as the bracketing eval phases it shares an axis
  with, and adds no endpoint concurrency; the cost is wall clock (≈ 35 min per point,
  ≈ 3 h at `K = 0.5` on a 3 EUR budget). Parallelism remains available per run.
  LC-D7 and LC-R4 updated accordingly.
- Two small additions the plan did not name: `CostMeter.probing()` warns if it is
  entered inside `excluded()` (a wiring mistake would otherwise silently move a
  reserved pass into the probe bucket), and `probing()` refuses to nest.
- The seed-cache round-trip that makes an unimproved-prompt probe free
  (`recombine(instruction_block(SYSTEM_PROMPT_TEMPLATE)) == SYSTEM_PROMPT_TEMPLATE`) is
  now asserted in the test suite, since silently losing it would turn every seed probe
  into a paid 55-record evaluation.

**Verification status.** Offline (`tests/experiments/test_learning_curve.py`, 19 asserts,
no endpoint): threshold arithmetic incl. interval skipping, cache + pre-seeding, the
`--max-probes` cap, probe/failure isolation, the three-bucket identity, `NullProbe`
inertness, the `_evaluate` degradation rules, GEPA best-candidate extraction incl. tie
handling and version-drift, and the real MLflow metric/artifact surface against a
throwaway sqlite store. **Not yet run:** the per-technique smoke runs and the
same-budget triple run of §9 — those spend real money against the endpoints and take
hours, so they are the experimenter's call.

## 12. Generated artifacts

- `specs/cost-budget-stopping/learning-curve-plan.md` (this file)
- Created: `src/experiments/text2sql/learning_curve.py`,
  `scripts/export_learning_curves.py`,
  `tests/experiments/test_learning_curve.py`
- Edited: `cost_meter.py`, `train_common.py`, `train_gepa.py`, `train_textgrad.py`,
  `textgrad_optimizer.py`, `train_skillopt.py`, `skillopt_optimizer.py`,
  `scripts/verify_budget_stop.py`, `common.just`, `experiments.just`, `README.md`.
  (`.example.env` needed no change — the curve reuses the existing `PRICE_*` config.)
