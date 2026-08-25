# Decisions

Rejected alternatives and evaluation-validity conditions, keyed by subject.

**Admission rule.** An entry earns its place here only by recording a *rejected
alternative* — a road not taken, with the reason — or an *evaluation-validity
condition*: something the thesis claim rests on, which would be void if the
system stopped doing it. Anything whose mechanism is simply how the system
works belongs in `agent_architecture.md`. Anything measured belongs in
`findings.md` and is cited here, never restated.

**How to read it.** Entries are keyed by subject and carry no numbers. Where a
decision was later narrowed or reversed, the narrowing is folded into the entry
rather than appended after it: one current position, one list of what it
rejects. The order in which any of this was settled is not recorded, and is not
needed — that history is what this file replaces.

---

## No fitted correction between the instrument and the oracle

Where a measured input is known to be biased, the bias is disclosed as a scope
limit and the raw value is served. No correction estimated from the site's own
data sits between an instrument and an oracle the agent is scored against.

The ground is reproducibility, not accuracy. An uncorrected station reading, or
a constant carried verbatim from the deployed controller, is reproducible from
the pinned database and the site's documentation by anyone; a factor fitted
here — over an overlap window we chose, against a reference we chose — is
reproducible only from this repository, and every oracle downstream inherits
that dependency. Two entries rest on this and neither restates it: **Weather
sources** (radiation is not calibrated against the on-site pyranometers) and
**The irrigation calculator** (thresholds are not re-derived after the unit
fix).

**Settled 2026-08-24: `et_fao56.py` follows the R's corrected `Rnl`, at the
site's direction.** Upstream commit `3e7405a` moved `GR2L_function.R` to absolute
temperature in the net-longwave term; the port followed, and the endpoint and the
calculator compute one ET again. The alternative — holding the port at the old
convention so families E and F stayed comparable with what had already been
measured — was rejected: it would have left a port that no longer ports, and two
ET conventions to disambiguate at every citation, in exchange for comparability
with a pilot the GR2L canary move had already broken on the model side.

**What this rule contributed is the shape of the change, not the choice.** The
correction raises ET0 by a median 1.223× and flips 4 of 921 roof-days
(`findings.md`), and the trigger levels in `rules_constants.py` were **not
re-derived to absorb it** — they are the site's, carried verbatim, and the same
thresholds are now compared against a larger ET. That is the third option this
rule forbids and the reason the decision belonged to the site rather than to this
repository: re-tuning is a change to the deployed controller's policy, made
against the decision-diff evidence, not a way of keeping a testbed's numbers
still.

**Validity condition for what this leaves:** the port and the R must be checked
against each other whenever either moves, because nothing in the pin list covers
the ET core (`findings.md`). The port's own test carries the endpoint's served
values as a fixture for exactly this — it is the only artefact in the repository
that would have noticed.

---

## Arbitration and naming

Where the specification and the code disagree about *mechanism* — names,
signatures, module paths, data sources — the code wins and the specification is
rewritten. Where they disagree about an *evaluation property* — determinism,
leakage, injectability, typed abstention, the addressable optimizable
surface — the specification wins and the code changes.

**Rejected:**

- *Letting the specification's preferred tool names stand in gold
  trajectories.* Scoring matches the strings the framework actually emits: the
  sub-agent is exposed under its own name, and its inner query tool never
  appears in a root-agent trajectory at all (`findings.md`, Codebase seams). A
  name chosen for prose scores 0 on a fully correct run, invisibly.

**Validity condition:** the second half of the rule is what makes the
evaluation properties non-negotiable — they are the conditions the thesis claim
rests on, so no implementation detail may trade one away by precedent. Where
the code contradicts one, the code is the defect.

---

## The construction seam

Nothing the harness needs is a module-level singleton: the scenario context is
built per case and passed explicitly, and the agent, its toolset and the frozen
sub-agent are built per rollout by factories taking the candidate's text as an
argument.

**Rejected:**

- *A contextvar-scoped context.* Far less invasive, but the optimizer evaluates
  records in a thread pool whose workers drop ContextVars — already observed
  here breaking cost attribution (`findings.md`, Codebase seams). The failure
  is silent: the context reverts to a production default and the case still
  answers.
- *Resolving `as_of` from session state inside the frozen sub-agent.* Much less
  code, and the state does cross the tool boundary — but a missing key falls
  back to the unbounded view, the exact leakage the testbed exists to prevent,
  and it fails quietly. It also couples the framework-free lower layers to
  ambient stringly-keyed state, the shape already rejected above.
- *Rejecting generated SQL containing `CURRENT_DATE` or `now()` rather than
  rewriting those nodes to the literal `as_of`.* The sub-agent that emits them
  is dateless by design — date resolution is the root agent's job — so
  rejection penalizes behaviour no candidate can optimize.

**Validity condition:** an unbound case must fail loudly before any rollout
runs. Every seam here is one where a silent default still yields a scoreable
answer, so "it produced a number" is not evidence the case was measured as
pinned.

---

## The response cache

One request-keyed response cache serves both live dependencies — the weather
API and the remote GR2L service — hashing the exact request into a committed
entry. A miss is filled by a live fetch and recorded, **gated on the service's
committed canary matching**; a diverging canary is a hard failure, and so is a
miss the service cannot serve.

**Rejected:**

- *A separate fixture path* with its own client, file schema and generator. Two
  replay mechanisms to keep honest where one covers both dependencies, and a
  fixture format is a second artefact that can drift from what the client
  actually sends — a request hash cannot.
- *Failing the case on every miss in replay.* The stricter rule, and the one
  held here until the candidate's argument freedom was priced in. A GR2L key
  hashes the weather rows and parameters of the window **the candidate chose**,
  while capture runs over the oracle's window: a candidate asking for five days
  where gold asked for three misses, and inside the search — which has no
  exclusion channel — scores 0. The search is then taught to reproduce the
  baseline's arguments, on the counterfactual and model-chain families above
  all, which are the families the thesis reads. The strict rule was also
  protecting a determinism GR2L supplies on its own: it is deterministic in
  `(rows, parameters)`, so a live call on a miss returns what a captured entry
  would have returned, provided the service has not moved — which is precisely
  what the canary decides.
- *Record-on-miss without the canary gate.* It cannot tell "this window was
  never captured" from "the service changed under us", which is the one failure
  the committed entries exist to catch.

**Validity conditions:** every input to a key derives from the case's `as_of`
and never from wall-clock time, so a key cannot change between capture and
replay. The canary is checked in the same pass as any run that records, so no
entry enters the cache from a service the run has not just verified. The count
of entries recorded per arm is published beside the harness-error count: a large
asymmetry means the arms explored different argument space, which is a finding
about the candidates rather than a fault in the run. Note what the cache does
*not* buy: GR2L is the only component whose determinism it carries, since
station rows are a pure function of the pinned database and the reanalysis is
re-fetchable indefinitely. For weather it is cost and speed, and no
reproducibility claim rests on it.

---

## Window resolution and the scenario clock

Time reaches every layer through one callable, evaluated per read, so
production and evaluation run identical readers and differ only in what it
returns. Relative windows resolve to absolute dates at layer 1 against the
case's `as_of` before any client is called; the pure layer takes the date it
needs as a required argument.

**Rejected:**

- *Letting the client read the context itself.* The pure layer has no channel
  to a context, and giving it one reintroduces the ambient state the
  construction seam rejects.
- *A wall-clock default on that required argument.* A caller who forgets it
  silently leaks real time into a pinned case; with no default the same mistake
  fails at the call site.
- *Removing the relative-window parameters from the tool signature* once
  resolution moved to layer 1. They help the model and match both tool specs,
  so they stay — with the cost accepted openly: a parameter name is a
  non-optimizable surface, and one of them voids a routing probe that pointed
  the other way (see **Trajectory scoring and routing probes**).
- *A numeric cap on the back-window,* validated in code. No source imposes one:
  the station is bounded by the record it covers, the reanalysis reaches back
  decades. A cap would abstain on an answerable question and hand the
  abstention metric a false positive, while the bounded-series rule already
  bounds what a long window returns — by truncating and summarizing, not by
  refusing. The forward horizon is a different mechanism and remains a real
  scope limit (see **Typed abstention**).

(A construction-time `as_of` datetime was a drafting slip rather than a
considered alternative: it reintroduces the midnight-staleness bug in
production.)

---

## Weather sources

Daily weather has two sources, chosen in code from the window alone and never
named by the agent: the site's own station, derived from the half-hourly record
through the as-of view wherever that record covers the window entirely, and the
Open-Meteo ERA5 Archive for everything else. Resolution lives in a composite
weather client at layer 2, constructed with the as-of executor, because the
station path reads the case's database handle and the pure layer has no channel
to it. Station rows are served uncorrected.

**Rejected:**

- *An agent-facing source selector.* The agent names a window and a question,
  never a provenance. A selector makes source an optimizable axis, so two arms
  could be scored against different forcing data on the same case.
- *The reanalysis as the retrospective source.* The ground for the station is
  **consistency with the validation data**, not "measurements beat a model":
  the lysimeters whose runoff and soil-moisture records the model is scored
  against sat under this gauge and this pyranometer, so forcing from the same
  instruments makes prediction and measurement commensurable, where reanalysis
  forcing against lysimeter truth mixes two worlds. The station is also better
  for temperature and is the conceptually correct wind forcing (`findings.md`,
  Weather source measurements) — supporting facts, not the ground, since it is
  not better everywhere.
