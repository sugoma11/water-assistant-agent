# Implementation plan — green-roof testbed for prompt optimization

Architecture: [`agent_architecture.md`](./agent_architecture.md) ·
Catalog: [`questions.md`](./questions.md) ·
Rejected alternatives: [`decisions.md`](./decisions.md) ·
Measurements: [`findings.md`](./findings.md) ·
Tasks: [`tasks.md`](./tasks.md)

**How to read this.** The architecture states the target system in the present
tense; this file states the order in which it gets built and what each phase may
not proceed without. Decisions are cited by their subject heading in
`decisions.md` — the old `D1`–`D31` numbering is retired along with the
architecture's former §10, so a reference here reads
`decisions.md § The construction seam`, never `D3`. Measurements are cited by
section in `findings.md` and never restated.

**Arbitration** is the architecture's: where this plan and the code disagree
about a *mechanism*, the code wins; where they disagree about an *evaluation
property*, the specification wins and the code is the defect.

**Task IDs.** Three-digit `T0nn` always means a task in [`tasks.md`](./tasks.md);
two-digit `Tnn` (`T01`, `T16a`, `T27`) always means a catalog template in
`questions.md`. The two vocabularies overlap by accident of history and are kept
apart by width.

---

## 1 Technical context

### 1.1 What exists

`src/water_assistant_agent/assistant/` runs a FastAPI + AG-UI service with a
module-level `root_agent` (ADK `Agent`) wired to three tools:
`text_to_sql_agent` (an `AgentTool` over the frozen text2SQL sub-agent),
`get_weather_forecast_tool`, `predict_green_roof_water_balance_tool`. The tool
layer is already split the way the architecture's three layers need — ADK
wrappers (`tools/gr2l.py`, `tools/weather.py`, `tools/warehouse.py`) over pure,
ADK-free clients (`gr2l_client.py`, `weather_client.py`, `swc.py`, `site.py`,
`schemas.py`). That split is what makes this plan tractable: layers 2 and 3
barely move, layer 1 is rewritten as factories.

Component status is `agent_architecture.md` §0's table and is not duplicated
here. The seams the phases below rest on are `findings.md` § Codebase seams,
also not duplicated.

### 1.2 What was verified while writing this plan

Read from installed source and from this checkout on 2026-08-19; the durable
ones belong in `findings.md` and are recorded there by T004.

- **GEPA's Pareto front is over instances, not objectives, unless asked
  otherwise.** MLflow's adapter does forward per-scorer values —
  `objective_scores=[result.individual_scores …]`
  (`mlflow/genai/optimize/optimizers/gepa_optimizer.py:194,206`) — but
  `gepa.optimize`'s `frontier_type` defaults to `"instance"`
  (`gepa/api.py:53,135`) and `GepaPromptOptimizer` never sets it
  (`gepa_optimizer.py:349-357`), so selection runs on the scalar `scores`, i.e.
  on the `aggregation` output (`gepa/core/state.py:204`). The non-instance
  frontiers additionally *raise* when the evaluator supplies no objective scores
  (`state.py:210-215`). §2.2 settles what this plan does about it.
- **MLflow ships no aggregation callable at all.** The `weighted_objective` in
  `optimize_prompts`' docstring is an example the caller writes
  (`mlflow/genai/optimize/optimize.py:172-182`); the parameter itself is
  `aggregation: AggregationFn | None = None` (`:54`), and omitting it makes the
  **mean of the numeric scorer values** the objective, with any non-numeric value
  raising instead (`optimize/util.py:196-214`). `create_metric_from_scorers`
  (`util.py:135`) returns `(aggregated_score, rationales, individual_scores)`
  (`:197,203`). The architecture's `aggregation=weighted_mean` sketch named no
  real callable and is corrected by T001 to name this repo's own.
- **`roof_type` is a plain `str` at the tool boundary** (`tools/gr2l.py:160`),
  so the gravel and wetland aliases family I needs are expressible. It must stay
  that way: an ADK `Literal` would render an enum into the function declaration
  and make T27 unaskable.
