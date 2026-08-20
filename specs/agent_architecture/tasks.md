# Tasks — green-roof testbed for prompt optimization

Plan: [`plan.md`](./plan.md) · Architecture: [`agent_architecture.md`](./agent_architecture.md) ·
Catalog: [`questions.md`](./questions.md) · Decisions: [`decisions.md`](./decisions.md) ·
Measurements: [`findings.md`](./findings.md)

**Conventions.** `[P]` marks a task parallelizable with the other `[P]` tasks in
its phase — no shared file, no ordering edge. `→ T0nn` is a hard dependency. Each
task names its target artifact. Three-digit `T0nn` is a task here; two-digit
`Tnn` (`T01`, `T16a`, `T27`) is always a catalog template.

**There are no user stories.** The deliverable is a frozen evaluation testbed,
not a user-facing feature, so tasks map to architecture sections and catalog
families instead; the mapping is `plan.md` §9. Tests are included where the
architecture or `decisions.md` names them as a validity condition, and nowhere
else.

---

## Phase 0 — Specification reconciliation (blocks everything)

Plan §2's five decisions become document edits here. Nothing in P1 may start
against a document that still contradicts itself.

- [x] T001 [P] Amend `agent_architecture.md` §7: selection runs on the aggregated
  scalar, per-scorer values reach logging and the reflective dataset only. Delete
  the sentence claiming a Pareto front over the four metrics. Correct §6's sketch
  to name a real aggregation callable rather than `weighted_mean`, and state that
  an explicit `aggregation` stays mandatory for the skip semantics
  (`mlflow/genai/optimize/util.py:200-214`). (plan §2.2)
  Done. One correction to the plan's own premise, carried into `plan.md` §1.2 and
  into T004's `findings.md` entry: MLflow ships **no** aggregation callable —
  `weighted_objective` is an example inside `optimize_prompts`' docstring
  (`optimize.py:172-182`), not an export — so §6's sketch names this repo's
  `aggregate_scores`, and omitting the argument would silently mean the *mean of
  the numeric scorer values*, with a non-numeric value raising.
- [x] T002 [P] Amend `agent_architecture.md` §0: add the wetland delta to the GR2L
  row's gap column — `NON_MODELLABLE_ROOFS` covers gravel only, `ROOF_PRESETS`
  still carries a reachable `wetland` entry, `gr2l.py` still routes it through
  `MM_ONLY_ROOFS`, and `ROOT_INSTRUCTION` still advertises four modellable
  segments. Add to §3.4 that `roof_type` is a plain `str` at the tool boundary and
  must not become a `Literal`, since family I is unaskable if it does. (plan §2.4)
  All four deltas confirmed in the tree: `gr2l_client.py:39-44` (gravel aliases
  only), `:63` (`wetland` preset), `swc.py:51` + `gr2l.py:52,78` (the mm-only
  route), `agents/root_agent/agent.py:36` ("four roof segments"). §3.4 now states
  the `str` rule as the deliberate opposite of §3.2's `topic` enum.
