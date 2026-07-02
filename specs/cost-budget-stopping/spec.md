# Feature Specification: Cost-Budget-Based Stopping for Prompt-Optimization Runs

## Overview / Context

This project compares automated prompt-optimization techniques (GEPA, TextGrad,
SkillOpt) for a water-management text-to-SQL assistant. Every technique runs in the
same harness, on the same data split, judged by the same quality measure, and is
tracked in MLflow so results can be compared side by side.

Today each technique stops on its own, technique-specific effort cap: GEPA on a
maximum number of metric calls, TextGrad on a number of epochs plus a metric-call
budget, SkillOpt on a number of epochs. These units mean different things,
so "equal effort" cannot be granted across techniques, and none of the caps reflects
the resource that actually matters: the language-model tokens consumed — and hence
the money spent — across *all* the calls a technique makes while optimizing (the
student/task model answering questions, the judge scoring answers, GEPA's
reflection, TextGrad's backward and proposal steps, SkillOpt's reflection/edit
steps).

This feature replaces the per-technique effort caps with a single, comparable
stopping criterion: a **money budget**. During optimization, every language-model
call is metered (input and output tokens), converted to money using configurable
per-role token prices, and accumulated. When the accumulated spend reaches the
budget, the optimization stops at its next natural checkpoint and the best prompt
found so far is kept — exactly as if the run had completed naturally. The
before/after evaluation phases that bracket the optimization are *not* metered
against the budget, since every technique pays the same fixed evaluation cost.

## Goals

- Give all three techniques (GEPA, TextGrad, SkillOpt) one identical, comparable
  effort budget denominated in money, replacing the incomparable per-technique
  effort caps as the stopping criterion.
- Meter every language-model call made during the optimization phase, in every
  role — task/student forwards, judge scoring, and the optimizer-side calls
  (GEPA reflection, TextGrad backward/proposal, SkillOpt reflection/edit).
- Price metered tokens with configurable, per-role input and output token prices,
  so runs whose roles use differently priced models are costed honestly.
- Stop the optimization once the budget is exhausted, keeping and saving the best
  prompt found so far through each technique's existing keep-best mechanism.
- Exclude the pre/post-training evaluation phases (baseline and final validation
  and test scoring) from the budget, so the budget buys optimization work only.
- Record the actual spend — token counts and cost, per role and in total — with the
  run in MLflow, so a reviewer can see what each technique achieved for the same
  money.

## Non-Goals

- Adding the mechanism to the upcoming FAPO pipeline (no training entry point
  exists yet; the mechanism should extend to it later, but that is out of scope).
- Changing the dataset, the train/validation/test split, the quality judge, or how
  the assistant answers questions.
- Changing what "best prompt" means: each technique's existing keep-best /
  candidate-selection logic is preserved, not redesigned.
- Estimating or forecasting cost before a run, or building real-time cost
  dashboards; the budget check is a stop condition, not a planning tool.
- Retroactively costing past runs (post-hoc trace analysis already exists for
  that).

## User Stories / Scenarios

1. **As an experimenter**, I can start an optimization run with any of the three
   techniques and give it a money budget (plus per-role token prices), so that the
   run consumes at most roughly that budget of language-model spend.
2. **As an experimenter**, I can run GEPA, TextGrad and SkillOpt with the *same*
   budget on the same data and judge, so that their results are comparable at equal
   cost — the comparison the project exists to make.
3. **As a reviewer**, I can open the tracking UI and see, for any run, the budget it
   was given, the actual spend (tokens and money, per role and total), and whether
   it stopped because the budget ran out, so I can trust the cost comparison.
4. **As an experimenter**, when the budget runs out mid-optimization, the run still
   ends with the best prompt found so far — saved and versioned exactly as a
   naturally completed run — rather than a wasted run or a worse last-tried prompt.
5. **As an experimenter**, if I forget to configure token prices, the run refuses to
   start with a clear message instead of silently metering everything at zero.

## Functional Requirements

- FR1. Every optimization run (GEPA, TextGrad, SkillOpt) MUST accept a money budget
  as an experimenter-set run parameter with identical meaning across techniques:
  the maximum spend, in a single agreed currency unit, on language-model calls made
  during the optimization phase.
