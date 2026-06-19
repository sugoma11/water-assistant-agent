# Feature Specification: SkillOpt as a Prompt-Optimization Technique in MLflow

## Overview / Context

This project evaluates different automated prompt-optimization techniques for a
water-management text-to-SQL assistant. The experiment harness can already optimize
the assistant's system prompt with two families of techniques — the GEPA reflection
optimizer and TextGrad — and records every run (baseline scores, optimized scores,
and the resulting prompt) in MLflow so techniques can be compared side by side.

This feature adds **SkillOpt** as an additional, selectable prompt-optimization
technique that runs inside the same harness, on the same data split, judged by the
same quality measure, and tracked the same way. SkillOpt treats the prompt as an
editable "skill document" and improves it through rounds of reflection on what went
right and wrong, proposing a bounded set of edits each round and keeping only edits
that improve a held-out validation score. The purpose is to obtain a fair,
apples-to-apples comparison of SkillOpt against the techniques already available,
not to change how the assistant itself answers questions.

## Goals

- Make SkillOpt available as a prompt-optimization technique that produces an
  improved version of the assistant's system prompt from training examples, using an
  iterative loop that reflects on graded examples, proposes a bounded set of edits to
  the prompt, and keeps the best prompt seen on the validation split.
- Let an experimenter choose which technique to run (SkillOpt, TextGrad, or GEPA) for
  a given optimization run.
- Reuse the existing dataset, train/validation/test split, and quality judge so
  results are directly comparable across techniques.
- Record each SkillOpt run in MLflow with the same before/after quality metrics and
  the same saved-prompt artifact as existing techniques, so a reviewer can compare
  techniques from the tracking UI alone.

## Non-Goals

- Changing the assistant's runtime behavior, the question dataset, or the quality
  judge.
- Improving or tuning SkillOpt's internal algorithm itself.
- Optimizing anything other than the assistant's system prompt (e.g. retrieval
  settings, few-shot example selection, or model weights).
- Adopting SkillOpt's multi-step agent environments, its self-evolution/"sleep"
  components, or any benchmark task other than this project's text-to-SQL task.
- Adding optimization techniques other than SkillOpt.
- Building a user-facing UI; the audience is experimenters running the harness.

## User Stories / Scenarios

1. **As an experimenter**, I can start an optimization run and select SkillOpt as the
   technique, so that it improves the system prompt using the training split.
2. **As an experimenter**, I can run SkillOpt and an already-available technique
   (GEPA or TextGrad) on the same dataset and split, so that I can compare their
   results fairly.
3. **As a reviewer**, I can open the tracking UI and see, for a SkillOpt run, the
   technique used, the quality before and after optimization on validation and test,
   and the optimized prompt, so that I can judge whether the technique helped.
4. **As an experimenter**, if SkillOpt fails partway through a run, I receive a clear
   error and the run is recorded as failed rather than silently producing a
   misleading result.

## Functional Requirements

- FR1. The harness MUST offer SkillOpt as a selectable prompt-optimization technique
  alongside the existing technique(s).
- FR2. Selecting SkillOpt MUST produce a revised version of the assistant's system
  prompt derived from the training examples.
- FR3. A SkillOpt run MUST use the same dataset, the same seeded train/validation/
  test split, and the same quality judge as the existing technique(s) when given the
  same inputs.
- FR4. The technique selection MUST be explicit and recorded with the run, so a
  reviewer can tell which technique produced a given prompt.
- FR5. Each SkillOpt run MUST record, at minimum: the technique name, all three model
  names involved (see FR9), the dataset and split sizes, the configured effort (see
  FR10), the quality score before optimization, the quality score after optimization
  (on both validation and test), and the resulting optimized prompt as a retrievable
  artifact.
- FR6. The optimized prompt MUST be saved/versioned the same way existing techniques
  save their result, and MUST be a complete, reusable system prompt of the same shape
  as the original, so downstream comparison and reuse work identically. Where SkillOpt
  edits an isolated, editable portion of the prompt, the unedited fixed context (e.g.
  the database schema) MUST be preserved unchanged in the saved result.
- FR7. If SkillOpt cannot complete (e.g. one of its models is unreachable or no
  improvement is produced), the run MUST surface a clear, actionable error and MUST
  NOT report an optimized result as if it succeeded.
- FR8. Adding SkillOpt MUST NOT change the behavior or results of the existing
  technique(s).