- [x] T003 [P] Repair stale references in `agent_architecture.md`: the header, §0
  and §9 point at a `plan.md` that was deleted and at a "plan.md, Open bug
  records" section that never existed — repoint the radiation-offset citation to
  `findings.md` and `decisions.md § The radiation timestamp offset`, and add
  `schema/` and `pins.json` to §9's `eval/` listing. (plan §1.2)
  The header's and §9's `plan.md` links resolve again — `plan.md` was rewritten,
  not left deleted — so only §0's "Open bug records" citation was stale; it now
  names `findings.md` plus the two `decisions.md` entries (the radiation offset
  and the as-of cut, the row's two claims). §9's `eval/` listing gains `schema/`
  and `pins.json`. Swept the file for further dangling ids: no `§9`–`§10`
  cross-reference and no `D1`–`D31` decision id survives anywhere, and every
  `§1.7`/`§1.8` reference is the catalog's, not this file's. Left as-is: §9 lists
  no top-level `harness/`, which is `plan.md` §3 and §10's listing.
- [ ] T004 [P] Record the plan's verified facts in `findings.md`, dated
  2026-08-19, each with how it was verified: GEPA's `frontier_type` default and
  MLflow's non-setting of it; MLflow's `weighted_objective` default and
  `create_metric_from_scorers`' return shape; `roof_type: str` at the tool
  boundary; the wetland's live modellability in code; `weather_client`'s wall-clock
  reads; `litellm_extra()` forwarding no decoding parameters; the semantic layer
  carrying neither the collection-area value nor an alias map; the GR2L base URL
  resolving to a local service.
- [ ] T005 [P] Add to `decisions.md`: an entry for the GEPA selection decision
  (rejected — `gepa_kwargs={"frontier_type": …}`, with the reason), fold plan
  §2.3's plot-resolution decision into `§ Plotting`, and record plan §8's Q2 and
  Q3 as accepted risks under `§ Trajectory scoring and routing probes`.
- [x] T006 Rewrite `questions.md` §1.6 and §1.7 so the derived table *is* the
  stated composition: the abstention band becomes the value the five-template set
  produces, the eight category shares become the derived ones, and `n = templates
  × m` is restated as the rule the numbers are verified against. Close the two
  matching items in §4. (plan §2.1) → T007
  Re-derived on T007's ledger: `25×4 + 25×5 + 7×8 = 281` (100 / 125 / 56), the
  eight shares at 19.2 / 9.3 / 15.7 / 12.8 / 18.9 / 6.0 / 11.7 / 6.4 %, abstention
  43/281 = 15.3 % (12.0 / 12.0 / 28.6 per split). §4's abstention and
  category-share items removed; the retired 7–10 % band and the eight old target
  shares are recorded as retired rather than silently dropped. Two consequences
  outside §1.6/§1.7 were carried so the set stays consistent: §2's legend loses the
  `train only` split value, and `plan.md` §1.2's arithmetic bullet is updated from
  276 to 281.
- [x] T007 Compute T12's qualifying rain-event count over the pinned record and
  P1f's four roofs (`scripts/`, committed with its output). If the count supports
  disjoint train and test_seen event sets, return T12 to test_seen and re-derive
  every §1.7 number; if not, record the count as the standing justification for
  train-only. Closes §4's T12 item either way.
  **Count: 11 qualifying events, 41 (event, roof) pairs** —
  `scripts/count_t12_rain_events.py`, output committed at `t12_rain_events.md`,
  measurement recorded in `findings.md`. Qualification is §1.6's own filters and
  nothing else: a maximal run of Europe/Berlin days at ≥ 0.2 mm station rain plus
  one drainage day, ≥ 10 mm deep, `outflow` coverage ≥ 95 % with no gap over 24 h,
  and retention inside [0, 1] per roof. 57 events in the band, 15 at depth, 4 lost
  to the lysimeter outages, 3 further pairs lost where outflow exceeds the gauge's
  rain on frozen days. Eleven covers train's 4 plus test_seen's 5 disjointly, so
  **T12 returns to test_seen**; §1.7's re-derivation is T006's. §4's T12 item
  removed.
- [ ] T008 [P] Audit the distractor coverage margin: assert every registered tool
  holds at least one must-not slot **inside train**, name the four columns resting
  on a single template, and record the result in `questions.md` §3 as either an
  accepted margin or a second authored slot per tool. This is a validity condition,
  not a nicety (`decisions.md § Trajectory scoring and routing probes`).
- [ ] T009 Settle T07's phrasing and the T16a/T16b cue in `questions.md` §2, and
  close both §4 items. T07's wording must stop cueing the docs route against a
  `calc_irrigation` gold set; T16a and T16b must be distinguishable by phrasing
  alone, since their must-nots are symmetric. The pilot freezes T07's route, so
  this cannot wait for P6.
- [ ] T010 `eval/schema/case.schema.json` + `eval/schema/template.schema.json`,
  plus the documented projection from template YAML to case JSON. Envelope is
  architecture §6.1 verbatim. Validators: every tool name is a registered name;
  `answer_metric: "skipped"` implies `answer: null`; non-empty `gold_cards` implies
  `lookup_reference` in `expected_tool_calls`. `argument_checks` carry the
  resolution flag plan §2.3 settles, so a relative window argument is normalized
  through the layer-1 resolver before comparison and the check never reads a tool
  result. **First writer of ground truth — nothing may emit a case before this
  lands.** → T006, T009
- [ ] T011 Standing task, closed at the freeze gate: keep `gr2l_tool.md`,
  `weather_tool.md` and `irrigation_tool.md` in step with P2 and P3 as behaviour
  lands — the seed rule, typed outcomes, window resolution, the series cap, the
  station derivation, the wetland's scope change. These specs are accurate today
  and must drift neither ahead of nor behind the code.

**Exit:** no document references a file, section or decision id that does not
exist, and `questions.md` §4 holds only items whose resolution is scheduled.

---

## Phase 1 — Injection seam and determinism

The blocking phase: no case is reproducible until this lands.

- [ ] T020 `assistant/context.py`: `ScenarioContext(clock, db_path,
  weather_client_factory, http_cache)` with `as_of` a property evaluating the
  clock per read, plus `connect_asof(db_path, clock)` building the in-memory
  DuckDB, attaching the pinned file read-only as `src`, and creating one bounded
  view per table over `outflow`, `radiation`, `swc`, `tsoil`, `wetter`. `ctx.db`
  is a `DuckDbQueryExecutor` over a **view-recreating factory**, never a bare
  connection, and reconnects when `clock().date()` moves. `as_of` is converted to
  UTC inside the seam, never by the caller (`decisions.md § The as-of cut`).
- [ ] T021 `assistant/cache.py`: `ResponseCache` keyed by the sha256 of the
  canonical request — URL plus sorted query parameters for Open-Meteo, `data[]`
  plus parameters for GR2L — as committed JSON under `eval/cache/`. A miss is
  filled live and recorded, **gated on the service canary matching**; a diverging
  canary and an unfillable miss are both hard failures carrying the unmatched
  request and a diff against the nearest captured request
  (`decisions.md § The response cache`). → T020
- [ ] T022 `WeatherClient` protocol plus the cached Archive implementation
  wrapping `fetch_daily_weather`; move the module-level `httpx.AsyncClient`
  singleton behind it. The composite that adds the station half is T043. → T021
- [ ] T023 Route `run_gr2l` through the same cache and add the canary
  request/response hash as the service-version proxy. → T021
- [ ] T024 [P] `tools/swc.py`: take the executor from `ctx` instead of building a
  connection from global settings; the module stays pure and ADK-free. → T020
- [ ] T025 [P] `tools/warehouse.py`: `make_query_database_tool(executor, clock)`
  returning a closure that preserves the exact name, signature and docstring of
  `query_database_tool` — the frozen instruction names the tool and ADK derives
  the declaration from the function. Keep the module-level tool as the production
  default bound to the settings executor and `site_now`. → T020
- [ ] T026 [P] `DuckDbExplainValidator(executor)`: take the executor as a
  constructor argument instead of importing the warehouse singleton
  (`dry_run.py:13`), so the sub-agent has exactly one DB seam. → T020
- [ ] T027 `build_text_to_sql_agent(executor, clock, description=None)`: rebuild
  the pipeline with a context-bound validator and querier and a fresh `Agent` with
  **byte-identical** frozen text; the module singleton becomes the production
  default built from it. → T025, T026
- [ ] T028 Extend the querier's sqlglot pass — today it parses only for the
  read-only guard and executes the original string, so it must now re-emit what it
  validated — to rewrite `CURRENT_DATE` and `now()`-family nodes to the literal
  `as_of`. Rewrite, never reject: the sub-agent that emits them is dateless by
  design. → T025
- [ ] T029 `make_*` tool factories closing over `ctx` for the built tools, plus
  `build_toolset(ctx, docstrings=None)` applying docstrings to the produced
  callables so they are candidate-addressable. The text2SQL entry is built per
  context, never the import-time singleton, with `description` threaded from the
  candidate docstrings. → T022, T024, T027
- [ ] T030 `build_root_agent(instruction, docstrings, tools, model)` binding the
  per-invocation instruction provider as a closure over `ctx.clock`; the
  module-level `root_agent` becomes the production default built from it, so
  `bootstrap.py` and the frontend are untouched. → T029
- [ ] T031 Scenario clock end to end: `current_datetime_block` gains a required
  `now` parameter; `fetch_daily_weather` and its backend selection gain a
  **required** date argument with no wall-clock default; the `site_now` import
  leaves `weather_client`; `site.py`'s module docstring stops claiming the weather
  client takes its clock from there. → T020
- [ ] T032 [P] Set the ~6-step tool cap on the root agent. → T030
- [ ] T033 Pin the task LLM: `temperature=0` and the decoding seed through
  `litellm_extra()`, the served model id and endpoint recorded per run, and the
  response cache switchable — on inside the search, off on the measurement path
  (`decisions.md § Replication and the LLM cache`). → T021
- [ ] T034 `eval/pins.json` and a `just pins` check covering plan §4's pin list,
  with the card-store and reflection-model entries stubbed until P4 and P8 fill
  them. → T033
- [ ] T035 Tests: `connect_asof` bounds every one of the five tables; the as-of cut
  is identical under at least two host `TZ` settings (nothing else in the pin set
  can detect a violation); cache round-trip, canary divergence and unfillable
  miss; two contexts with different `as_of` running the same query concurrently —
  through the sub-agent path as well — each seeing its own bound; a
  production-clocked context reflecting a date change on the next query without a
  rebuild; the T028 rewrite pinned. → T020, T021, T027, T028
- [ ] T036 Smoke-run the chat UI against the refactored service and confirm the
  prose answer path is unchanged. → T030

**Exit:** two `ScenarioContext`s with different `as_of` run concurrently in one
process and neither sees the other's data or clock.

---

## Phase 2 — Tool completeness (weather + GR2L)

- [ ] T040 Resolve `past_days` / `forecast_days` to absolute dates against
  `ctx.as_of` in both wrappers before any client call. Malformed windows — end
  before start, negative counts, unparseable dates — are `error` /
  `invalid_argument`; nothing else in window validity is an error. → T031
- [ ] T041 Typed `not_available` on `get_weather_forecast_tool` for the **single**
  scope limit: a well-formed window whose end lies more than 16 days past
  `ctx.as_of`. No back-window cap, no coverage class, no cutoff class — the old
  three-class split is deleted, not narrowed (`decisions.md § Window resolution and
  the scenario clock`). Enforced against `ctx.as_of` and asserted by a harness
  test. → T040
- [ ] T042 `StationWeatherSource` in the pure layer: derive `DailyWeatherRow`s
  from `wetter` per `weather_tool.md` § Station source — per-field aggregation, the
  `tn` estimator, the `−7999` sentinel filter, UTC→Europe/Berlin **before**
  grouping, and complete days only. Served uncorrected: no calibration, no gap
  filling, no per-day fallback. → T020
- [ ] T043 Composite `WeatherClient` at layer 2, constructed with the case's
  as-of executor: the station serves when the record covers the **whole** window,
  tested through the as-of view; everything else, including every window reaching
  past `as_of`, falls to Archive whole. Every window has exactly one provenance;
  the response echoes the source. → T042, T022
- [ ] T044 Station windows bypass the response cache entirely — a pure function of
  the pinned DB, so record, replay and off are all no-ops there and a miss must not
  raise. The record's first and last complete day join `eval/pins.json`, read from
  the DB rather than hardcoded. → T021, T043, T034
- [ ] T045 [P] Delete the Forecast backend and `_FORECAST_PAST_LIMIT_DAYS`: with
  two sources chosen from the window, the third backend and its wall-clock cutoff
  have no caller. → T043
- [ ] T046 [P] Remove `WeatherResult.elevation` from `schemas.py` — the client
  cannot know the surveyed height. The weather wrapper composes the site's own
  `latitude` / `longitude` / `elevation` into its agent-facing payload; consumers
  needing `hoehe_nn` take it from `site.py` explicitly.
- [ ] T047 Seed rule `seed_at = min(window_start, as_of)` in
  `swc.latest_measured_swc`, with staleness flagging beyond 7 days and the
  never-substitute-a-default rule (`not_available`, never a generic value). Every
  seeded component uses this one rule. → T024
- [ ] T048 `forcings={"precip": {"2026-07-22": 50.0}}` applied to the fetched rows
  before the GR2L request: sparse, keyed by the row's own field names, validated
  against the window, echoed in the response for argument checking. → T040
- [ ] T049 `evaluate_against_measured=True`: join the predicted `swc_pct` series to
  the `swc` as-of view and return mean and max |predicted − measured| in %θ over
  the overlap, plus the overlap window. → T024, T047
- [ ] T050 Bounded series in **both** wrappers: cap the daily series at 31 days,
  beyond which return the summary plus weekly aggregates and set a truncation flag.
  An absolute Archive window is otherwise unbounded. → T040
- [ ] T051 Wetland out of scope (plan §2.4): add the wetland and its aliases to
  `NON_MODELLABLE_ROOFS`, delete `MM_ONLY_ROOFS` and the mm-only branch, leave the
  `wetland` preset in `ROOF_PRESETS` unchanged and unreachable so the preset pin
  does not move, and normalize `roof_type` **once at entry** instead of at one call
  site out of five. A test pins that `roof_type` is not a `Literal`.
- [ ] T052 `ErrorResult.error_type: "invalid_argument" | "upstream"` in
  `schemas.py`, plus a classification pass over every catch site: pre-I/O
  validation and malformed windows are `invalid_argument`; fetch, seed, GR2L,
  configuration and the wrapper catch-alls are `upstream`. The two populations are
  already separated by exception type, so this is tagging, not analysis.
- [ ] T053 [P] Update the production `ROOT_INSTRUCTION`: three modellable
  segments, not four; the wetland joins the gravel roof as measured-only. → T051
- [ ] T054 Tests for `gr2l`, `weather` and `swc`: %θ↔mm round-trip per roof;
  gravel and wetland → `not_available`; no trustworthy seed → `not_available`;
  stale-seed flag; seed-day retention flag; forcings application and echo;
  `evaluate_against_measured` arithmetic; beyond-horizon → `not_available`; each
  error site's `error_type`. → T041, T047, T048, T049, T051, T052
- [ ] T055 Tests for the station path: per-field derivation against hand-computed
  values; a rain event straddling local midnight landing in the right Berlin day;
  sentinel exclusion from the wind mean; incomplete-day exclusion; routing at both
  record edges; partial coverage falling to Archive whole; a future window never
  resolving to the station; `source == "station"` echoed end to end through GR2L;
  and a retrospective station case completing in replay with **zero** cache entries
  and zero live calls. → T042, T043, T044

**Exit:** every §3.3 and §3.4 outcome is reachable and tested, and no wrapper
reads a wall clock.

---

## Phase 3 — Rules, single source of truth

- [ ] T060 `assistant/tools/roofs.py`: one table carrying each roof's canonical
  name, DE/EN labels, site ids, substrate height, lysimeter area, per-column
  plausibility bounds, alias set, and **its column in each of the five tables,
  absent where the roof is not instrumented**. Repoint `swc.ROOF_SWC_COLUMNS` and
  `gr2l_client.ROOF_PRESETS` at it with no value changes, so the pins are
  untouched. The catalog's sampling pools are read off this table. → T051
- [ ] T061 [P] `assistant/et_fao56.py`: FAO-56 Penman-Monteith ET0 at albedo 0.23,
  a verbatim port of the R implementation, with the fixed-pressure simplification
  carried over deliberately and documented as a scope limit so the two languages
  agree.
- [ ] T062 `assistant/rules_constants.py`: per-roof wilting / dry / capacity /
  residual authored in the site's own units and converted **once** through
  `swc.theta_pct_to_mm`; hour-based horizons; the heat threshold; the outflow
  epsilon; both dose fields. Two constants have no deployed source and are flagged
  in-module as **eval policy** — the heatwave duration rule T08 counts against, and
  T12's retention target. One module, versioned, duplicated nowhere. → T060
- [ ] T063 `assistant/irrigation.py`: `simulate_store` / `summarize` /
  `irrigation_decision`, pure, no LLM. Preserves the deployed controller's
  semantics exactly — one store, the stress coefficient evaluated on the previous
  step, ET applied before the cap, seed-day initialization only, and the two
  window conventions — returning **bool plus reason code plus the fixed dose
  constant**, never a computed volume. → T062, T061
- [ ] T064 **Faithfulness before correctness**: a golden-series test asserting the
  port reproduces the deployed controller element for element *in its original
  unit regime*, landing **before** the unit fix, so every later difference is
  attributable to the fix rather than to the port. → T063
- [ ] T065 Carry the balance into millimetres, rescaling each roof's response to
  rain by `100/SH_mm`. The deployed trigger levels are carried verbatim as site
  policy and are **not** re-derived (`decisions.md § No fitted correction between
  the instrument and the oracle`). → T064
- [ ] T066 `calc_irrigation` ADK tool and factory: self-contained by default with
  its own seed via `swc` and forcing via `ctx.weather`; supplying soil moisture,
  max temperature and forecast rain makes the call pure — no DB, no weather, no
  simulation. Gravel, wetland and seedless windows → `not_available`; three
  outcomes with `error_type`. → T063, T029, T043
- [ ] T067 Decision-diff harness: replay a historical window through **both** unit
  regimes with the same Python ET0, so unit handling is the only variable, and emit
  a markdown table of every date and roof where the irrigate decision flips, with
  the driving feature values. This is the evidence the site re-tunes against, and
  the bound on what the testbed's irrigation answers say about the deployed
  system. → T065
- [ ] T068 `values_for(card_id)`: project `rules_constants.py` and `roofs.py` into
  each `provenance: rendered` card's `values:` / `applies_to:` / `not_applicable:`
  blocks, plus the drift test asserting the committed card equals it and a
  `just cards-check` that prints the correct block on failure. A test, not a
  writer — hand prose and generated values share one file. → T062, T060
- [ ] T069 [P] Derive the `roof_reference_ranges` values (normal / low / high per
  roof segment) from the `swc` record under the same drift discipline. → T068
- [ ] T070 Tests: the ladder's reason codes in priority order; the stated-value
  path issuing no I/O at all; `not_available` for both non-modellable roofs; unit
  conversion round-trips; and an irrigation case completing in replay with zero
  cache entries and zero live calls. → T066

**Exit:** the port reproduces the deployed controller before the unit fix, and
the diff list exists.

---

## Phase 4 — Reference cards

- [ ] T080 Author `assistant/knowledge/cards/*.yaml` — **11 cards**, one file each,
  matching the §3.2 enum exactly: `irrigation_rule`, `irrigation_threshold`,
  `substrate_hydraulics`, `irrigation_dose`, `heatwave_definition`,
  `retention_target`, `roof_reference_ranges`, `data_freshness` (rendered);
  `roof_directory`, `sensor_reference`, `et0_method` (static). Hand-authored
  `text:` carrying **no numerals**; no card named after a single constant.
  `irrigation_rule` and `irrigation_threshold` must together answer T16a without
  the calculator, and `irrigation_threshold.not_applicable` must state the
  wetland's exclusion or T17b is silently answerable. → T068, T069
- [ ] T081 Decide `data_freshness`'s provenance (plan §8 Q1): either `static` with
  the record dates carried by the station-derivation pin, or `rendered` with
  `values_for()` gaining a pinned-DB source. Its drift test is meaningless until
  this is settled. → T080
- [ ] T082 `assistant/knowledge/store.py`: pydantic `Card` model, loader over the
  packaged YAML, `enum == card keys` test, the T068 drift test wired in, and the
  card-store sha256 into `eval/pins.json`. Pure, ADK-free, no I/O beyond the
  packaged files. Add `pyyaml` to `[project] dependencies`. → T080, T034
- [ ] T083 `lookup_reference` ADK tool and factory: `topic: Literal[…]` in the
  **signature** so the vocabulary survives any docstring rewrite, `roof` filtering
  but never suppressing `not_applicable:`, the card returned **whole**, no
  roof-scoped `not_available`, and an unknown topic returning `invalid_argument`
  echoing the valid list. → T082, T029, T052
- [ ] T084 Tests: card round-trip and schema validation; `enum == card keys`;
  every rendered card's `values:` equal to `values_for(card_id)`; no numerals in
  any `text:`; the wetland exclusion surviving `roof="wetland"` on
  `irrigation_threshold` (T17b's mechanism); unknown topic naming the valid
  topics; and a lookup case completing in replay with zero cache entries and zero
  live calls. → T083

---

## Phase 5 — Plotting

- [ ] T090 `tools/plot.py`: `plot_timeseries(series, start, end, kind)` with no
  `agg` argument, each `SeriesSpec` naming a **source** and a variable, never data.
  `measured` runs a fixed parameterized query against the as-of view over the five
  tables through a **closed vocabulary** of columns and aggregations; derived
  quantities exist there as flags, and area-normalized outflow is not one of them —
  the vocabulary relabels the unit rather than scaling the values. → T020, T029
- [ ] T091 `weather` and `model` series resolve through `ctx.weather` and
  `run_gr2l` — the same clients, cache, window resolution and seed rule the
  standalone tools use. A `model` series accepts `initial_soil_moisture_pct`,
  `albedo` and `forcings` and echoes them for argument checking. No new DuckDB
  connection, no new HTTP client. → T022, T023, T040, T047, T048
- [ ] T092 Echo the **resolved spec** in full: source and variable per series, the
  resolved absolute range, the derived aggregation and resolution, unit and axis
  per series, gap and truncation flags, and any modelling arguments. → T091
- [ ] T093 Return **no series to the model** — spec, summary statistics and
  `artifact_ref` only. → T092
- [ ] T094 Force daily resolution on mixed plots: any plot combining a `model` or
  `weather` series with a `measured` one aggregates the measured series to
  Europe/Berlin calendar days with that variable's derived operator, and reports
  the resolution in the spec. → T090
- [ ] T095 Derive unit, axis assignment and aggregation operator in code from the
  variable — fluxes sum, states average — never model-chosen. → T092
- [ ] T096 Typed outcomes: a `model` series for the gravel roof or the wetland →
  `not_available`; a `measured` series for either stays valid. → T051
- [ ] T097 Headless handoff: nothing renders server-side and no plotting library
  enters the Python dependency set. The tool stashes the series under a
  session-state key and the wrapper merges it into the tool-result event, while the
  model-visible result stays spec, statistics and `artifact_ref`. Evaluation never
  reads the state key. → T090
- [ ] T098 [P] Frontend render for `plot_timeseries` mirroring the existing
  tool-result component, reading the wrapper-enriched payload, plus a charting
  dependency in `web/package.json`. Unscored side effect; keep it out of the
  evaluation path. → T097
- [ ] T099 Tests: source resolution per kind; closed-vocabulary rejection of an
  unknown table or column; mixed-plot daily aggregation; unit and axis derivation;
  gravel and wetland `not_available`; the resolved-spec shape pinned; and **no live
  call in replay** for a `model` + `weather` plot. → T091, T094, T096

---

## Phase 6 — Harness, scoring, pilot → FREEZE GATE

- [ ] T100 `harness/run_case.py`: case → `ScenarioContext` → `build_toolset` →
  `build_root_agent` → runner → parse the answer contract → structured result with
  trajectory and diagnostics. Scans the event log for root-level tool results
  carrying `error_type: "upstream"` — plus, keyed on tool name, any
  `text_to_sql_agent` result parsing as a dict with an error status, since the
  sub-agent's payloads carry no `error_type` — and marks the case `harness_error`.
  `invalid_argument` errors leave the case scored; the sub-agent's inner fixer-loop
  errors are not scanned. **`predict_fn` calls this same function**, or the search
  and the measurement run diverge on the one path that must be identical. → T030,
  T052
- [ ] T101 Answer-contract parsing plus the **evaluation-only** candidate
  instruction carrying it, including the explicit no-clarification clause; the
  production instruction is untouched. A final message parsing to neither status is
  recorded as a `parse_failure` diagnostic, never as a wrong answer. → T100
- [ ] T102 `harness/scoring.py`: per-case metric functions plus an aggregation
  layer. Answer (exact or tolerance, after unit normalization, **skipped with
  coverage reported** where the contract answer is `null`); trajectory (**binary**:
  gold ⊆ called ∧ no listed must-not called ∧ argument checks pass, no partial
  credit, no extra-call penalty); card recall (`|gold ∩ retrieved| / |gold|` over
  the union of every lookup call, **skipped with coverage** where `gold_cards` is
  empty); abstention accuracy **and** false-abstention rate, never blended.
  `harness_error` cases excluded from every aggregate and counted per arm, broken
  down by source. Diagnostics: fixer iterations, steps, tokens, latency, mean extra
  calls per arm, `parse_failure`. → T010
- [ ] T103 Oracles for the three pilot templates only: T01 (SQL sum), T07
  (importing `irrigation_decision` — local, no HTTP) and T09 (model chain,
  importing `run_gr2l`). → T063, T023, T009
- [ ] T104 Harness assertions: retrospective comparison windows end at or before
  `as_of`; no live call in replay; the roof pool respected per family; every
  template's period parameter intersected with its own `as_of`. → T100
- [ ] T105 **Before the gate**: state the lysimeter collection area's *value*
  (1 m²) in the frozen sub-agent's semantic layer, which today names the area
  without it. Unblocks T12 and every L↔mm answer. The text is byte-stable after the
  freeze. → T027
- [ ] T106 **Before the gate**: add the alias map to the semantic layer — DE/EN
  roof aliases bridging `Kies` / `KD` / `Kiesdach` / `QGravel` to the gravel roof
  and the equivalents for the other four — together with the `radiation` hour
  offset and the day boundary. Blocks German paraphrase generation. → T027
- [ ] T107 **Pilot run** on T01 / T07 / T09 with the handwritten instruction, three
  repeats, paired — then **freeze the testbed**. Everything after this point may
  change only the optimizable text. → T100, T101, T102, T103, T105, T106

---

## Phase 7 — Oracles and case generation

- [ ] T110 Remaining oracles per family, sharing the tool chain's own code so
  oracle and tool cannot diverge. Each writes into the case's `expectations` and
  stamps `expectations.pins` with the surface the answer was computed against — DB
  sha256, GR2L canary, station-derivation version, resolved weather source. → T010
- [ ] T111 Template instantiation with the §1.6 generation filters, evaluated
  **through the as-of view** and anchored at `seed_at` for seed-bearing families:
  coverage, per-column plausibility, and the frozenness run test applied to state
  columns only and never to the gravel roof's. Balance by rejection sampling to
  ~50/50 within each split on every bool template — T04 is the binding case and
  needs roughly 6:1 oversampling of wet days. Abstention share per the band T006
  settles. → T060, T006
- [ ] T112 EN and DE paraphrase generation, 50/50 within each split, style pools
  disjoint between train and test, colloquial German included, roofs named in the
  agent's vocabulary or in alias-covered natural language and **never** by raw
  column name. Spot-check the German pool for referential ambiguity before splits
  are cut. → T106
- [ ] T113 Splits per §1.7 with the generation constraints enforced *in
  generation*: per-param disjointness between train and test_seen on every sampled
  parameter; `as_of` as a striped partition, never a cut point; roof deliberately
  shared; language balanced and reported as a stratum. → T111, T007
- [ ] T114 Emit `eval/cases/{train,test_seen,test_unseen}.json` as pretty-printed
  arrays through T010's schema — deterministic key order, sorted by `case_id`,
  `indent=2`, trailing newline, generated and never hand-edited. Loadable directly
  as MLflow `train_data` by both the search and the measurement run. → T113, T110
- [ ] T115 Stand up the GR2L service, record its served build, and commit the
  canary request/response hash to `eval/pins.json`. Capture cannot start without
  it, and a diverging canary is a hard failure by design. → T034
- [ ] T116 Capture pass in record mode over every case, then commit `eval/cache/`;
  re-run in replay and assert zero live calls. Family H is part of the capture
  surface wherever a plot fetches weather or GR2L itself. → T114, T115

---

## Phase 8 — Optimizer

- [ ] T120 Candidate surface in the MLflow prompt registry: the root instruction
  and **one prompt per optimizable tool docstring**, including the sub-agent's
  outward `description` — never one concatenated blob. Prompt names and seed
  versions join `eval/pins.json`. **Assert that every registered candidate prompt
  was read during an evaluation pass**: a component never read is silently frozen
  while appearing optimizable, and the framework's warning is the only signal.
  → T030, T034
- [ ] T121 `predict_fn(inputs) -> dict`: build the `ScenarioContext` from
  `inputs["as_of"]`, read candidate text back through the registry, build the
  toolset and agent, and delegate the rollout to T100's `run_case`. Per record, no
  ambient state. Test: two records with different `as_of` evaluated concurrently
  *through `predict_fn`* under the real thread pool, each seeing its own bound.
  → T100, T120
- [ ] T122 Scorers as MLflow `Scorer`s wrapping T102's per-case functions, one per
  metric, each returning a `Feedback` whose **`rationale` is the reflection
  signal** — trajectory diff against gold, cards fetched versus cards wanted, SQL
  errors, abstention outcome. An explicit `aggregation` callable is **mandatory**
  and implements "skipped, not 0" for the answer metric on null answers and for
  card recall on empty `gold_cards` — one mechanism, two users. → T102, T010
- [ ] T123 `harness_error` at search time: pre-filter `train_data` to fully
  captured cases; a residual failure scores 0 as a **declared** residual with the
  per-arm count published beside newly recorded cache entries. Diverging failure
  counts between arms mean the run is repeated; diverging record counts are
  reported, not repaired. → T122, T116
- [ ] T124 Configure the reflection model as a **second, distinct** model, and pin
  its served id, endpoint, decoding parameters and canary — without which a run is
  unrepeatable even with the task model fixed. → T034
- [ ] T125 Register the handwritten baseline candidate (instruction plus
  docstrings) as prompt versions: it is simultaneously the reference arm and the
  search's seed candidate, so the two cannot drift apart. → T120
- [ ] T126 `harness/optimize.py` wiring `mlflow.genai.optimize_prompts` with
  `GepaPromptOptimizer`. **No adapter is written.** Selection runs on the
  aggregated scalar per plan §2.2; the mlflow and gepa versions join the pins,
  since candidate injection rests on an internal patch whose failure is quiet.
  → T121, T122, T123, T124, T125
- [ ] T127 Run ledger into MLflow: candidate id, case id, served model id, every
  pin, trajectory and cost per rollout, on top of what `optimize_prompts` already
  logs. → T034
- [ ] T128 Statistics on the **measurement** path, never on the optimizer's
  internal scores: three repeats per condition at one pinned seed with the LLM
  cache off, paired on identical cases; a paired bootstrap resampling
  `template_id` rather than `case_id`; the train number reported as a selection
  score; the two generalization gaps reported separately; test_unseen's trajectory
  as a per-template win/loss table, never an accuracy with an interval. → T102,
  T126
- [ ] T129 Pre-register the budget and hyperparameters before any test run, record
  that all method debugging happened on train, and report every arm on test —
  never "best of". → T128

---

## Final

- [ ] T130 Run the retrospective skill to review all implemented changes for code
  quality and architectural decisions
  (`specs/agent_architecture/retrospective.md`). → T129

---

## Summary

**96 tasks** across nine phases, 16 of them parallelizable.

| Phase | Tasks | Parallelizable | Gates |
|---|---|---|---|
| P0 specification reconciliation | 11 | 6 | T010 blocks every ground-truth writer |
| P1 injection seam | 17 | 4 | blocks P2–P8 entirely |
| P2 tool completeness | 16 | 3 | blocks P5, P7 |
| P3 rules | 11 | 2 | blocks P4 |
| P4 cards | 5 | — | — |
| P5 plotting | 10 | 1 | — |
| P6 harness and pilot | 8 | — | **T107 freezes the testbed** |
| P7 oracles and generation | 7 | — | T115 gates capture |
| P8 optimizer | 10 | — | — |
| final | 1 | — | — |

**Parallel opportunities.** P0's documentation edits (T001–T005) touch five
different files and run together. Inside P1, T024, T025 and T026 are independent
seams over the same context. P3 and P4 depend on P1 but not on P2, so the rules
and card track can run beside the weather and GR2L track; P5 needs both. Within
P2, T045, T046 and T053 are independent cleanups.

**The one irreversible edge** is T107. Two of its prerequisites — T105 and T106 —
edit text that is byte-stable afterwards, so neither can be deferred past the
gate.