- *Calibrating the station's shortwave radiation against the on-site
  pyranometers.* The station reads low on shortwave (`findings.md`, Weather
  source measurements) and the overlap exists, so the factor is computable — and
  it is exactly the fitted correction the standing principle forbids.
- *Taking radiation from Open-Meteo with everything else from the station.* It
  mixes provenance inside one window, so no forcing series is attributable to
  one instrument set.
- *Falling back to the reanalysis on frozen-precipitation days.* Same
  single-provenance objection, per day rather than per variable, and the switch
  is itself a correction chosen by knowing which days the gauge undercatches
  (`findings.md`, Weather source measurements).
- *Open-Meteo's live Forecast endpoint.* It refuses a window as old as a case's
  `as_of` outright (`findings.md`, Weather source measurements), so it cannot
  serve a testbed whose clock sits inside the measurement record; the two
  Open-Meteo backends also disagree materially on the same past day, a second
  reason no window may draw from both.
- *Open-Meteo's Historical Forecast API.* It serves those windows at finer
  resolution from a nearer grid cell, but archives model analyses rather than
  lead-time forecasts (`findings.md`, Weather source measurements) — resolution
  without forecast semantics, at the price of a third source, a new cache
  surface and a new pin. Revisit only if the reanalysis cell proves too coarse
  for model forcing.