- FR2. The money budget MUST be the sole stopping criterion for the optimization
  phase. The existing effort caps (GEPA's metric-call maximum, TextGrad's epochs and
  metric-call budget, SkillOpt's epochs) MUST no longer stop a run;
  optimization keeps iterating (repeating passes over the training data where the
  technique is pass-based) until the budget stops it. Structural knobs that shape
  *how* a technique works (batch size, minibatch size, validation-gate size,
  SkillOpt's per-round edit budget, reflection settings) remain configurable and
  recorded.
- FR3. Input and output token prices MUST be configurable independently for each
  model role — the task/student model, the judge model, and the optimizer/teacher
  model — and MUST be recorded with the run. A run MUST refuse to start if any
  price needed for its roles is not configured (see EC5).
- FR4. During the optimization phase, every language-model call MUST be metered
  (input tokens and output tokens attributed to the role that made the call) and
  converted to money using the configured prices. This includes, per technique, at
  minimum:
  - task/student forwards used for training signal (TextGrad gradient-step
    forwards, SkillOpt rollouts, GEPA's candidate evaluations);
  - judge calls that score those forwards, including per-step gate evaluations
    (TextGrad's validation gate, SkillOpt's internal gates, GEPA's Pareto
    scoring);
  - optimizer-side calls: GEPA's reflection, TextGrad's backward and proposal
    steps, SkillOpt's reflection and edit steps.
- FR5. The following MUST be excluded from the budget: the pre- and post-training
  test evaluations, and the reserved baseline/final full-validation passes that
  bracket the optimization (these are already treated as outside the effort budget
  today, and every technique pays them equally).
- FR6. Metering MUST happen live, inside the running process, so the accumulated
  spend can stop the run; after-the-fact analysis of traces is not an acceptable
  substitute for the stop decision (it remains useful for audit).
- FR7. Budget exhaustion MUST be checked at each technique's natural checkpoint
  (its per-step / per-iteration boundary). A step already in flight when the budget
  is crossed MAY finish; the run then stops. The resulting overshoot is acceptable
  and MUST be visible, because the *actual* spend is recorded (FR9).
- FR8. When the budget stops a run, the best prompt found so far MUST be selected,
  saved and versioned exactly as it would be for a naturally completed run, using
  each technique's existing keep-best mechanism. A budget stop MUST NOT discard
  progress or report the last-tried prompt in place of the best one.
- FR9. Each run MUST record in MLflow: the configured budget, the configured
  per-role prices, the metered input/output token counts per role, the cost per
  role, the total cost, and the reason the optimization ended (budget exhausted vs.
  technique finished on its own, e.g. an optimizer that converges or errors).
- FR10. The budget parameter, price parameters and recorded spend metrics MUST use
  identical names and units across the three techniques, so runs line up side by
  side in the tracking UI.
- FR11. If a metered call's response does not carry token-usage information, the
  run MUST surface this (see EC2) rather than silently pricing the call at zero and
  understating spend.
- FR12. Existing evaluation-only paths (the standalone eval command, the pre/post
  test phases) MUST behave exactly as before; the metering and stopping applies to
  the optimization phase only.

## Non-Functional Requirements

- NFR1. **Comparability** — Given the same budget, prices, dataset, split seed and
  judge, the recorded budget/spend parameters and metric names MUST line up across
  techniques so a reviewer can compare them directly at equal cost.
- NFR2. **Accounting accuracy** — The live meter is the source of truth for the
  stop decision, and its totals SHOULD be reconcilable against the post-hoc trace
  accounting already used for audits; material, systematic undercounting (e.g. an
  entire role invisible to the meter) is not acceptable.
- NFR3. **Reproducibility** — The budget and prices are recorded run parameters;
  re-running with the same configuration reproduces the same setup. (The exact
  stopping point may vary with model output lengths, which is inherent to a
  spend-based stop.)
- NFR4. **Isolation** — Introducing the budget mechanism MUST NOT change the
  quality results a technique produces up to the point it is stopped, and a failure
  in the metering of one technique MUST NOT affect the others.

## Data / Entities

- **Money budget** — a single number (one agreed currency unit) set per run;
  identical semantics for all techniques.
- **Price configuration** — per-role (task, judge, optimizer/teacher) input and
  output token prices; part of the run's recorded configuration.
- **Spend record** — per-role input/output token counts, per-role cost, total
  cost, and stop reason; recorded with the run alongside the existing
  before/after quality metrics.
- **Run record** — the existing per-run MLflow entry, extended with the budget,
  prices and spend record; effort-cap parameters that no longer exist as stops are
  no longer recorded as such.
- **Best prompt** — unchanged: the best-on-validation prompt each technique
  already tracks, saved/versioned the same way on a budget stop as on natural
  completion.

## Assumptions

- A1. The inference endpoints in use return token-usage information (input and
  output token counts) on their completion responses; the harness already relies
  on this for its per-call usage extraction.
- A2. GEPA supports custom stop conditions supplied by the caller, so a
  spend-based stop can be injected without forking GEPA. (Confirmed: the GEPA
  engine accepts caller-provided stop callbacks, reachable through the
  pass-through configuration the GEPA training entry point already uses.)
- A3. TextGrad's model-calling layers in this project are project-owned wrappers,
  so token usage on task forwards and backward/proposal calls is observable at the
  call site, and the loop already has a per-step checkpoint where a stop can be
  decided.
- A4. SkillOpt is an external dependency whose reflection/edit calls happen inside
  its own trainer on its own model client. Its task and judge calls flow through
  the project's adapter (directly meterable and stoppable); metering the
  reflection/edit calls requires observing SkillOpt's model-client layer from the
  outside. This is assumed feasible without forking SkillOpt; the concrete
  mechanism is an implementation-plan concern.
- A5. A single currency unit and per-token (or per-million-token) price convention
  will be agreed and used consistently; prices are supplied by the experimenter
  (there is no authoritative built-in price list for the self-hosted endpoints in
  use).
- A6. Comparison runs are initiated manually by an experimenter, not
  automatically.
- A7. Experimenters set budgets that comfortably cover the reserved baseline
  work plus at least a few optimization evaluations (in particular GEPA's seed
  full-validation pass and several candidate evaluations). EC1 (a budget smaller
  than one step) therefore remains a designed defensive behavior, but is
  verified end-to-end on TextGrad only.

## Success Criteria

- SC1. An experimenter can run each of GEPA, TextGrad and SkillOpt with the same
  money budget and the same per-role prices, without editing harness code, and each
  run stops within one checkpoint (one in-flight step) of the budget being reached.
- SC2. For each such run, the tracking UI shows: the budget, the per-role prices,
  per-role input/output token counts, per-role and total cost, and the stop
  reason — under identical parameter/metric names across the three techniques.
- SC3. A run stopped by the budget produces a saved, versioned optimized prompt
  that equals the best-on-validation prompt found before the stop (verifiable
  against the technique's logged score progression), not the last candidate tried.
- SC4. The pre/post test evaluations and the reserved baseline/final validation
  passes demonstrably add nothing to the recorded spend: a run's recorded total
  cost reflects optimization-phase calls only.
- SC5. The live meter's totals reconcile with the post-hoc trace-based token
  accounting for the same run within a small tolerance, for every role — including
  the optimizer-side roles that the post-hoc tooling previously flagged as
  untraced.
- SC6. A run started without the required price configuration refuses to start
  with a clear, actionable message (EC5); a budget so small that not even one
  optimization step fits still ends honestly (EC1).

## Edge Cases / Error Handling

- EC1. **Budget smaller than one step** — the run performs no (or one in-flight)
  optimization step, stops, and reports the seed/baseline prompt as the result
  with the stop reason "budget exhausted"; it is recorded as a valid (if
  uninformative) run, not a failure.
- EC2. **Response without usage data** — if a metered call returns no token-usage
  information, the run must not silently count it as free. It surfaces the gap
  loudly (at minimum a prominent warning and a recorded count of unmetered calls);
  if unmetered calls are frequent enough to make the spend meaningless, the run
  fails rather than reporting a misleading cost.
- EC3. **Budget crossed during an excluded phase** — the pre/post evaluations run
  to completion regardless of the budget; the budget only governs the optimization
  phase between them.
- EC4. **Technique ends before the budget is spent** — if an optimizer finishes or
  errors on its own before exhausting the budget, the run records the actual spend
  and the true stop reason; underspend is not an error.
- EC5. **Missing or invalid price configuration** — a run with an absent, negative
  or non-numeric price for any role it uses refuses to start with a clear message
  naming the missing configuration.
- EC6. **Judge or optimizer model unreachable mid-run** — existing failure
  semantics are unchanged: the run surfaces the error; spend accumulated up to the
  failure is still recorded.

## Clarifications (resolved)

- C1. **Scope** — GEPA, TextGrad and SkillOpt only; FAPO is explicitly deferred
  until its training pipeline exists. (Non-Goals)
- C2. **Price granularity** — prices are configured per role (task, judge,
  optimizer/teacher), not as a single global pair and not per model name. (FR3)
- C3. **Fate of the old caps** — the money budget is the sole stop; the old effort
  caps are removed as stopping criteria rather than kept as secondary safety
  limits. Structural knobs survive — including SkillOpt's per-round edit budget,
  which bounds edits per update round and never terminates a run, so it is
  preserved. (FR2)
- C4. **Stop strictness** — stop at the next checkpoint: the in-flight step may
  finish, overshoot is tolerated, and honesty is preserved by recording actual
  spend rather than by pre-emptively refusing calls. (FR7, FR9)
- C5. **What "pre/post evaluations" excludes** — both the test-set evaluations
  around the optimization and the reserved baseline/final full-validation passes,
  matching how the current effort budgets already treat them. (FR5)