- **The wetland is still modellable in code.** `NON_MODELLABLE_ROOFS` holds only
  gravel aliases (`gr2l_client.py:39-45`), `ROOF_PRESETS` still carries a
  `wetland` entry (`:63`), `gr2l.py` still routes it through `MM_ONLY_ROOFS`
  (`:78`), and `ROOT_INSTRUCTION` still advertises "four roof segments: wetland,
  …". Architecture §3.4, §1.8 and `gr2l_tool.md:298` all say otherwise; §0's gap
  column omits the delta. P2 closes it.
- **The weather client still reads the wall clock**: `site_now` is imported at
  `weather_client.py:23`, `_FORECAST_PAST_LIMIT_DAYS = 92` at `:32`, and
  `_choose_backend` reads `site_now()` at `:197`. No station path exists.
- **The task LLM carries no decoding pins.** `settings.litellm_extra()`
  (`settings.py:79-86`) forwards `api_base` and `api_key` only — no
  `temperature`, no `seed`. All four model roles resolve to one model
  (`.env:59-62`); no reflection model is configured, which §5's pin list
  requires to be a *second, distinct* model.
- **The semantic layer carries neither number nor alias.** Every efflux column
  in `tenants/green_roof/sensordata.py` is described as "Outflow of the Lysimeter
  (with m² collection area) … (in liter)" — the area is named without its value —
  and the file contains no alias map (`Kies`/`KD`/`Kiesdach`) at all.
- **GR2L is served locally.** `.env:56` resolves
  `WATER_ASSISTANT_GR2L_API_BASE_URL` to `http://localhost:8000/api-weinbau`, so
  every capture pass over families D, G and the model-bearing half of E and H
  needs that service running and its canary committed.
- **The catalog's arithmetic reproduces.** 32 entries over 27 families;
  `25×4 + 24×5 + 7×8 = 276`; all eight category counts; 43 abstention instances
  split 12 / 15 / 16. Nothing in §1.7 needs re-deriving except what §2.1 settles —
  which it since has: T007 returned T12 to test_seen, so the ledger is
  `25×4 + 25×5 + 7×8 = 281` and the abstention split is 12 / 15 / 16 of
  100 / 125 / 56 (`questions.md` §1.7).

### 1.3 Constraints

The production service and the CopilotKit frontend keep working throughout.
`bootstrap.py` imports `root_agent` at module scope and the chat UI depends on
the prose answer format, so every refactor below is additive at that boundary:
each factory keeps a module-level default built from itself, and the answer
contract lives in the *evaluation* instruction only
(`decisions.md § The answer contract`).

---

## 2 Decisions taken in this plan

Five questions were open when planning started. Each is settled here, and each
one's consequence is a P0 task that edits the specification rather than a
convention living only in this file.

### 2.1 The derived suite composition becomes the spec

`questions.md` §4 recorded that the swept template set yields a 15.6 % abstention
share against a 7–10 % target, and category shares up to 8.6 pp from the eight
figures named before the set settled. **`n = templates × m` stays the derivation
rule**; the derived table becomes the suite's stated composition, and §1.6's band
is rewritten to the value the five-template abstention set produces. Per-template
`m` is rejected: it makes `n` unverifiable against the product, which is the
property §1.7 exists to hold.

Two of §4's remaining items are computations, not preferences, and are scheduled
rather than decided: **T12's qualifying-event count** (the area blocker is gone,
so the count is computable and decides whether T12 returns to test_seen and every
§1.7 number moves) and the **coverage margin** audit. The **T07 phrasing** and the
**T16a/T16b cue** are decided in P0 because the pilot freezes T07's route.

### 2.2 §7's multi-objective claim is amended, not engineered

Selection runs on the aggregated scalar. Per-scorer values are logged and reach
the reflective dataset; they do not hold a Pareto front. §7's sentence claiming
otherwise is rewritten by T002, and `decisions.md § Tool errors and harness
exclusion` — which already reasons from "the optimizer consumes one float per
record" — becomes consistent with it rather than contradicted by it.

Rejected: passing `gepa_kwargs={"frontier_type": "objective"}`. It works, and it
is the only way to make the original claim true, but it rests a thesis claim on
an undocumented passthrough of an `@experimental` API whose failure mode is
quiet. The explicit `aggregation` callable stays mandatory regardless — §7's
"skipped, not scored 0" needs it (`optimize/util.py:200-214`). Recorded by T005
as `decisions.md § Candidate selection and the scorers' aggregation`.

