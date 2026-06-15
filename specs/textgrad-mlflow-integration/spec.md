# Feature Specification: TextGrad as a Prompt-Optimization Technique in MLflow

## Overview / Context

This project evaluates different automated prompt-optimization techniques for a
water-management text-to-SQL assistant. Today the experiment harness can optimize
the assistant's system prompt with one family of techniques (the GEPA reflection
optimizer) and records every run — baseline scores, optimized scores, and the
resulting prompt — in MLflow so techniques can be compared side by side.

This feature adds **TextGrad** as an additional, selectable prompt-optimization
technique that runs inside the same harness, on the same data split, judged by the
same quality measure, and tracked the same way. The purpose is to obtain a fair,
apples-to-apples comparison of TextGrad against the techniques already available,
not to change how the assistant itself answers questions.

## Goals

- Make TextGrad available as a prompt-optimization technique that produces an
  improved version of the assistant's system prompt from training examples, using
  an iterative epoch-based loop that keeps the best prompt seen on the validation
  split.
- Let an experimenter choose which technique to run (TextGrad or an existing one)
  for a given optimization run.
- Reuse the existing dataset, train/validation/test split, and quality judge so
  results are directly comparable across techniques.
- Record each TextGrad run in MLflow with the same before/after quality metrics and
  the same saved-prompt artifact as existing techniques, so a reviewer can compare
  techniques from the tracking UI alone.

## Non-Goals

- Changing the assistant's runtime behavior, the question dataset, or the quality
  judge.
- Improving or tuning TextGrad's internal algorithm itself.
- Optimizing anything other than the assistant's system prompt (e.g. retrieval
  settings, few-shot example selection, or model weights).
- Adding optimization techniques other than TextGrad.
- Building a user-facing UI; the audience is experimenters running the harness.

## User Stories / Scenarios

1. **As an experimenter**, I can start an optimization run and select TextGrad as
   the technique, so that it improves the system prompt using the training split.
2. **As an experimenter**, I can run TextGrad and an already-available technique on
   the same dataset and split, so that I can compare their results fairly.
3. **As a reviewer**, I can open the tracking UI and see, for a TextGrad run, the
   technique used, the quality before and after optimization on validation and test,
   and the optimized prompt, so that I can judge whether the technique helped.
4. **As an experimenter**, if TextGrad fails partway through a run, I receive a
   clear error and the run is recorded as failed rather than silently producing a
   misleading result.

## Functional Requirements

- FR1. The harness MUST offer TextGrad as a selectable prompt-optimization technique
  alongside the existing technique(s).
- FR2. Selecting TextGrad MUST produce a revised version of the assistant's system
  prompt derived from the training examples.
- FR3. A TextGrad run MUST use the same dataset, the same seeded train/validation/
  test split, and the same quality judge as the existing technique(s) when given the
  same inputs.
- FR4. The technique selection MUST be explicit and recorded with the run, so a
  reviewer can tell which technique produced a given prompt.
- FR5. Each TextGrad run MUST record, at minimum: the technique name, all three model
  names involved (see FR9), the dataset and split sizes, the configured effort (see
  FR10), the quality score before optimization, the quality score after optimization
  (on both validation and test), and the resulting optimized prompt as a retrievable
  artifact.
- FR6. The optimized prompt MUST be saved/versioned the same way existing techniques
  save their result, so downstream comparison and reuse work identically.
- FR7. If TextGrad cannot complete (e.g. one of its models is unreachable or no
  improvement is produced), the run MUST surface a clear, actionable error and MUST
  NOT report an optimized result as if it succeeded.
- FR8. Adding TextGrad MUST NOT change the behavior or results of the existing
  technique(s).
- FR9. A TextGrad run MUST allow three model roles to be configured independently:
  the **task model** that answers using the prompt being optimized, the **optimizer
  model** that proposes prompt edits, and the **judge model** that scores a candidate
  and returns a written rationale. Each MUST be recorded with the run. The judge role
  MUST be the same SQL judge already used by the existing (GEPA) technique, so both
  techniques are scored by an identical measure.
- FR10. The optimization effort MUST be expressed and recorded as a number of epochs
  (full passes over the training split), consistent with the technique's epoch-based
  loop. The number of epochs MUST be a human-set run parameter (no implicit default is
  assumed correct). Any batch size or per-epoch step limit that affects effort MUST
  likewise be configurable and recorded.
- FR11. The judge's written rationale (not only a pass/fail score) MUST be the feedback
  signal TextGrad uses to propose prompt edits. The same judge's pass/fail outcome MUST
  be what produces the comparable before/after quality metrics, so the optimization
  signal and the reported metric come from one shared judge.
- FR12. At the end of each epoch the run MUST evaluate the current candidate prompt on
  the validation split and keep the best-scoring prompt, discarding (reverting) a
  candidate that does not improve on validation, so the reported result is the best
  prompt found rather than the last one tried.