- *Dropping the future-facing templates.* It removes the forecast-weather,
  model-chain and hybrid-chain families — around a third of the suite — to avoid
  a limit that can simply be stated (see the architecture's scope limits).

**Validity condition:** coverage is tested **through the as-of view**, so a
window reaching past `as_of` can never resolve to the station and a partly
covered window falls to the Archive whole. Every window therefore has exactly
one provenance, which keeps a case's forcing reproducible from the pin and
keeps the station biases disclosable as one property of one source rather than
as a per-day mixture.

---

## Typed abstention

Tools report one of three outcomes, and `not_available` always means a genuine
scope limit, never a bad argument. On weather the single scope limit is the
forecast horizon, checked in code against the case's `as_of`. Where a limit has
no tool surface at all, the abstention is the agent's alone, grounded in the
documented variable list, and the gold trajectory is empty.

**Rejected:**

- *A `variables` selector on the weather tool.* It changes the tool contract
  and the upstream request shape for one template family — and hands the
  unsignalled-abstention holdout exactly the tool surface it must lack, turning
  a test of the agent's own judgment into another relay of a typed
  `not_available`.
- *Requiring a verification call before an unsignalled abstention,* as
  "evidence-based abstention". The call proves nothing the agent's context
  lacks — the documented contract already says the field does not exist — so it
  contradicts the ground of the typed abstention and penalizes the agent that
  correctly trusts the documentation.

**Validity conditions:** the holdout claim is transfer from *tool-signalled* to
*unsignalled* abstention, and is void if any other template presents an
unsignalled abstention — which is what forces card granularity under
**Retrieval**. And the false-abstention metric is interpretable only because a
malformed argument never returns `not_available`; otherwise it mixes agent
fumbles with wrong declines, two behaviours with different fixes.

---

## Bounded series

Every tool's summary is sufficient on its own to answer, and the series
returned alongside it is capped, with truncation flagged.

**Rejected:**

- *Dropping the series entirely* to satisfy the "never row dumps" principle
  both built tools were already violating. A bounded series *is* the tool's
  product — plot specs, counterfactual comparisons and per-day answers need
  days — and re-deriving them from a summary would measure something the
  deployed assistant does not do. What the tools violated was unboundedness,
  not the presence of rows.

**Validity condition:** the cap keeps answer accuracy independent of how much
data survived the turn, so arms differ in their prompts rather than in how much
of a long window each managed to hold in context.

---

## The answer contract

The evaluation contract — a small JSON object with a two-valued status and a
scalar-or-null answer — is carried by the *candidate* instruction, which also
explicitly forbids asking a question back. The production instruction keeps
prose and keeps clarification.

**Rejected:**

- *One unified contract with a `needs_clarification` status.* No case would
  exercise it, so no optimizer would ever receive signal on it and the shipped
  behaviour would be the one branch the thesis never measured. "It would break
  the frontend" is explicitly **not** the reason — the frontend could render a
  contract card, as it already does for SQL results; the reason is that
  production needs a turn type the contract cannot express and the evaluation
  cannot exercise.
- *Extending the answer field to numeric arrays* so a two-roof comparison could
  report both values. It touches the contract, the parser and the scorer for
  one template; that template is rephrased to a single percentage-point gap
  instead.
- *Narrowing that template to one roof.* Cheaper still, but it breaks the
  presentation-verb pair whose twin plots both roofs, so the halves would no
  longer share an information need.

**Validity condition:** the two-valued contract is safe **only** because the
generation filter discards sampled parameters whose oracle is ambiguous. A
genuinely ambiguous paraphrase surviving the filter forces the model to guess,
and a clarifying question would score as a wrong answer rather than as correct
behaviour. Separately, a message parsing as neither status is reported as a
parse failure apart from answer accuracy, so a candidate degrading the output
format stays distinguishable from one degrading its reasoning.

---

## Tool errors and harness exclusion

The criterion: exclude what the candidate could not have avoided and the
harness cannot reproduce; score what the candidate deterministically causes.
Deterministic argument errors never exclude; upstream failures and replay cache
misses mark the case a harness error, excluded from every aggregate in the
measurement run and counted per candidate arm.

**Rejected:**

- *Excluding argument errors too.* Exploitable: a candidate's errors on hard
  cases would leave the denominator and *raise* its average, so the worse it
  handled a template the better it would look.
- *Scoring harness errors 0 as the design.* It penalizes a candidate for an
  outage and biases selection toward whichever candidate ran while the service
  was up.
- *Neutral fill at the batch mean.* It preserves the denominator while
  shrinking the effective *n* — hiding the loss instead of reporting it, the
  one outcome worse than either honest option.

**Validity condition:** exclusion is a property of the measurement path only.
Inside the search there is no exclusion channel — the optimizer consumes one
float per record (`findings.md`, Optimizer internals) — so the protection is
structural: the search replays over a cache asserted complete, with training
records pre-filtered to fully captured cases. A residual failure there scores 0
as a declared residual with the per-arm count published beside it; diverging
counts between arms mean the run is compromised and is repeated. That
comparison is the condition under which the search numbers are interpretable at
all.

---

## Plotting

The plot tool takes source *declarations*, never data: each names where a
series comes from and which variable to draw, and the tool fetches through the
same context seams every other tool uses. It returns the resolved spec and
summary statistics, no series.

**Rejected:**

- *Letting the agent pass the series in as an argument.* It drives the data
  through the LLM and turns the family's argument checking into a test of
  whether the model retyped forty floats correctly — non-deterministic, and
  measuring transcription rather than tool selection.
- *A session-state handle to a prior tool result.* It avoids the re-fetch, but
  makes a plot scorable only in the context of the turn before it and ends the
  family's single-call property. It stays available as a chat-path
  optimization; it is not the evaluation mechanism.
- *A server-side artifact store.* A new HTTP endpoint plus a retention story
  the testbed does not otherwise need.
- *The framework's own artifact service.* It couples the render path to
  plumbing the frontend bridge may not forward.
- *A model-chosen aggregation argument.* One plot-level operator cannot serve a
  mixed plot — precipitation sums where soil moisture averages — so aggregation
  is derived from the variable in code, adding no model-owned field to fumble.
- *Echoing the plot spec into the final message as the answer.* A transcription
  slip inside a perfectly executed call would score as a wrong answer.
- *Scoring the resolved range off the tool's echoed spec.* The family's scored
  surface includes the absolute range, and when the candidate wrote "last month"
  the only absolute range in existence sits in the tool's *result* — which is
  exactly what `argument_checks` may never read (architecture §6.1). A
  result-reading check scores the resolver, not the agent. The scorer resolves
  the **argument** through the same layer-1 resolver instead, against the case's
  `as_of`.
- *Dropping the relative window forms from the plot signature* so `start` / `end`
  are always absolute and comparable as written. It buys the comparison by
  reversing a decision already taken for the weather tool (**Window resolution
  and the scenario clock**) and splits one window vocabulary into two.
- *A new `op` meaning "compare after resolving".* The `op` vocabulary is the
  scorer's arithmetic; resolution is a property of the value being compared, so
  it rides as a flag on the check and leaves `eq`, `set_eq` and `present`
  meaning what they mean everywhere else.
- *Leaving the family out of the oracle registry,* which is what T103 did and
  what "a plot's deliverable is the spec, so there is nothing to materialize"
  argued for. It was right about the **answer** and stopped being right about
  the **case** once T24a drew three variants: the answer is null on all three
  and the answer metric skips on all three, but two are `answered` and one is
  `not_available`, and §6.1 gives `status` no channel except the oracle's. So
  family H has an oracle that returns no number — which is the same shape the
  abstention templates already have, and not a special case (T110).
- *Deciding that status from the template's declared variant.* The stopgap
  generator did, by copying a `status` field written beside the draw. It makes
  the variant label load-bearing and unchecked, and a mislabelled draw is silent
  in both directions: a model overlay on the gravel roof records an abstention
  as answerable, and a non-modellable overlay on an extensive roof records an
  answerable case as an abstention. The second is invisible to the false-
  abstention rate, because that rate is computed against the gold set containing
  it. The oracle runs `prepare_series` — §3.6's own trigger, the function the
  tool calls before it fetches anything — and refuses the draw when the label and
  the trigger disagree.

**Validity conditions:** the scored surface is the agent-supplied half of the
spec; derived fields — unit, axis, aggregation — are constant across candidates
and discriminate nothing, so scoring them inflates every candidate equally. The
resolved-argument comparison is what keeps that surface fair: "last month" and
the two dates it denotes must score identically, or the family measures date
arithmetic rather than source and variable selection. And the answer metric is
*skipped* for this family rather than scored 0: comparing a null answer against
an oracle would charge the family's share of the suite against fully correct
cases.

---

## Trajectory scoring and routing probes

Trajectory is binary per case — 1 if and only if every gold tool was called, no
listed must-not tool was called, and the declarative argument checks pass.
Calls beyond the gold set cost nothing and are reported as a diagnostic, so a
template discriminates routing only through its must-not set; a must-not binds
only where the wrong route is inferable from a surface no candidate can
rewrite — tool name, signature, parameter names — or from a disclosure the
tool's contract mandates.

**Rejected:**

- *Unordered-set precision/recall/F1 with a mild extra-call penalty.* Doubly
  undefined and double-counting: the penalty had no magnitude, and with gold
  sets of one to three tools a single extra call inside the F1 already costs up
  to a third of the score — not "mild" — while a penalty outside the F1 charges
  the same call twice.
- *Optional-call markers.* With no penalty an optional call and an unlisted
  call are indistinguishable, so the marker names a distinction the scorer
  cannot make.
- *Narrowing the weather tool to forecast-only* so its signature would enforce
  the routing convention. It makes the failed probes fair by construction and
  costs the lower layers nothing, but removes "what was the weather last week"
  from the deployed assistant — a real regression in a shipped capability,
  traded for a scoring convenience.
- *Stating the routing convention in the candidate instruction.* The
  instruction is itself optimizable, so the probe still fails whichever
  candidate deleted the clause — measuring compliance with a convention rather
  than routing judgment.
- *Letting each argument check hunt the trajectory for its own satisfying call.*
  The obvious reading of "the argument checks pass", and the one held until the
  pilot showed candidates make several calls to one tool before getting it right.
  It lets a run assemble a pass out of fragments — one plot call with the right
  roofs and the wrong variable, another with the right variable and the wrong
  roofs, and no correct call anywhere. That is not a hypothetical margin: on the
  plotting family the argument checks are the *entire* scored surface, since the
  answer metric skips a null answer and the gold and must-not sets are satisfied
  by any candidate that reaches for the tool at all. On T26(i) the same hole
  would pass the compositional holdout — the thesis's headline transfer claim —
  on `albedo` set in one call and `forcings` in another, never composed. The rule
  is instead existential over calls of the *conjunction*: some one call satisfies
  every check in its group.
- *Per-tool conjunction without a group key,* which is the same rule stated one
  notch too coarsely. It would have voided T26(ii): a cross-roof comparison is
  genuinely two model runs, so `roof_type` cannot equal both roofs inside one
  call and the template becomes unsatisfiable. The fix would have closed one hole
  in the holdout by breaking another. An optional `group` splits the checks a
  template means to spread across calls; omitting it — the common case — gives
  the tight reading a single plot spec wants.
- *Requiring every call to satisfy the checks.* The universal reading closes the
  same hole and costs the self-repair that `invalid_argument` is designed to
  make free: a candidate that fumbles a variable name, reads the rejection and
  repairs it made the right call, and charging it for the fumble reintroduces the
  extra-call penalty rejected at the top of this entry.

**Accepted risks, stated plainly:**

- A shotgun candidate that calls every tool scores perfect trajectory on any
  template without a must-not. Two mitigations, and both are in place. Coverage:
  every tool holds at least one distractor slot inside train, so a
  call-everything policy hard-fails those templates and cannot win overall. And
  the step cap bounds the excess physically — `MAX_TOOL_STEPS = 6` for a rollout,
  which is six tool calls to spend across a suite whose gold sets run to three.
  The cap became load-bearing rather than incidental once the argument checks
  were tightened below: a candidate cannot buy a pass by emitting calls until one
  of them happens to satisfy every check, because six is the whole budget and an
  exhausted cap leaves no final message to parse.
- A candidate that looks the rule up before calculating fails the
  calculator-routing template, though the behaviour is defensible. Accepted
  because the pair probes routing only through symmetric must-nots — which is
  what makes the question phrasing load-bearing, since the wording must cue the
  intended route unambiguously.
- **Two must-nots rest on candidate-owned text.** T19 and T23 forbid the weather
  tool because the green-roof tool fetches its own weather — a fact principle 5
  requires the baseline's docstring to state, and a docstring is candidate-owned.
  The surface carrying it that no candidate can rewrite is the tool's response
  echo, which the agent sees only *after* the call the must-not is about.
  Accepted rather than repaired: moving the disclosure into the signature or the
  registered name would take self-containment out of the optimizable surface,
  and disclosure quality is part of what the search is meant to move. The failure
  is self-inflicted and legible — a candidate that deletes the disclosure and
  then fetches weather loses trajectory while its answer stays right, which
  surfaces as a trajectory-only loss on those two templates.
- **T15a's gold set punishes an equivalent route.** Its gold set is
  `{text_to_sql_agent}`, but the station weather path derives `precip` from the
  same column and returns the identical number, so a candidate that answers
  correctly through the weather tool alone scores 0 on trajectory: an extra call
  is free, a missing gold call is fatal. The same shape as the T16a/T16b risk
  above, and accepted on the same ground — the wording cues the intended route,
  and the template's discriminating work is the tense pair on the *answer* side,
  T15b's must-not carrying the routing half. Naming both routes gold, as T25
  does, stays available if the pilot shows candidates actually take the weather
  route here; it is not taken pre-emptively, because it would make T15a's
  trajectory satisfiable by either tool and drop it from the templates on which
  the database route is sole-necessary.

**Validity conditions:**

- The shotgun mitigation is void unless every registered tool holds a must-not
  slot **inside train**. A distractor slot living only in the holdout leaves the
  search free to learn the shotgun policy and reveals it only afterwards.
- The plotting family and the counterfactual families measure what they claim
  only while a group's checks are evaluated **against one call**. Both families
  are scored on arguments alone — plotting because its answer is a null artifact
  deliverable, the counterfactuals because their arguments are scored on presence
  and plausibility and never on the tool result — so a per-check search does not
  weaken those metrics, it removes their content. A candidate need never have
  made the call the case is about.

---

## The irrigation calculator

The calculator answers "irrigate?" as a boolean and states the fixed per-roof
dose alongside it for disclosure. It runs its own bucket model in local Python
against constants carried verbatim from the deployed controller, and the unit
correction is measured rather than absorbed: a decision-diff harness replays a
historical window through both unit regimes and reports every date and roof
where the decision flips.

**Rejected:**

- *A per-case volume computation.* The deployed algorithm has none — the dose
  is policy, not a calculation — so a volume template would score the agent
  against arithmetic nobody runs.
- *Reusing the GR2L call for the predictive irrigation chain.* It would score
  the agent against a model the site does not run, and the disagreement is not
  academic: the deployed controller applies a flat 22 %θ field capacity to all
  three substrate roofs, which for the semi-intensive roof sits **12.6 mm
  below** GR2L's measured `Ssubmax`, so the two models disagree about when that
  roof overflows at all. The property at stake is that these templates measure
  the *deployed* rule.
- *Routing the agent's irrigation path through HTTP,* as the model path does.
  GR2L's remoteness is a constraint — its core exists only as a service — not a
  precedent; here the implementation is ours and the placement is free, so it
  goes where it costs nothing to reproduce.
- *Re-deriving the trigger thresholds* so the unit-corrected model reproduces
  the controller's historical decisions. This is the standing principle: a
  fitted correction between instrument and oracle. The deployed constants are
  reproducible from the site's own documentation by anyone; a rescaled set is
  not. Whether to re-tune is the site's call, made against the decision-diff
  evidence.
- *Any conformance check against the R port of the same model* — a bespoke
  fixture file, or a committed canary replayed through the response cache. Both
  were held here while the R endpoint sat in the plan; it no longer does. The
  endpoint serves consumers outside this system and no case's answer depends on
  it (architecture §1.1), so a conformance artefact in *this* repo pins a surface
  the testbed never reads, and lands in a pin list whose every other entry can
  fail a run. Keeping the two implementations in step is the API repo's
  obligation, discharged there.

**Validity condition:** irrigation cases are fully offline and the oracles
import the very function the tool calls, so oracle and tool cannot diverge and
no network sits on the answer path. The cost is stated rather than hidden: the
bucket and the threshold ladder exist in two languages with no shared CI, and
this repo carries no check that they agree. Nothing measured here rests on that
agreement — the R port is downstream of the model, never upstream of an answer.

---

## Retrieval

Reference lookup is an exact card read over a closed, packaged set of YAML
cards, addressed by a topic enum in the tool signature. A known topic returns
its card whole, including the block stating what the card does not cover.

**Rejected:**

- *Lexical (BM25) retrieval over a chunked markdown corpus.* Ranking a closed
  set by lexical overlap is machinery without a problem, and it was not free: it
  imported a German-query-against-English-corpus gap to be settled before
  generation, a chunker whose heading-derived section IDs became a scored
  contract, and an index hash in the pin set. A lexical miss is also a failure
  mode the thesis would have to explain rather than measure.
- *The dense-retrieval ablation arm.* It existed only to mitigate lexical
  brittleness; an exact lookup has none, so it was dropped with its motivation
  rather than deferred.
- *A free-text query matched against card keys.* It reintroduces lexical
  matching at the boundary while appearing not to, and makes an unknown query
  indistinguishable from an absent fact.
- *Typing the abstention at the tool level.* The cleanest contract, but it
  collapses the scope-exclusion template into the already-tested "relay a typed
  `not_available`" behaviour and costs the catalog its strongest hallucination
  probe.
- *A database-backed source inside `values_for()`, so `data_freshness` could be
  rendered.* That card states where each table's record ends, so its values come
  from the pinned database and not from the two constants modules `rendered` is
  defined against. Rendering it would have put a database read inside the one
  tool specified as a pure function of packaged files — no network, no cache
  entry, no `upstream` class — and bought a drift test that only re-checks a
  file the pin set already hashes. So `data_freshness` is **static**: its dates
  are authored once and held by the database hash and the station-derivation
  pin, and the transcription is verified where the database is already open, in
  the pin check, rather than in the knowledge layer. The alternative reading —
  keep it `rendered` and let the *test* open the database — was rejected for the
  same reason with an extra one: it makes the card's guarantee depend on a file
  the card store itself is forbidden to read.

**Validity conditions:**

- The topic vocabulary lives in the **signature**, not the docstring, because
  docstrings are candidate-owned: a candidate that rewrote the topic list away
  would disable the reference route while still appearing optimizable. The
  framework renders the literal type into the function declaration, so the
  vocabulary reaches the model either way (`findings.md`, Codebase seams).
- **No card is named after a single constant,** so absence is never inferable
  from the vocabulary alone. Otherwise the agent abstains from reading the enum
  without opening anything, that template's gold set is forced empty, and it
  duplicates the unsignalled-abstention holdout — voiding the claim that the
  holdout is the only abstention with no tool signal (see **Typed abstention**).
- A non-empty gold card set implies a lookup call in the gold trajectory. Card
  recall is scored, so a gold card on a template that never looks one up scores
  0 on an otherwise correct run.
- The German/English gap is **relocated into the model**, not eliminated: a
  German question maps to an enum value through the LLM rather than through
  lexical overlap with an English corpus, and is measured as routing accuracy
  on German paraphrases instead of as a retriever scope limit.
- **`rendered` names exactly two sources**, `rules_constants.py` and `roofs.py`,
  and a rendered card's drift test asserts equality against them. A third source
  with its own pinning would turn the label from a checkable equality into a
  claim about provenance in general, and a reader could no longer tell which of
  a card's numbers a test is holding. `static` is therefore not "unpinned" — it
  is pinned somewhere other than those two modules, and every static card says
  where in its own entry.

---

## Case time (the `as_of` band)

Each case's `as_of` is drawn from a band inside the measurement record; the
wall clock never enters a case. The floor is derived rather than chosen — from
when runoff recording starts, so the earliest case has weeks of runoff and
months of station weather behind it, and so summer falls inside the band for
the heat templates to balance (record dates: `findings.md`, Data record).

**Rejected:**

- *A single frozen `as_of` at the record's end.* Simplest, and the model seeds
  are maximally fresh — but the forecast-side heat template is degenerate from
  late April, the balance filter works by rejection-sampling dates and would
  have nothing to sample, and every forecast-bearing case (about 30 % of the
  suite) would share one horizon window of ground truth, destroying the
  effective *n*.
- *`as_of` at wall-clock time with the freshness filter dropped.* Every model
  chain would seed from a reading months stale: the score stays well-defined,
  since tool and oracle share code, but the initial condition is physically
  meaningless — and the station source would serve nothing, making it dead
  weight.

**Validity condition:** the band is what makes `as_of` a genuine split
dimension and keeps the database hash a permanent pin. Each template's period
parameter must be intersected with its own `as_of`, since the as-of view makes
any later period empty — an un-intersected period silently yields an empty
result the oracle would then encode as truth.

---

## Splits, sizing and the holdout

Three splits — train, test_seen, test_unseen. Train doubles as the optimizer's
Pareto-tracking set, so the number reported on it is a **selection score**,
never a training accuracy. Per-split sizing is derived from template count
times instances per template, the paired bootstrap resamples the template, and
every holdout template is the b-side of a train-side pair moving exactly one
named axis.

**Rejected:**

- *Passing a validation set through the optimizer's escape-hatch keyword
  arguments.* It would in fact arrive — the merge order lets the key survive
  (`findings.md`, Optimizer internals) — but it stakes an evaluation-validity
  property on an undocumented keyword of an experimental API, where a future
  version setting the key itself would silently drop ours with no warning.
- *Keeping a fourth split for method selection.* The entry point exposes one
  dataset channel and the optimizer never sets a validation set (`findings.md`,
  Optimizer internals), so a committed validation file is a file nothing reads.
  The outer role that split served — choosing between arms, reflection models
  and budget — is replaced by a protocol, not a split: budget and
  hyperparameters are pre-registered before any test run, all method debugging
  happens on train, and every arm is reported on test, never "best of".
- *Growing the holdout by instances rather than templates.* At seven templates,
  doubling instances moves effective trajectory *n* from roughly nine to
  eleven; authoring templates is the only lever that moves the ceiling.

**Validity conditions:**

- Errors cluster by template — trajectory is route-determined and binary, so a
  candidate that misroutes a template misroutes every instance of it.
  Resampling cases rather than templates reports intervals several times too
  narrow, and the holdout's trajectory result is a per-template win/loss table,
  never an accuracy with an interval.
- The two generalization gaps are reported separately because they catch
  different failures, and the template-overfit gap is invisible on test_seen by
  construction — a candidate accreting a per-template routing rule scores
  perfectly there. Since such a routing table is the most likely artifact the
  search produces, the failure of greatest concern is visible only on the
  smallest split.
- Per-parameter disjointness between train and test_seen is a hard generation
  constraint, not a nicety: the reflective record carries the gold answers
  (`findings.md`, Optimizer internals), so a candidate can accrete a memorized
  constant and only disjoint parameter values detect it.
- A holdout entry composing an axis that is itself holdout is confounded — a
  failure cannot be separated from failure on that axis — so it is interpretable
  only where the axis passes, and is reported conditionally.
- The suite detects large effects and not small ones: a stated limit of a suite
  this size, not a defect more instances would repair.

---

## The optimizer entry point and the candidate surface

The search runs through the MLflow prompt-optimization entry point with the
GEPA optimizer; the candidate surface is a set of registered prompts, one per
optimizable component, reached by a per-case prediction function that builds a
scenario context and reads candidate text back through the registry.

**Rejected:**

- *A hand-written GEPA adapter driving the optimizer library directly.* The
  entry point already ships an adapter implementing both required methods plus
  per-iteration logging against the sink already in use here (`findings.md`,
  Optimizer internals). Writing our own buys nothing and duplicates a moving
  part; it stays the fallback if the experimental API moves.
- *The agent framework's own evaluator and evalset format.* Three independent
  disqualifiers: candidate text has no channel into an agent looked up by module
  path, so every iteration would score the production singleton; its trajectory
  metric is argument-exact with no must-not concept; and its answer metric is
  ROUGE over the final message, which cannot express the
  tolerance/skip/abstention split. The format exists for the inverse loop —
  capture-replay regression of a *fixed* agent — and binds the agent by module
  path precisely because of that assumption. Its resemblance to our ground truth
  is a coincidence of shape.
- *One concatenated prompt holding all optimizable text.* It denies
  per-component mutation, which is the granularity the optimizer works at, and
  attributes every reflective observation to a single blob — collapsing the
  addressable optimizable surface the arbitration rule names as a validity
  condition.

**Validity conditions:**

- A component is addressable per candidate only if its text is applied to a
  factory-produced callable and read back through the registry inside the
  prediction function. A module-level docstring is not addressable; and a
  registered prompt never read is *silently frozen while appearing
  optimizable* — invisible in the results, visible only in the framework's
  "prompts were not used" warning, which is therefore asserted on.
- The reflective signal is the scorer's rationale, not its float
  (`findings.md`, Optimizer internals): a float-only scorer optimizes blind.
- The ground-truth envelope is dictated, not designed — ground truth reaches
  scorers only through the expectations column, and inputs is the sole required
  column (`findings.md`, Optimizer internals).
- Candidate text is injected by a process-global patch for the duration of a
  batch (`findings.md`, Optimizer internals): one candidate per process, and
  any other in-process reader of those prompts sees candidate text.

**The surface is pinned in two slots, not one, because its halves are
verifiable in different ways.** `candidate_prompts` carries each component's
registered *name* and the sha256 of its *seed text* and is recomputed offline
from the code, so it makes the T107 freeze checkable — an edit to the
handwritten instruction or to any tool docstring moves a hash and `just pins`
fails on it. `candidate_prompt_versions` carries the version numbers the
registry assigned, which no offline run can derive, so it is pinned by
registration and reported like the canaries. Collapsing the two would cost the
recomputable half its check.

**And the seed is read by version, never by `@latest`.** An alias read would
resolve to whatever was registered most recently, so a re-registration — the
routine consequence of re-pinning — would move the reference arm and the search's
seed under a measurement already in flight, silently and in the direction
nobody chose. An unpinned slot is therefore an error rather than a fallback.

**`predict_fn` assembles a rollout's arguments; it does not build one.** §6's
sketch inlines the context, the toolset and the agent, which is the construction
`run_case` already performs from the same three arguments. The prediction
function therefore reads the candidate, splits the instruction off the tool
docstrings, and delegates. Repeating the construction was rejected: it is the
one way for the search and the measurement run to differ on the path that has
to be identical — the run config's step cap above all, since a search that
bounded a looping candidate differently would disagree with the measurement run
about whether that candidate finishes at all — and it would mean handing over a
pre-built toolset, which the constructor refuses alongside docstrings precisely
so that a silently truncated candidate cannot happen.

**The baseline is registered once and read twice, never copied.** It is the
reference arm §7 measures against *and* the seed the search starts from. A
second copy — a constant for the arm beside a registration for the seed — would
drift the first time either was touched, and the drift would surface as a search
improving on something nobody measured. So the arm is a rollout with **no
candidate patched**, reading the same pinned versions the search reads with one
patched over them.

**And the tool text is registered stripped while the instruction is registered
verbatim.** The asymmetry follows the observability: ADK strips a docstring into
the function declaration (`findings.md`), so a docstring's surrounding
whitespace is a byte no model can read, and registering it would pin something
that cannot move a result while leaving the seed differing from the declaration
it *is*. The instruction is sent verbatim as `static_instruction`, so its bytes
are the frozen text's and stay that way.

**Candidate text is re-read per record and never cached.** The patch carrying it
is installed and reverted around each batch, so the identical read returns
different text on different iterations. A cached read would run every iteration
of the search on whichever candidate was installed first and report the results
under the others' names — a failure with no exception and entirely plausible
numbers. Only the *versions* are resolved once, at wiring time, because they say
which prompts are being optimized and cannot change within a run.

---

## Candidate selection and the scorers' aggregation

Selection runs on **one scalar per record**: what an explicit `aggregation`
callable returns from the four scorers. The per-scorer values are logged and
reach GEPA's reflective dataset, where reflection reads them as text; they do
not hold a front.

**Rejected:**

- *Asking for a per-objective front through the optimizer's escape-hatch keyword
  arguments* — `gepa_kwargs={"frontier_type": "objective"}`, the only way to make
  the four metrics select without being blended. It would in fact arrive: the
  default is `"instance"`, the entry point never sets the key, and a caller's
  value survives the merge (`findings.md`, Optimizer internals). That is the
  objection rather than the recommendation — a headline claim would rest on an
  undocumented passthrough of an `@experimental` API whose failure mode is not an
  error but a *different selection rule*, and the non-instance frontiers raise
  only when no objective scores arrive at all, which is not the case here. The
  same shape as the validation-set keyword rejected under **Splits, sizing and
  the holdout**, refused for the same reason.
- *Omitting `aggregation` and letting the library's default stand.* There is no
  shipped callable to fall back on: the parameter defaults to `None` and the
  metric then averages the numeric scorer values (`findings.md`), which is a
  weighting nobody chose, and it raises outright on the two metrics that must
  skip. An explicit callable is mandatory whatever the front does.

**Validity conditions:** because selection is blended, the weights inside
`aggregation` are part of the method and are pre-registered with the budget and
the hyperparameters (**Splits, sizing and the holdout**), never adjusted after a
test number is seen. And the per-scorer values must still reach the reflective
dataset unblended — reflection over a single float is what **The optimizer entry
point and the candidate surface** already rejects.

---

## GR2L argument surface

The model tool's arguments are flat scalars; forcing overrides are keyed by the
weather row's own field names and echoed back in the response; the model is
seeded at the earlier of the window start and the case's `as_of`.

**Rejected:**

- *Nested argument objects* — a parameters dictionary rather than a scalar
  argument. Flat scalars are the right shape for function declarations and
  nested dicts measurably degrade tool-calling accuracy; the declarative
  argument checks would also have to reach inside a dict to score a value the
  model chose.
- *Either one-sided seed rule.* Seeding at or before `as_of` alone seeds a
  retrospective window from a much later reading; seeding at or before the
  window start alone leaks post-`as_of` sensor data into forecast cases. Each
  fails a case the other handles.
- *A separate forcing vocabulary* with a translation table to the weather row.
  One vocabulary means a counterfactual argument and the row it overrides never
  need translating, and a translation table is a second place for a field rename
  to go wrong silently.

**Validity condition:** the counterfactual families are scored on the presence
and plausibility of these arguments, never on the tool result — possible only
because the arguments are flat, named after the fields they replace, and echoed
back unchanged.

**Second validity condition, and it binds on the answer rather than the
trajectory: an override must be able to move the number.** A counterfactual case
whose answer equals the un-overridden prediction is one a candidate earns in full
by ignoring the argument the template exists to probe, so the answer metric would
report as evidence of counterfactual reasoning something that is evidence of
nothing. T110 found both ways this happens. `initial_soil_moisture_pct` stops
mattering once the store saturates and forgets its initial condition, which is a
property of the *draw* and is resampled away. `albedo` stopped mattering because
the served GR2L accepted the parameter and discarded it, which is a property of
the *service* and could not be resampled away at all. The oracles therefore
measure the dependence per draw — a probe run against the baseline — and refuse
rather than emit, which is the same rule §1.6 already applies to an answer sitting
within tolerance of its own threshold. Refusing is what keeps the failure visible:
a T22 that quietly materialized would look like a passing template.

**The albedo case is closed and the rule is what closed it.** The service was
repaired on 2026-08-24 and T22 materialized on the next run with no edit to the
oracle, because the guard compares the override against the roof's own default
rather than asserting a known-bad build (`findings.md`). That is the property to
preserve if either check is ever revisited: a guard written as "this service is
broken" would have had to be found and removed by hand, and until someone did,
the template would have stayed silently empty.

**Rejected here too:**

- *Hard-coding the albedo template as retired.* The refusal is computed from the
  service's own behaviour, so T22 started materializing again the day the
  parameter was wired up, with no edit — which is what happened. A retirement
  would have had to be noticed and undone by hand.
- *Choosing a "safe" window length for T23 instead of probing.* Measured, the
  safe length does not exist: the seed survives ten days in October and two in a
  wet April week.

---

## Model pinning

The task model and the reflection model are pinned by **served model id,
endpoint, decoding parameters and a committed request/response canary** — the
same mechanism GR2L already uses — rather than by a dated version string.

**Rejected:**

- *Requiring a dated model version.* The specification asked for one, and the
  provider does not offer one: the endpoints in use serve open-weight models
  under undated names. A requirement no provider can satisfy is not a pin, it is
  an unmet condition that would sit in the document unenforced.
- *Moving the task model to a provider that does publish dated versions,* to
  satisfy the requirement literally. It changes the system under study — the
  thesis measures prompt optimization over the assistant this site actually runs
  — for a reproducibility gain the canary already delivers.

**Validity condition:** the canary is checked in the same pass on every arm of a
comparison. It detects a provider-side model swap **after the fact**, never
prevents one, so a swap landing between two arms is the residual risk; diverging
canaries mean the comparison is repeated. Decoding is otherwise fixed —
`temperature=0` and one pinned seed, the seed **sent but not verifiably
honoured**, since these endpoints serve open-weight models under no documented
seed contract. Residual nondeterminism is therefore measured rather than
controlled, on §7's statistical terms (see **Replication and the LLM cache**).

---

## A pinned service moved after the freeze

The GR2L deployment was changed on **2026-08-24**, five hours after T107 froze
the testbed that same day. The change was deliberate and was a repair — the
service had been accepting `albedo` and discarding it — but it moved the model's
ET routine as well, so the canary moved with it:
`0c39f945a3f94073847202804d7e21e686dd8651b5ae81cb44bf4d04fbf20faa` →
`c8f51c82fe5f8602577831c84cc8ad7aa31ca5143cc57bcf5014c44491a0edf7`. It was
re-pinned through `check_pins.py --capture-gr2l-canary --accept-moved`, which is
a separate flag rather than a prompt precisely so that a change of this kind
appears in a shell history and a commit message. This section is what §5 owes a
reader who later finds two different numbers for the same roof and the same
week.

**What moved.** Served `ET_PM` rose by a constant 0.4489 mm/day across the whole
albedo range, where before the repair it matched our port of `GR2L_function.R`
to 4e-5 at the default 0.2. `ET` follows `ET_PM` through
`ET_PM · kg · Ssub/Ssubmax`, and `Ssub` follows it through the store's own
bookkeeping. On the canary's single seed day that is `ET` 1.045 → 1.144 with
`Ssub` unchanged at the 8.0 mm the probe seeds; over a 28-day run it is a
divergence that starts at 0 and reaches 1.1 mm (`findings.md`). The endpoint
therefore no longer reproduces the R checkout, and the pin is what says so.

**Why the two sides are not comparable.** Every modelled answer — families D and
G entirely, family E's calculator chain where it reads a modelled state, family
H's model overlay — is a function of that series. A soil-moisture minimum, a
threshold crossing, a deviation from the sensor record: each is read off days
the repair moved, and **none of them moved by a constant**, because the
divergence compounds through the store. So a result measured before 2026-08-24
and one measured after are measurements of two different models, and averaging
or comparing them would report neither. This is the failure mode §5's pin list
exists for, and it is why a service with no version string to pin is pinned by a
request/response hash instead: the deployment published nothing that would have
revealed the change, and the canary was the only thing in the repository that
was ever going to notice.

**What it invalidated, and what it did not.** The eleven GR2L entries in
`eval/cache/` were the old build's and were discarded rather than merged (T116),
with the eighteen Open-Meteo Archive entries beside them kept — ERA5 reanalysis
did not move, and the cache serves two services whose requests separate on
shape. The evaluation suite was generated *after* the re-pin and reproduces byte
for byte against the current build, so none of the three split files under
`eval/cases/` needed re-materializing and none of them was rewritten.
`eval/cases/pilot.json` is the exception and is left standing: its three T09
cases were answered against the old build and carry the old canary in their
pins, which is exactly the mismatch `expectations.pins` exists to make visible.
Their answers happen not to have moved — they are booleans, and no minimum sits
near its threshold. The file is left as it is, deliberately: it was P6's freeze
gate rather than a dataset, that gate has been passed, and nothing downstream
reads it — the search and the measurement run take `train.json` and the two test
splits. Re-running it would re-measure a gate rather than measure anything, so
the stale stamp stands as a record of when the pilot was taken.

**Validity condition:** a moved canary is a hard failure, and re-pinning one is
an act with a date attached rather than a maintenance step. Any result quoted in
the thesis names which side of 2026-08-24 it was measured on, and no comparison
spans the two.

---

## Replication and the LLM cache

Three rollouts per condition on the measurement path, at one pinned decoding
seed, with the LLM response cache **off** for those runs. The cache stays on
inside the search, where it is keyed by the **full request** — messages, tool
declarations, model id and decoding parameters — so it can never serve one
candidate's response to another.

**Rejected:**

- *Three distinct decoding seeds with the cache left on.* The obvious reading of
  "three seeds per condition", and it does yield three keys and three samples —
  but only where the endpoint honours `seed`, which this deployment's endpoints
  do not guarantee (see **Model pinning**). Where the parameter is ignored the
  arrangement is the accepted one wearing extra bookkeeping, and the bookkeeping
  asserts a control that is not there.
- *One fixed seed with the cache on,* which is what the architecture said until
  now. The three replicates hash to one key, return the same bytes, cost nothing
  and carry no information: the clause multiplies *n* by one while appearing to
  shrink noise.
- *Dropping replication entirely.* Cheapest, and defensible at temperature 0 —
  but nondeterminism on a batched endpoint survives greedy decoding, and it would
  become an error term stated rather than bounded, on a suite already limited to
  large effects.
- *Moving replication into the search* — three optimizer runs, each measured
  once. It measures search variance, a real quantity and arguably the more
  interesting one, but it leaves per-case noise unmeasured and spends three times
  the optimizer budget instead of three times the far cheaper evaluation.

**Validity conditions:** the repeats are near-replicates, not independent draws —
they shrink per-case noise, they do not multiply *n*, and the unit of analysis
stays the template. And the cache key must carry the tool declarations, not the
messages alone: two candidates differing only in a docstring would otherwise
share entries and one would be scored on the other's responses — the
silently-frozen-component failure of **The optimizer entry point and the
candidate surface**, arriving through a second door.

---

## The day boundary

A day is a **Europe/Berlin calendar day**, everywhere: the station derivation,
the semantic layer the SQL model reads, every oracle, the generation filters'
coverage predicate, and the plot tool's aggregation. The five tables hold naive
UTC, so a conversion sits at every grouping site — and it is written once, in
code, rather than retyped per site.

**Rejected:**

- *UTC days,* which the columns already carry, so no conversion would exist
  anywhere and no generated SQL could forget one. Rejected because "a day" would
  stop meaning what the researchers asking the questions mean, and because the
  station derivation and GR2L's daily rows are already specified in local days
  (`weather_tool.md` § Station source): the choice is not between two conventions
  but between one convention and two.
- *Berlin for the weather and model paths, UTC for database aggregation* — the
  de-facto state of these documents before this entry. The two groupings differ
  on 106 of 482 record days by up to 6.664 mm of rain (`findings.md`), which
  moves a peak-day argmax, flips a daily runoff boolean near a boundary, and
  breaks the tense-pair template whose two routes are supposed to return the
  identical number.

**Validity condition:** the oracle and the candidate must group alike, or the
oracle encodes as truth a total no route could produce. The semantic layer states
the boundary so the model can honour it, and the boundary lives in one helper for
the same reason the window resolver does: three call sites that each convert
correctly today drift independently tomorrow.

---

## The as-of cut

A case's `as_of` is an **instant**, written in the case file in the site's own
offset-bearing form and converted to UTC inside `connect_asof` before it meets
the tables' naive UTC timestamps. The conversion belongs to the seam, never to
the caller.

**Rejected:**

- *Passing the bound to DuckDB as written.* A timezone-aware parameter is
  rendered into the connection's **session** timezone — inherited from the host
  `TZ` — before being compared against a naive column, so one case file admits an
  hour of post-`as_of` data in winter, two in summer, and a different hour again
  on a host set to UTC (`findings.md`). The `water.duckdb` hash cannot see it, so
  every captured response and materialized answer downstream would be stamped
  against a pin that does not cover the thing that moved.
- *Naive UTC in the case file.* It removes the conversion and the whole bug class
  at the source, and was close-run — but the stamp then reads an hour or two off
  the site's own clock, and near midnight it names a different Berlin day than the
  case is about, which is the boundary every oracle groups on (see **The day
  boundary**).
- *Requiring the caller to pass a correctly normalized bound.* The failure is
  silent and the case still answers, which is the shape **The construction seam**
  rejects wholesale.

**Validity condition:** the convention is pinned by a test asserting the cut is
identical under at least two host `TZ` settings. Nothing else in the pin set can
detect a violation.

---

## The `radiation` timestamp offset

`radiation` is stamped an hour behind the other four tables (`findings.md`). The
pinned database is **not** rebuilt: the offset is stated in the semantic layer
and disclosed as a scope limit, and the data is served as recorded.

**Rejected:**

- *Correcting the timestamps on ingest.* The clean fix, and the one that would
  let a single `as_of` literal cut all five tables alike — but it moves the
  `water.duckdb` sha256, which is the permanent pin every captured cache entry
  and every materialized answer is stamped against. Paying that at any point
  after the first capture means regenerating the suite; paying it before buys a
  correction on a table most of the suite never reads, since `radiation` covers
  only 2025-03-01 → 2025-10-01 and leaves most of the `as_of` band empty.
- *Applying the shift in the as-of view instead,* so the file stays byte-stable.
  It hides the offset behind a view definition that the DB hash does not cover
  and the semantic layer does not describe, which is the failure mode the
  station-derivation pin exists to prevent.

**Validity condition:** the offset is disclosed wherever `radiation` is
reachable — the semantic layer and the plot vocabulary — because a `radiation`
series drawn beside another table's is misaligned by two half-hourly rows, and a
reader who does not know that reads the misalignment as physics.

---

## Family A and the station derivation

The pure-SQL oracles read `wetter` with their own `sum` and `max`; they do
**not** call `weather_station`'s derivation, even though it aggregates the same
two columns from the same table through the same day expression.

The reason is the completeness rule. The derivation serves a day only when it
carries all 48 half-hourly rows and drops it otherwise, because a weather
*source* that returned partial days would hand GR2L a forcing row assembled from
half a day. A candidate answering T02 or T15a writes SQL against the semantic
layer and applies nothing of the kind. So importing the derivation would make
the oracle answer a question the gold trajectory does not ask, and a case whose
window contained one short day would score a correct candidate wrong.

What is shared instead is everything that can be: the day expression comes from
`site_day_expr()`, `Rain` comes from the plot tool's closed vocabulary, and
`Tmax` — the one name with no shared constant anywhere — is checked against the
semantic layer's schema block before it is used, so a column the candidate is
not shown cannot be read by the oracle either.

**Rejected:**

- *Calling `StationWeatherSource.daily_rows` and summing its `precip`.* It is the
  stronger sharing and it is the wrong sharing: it silently narrows the oracle's
  window to complete days, which is a property of the weather tool rather than of
  the question. Family C does exactly this, and correctly, because there the
  weather tool *is* the gold trajectory.
- *Copying the derivation's `_AGGREGATES` fragment.* A private SQL string,
  written for a caller with a different contract; borrowing it inherits the
  completeness rule through the `HAVING` clause it is paired with, or drops it and
  keeps a second copy of the aggregation to drift.
- *Applying §1.6's coverage predicate inside the oracle.* Generation's job. Run
  here it would turn a badly sampled case into a wrong answer instead of a
  rejected draw, and the two failures need different repairs.

**Validity condition:** the two routes agree on a fully covered window, which is
what makes T15a's accepted trajectory cost (see **Trajectory scoring and routing
probes**) worth accepting. Measured at 29.257 mm both ways over 2026-04-13..19
(`findings.md § T15a and the station weather path`), and it is a claim about
covered windows only.