### 2.3 The plot family's resolved range is scored as a normalized argument

`argument_checks` address tool arguments only (§6.1), and the plotting family's
scored surface includes the resolved absolute range (§3.6, T24a) — irreconcilable
while `start`/`end` admit relative forms. Settled: **relative forms stay in the
signature** (`decisions.md § Window resolution and the scenario clock` already
paid for that decision on the weather tool), and the scorer resolves the
*argument* through the same layer-1 resolver before comparing. The check stays
arguments-only, never reads the result, and a candidate that supplies "last
month" scores identically to one that supplies the dates. The case schema
carries this as a resolution flag on the check, not as a new `op`. Folded by
T005 into `decisions.md § Plotting`.

### 2.4 The wetland leaves both water-balance tools; `roof_type` stays a string

Code follows the specification here (arbitration: this is a scope property, not a
mechanism). The wetland and its aliases join `NON_MODELLABLE_ROOFS`, the mm-only
path in `gr2l.py` is deleted with its `MM_ONLY_ROOFS` set, the `wetland` preset
stays in `ROOF_PRESETS` **unchanged and unreachable** so §5's preset pin does not
move, and the root instruction stops advertising four modellable segments. The
`roof_type` argument stays a plain `str` on both tools; a test pins that, because
family I is unaskable the moment it becomes a `Literal`.

### 2.5 Two infrastructure prerequisites are scheduled, not assumed

The GR2L service is stood up and its canary committed **before** any capture pass
(P7), and the reflection model becomes a distinct configured model with its own
served id, endpoint, decoding parameters and canary **before** any search run
(P8). Neither blocks P1–P6.

---

## 3 Components

The layering is architecture §1's and is not restated. What this plan adds is
where each piece lands in the tree:

```
src/water_assistant_agent/assistant/
  context.py            ScenarioContext, connect_asof            [P1]
  cache.py              ResponseCache (request-keyed)            [P1]
  rules_constants.py    site policy + eval policy, versioned     [P3]
  irrigation.py         bucket model, ladder, decision           [P3]
  et_fao56.py           ET0, albedo 0.23                         [P3]
  tools/roofs.py        the one roof table                       [P3]
  tools/{lookup,irrigation,plot}.py                              [P3–P5]
  knowledge/{store.py,cards/*.yaml}   11 cards                   [P4]
harness/                run_case · scoring · scorers · optimize  [P6, P8]
eval/                   schema/ templates/ oracles/ cases/ cache/ pins.json
```

`harness/` is deliberately outside the service package: nothing the production
image ships imports it. The card store is the exception in the other direction —
it ships, because `lookup_reference` is a production tool.

---

## 4 Data model

Five artifacts carry structure. Each is pinned by a schema or a test, and none
duplicates another.

| Artifact | Shape | Pinned by |
|---|---|---|
| **Case** | `{"inputs": {...}, "expectations": {...}}`, architecture §6.1 verbatim | `eval/schema/case.schema.json`; the envelope is dictated by MLflow (`findings.md` § Optimizer internals), not chosen |
| **Template** | the catalog entry's machine twin: params, pools, filters, `A:`/`Traj:`/`Must-not:`/`Cards:`, tolerance | `eval/schema/template.schema.json`; per-template constants are **copied into** each case at generation, never referenced across files |
| **Roof** | canonical name, DE/EN labels, site ids, `SH_cm`, lysimeter area, per-column plausibility bounds, aliases, and **its column in each of the five tables, absent where uninstrumented** | one table in `tools/roofs.py`; `swc.ROOF_SWC_COLUMNS` and `gr2l_client.ROOF_PRESETS` are repointed at it with no value changes |
| **Card** | `id`, `title`, `provenance`, numeral-free `text:`, plus `values:` / `applies_to:` / `not_applicable:` | pydantic model in `knowledge/store.py`; `enum == card keys` test; drift test against `values_for(card_id)` |
| **Cache entry** | committed JSON keyed by the sha256 of the exact request | `cache.py`'s canonicalization, asserted by a round-trip test |