- FR9. A SkillOpt run MUST allow three model roles to be configured independently: the
  **task model** that answers using the prompt being optimized, the **optimizer
  model** that reflects on graded examples and proposes prompt edits, and the **judge
  model** that scores a candidate. Each MUST be recorded with the run. All three roles
  MUST be pointed at the project's own blablador / kisski endpoints, not any external or
  library-default endpoint (see Q4). The judge role MUST be the same SQL judge already
  used by the existing (GEPA, TextGrad) techniques, so all techniques are scored by an
  identical measure.
- FR10. The optimization effort MUST be expressed and recorded as a number of epochs
  (passes over the training split) together with the per-round **edit budget** (the
  maximum number of edits proposed per round) and the **minibatch size** over which
  examples are reflected on together. These MUST be human-set run parameters (no
  implicit default is assumed correct) and MUST be recorded with the run.
- FR11. The judge's per-example pass/fail outcome MUST partition the graded training
  examples into successes and failures. SkillOpt MUST reflect on failures to propose
  edits to the prompt; reflecting on successes is an optional, recorded run parameter
  (see Q3). The same judge's pass/fail outcome MUST be what produces the comparable
  before/after quality metrics, so the optimization signal and the reported metric come
  from one shared judge.
- FR12. At the end of each round the run MUST evaluate the candidate prompt on the
  validation split and keep the best-scoring prompt, rejecting (reverting) a candidate
  that does not improve on validation, so the reported result is the best prompt found
  rather than the last one tried.
- FR13. The edits SkillOpt applies each round MUST be limited to the configured edit
  budget (FR10), so the amount of change per round is bounded and recorded rather than
  unbounded rewriting.

## Non-Functional Requirements

- NFR1. **Comparability** — When run on identical inputs, the recorded inputs and
  metric names MUST line up across techniques so results can be compared directly.
- NFR2. **Reproducibility** — Given the same dataset, split seed, and models, a
  SkillOpt run's setup (data, split, judge) MUST be reproducible; any inherent
  randomness in the technique MUST be controllable via a configurable seed where the
  technique supports it.
- NFR3. **Cost transparency** — The amount of optimization effort (for SkillOpt, the
  number of epochs plus the edit budget and minibatch size) MUST be configurable and
  recorded, so comparisons account for the work each technique was given. Because
  techniques express effort differently (epochs and edit budgets here vs.
  evaluation-call budgets for GEPA), the recorded effort SHOULD be enough to reason
  about comparability even though the units differ.
- NFR4. **Isolation** — A failure or change in SkillOpt MUST NOT degrade or block the
  existing techniques.

## Data / Entities

- **Training/validation/test examples** — existing question-and-reference-SQL records,
  reused unchanged.
- **System prompt** — the existing optimizable prompt; SkillOpt produces a new version
  of it by editing it as a "skill document".
- **Edit** — a single bounded change to the prompt (add / insert / replace / delete),
  derived from reflection on graded examples; multiple edits per round are limited by
  the edit budget.
- **Model roles** — three independently configurable models per SkillOpt run: task
  model, optimizer model, and judge model.
- **Run record** — the existing per-run tracking entry, extended to identify the
  technique, the three model roles (each with its blablador / kisski endpoint), the
  configured effort (epochs, edit budget, minibatch size), and the success-reflection
  toggle (see Q3), and to carry the same before/after metrics and optimized-prompt
  artifact.

## Assumptions

- A1. The tracking platform's prompt-optimization entry point supports plugging in an
  additional technique without forking or replacing the platform. (Confirmed: the same
  documented custom-optimizer extension point used for TextGrad is reused.)