---

## Forward horizons are counted in days

Every template whose window reaches forward — T09, T10, T13, T14, T15b, T18a,
T18b, T20, T21, T26, T27 — carries the horizon as a **whole number of days** in
its parameters, and the question names that number. No template asks in hours,
and none carries a future window as a `start..end` range. **A relative window
that reaches backward is bound by the same rule**: T19's look-back is *d*
complete past days and T23's overridden state sits on a named date, never on
"last week" or "last Monday".

Two independent forcings, either of which alone would settle it.

**Hours hid a disagreement, measured.** Weather rows and GR2L rows are daily, so
an hourly horizon has to become a day count somewhere. T107's pilot found the
oracle reading "the next 72 hours" as three days while a candidate writing the
same phrase as explicit dates read it as four, and nothing on either side could
see the disagreement: the two silently differed on every draw whose answer did
not happen to fall the same way in both windows. T09 was repaired then; T13 and
T15b carried the same shape into T110's first packet, and T10, T19, T20, T21,
T23, T26 and T27(i) into its second. All of them are repaired, and the list above
is now every template with a relative window rather than the four that had been
noticed.

**A named calendar span is the same defect wearing a noun.** "Last week", "the
coming week" and "last Monday" are not day counts — the validity condition below
already says why, and it says it about paraphrases, but a *sketch* that writes
one is the same redenotation authored into the template instead of introduced by
a translator. T19, T20 and T23 carried one each until T110's second packet.