**Pins** (`eval/pins.json`) are architecture §5's list, one file, checked by
`just pins`: `water.duckdb` sha256; one sha256 over `knowledge/cards/`;
`rules_constants.py` and `roofs.py` versions; the GR2L presets, base URL and
canary; the task model's served id, endpoint, decoding parameters and canary; the
same three for the reflection model; the candidate prompt names and seed versions;
the dependency lockfile hash (adk, litellm, mlflow, gepa, pyyaml, duckdb); and the
station derivation, which the DB hash does not cover.

**Open in the data model, to close in P4:** `data_freshness` is listed as a
`rendered` card, but its values are record dates — they come from the pinned
database, not from `rules_constants.py` or `roofs.py`, which is what `rendered`
is defined against. Either it becomes `static` with the dates carried by the
station-derivation pin, or `values_for()` gains a DB-backed source. T041 decides
it; the drift test is meaningless until it does.

---

## 5 Interfaces and contracts

Constructors, stated once, because every phase below either produces or consumes
one of them:

```python
ScenarioContext(clock, db_path, weather_client_factory, http_cache)   # P1
connect_asof(db_path, clock) -> DuckDBPyConnection                    # P1
build_toolset(ctx, docstrings=None) -> list[Tool]                     # P1
build_root_agent(instruction, docstrings, tools, model) -> LlmAgent   # P1
build_text_to_sql_agent(executor, clock, description=None) -> Agent   # P1
make_query_database_tool(executor, clock) -> Callable                 # P1
run_case(case, ctx) -> CaseResult                                     # P6
predict_fn(inputs: dict) -> dict                                      # P8
```

Every one of them keeps a module-level default built from itself, so the running
service imports what it imports today.

**Tool outcomes** are architecture §3's three, uniform across all six tools:
`success`; `not_available` for a genuine scope limit with the per-tool trigger
list; `error` carrying `error_type: invalid_argument | upstream`. The taxonomy is
a `schemas.py` field plus a classification pass over every existing catch site —
`T030a` already separated the two populations by exception type, so the pass is
tagging, not analysis.