- A2. SkillOpt's three model roles (task, optimizer, judge) MUST use the project's own
  blablador / kisski endpoints already configured for the project's other techniques —
  not any external or library-default endpoint. (The optimizer role is driven through
  SkillOpt's own model layer pointed at one of those project endpoints; the task and
  judge roles reuse the project's existing inference paths.) See Q4. (FR9)
- A3. SkillOpt's judge role is the project's existing SQL judge (the same one used by
  the GEPA and TextGrad techniques). Its pass/fail outcome both partitions examples
  into successes/failures for reflection and produces the comparable before/after
  quality metrics, so optimization signal and reported metric share one judge across
  techniques.
- A4. Optimizing a single system prompt (treated as one skill document) is in scope and
  matches the assistant's single-step SQL-generation task; SkillOpt's multi-step agent
  rollouts are not used — each training example is a single question-to-SQL exchange.
- A5. Comparison runs are initiated manually by an experimenter, not automatically.
- A6. Only the editable instruction portion of the prompt is changed; the fixed
  database schema is preserved as-is and is not subject to SkillOpt's edits (FR6).

## Success Criteria

- SC1. An experimenter can start a run, select SkillOpt, and obtain a revised system
  prompt without editing harness code for that run.
- SC2. For a SkillOpt run, the tracking UI shows the technique name, the three model
  roles, the configured effort (epochs, edit budget, minibatch size), before/after
  quality on validation and test, and the optimized prompt is retrievable as an
  artifact — with the same metric names used by existing techniques. Concretely, those
  names match the GEPA / TextGrad runs: validation via the optimizer's logged
  `eval_score` progression plus the run's `initial_eval_score` / `final_eval_score`,
  and test via `test_quality_{before,after}`. (No `val_quality_*` metric is
  introduced.)
- SC2a. The run keeps the best validation-scoring prompt: if a later round scores worse
  on validation than an earlier one, the reported optimized prompt is the earlier
  (better) one, not the last round's.
- SC3. Running SkillOpt and an existing technique on the same dataset, split seed, and
  judge yields records that can be placed side by side and compared on identical metric
  names.
- SC4. Existing technique runs (GEPA, TextGrad) produce the same results after this
  feature is added as before (no regression).
- SC5. A deliberately induced SkillOpt failure (e.g. unreachable optimizer model)
  produces a clear error and a run marked failed, with no optimized result reported.
- SC6. The saved optimized prompt is a complete, reusable system prompt that still
  contains the unchanged fixed schema context (FR6, A6).

## Edge Cases / Error Handling

- EC1. **Optimizer model unreachable / times out** — fail clearly (FR7); do not emit a
  fabricated optimized prompt.
- EC2. **No improvement found** — record the run and report that the result did not beat
  the baseline rather than presenting an unchanged prompt as an improvement.
- EC3. **Empty or too-small training or validation split** — refuse to run with a clear
  message rather than producing an unreliable result. The train/validation/test split is
  the same seeded split the other techniques use (same seed, same validation set), so the
  per-round keep-best stays comparable across techniques (FR3, NFR2).
- EC4. **Quality judge unavailable mid-run** — surface the judge failure; do not score an
  example as failing or passing by default, since a fabricated grade would corrupt the
  success/failure partition that drives reflection (FR11).
- EC5. **Optimizer proposes no usable edits in a round** — keep the current best prompt
  and proceed (or stop) cleanly; never apply a malformed edit and never present a
  non-edit as an improvement.
- EC6. **Optimized prompt fails to save/version** — treat the run as failed for comparison
  purposes and report why, since an unsaved result is not reusable.

## Clarifications

### 2026-06-17

- Q1. **Effort budget shape** — Resolved: a SkillOpt epoch is a full minibatch sweep
  over the train split; effort is (epochs × minibatch size × edit budget). No per-epoch
  step cap is exposed (unlike TextGrad). Effort stays directly comparable across epochs
  and the three recorded knobs (epochs, edit budget, minibatch size) fully describe it.
  (FR10, NFR3)
- Q2. **Validation-gate metric** — Resolved: the per-round keep/reject gate uses the
  same SQL-judge pass/fail measure that produces the reported before/after metric (a
  "hard" gate), not a separate partial-credit score. This keeps the keep-best decision
  identical in meaning to the headline metric and maximizes cross-technique
  comparability. (FR12, NFR1)
- Q3. **Reflection scope** — Resolved: failure reflection is always on; success
  reflection is an optional run parameter (on/off) that MUST be recorded with the run.
  This lets an experimenter isolate the effect of success reflection and disable it to
  save cost, while keeping the choice visible in tracking. (FR11)
- Q4. **Optimizer-model endpoint path** — Resolved: acceptable for the optimizer to use
  SkillOpt's own model layer rather than the task/judge client path — but all three
  roles (task, optimizer, judge) MUST be pointed at the project's own
  blablador / kisski endpoints, not any external or library-default endpoint. The
  optimizer's endpoint and model MUST be recorded with the run so the differing client
  path is visible. (A2, FR9)