**A future range is unrunnable.** `period_param_within_as_of` intersects every
`YYYY-MM-DD` a parameter carries with the case's own cut (T104), and a future
window's days are all past it — so `future_period: "2026-04-20..2026-04-26"`
raises `CaseAssertionError` before the case is scored. T18a already carried
`ahead_days` as a count for exactly this reason; T15b's `{future_period}` was the
one entry that had not followed.

**Rejected:**

- *Converting hours to days inside each oracle.* It is the conversion itself that
  is the defect: wherever it happens privately, it is a rule the candidate was
  never told, and both sides then apply their own.
- *Relaxing the assertion for parameters whose name says they are forward-looking.*
  The invariant is read off parameter *values* on purpose, because the name is the
  template author's and the invariant is not. An exemption keyed on a name is an
  exemption a future template gets by spelling.
- *Writing the future window as `start..end` in the question but keeping the
  parameter a count.* The case file would then carry a window nothing checks
  against the horizon, which is the surface T18a exists to probe.

**Validity condition:** a paraphrase may restate a window but not **redenote**
it. "In den nächsten sieben Tagen" is `d = 7`; "nächste Woche" is not, because a
calendar week beginning Monday is a different set of days from the seven
beginning today. The oracle resolves the parameter and never the prose, so a
paraphrase that moves the window makes the case wrong rather than hard — T112's
constraint, stated here because it is the same failure as the hours one.