**Scorers** are four MLflow `Scorer`s over per-case metric functions shared with
the measurement path (§7's aggregates stay out of the search). `Feedback.rationale`
is the reflection signal and is treated as a deliverable, not decoration.

---

## 6 Phases and dependencies

| Phase | Delivers | Blocked by | Exit criterion |
|---|---|---|---|
| **P0** Specification reconciliation | the five §2 decisions written into the architecture and the catalog; §4's computable items computed | — | no document references a file, section or decision id that does not exist |
| **P1** Injection seam and determinism | `ScenarioContext`, as-of views, response cache, factories, SQL clock rewrite | P0 | two contexts with different `as_of` run concurrently in one process and neither sees the other's data or clock |
| **P2** Tool completeness | station source, typed abstention, bounded series, forcings, retrospective evaluation, error taxonomy, wetland scope | P1 | every §3.3/§3.4 outcome is reachable and tested; no wrapper reads a wall clock |
| **P3** Rules single source of truth | `roofs.py`, ET0, `rules_constants.py`, the bucket model, `calc_irrigation`, the decision-diff list | P1 | the port reproduces the deployed controller in its original units *before* the unit fix lands |
| **P4** Reference cards | 11 cards, the store, `lookup_reference` | P3 | a lookup case completes in replay with zero cache entries and zero live calls |
| **P5** Plotting | `plot_timeseries` over three sources, derived units and aggregation | P1, P2 | a `model`+`weather`+`measured` plot issues no live call in replay |
| **P6** Harness, scoring, pilot | `run_case`, the four metrics, the answer contract, the pilot → **FREEZE GATE** | P2–P5 | pilot runs on T01/T07/T09, three repeats, paired; after this only optimizable text changes |
| **P7** Oracles and case generation | remaining oracles, generation filters, splits, capture pass | P6 | every case replays with zero live calls; `pins.json` verifies |
| **P8** Optimizer | prompt registry, `predict_fn`, scorers, the two arms, the statistics | P7 | every registered candidate prompt is read during an evaluation pass, asserted |

**The freeze gate is the plan's one irreversible edge.** Two prerequisites must
land before it and cannot land after: the **lysimeter collection area** and the
**alias map** in the frozen sub-agent's semantic layer. Both are byte-stable text
thereafter, and both are load-bearing — the area for T12 and the L↔mm
normalization, the alias map for German paraphrase generation (§1.6's roof
vocabulary).

**Parallelism.** P3 and P4 depend on P1 but not on P2; P5 needs both. P3's R-side
deliverable is gone (`decisions.md § The irrigation calculator` retired it: the
endpoint serves consumers outside this system and no case's answer depends on
it), so P3 carries no cross-repo edge at all.

---

## 7 Testing strategy

Three populations, each with a different reason to exist.

**Unit tests, per phase, never after it.** `gr2l`, `weather` and `swc` have no
tests today (`findings.md` § Codebase seams) and are the modules P1 and P2 rewrite
most heavily, so their tests are phase deliverables: %θ↔mm round-trip per roof,
typed `not_available` per trigger, seed staleness, forcings application,
retrospective deviation arithmetic, the station derivation field by field, the
sentinel exclusion, and the UTC→Europe/Berlin day boundary against a rain event
straddling local midnight.

**Property tests, where a silent default would still produce a score.** These are
the ones `decisions.md § The construction seam` names as the validity condition —
an unbound case must fail loudly *before* a rollout runs. Two contexts in
parallel; a production-clocked context reflecting a date change without a rebuild;
the generated-SQL clock rewrite; the as-of cut identical under at least two host
`TZ` settings, which nothing in the pin set can otherwise detect
(`decisions.md § The as-of cut`).

**Drift tests, where two artifacts must agree and nothing forces them to.** The
card `values:` against `values_for(card_id)`; `enum == card keys`; no numerals in
any `text:` block; the roof table against `ROOF_SWC_COLUMNS` and `ROOF_PRESETS`;
and the golden-series test asserting the irrigation port reproduces the deployed
controller *in its original unit regime*, landing before the unit fix so every
later difference is attributable to the fix rather than to the port.

**Replay assertions** ride on top: a lookup case, an irrigation case and a
station-weather case each complete with zero cache entries and zero live calls;
a model case completes with entries and no live call.

---

## 8 Risks and open questions

- **R1 — GR2L availability gates capture.** A cache miss the service cannot fill
  fails the case by design, and the service is local (`.env:56`). Mitigation:
  T072 stands it up and commits the canary before P7's capture pass, which is one
  batch. GR2L is deterministic in `(rows, parameters)`, so the deadline is service
  availability and canary stability, not an upstream data window — the Forecast
  backend's capture deadline is retired with the backend
  (`decisions.md § Weather sources`).
- **R2 — the optimizer entry point is `@experimental`.** Candidate injection rests
  on a process-global patch of `PromptVersion.template`, and the failure is quiet:
  prompts stop being patched and the search optimizes nothing while still
  reporting scores. Mitigation: mlflow and gepa versions join the pins, and the
  "prompts were not used" assertion is a test, not a log line. The fallback —
  a hand-written `GEPAAdapter` — stays available at the cost of re-implementing
  per-iteration logging.
- **R3 — refactoring three untested modules.** P1 and P2 rewrite the wrappers
  around `gr2l`, `weather` and `swc`. Their tests are phase deliverables, not
  follow-ups.
- **R4 — frontend regression.** P1 touches modules the service imports at module
  scope. One smoke run of the chat UI closes each of P1 and P5.
- **R5 — irrigation thresholds were tuned against uncorrected dynamics.** The
  deployed trigger levels are carried verbatim as site policy, so the corrected
  model's behaviour around them need not reproduce the decisions the controller
  actually made. The decision-diff list is the disclosure; re-tuning is the site's
  call (`decisions.md § The irrigation calculator`).
- **R6 — cross-language drift.** The bucket model and its ladder exist in Python
  and in R with no shared CI. No case's answer depends on the R side, so drift is
  a defect in that deliverable rather than in the evaluation.
- **R7 — an ambiguous paraphrase survives the generation filter.** The filter
  tests *oracle* ambiguity, not linguistic ambiguity introduced at paraphrase
  time, and the candidate instruction forbids clarifying questions. Mitigation is
  disclosure, plus a spot-check of the German pool before splits are cut.
- **Q1 — `data_freshness`'s provenance** (§4 above). Blocks its drift test only.
- ~~**Q2 — must-nots grounded in candidate-owned text.**~~ Settled by T005 as an
  accepted risk under `decisions.md § Trajectory scoring and routing probes`: the
  disclosure stays in the candidate-owned docstring, because moving it would take
  self-containment out of the optimizable surface, and the failure it exposes is
  self-inflicted and shows as a trajectory-only loss on T19 and T23.
- ~~**Q3 — T15a punishes an equivalent route.**~~ Settled by T005 in the same
  entry, on the T16a/T16b ground: the phrasing cues the route, and naming both
  routes gold — T25's shape — stays available if the pilot shows candidates
  taking the weather route.
- **Q4 — family H's roof pools are notional.** §1.8 assigns H to P1, P1f and P2,
  but T24a fixes both roofs and draws only a `measured` swc series, so no case
  exercises a `model` series or the plot tool's `not_available`. Either an H
  variant is authored or the pool rows are marked notional; P7's capture surface
  differs between the two.

---

## 9 Traceability

| Architecture / catalog item | Phase |
|---|---|
| §4 ScenarioContext; `decisions.md § The construction seam` | P1 |
| §5 as-of views, leakage, `§ The as-of cut`, `§ The day boundary` | P1, P2 |
| §5 response cache; `§ The response cache` | P1, P7 |
| §2 scenario clock; `§ Window resolution and the scenario clock` | P1, P2 |
| §2 LLM pinning, step cap; `§ Model pinning` | P1, P8 |
| §2 candidate injection; `§ The optimizer entry point and the candidate surface` | P1, P8 |
| §3.1 sub-agent context binding | P1, P6 (semantic-layer prerequisites) |
| §2 answer contract; `§ The answer contract` | P6 |
| §3 error taxonomy; `§ Tool errors and harness exclusion` | P2, P6, P8 |
| §3.3 station source, typed abstention; `§ Weather sources`, `§ Typed abstention` | P2 |
| §3.4 seed rule, forcings, retrospective evaluation; `§ GR2L argument surface` | P2 |
| §1.3 bounded series; `§ Bounded series` | P2 |
| §3.4/§3.5 non-modellable roofs (§2.4 above) | P2, P3 |
| §3.5 `calc_irrigation`; `§ The irrigation calculator` | P3 |
| §3.2 `lookup_reference`; `§ Retrieval` | P4 |
| §3.6 `plot_timeseries`; `§ Plotting` | P5 |
| §6 harness, §7 scoring; `§ Trajectory scoring and routing probes` | P6 |
| §6.1 case envelope | P0 (schema), P7 (emission) |
| catalog §1.6/§1.7 filters, splits, sizing; `§ Case time`, `§ Splits, sizing and the holdout` | P0, P7 |
| §7 reporting, replication; `§ Replication and the LLM cache` | P8 |
| §8 scope limits | P0 (recorded), P3 (decision-diff evidence) |

---

## 10 Generated artifacts

- **This plan** and [`tasks.md`](./tasks.md).
- **To create:** `assistant/{context,cache,rules_constants,irrigation,et_fao56}.py`;
  `assistant/tools/{roofs,lookup,irrigation,plot}.py`;
  `assistant/knowledge/{store.py,cards/*.yaml}`;
  `harness/{run_case,scoring,scorers,optimize}.py`;
  `eval/{schema,templates,oracles,cases,cache}/` and `eval/pins.json`.
- **To edit:** `tools/{gr2l,weather,warehouse,swc,weather_client,gr2l_client,schemas}.py`;
  `agents/root_agent/agent.py`; `agents/text_to_sql/{agent,dry_run,executor}.py` and
  their callers; `prompts/temporal.py`; `settings.py`;
  `tenants/green_roof/sensordata.py`; `agent_architecture.md`; `questions.md`;
  `decisions.md`; `findings.md`; all three tool specs;
  `web/` (plot rendering) and `web/package.json`.
- **To add to `[project] dependencies`:** `pyyaml` — the card store ships with the
  production service. No plotting library is added at any point; the renderer is
  the frontend's.