## Non-Functional Requirements

- NFR1. **Comparability** — When run on identical inputs, the recorded inputs and
  metric names MUST line up across techniques so results can be compared directly.
- NFR2. **Reproducibility** — Given the same dataset, split seed, and models, a
  TextGrad run's setup (data, split, judge) MUST be reproducible; any inherent
  randomness in the technique MUST be controllable via a configurable seed where the
  technique supports it.
- NFR3. **Cost transparency** — The amount of optimization effort (for TextGrad, the
  number of epochs plus any batch size / per-epoch step limit) MUST be configurable
  and recorded, so comparisons account for the work each technique was given. Because
  techniques express effort differently (e.g. epochs here vs. evaluation-call budgets
  elsewhere), the recorded effort SHOULD be enough to reason about comparability even
  though the units differ.
- NFR4. **Isolation** — A failure or change in TextGrad MUST NOT degrade or block the
  existing techniques.

## Data / Entities

- **Training/validation/test examples** — existing question-and-reference-SQL records,
  reused unchanged.
- **System prompt** — the existing optimizable prompt; TextGrad produces a new version
  of it.
- **Model roles** — three independently configurable models per TextGrad run: task
  model, optimizer model, and judge model (the judge returns a written rationale).
- **Run record** — the existing per-run tracking entry, extended to identify the
  technique, the three model roles, and the configured epochs, and to carry the same
  before/after metrics and optimized-prompt artifact.

## Assumptions

- A1. The tracking platform's prompt-optimization entry point supports plugging in an
  additional technique without forking or replacing the platform. (Confirmed: the
  platform exposes a documented extension point for custom optimizers.)
- A2. TextGrad's three model roles (task, optimizer, judge) can use the same model
  endpoints already configured for the project's other techniques.
- A3. TextGrad's judge role is the project's existing SQL judge (the same one used by the
  GEPA technique). It both returns a written rationale that drives prompt edits and
  produces the pass/fail outcome used for the comparable before/after quality metrics, so
  optimization signal and reported metric share one judge across techniques.
- A4. Optimizing a single system prompt (rather than a multi-part document) is in scope
  and matches the assistant's single-step SQL-generation task.
- A5. Comparison runs are initiated manually by an experimenter, not automatically.

## Success Criteria

- SC1. An experimenter can start a run, select TextGrad, and obtain a revised system
  prompt without editing harness code for that run.
- SC2. For a TextGrad run, the tracking UI shows the technique name, the three model
  roles, the configured number of epochs, before/after quality on validation and test,
  and the optimized prompt is retrievable as an artifact — with the same metric names
  used by existing techniques.
- SC2a. The run keeps the best validation-scoring prompt: if a later epoch scores worse
  on validation than an earlier one, the reported optimized prompt is the earlier
  (better) one, not the last epoch's.
- SC3. Running TextGrad and an existing technique on the same dataset, split seed, and
  judge yields records that can be placed side by side and compared on identical metric
  names.
- SC4. Existing technique runs produce the same results after this feature is added as
  before (no regression).
- SC5. A deliberately induced TextGrad failure (e.g. unreachable optimization model)
  produces a clear error and a run marked failed, with no optimized result reported.

## Edge Cases / Error Handling

- EC1. **Optimization model unreachable / times out** — fail clearly (FR7); do not emit
  a fabricated optimized prompt.
- EC2. **No improvement found** — record the run and report that the result did not beat
  the baseline rather than presenting an unchanged prompt as an improvement.
- EC3. **Empty or too-small training split** — refuse to run with a clear message rather
  than producing an unreliable result.
- EC4. **Quality judge unavailable mid-run** — surface the judge failure; do not score the
  candidate as zero or as passing by default.
- EC5. **Optimized prompt fails to save/version** — treat the run as failed for
  comparison purposes and report why, since an unsaved result is not reusable.

## Clarifications (resolved)

- C1. **Effort budget** — TextGrad runs as an epoch-based loop (PyTorch-style), so effort
  is expressed as a number of epochs (plus optional batch size / per-epoch step limit),
  not as an evaluation-call budget. (FR10, NFR3)
- C2. **Feedback signal** — TextGrad uses its own judge model that returns a written
  rationale; that rationale, not just a pass/fail score, drives the prompt edits. This
  introduces a third configurable model role. (FR9, FR11)
- C3. **Models** — Three model roles are configured independently: task model, optimizer
  model, and judge model. (FR9)
- C4. **Shared judge** — TextGrad's judge role is the same SQL judge used by the existing
  (GEPA) technique; it supplies both the optimization rationale and the comparable
  pass/fail metric. (FR9, FR11, A3)
- C5. **Epochs are human-set** — The number of epochs is a run parameter set by the
  experimenter, not a fixed or inferred default. (FR10)