---

## Generation draws the window first and the cut second

A retrospective template — anything carrying a `{month}`, a `{period}`, T04's
`{date}` or T12's `{event}` — draws its **parameter window first**, then samples
`as_of` from the days that can see it. The forward families keep the opposite
order, because their window is defined relative to `as_of` and has no existence
without it.

**Measured, not reasoned.** The first loop drew `as_of` and then a window behind
it, which silently weights every window by how many band days can still reach it:
a June window is reachable from 328 days and an April one from a handful. On T04
that thinned the wet class from the record's own 1-in-6.3 to **1 in 25**, and the
loop spent 13.5 draws per accepted case against 3.8 after the fix (`findings.md`).
The balance rule still produced a 50/50 split either way — that is what rejection
sampling is for — so the defect was invisible in the output and visible only in
the draw counts.

**Rejected:**

- *Leaving the order alone and raising the attempt ceiling.* It fills, and it
  fills from a distribution nothing states: `as_of` and the parameter window are
  correlated by construction, and T113's striped `as_of` partition would inherit
  that correlation as a confound between split and season.
- *Sampling the window uniformly and then fixing `as_of` at the band's ceiling.*
  Simplest, and it destroys `as_of` as a split dimension — `decisions.md § Case
  time` rejects the single frozen cut for reasons that apply here verbatim.
- *Rejection-sampling the pair until the window's marginal is uniform.* The same
  distribution at many times the cost, and it hides the correlation inside a loop
  instead of removing it.

**Validity condition:** a template whose window is an absolute stretch of the
record draws it independently of the cut. Where the split's day pool holds no day
late enough to see the window drawn, the draw is *undrawable* and resampled —
counted separately from a predicate rejection, because a template that is
undrawable everywhere is a sizing problem and one that fails a predicate is a
data problem.

---

## `as_of` is stamped at a fixed late hour

Every case's `as_of` is 23:00 site time on its drawn day. The day varies and is
what T113 stripes; the hour does not.

**The seed forces it.** A seed-bearing family anchors at
`seed_at = min(window_start, as_of)`, so a forward window's seed day *is* the
`as_of` day — and §1.6 wants a point query's day at ≥44 of 48 rows. At a morning
cut that day is structurally incomplete (18 rows at 09:00), so every forward draw
would fail a coverage predicate that is asking about a sensor and answering about
the cut. At 23:00 the day carries 47 of its 48 rows and one predicate serves the
seed and every other point query alike.

**Rejected:**

- *A second coverage rule for the seed day, prorated against the hour.* It makes
  the predicate depend on the cut it is supposed to be evaluated through, and the
  rule that "a check never passes on data the tool cannot see" stops being one
  rule.
- *Sampling the hour alongside the day.* It buys no coverage the band does not
  already have, and it makes T113's per-parameter disjointness on `as_of` a
  statement about two quantities instead of one.
- *Seeding the forward families at `as_of - 1 day`.* That is not the rule
  architecture §3.4 states, and an oracle and a tool that disagree about the seed
  day disagree about the answer.

**Validity condition:** the hour is a property of the generator, not of a
template. A case that needs a different one is a case whose window is wrong.

---

## An answer the schema cannot carry is not resampled

Where an oracle answers something `eval/schema/case.schema.json` does not admit,
generation raises `Unemittable` and stops, rather than putting the draw back.

**Because every draw fails identically.** T26(ii) and T26(iii) answer the winning
roof's canonical name, and the schema's `answer` admits a boolean, a number, an
ISO-day string or null (`findings.md`). Resampling that 400 times and then
reporting "the pool is too tight" would name the data as the cause of a
disagreement between two frozen specifications. The two failure modes are
different in kind and the generator says which one it hit.

**Rejected:**

- *Coercing the answer — emitting the roof name as a bare string, or as the index
  of the winning roof.* The first needs the schema changed anyway; the second
  invents a convention no scorer, oracle or catalog entry knows, which is exactly
  the drift §6.1's copied-constants split exists to prevent.
- *Dropping the two variants and refilling T26 from variant (i).* It fills the
  ledger and silently deletes a probe: (iii) exists to keep the compositional
  headline off double transfer (`questions.md` §2 T26), and a T26 made of (i)
  alone is a different holdout entry wearing the same id.
- *Skipping schema validation at generation and catching it at emission.* T114
  would then discover it, over a whole run, with the draw that caused it gone.

**Validity condition:** the case schema is checked at the point the case is
built, not at the point it is written. A generator that emits a case the schema
rejects has produced a case nothing can score, and the earliest place to find
that out is the draw that produced it.

---

## Both sides of the style cut carry colloquial German

Paraphrase style pools are disjoint between train and test (§1.7), and the
colloquial German register is on **both** sides of that cut: `de_umgangssprachlich`
in train, `de_knapp` in test, sharing no sketch. Eight registers in all, two per
(side, language).

**Because disjointness is about surface forms, not about registers.** The rule
sits beside per-parameter disjointness as a memorization guard: no wording a
candidate met in train is met again in test. A whole register withheld is a
different claim, and either direction of it is worse than useless — colloquial
German in test alone confounds German register with style novelty on the split
that carries the generalization headline, and in train alone the suite never
measures the register the site actually speaks.

**Rejected:**

- *One colloquial register, in test.* The `de` stratum's number would then mix
  two changes at once, and §1.7 reports DE routing accuracy precisely because it
  is a measured quantity rather than sampling noise.
- *One register per (side, language).* Every instance of a template inside a
  split then reads identically, which is what the canonical rendering already
  did and is what "paraphrases" was asked for instead.
- *Generating paraphrases by transformation rather than authoring them.* A
  mechanical rewrite cannot keep the route cue and the window invariant — the two
  things §1.6 says a paraphrase may not touch — and the failure would be silent
  in exactly the families where phrasing is the only discriminator (T16a/T16b).

**Validity condition:** the two German registers also differ in date convention —
`de_standard`/`de_hoeflich` write a window in ISO dates, `de_umgangssprachlich`/
`de_knapp` in the German `03.07.2025` form — so both conventions reach a
candidate on a *stated* axis. A German paraphrase that used both conventions
would make a parsing failure unattributable.

---

## Language is a positional stratum, not a sampled one

Each instance's language and register come from `paraphrases.plan`, a pure
function of the split and the template's position in the registry. Languages
alternate inside a template and the starting language alternates with the
position, so train lands 50/50, test_unseen 28/28 and test_seen **63/62**.

**Because a stratum drawn at random is a stratum whose balance is a property of
the seed.** This is §1.7's reason for stratifying the `variant` axis, and
language is reported the same way. The arithmetic is also stated rather than
rounded: 125 is odd, so test_seen cannot be halved, and 63/62 is what "50/50
within each split" means there.

**Rejected:**

- *Sampling the language per instance and rejection-sampling until balanced.*
  The same distribution at more cost, and it makes the per-template mix a draw —
  a template answered only in English would balance the split and leave that
  template's German accuracy unmeasured.
- *Carrying the style in the case file.* `case.schema.json`'s `inputs` admits no
  such key, and it does not need one: the plan is recoverable exactly from the
  split, the registry and the instance index.
- *Balancing language across the whole suite instead of within each split.* The
  stratum is reported per split, so a suite-level balance can hide a split that
  is 70/30.

**Validity condition:** every template is asked in both languages in every split
it carries. A per-split balance assembled from monolingual templates reports the
same 50/50 and measures nothing.

---

## The `as_of` band is dealt out one day at a time

The three splits take every third day of the band — train 2025-06-01, test_seen
06-02, test_unseen 06-03, and so on to 2026-04-24. Disjoint, and interleaved at
the finest stride there is.

**Because what the interleaving protects against clusters on a multi-day scale.**
A storm lasts two to six days, a heatwave three or more, the record's two
lysimeter outages four and thirty-five. A stripe wider than the episode hands
whole episodes to one split, which is the seasonal imbalance a contiguous cut has
in a milder form: §1.7 rejects the cut because it strands summer on one side and
makes T02, T08 and T20 unbalanceable. At stride 3 no split is ever more than two
days from any event in the record, and each holds a third of every week and every
month of the band.

**Rejected:**

- *A contiguous cut — train the first two thirds, test the last third.* §1.7's
  own rejection, and it also puts the two outages entirely inside one split.
- *Week-long or month-long stripes.* Each split still spans the band, but a
  single storm or heatwave now falls inside one stripe, and T04's wet days —
  1 in 6.3 of the pool and clustered — thin unevenly between the splits.
- *Assigning days at random.* Equivalent in expectation and worse in fact: the
  assignment is then a property of the seed rather than of the calendar, and a
  reviewer cannot check a split's membership without rerunning the generator.

**Validity condition:** the day stripe is also what makes a `{period}`
disjoint. A window is a continuum and cannot be enumerated into halves, so its
end day is drawn from the split's own days — and two splits' periods then differ
in a value the case file carries.

---

## Value pools are striped, and the holdout takes them whole

Every discrete pool a template samples — thresholds, months, events, aliases,
horizons, override values, stated values, the forcing offset — is cut by position:
train takes the even members, test_seen the odd, test_unseen all of them.

**Striped rather than cut at a point**, for the reason `as_of` is: a pool split at
its median gives train the low thresholds and test_seen the high ones, which
confounds the split with the difficulty of the draw and can put one class of a
bool template out of reach on one side. Every other member leaves both sides
spanning the same range.

**The holdout takes every pool whole**, because its novelty is its templates.
T16b, T20, T22, T23, T26 never appear in train at all, so a shared value carries
no answer with it; a third stripe would thin every pool by another third — T20
has to reach both classes out of five horizons — and would confound a
template-transfer failure with an unseen parameter value, which is not the one
axis a holdout entry moves.

**Rejected:**

- *Drawing freely and rejecting values the other split has used.* It converges to
  the same sets and is precisely the "achieved by independent resampling" §1.7
  rules out: the constraint would then hold of one run rather than of the design,
  and the order the splits are generated in would decide who gets which value.
- *Assigning each value to a side by a hash of the value.* Value-intrinsic, which
  is the property that matters, but the split of a seven-member pool is then
  binomial — 7/0 is a pool one side cannot draw from at all.
- *Cutting the pools by hand, per template.* Thirty-two entries, each a place to
  forget a parameter; the memorized-constant detector fails open silently when
  one is missed.

**Validity condition:** the stripe must be a property of the *value*, not of its
index in one particular list. Two lists that share values and are striped
separately put the same value on both sides — measured on T24a's `{month}`, whose
two tables offer overlapping month lists (`findings.md`). Pools that overlap are
striped once, over the widest of them, and narrowed afterwards.

---

## A shortfall is stated in the suite, not resolved by coercion

Where an oracle answers something the case schema cannot carry, emission writes
every other case and records the missing instances as a **shortfall** — the
template, the split, the count and the schema error. It neither withholds the
suite nor invents a representation for the answer.

**The suite currently has none**: T26's comparison variants were the case this
was built for, and they were repaired rather than carried (§ A comparison is
answered as a boolean over an ordered pair). The mechanism stays, because the
alternative to it is a generator that either crashes or lies, and the next such
collision should not have to choose between those.

**Because the two surfaces that disagree are not emission's to move.** The repair
for a shape mismatch is a specification change in the schema or in the oracle,
and a packet that discovers one mid-run is not the packet that decides it. The
honest interim outcome is a suite that says which instances are missing and why,
rather than one with coerced answers or no suite at all.

**This refines T111's `Unemittable` rather than reversing it.** That decision —
"an answer the schema cannot carry is not resampled" — stands: the draw is not
put back, because every draw fails identically. What changes is the blast radius.
T111 raised for the whole template, so a whole-catalog pass had to exclude T26
entirely and the holdout landed at 48. Carrying the shortfall per *instance*
meant variant (i) still emitted its three, which is what made the gap legible as
"two variants" rather than "one template".

**Rejected:**

- *Failing the whole emission.* One holdout variant would then block every other
  case in the catalog from being committed, and the packet that could fix it is a
  specification change nobody is necessarily in the middle of.
- *Backfilling the missing instances from a sibling variant.* It fills the ledger
  and silently deletes a probe — T26(iii) exists to keep the compositional
  headline off double transfer — and § An answer the schema cannot carry is not
  resampled already rejects it for that reason.
- *Emitting them with `answer: null` and `answer_metric: "skipped"`.* Valid
  against the schema, and it records a case with an answer as a case without one:
  the abstention metric would score answerable cases as abstentions.

**Validity condition:** a shortfall is reported per instance with the cause that
produced it, and the suite's stated size is what it contains. Any total quoted
against `templates × m` names the difference rather than rounding to it.

---

## A comparison is answered as a boolean over an ordered pair

T26's cross-roof variants ask whether the **first** roof the question names ends
the window wetter than the second, and answer `true`/`false`. They answered the
winning roof's canonical name until T114.

**Because a roof name is a shape nothing in the testbed admits, the agent
included.** `case.schema.json`'s `answer` takes a boolean, a number, an ISO day
or null — and the root instruction states the same three to the candidate:
"`true` or `false` for a yes/no question, a bare number for a quantity,
`"YYYY-MM-DD"` for a date". So the mismatch was never symmetric. Two surfaces
agreed with each other and the oracle was the outlier, and the consequence was
not merely that no case file could carry the answer: **no candidate had been told
it could give one.** Five holdout instances were unemittable and, had they been
emitted, unanswerable.

**Rejected:**

- *Widening the schema's `answer` to admit a categorical string.* It makes the
  file writable and leaves the case unanswerable, because the answer vocabulary
  the agent is given lives in `EVALUATION_ROOT_INSTRUCTION` — the search's own
  starting point. Widening that too would make the gold answer's reachability a
  function of candidate text, which is not ground truth. It would also score by
  exact string equality, so "the semi-intensive roof" fails where
  `semi_intensive` passes.
- *Answering the signed gap in pp.* The oracle already computes both finals, so
  it costs the same edit — and it turns a comparison probe into a quantity probe:
  a candidate with the direction right and the magnitude outside tolerance fails,
  and a near-tie draw is scored on noise. The tie guard exists precisely because
  the interesting content is the direction.
- *Leaving the suite at 276 with the two variants withheld.* The cheapest option
  and the most expensive consequence: (i) and (ii) both compose `albedo`, an axis
  that is itself holdout, so with (iii) absent the compositional headline is
  **entirely** conditional on T22 — there is no unconditional compositional probe
  in the suite at all.

**Validity condition:** the pair is drawn in the order the question names it and
is never sorted, so both classes are reachable by construction and the balance
rule has something to balance. A tie is still refused rather than broken. And the
answer contract, the case schema and every oracle state the same answer
vocabulary — a fourth shape appearing in one of them is the defect this entry
records, not a fact about the question.

---

## The emitted key order is written out, not inherited

`eval/cases/*.json` carry their keys in the order `emit.py` names, and a key the
emitter does not know raises rather than being written or dropped.

**Because insertion order is a property of a code path.** A dict preserves the
order keys were added in, and that order is decided by branches: the optional
`tolerance` is appended only where the answer is numeric, so it lands last on
some cases and nowhere on others. Committing that means committing the shape of
the generator's control flow, and a refactor that moved one assignment would
rewrite every case file with no change to a single value.

**Rejected:**

- *`sort_keys=True`.* Stable, and it interleaves the fields nobody reads with the
  ones a reviewer scans first — `answer` between `answer_metric` and
  `argument_checks`, `case_id` before `as_of` but after nothing legible.
- *Letting an unknown key through.* It is written in dict order, which is the
  thing this exists to prevent, and it does so silently on a schema change.
- *Dropping an unknown key.* Silently loses a field the schema may since have
  required, and the case still validates because the schema's required list is
  what was checked before the drop.

**Validity condition:** the order is stable across runs and across refactors of
the generator. Adding a field to the schema is a two-line change here, and
forgetting it is a loud failure rather than an unstable file.
