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

## Work packets — the unit of execution

A phase is not a session. The binding constraint is neither task count nor code
volume — every module P1 and P2 touch is small, 2,505 lines across all of them —
but **the specification re-read** (the four core documents are 2,604 lines, the
three tool specs another 1,207) and **the debugging loop**. A packet is therefore
sized so that one session reads a few hundred lines of specification, edits six
to ten files, and fails in **one library's vocabulary** rather than three.

Three rules make a packet fit:

1. **Read the sections, not the documents.** Each packet below names its own; a
   session that opens all four core documents has spent a quarter of its window
   before writing a line.
2. **Commit at every packet boundary.** A session that starts from a dirty tree
   spends its opening on reconstructing what the last one was doing.
3. **Write findings back as you go** — measurements into `findings.md`, decisions
   into `decisions.md`, consequences into `plan.md`. Anything re-derived in a
   later session is a session half spent.
4. **A packet that changes behaviour syncs its own tool spec, inside the packet**
   — T056, T057, T071. The obligation used to be one standing task and it did not
   fire: P2a shipped the station source while `weather_tool.md` still called it
   planned. An obligation attached to no boundary is deferred by default, which is
   the same reason T035 became three tasks.

| Packet | Tasks | Sections to read | Exit |
|---|---|---|---|
| **P0** specification reconciliation | T001–T010 | all four core documents — cross-document consistency *is* the deliverable, so this one must not be split | no document references a file, section or decision id that does not exist |
| **P1a** context, cache, clock | T020–T023, T031, T035a | arch §4, §5 (as-of views, response cache); `decisions.md § The construction seam`, `§ The response cache`, `§ The as-of cut`, `§ Window resolution and the scenario clock`; `findings.md § Codebase seams` | as-of bound on all five tables; the cut identical under two host `TZ`s; cache round-trip, canary divergence, unfillable miss |
| **P1b** DB seams, frozen sub-agent | T024–T028, T035b | arch §3.1, §5 (generated-SQL bullet); `decisions.md § The construction seam`; `findings.md § Codebase seams` (executor injectability, `AgentTool` name) | two contexts' sub-agents run one query concurrently, each on its own bound; the `CURRENT_DATE` rewrite pinned |
| **P1c** factories, pins, smoke | T029, T030, T032–T034, T035c, T036 | arch §2, §5's pin list; `decisions.md § Model pinning`, `§ Replication and the LLM cache` | independent toolsets for two `as_of` values; `just pins` verifies; the chat UI unchanged |
| **P2a** weather: window, horizon, sources | T040–T045, T055, T056 | arch §3.3; `weather_tool.md § Station source` + the two unit conversions; `decisions.md § Weather sources`, `§ Typed abstention`, `§ The day boundary`; `findings.md § Weather source measurements`, `§ Data record` | station derivation field by field; midnight-straddling rain in the right Berlin day; routing at both record edges; a station case in replay with **zero** cache entries and zero live calls |
| **P2b** GR2L: seed, counterfactuals, scope, taxonomy | T046–T054, T057, and **T115 pulled forward** | arch §3.4 + §3's preamble; `gr2l_tool.md`; `decisions.md § GR2L argument surface`, `§ Bounded series`, `§ Tool errors and harness exclusion`; `findings.md § Data record` (QWetland) | T054's list green; `roof_type` pinned as `str`; a model case in replay issues no live call; the GR2L canary committed |
| **P3a** roof table, ET0, constants | T060–T062 | arch §1 principle 4, §3.5; `irrigation_tool.md § Units`, `§ Which extensive roof is which`; `findings.md § Not every roof is instrumented`, `§ The lysimeter collection area` | one roof table feeds `swc` and the presets with no value changes |
| **P3b** bucket, faithfulness, unit fix, tool | T063–T067, T070, T071 | `irrigation_tool.md` in full; `decisions.md § The irrigation calculator`, `§ No fitted correction between the instrument and the oracle` | the port reproduces the deployed controller **before** the unit fix; the diff list exists |
| **P4** cards | T068, T069, T080–T084 | arch §3.2; `decisions.md § Retrieval` | a lookup case in replay with zero cache entries and zero live calls; every drift test green |
| **P5** plotting | T090–T097, T099 | arch §3.6; `decisions.md § Plotting`, `§ Bounded series` | a `model` + `weather` + `measured` plot issues no live call in replay |
| **P5f** frontend render | T098 | the existing tool-result component; arch §3.6's headless paragraph | the chat renders a plot from the stashed payload |
| **P6a** rollout, contract | T100, T101, T104 | arch §2 (contract), §6, §7's exclusion paragraph; `decisions.md § The answer contract`, `§ Tool errors and harness exclusion` | one case runs end to end; an injected `upstream` error marks `harness_error` where an `invalid_argument` does not; `parse_failure` reported apart from a wrong answer |
| **P6b** scoring | T102 | arch §7 in full, §6.1's `expectations` fields; `decisions.md § Trajectory scoring and routing probes`, `§ Plotting` (skip), `§ Retrieval` (card recall) | four metrics over fixture results, each with its edge case: unit normalization, binary trajectory, skip **and** coverage on both users, false abstention kept separate |
| **P6c** pre-freeze text, pilot oracles | T103, T105, T106, T011 | arch §3.1; `questions.md` §1.6 and the T01/T07/T09 entries; the three tool specs | the semantic layer carries the area *value* and the alias map; three oracles reproduce hand-computed answers; T011 finds no tool spec contradicting the code |
| **P6d** pilot and freeze | T107 | — | three repeats on T01/T07/T09, paired; the freeze recorded |
| **P7a1** oracles, measured families | T110 (A, B, C, H) | `questions.md` §2 those families; arch §3.2, §3.3, §3.6 | each oracle reproduces a hand-checked answer and stamps `expectations.pins` |
| **P7a2** oracles, model families | T110 (D, E, F, G, I) | `questions.md` §2 those families; arch §3.4, §3.5; `decisions.md § The irrigation calculator` | same, and every oracle imports the very function its tool calls |
| **P7b** filters, instantiation | T111 | `questions.md` §1.6, §1.8; `findings.md § Validity-predicate specificity`, `§ Zero-outflow days`, both outage entries | the filters reject the known bad windows and accept the known good ones; T04 balances at ~50/50 |
| **P7c** paraphrases, splits, emission | T112–T114 | `questions.md` §1.6 (paraphrases, roof vocabulary), §1.7; `decisions.md § Splits, sizing and the holdout`, `§ Case time` | per-parameter disjointness asserted; `as_of` striped; the emitter byte-stable across two runs |
| **P7d** capture | T116 | `decisions.md § The response cache` | every case replays with zero live calls |
| **P8a** candidate surface, `predict_fn` | T120, T121, T125 | arch §6, §2's optimizable-text bullet; `decisions.md § The optimizer entry point and the candidate surface`; `findings.md § Optimizer internals` | two records with different `as_of` evaluated concurrently *through* `predict_fn`; the unread-prompt assertion fires when a component is unread |
| **P8b** scorers, search wiring | T122–T124, T126 | arch §7; `decisions.md § Candidate selection and the scorers' aggregation` | a short search over a handful of train cases completes; the skip semantics run through this repo's own aggregation callable, asserted to be passed — omitting it silently makes the objective the mean of the numeric scorer values |
| **P8c** measurement run, statistics | T127–T129 | arch §7's reporting rules; `decisions.md § Replication and the LLM cache`; `specs/prompt-tuning-stats/plan.md` §5–§7 | three repeats × two arms; the bootstrap resamples `template_id`; both gaps separate; test_unseen as a win/loss table |

**Sequencing that the table does not show.**

- **P2a and P2b are sequential, not parallel** — T050 caps the weather wrapper
  P2a has just rewritten, and `schemas.py` belongs to P2b alone.
- **P3 and P4 need P1 but not P2**, so the rules and card track can run beside
  the weather and GR2L track. P5 needs both.
- **T105 and T106 must be committed before P6d opens.** They edit text that is
  byte-stable after the freeze, and there is no second chance at it.
- **T115 runs in P2b.** It is the one task whose failure lives outside this
  repository, and P7d is the worst place to discover it.

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
- [x] T004 [P] Record the plan's verified facts in `findings.md`, dated
  2026-08-19, each with how it was verified: GEPA's `frontier_type` default and
  MLflow's non-setting of it; MLflow's `weighted_objective` default and
  `create_metric_from_scorers`' return shape; `roof_type: str` at the tool
  boundary; the wetland's live modellability in code; `weather_client`'s wall-clock
  reads; `litellm_extra()` forwarding no decoding parameters; the semantic layer
  carrying neither the collection-area value nor an alias map; the GR2L base URL
  resolving to a local service.
  Eight entries landed, each re-read from source or from `.env` on 2026-08-20 and
  dated for both readings. Two corrections to what the plan asked for: MLflow
  ships **no** aggregation callable (`weighted_objective` is docstring text), and
  the silent default is the *mean of the numeric scorer values* rather than an
  error — that entry is dated 2026-08-20 alone, since the plan's version of it was
  wrong. Line citations were re-derived rather than copied.
- [x] T005 [P] Add to `decisions.md`: an entry for the GEPA selection decision
  (rejected — `gepa_kwargs={"frontier_type": …}`, with the reason), fold plan
  §2.3's plot-resolution decision into `§ Plotting`, and record plan §8's Q2 and
  Q3 as accepted risks under `§ Trajectory scoring and routing probes`.
  New entry `§ Candidate selection and the scorers' aggregation` (both rejections:
  the `frontier_type` passthrough and the absent-by-default `aggregation`), three
  new rejected alternatives under `§ Plotting` for the resolved range, and Q2/Q3
  as two accepted risks with their escape hatches named. `plan.md` §2.2, §2.3 and
  §8 now point at where each landed, so no decision lives only in the plan.
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
- [x] T008 [P] Audit the distractor coverage margin: assert every registered tool
  holds at least one must-not slot **inside train**, name the four columns resting
  on a single template, and record the result in `questions.md` §3 as either an
  accepted margin or a second authored slot per tool. This is a validity condition,
  not a nicety (`decisions.md § Trajectory scoring and routing probes`).
  Audited from §2's must-not lines: 4 / 2 / 1 / 1 / 1 / 1 train-side slots for
  `text_to_sql_agent` / `get_weather_forecast_tool` / `lookup_reference` / the
  model tool / `calc_irrigation` / `plot_timeseries`, so Invariant 2 holds and
  T16a supplies three of the four one-deep columns. **Margin accepted**: 8 of
  train's 25 templates carry a must-not, so a shotgun candidate hard-fails 32 % of
  train trajectory — the claim the mitigation actually makes — while a one-deep
  column is admitted to support no per-tool claim. Second slots are named as
  available (T03, T05), and `calc_irrigation`'s absence of a clean one is recorded
  as the reason not to legislate. §4's coverage-margin item removed.
- [x] T009 Settle T07's phrasing and the T16a/T16b cue in `questions.md` §2, and
  close both §4 items. T07's wording must stop cueing the docs route against a
  `calc_irrigation` gold set; T16a and T16b must be distinguishable by phrasing
  alone, since their must-nots are symmetric. The pilot freezes T07's route, so
  this cannot wait for P6.
  Settled by one convention rather than four rewordings — §1.6's **route cue**: a
  documentary reference, or a question asking what a documented value is, routes
  to `lookup_reference`; a question asking for a decision or a measured number
  routes to the tool that computes it, and a paraphrase may neither add nor remove
  such a reference. Under it T07 drops "according to the operations manual", T11
  drops "per the standard rule" (same defect, same gold set — fixed with T07 or
  the convention breaks on its neighbour), T16b drops "does the standard rule say
  to irrigate", and T16a's reference becomes explicit. §4 is now empty and says so.
- [x] T010 `eval/schema/case.schema.json` + `eval/schema/template.schema.json`,
  plus the documented projection from template YAML to case JSON. Envelope is
  architecture §6.1 verbatim. Validators: every tool name is a registered name;
  `answer_metric: "skipped"` implies `answer: null`; non-empty `gold_cards` implies
  `lookup_reference` in `expected_tool_calls`. `argument_checks` carry the
  resolution flag plan §2.3 settles, so a relative window argument is normalized
  through the layer-1 resolver before comparison and the check never reads a tool
  result. **First writer of ground truth — nothing may emit a case before this
  lands.** → T006, T009
  Both schemas (draft 2020-12), the projection in `eval/schema/README.md`, a
  worked template/case pair under `eval/schema/examples/`, and
  `tests/eval/test_schemas.py` — which exercises each validator twice, once on the
  example that must pass and once on the mutation that must fail, so no validator
  can decay into a comment. The tool and card vocabularies live once, in the case
  schema, and the template schema `$ref`s them. Beyond the three required
  validators: `as_of` must carry a numeric offset (`Z` is rejected — the stamp has
  to name the Berlin day), a numeric answer must carry its tolerance, `present`
  checks take no `value` and may carry `plausible: {min, max}`, and
  `resolve: "window"` is the plan §2.3 flag, admitted on comparing ops only. The
  projection's own rule, stated because §6.1 only implies it:
  **`answer_metric` is `skipped` if and only if `answer` is null** — the plot
  deliverable and the abstention alike. One constraint is left to the generator
  because a schema cannot compare sibling arrays: a gold tool may not also be a
  must-not.
- [ ] T011 **Assert at the freeze gate** (packet P6c) that no tool spec
  contradicts the code: read `gr2l_tool.md`, `weather_tool.md` and
  `irrigation_tool.md` against the built behaviour one final time and record the
  result. This is a **check, not a sync** — the syncing itself belongs to the
  packet that changes the behaviour, and is T056, T057 and T071. A tool spec is
  frozen text after T107 in the same sense the sub-agent's prompt is, so this is
  the last moment a contradiction can be repaired rather than disclosed. → T056,
  T057, T071
  **Was a standing task; restated after P2a.** The standing form did not fire:
  P2a built, tested and committed the station source while `weather_tool.md` went
  on saying "Station source (planned)", which is the failure mode T035's split
  was meant to prevent — an obligation with no packet boundary attached is an
  obligation that gets deferred. Its P0 record stands: Phase 0 changed no tool
  behaviour, the wetland's scope is stated ahead of the code by design (plan
  §2.4, code follows in T051), and at the P0 exit every markdown link in the five
  specs and the three tool specs resolved with every `decisions.md § …` citation
  naming a heading that exists.

**Exit — met.** Every cross-document reference resolves (links and
`decisions.md § …` citations swept mechanically), and `questions.md` §4 is empty:
its six items are settled and recorded in the sections they govern, with the one
residual filed as an accepted risk in `decisions.md`. T011 stays open — it is now
the freeze-gate check rather than the standing task it was written as.

---

## Phase 1 — Injection seam and determinism

The blocking phase: no case is reproducible until this lands.

- [x] T020 `assistant/context.py`: `ScenarioContext(clock, db_path,
  weather_client_factory, http_cache)` with `as_of` a property evaluating the
  clock per read, plus `connect_asof(db_path, clock)` building the in-memory
  DuckDB, attaching the pinned file read-only as `src`, and creating one bounded
  view per table over `outflow`, `radiation`, `swc`, `tsoil`, `wetter`. `ctx.db`
  is a `DuckDbQueryExecutor` over a **view-recreating factory**, never a bare
  connection, and reconnects when `clock().date()` moves. `as_of` is converted to
  UTC inside the seam, never by the caller (`decisions.md § The as-of cut`).
  Done. `connect_asof` opens `:memory:`, attaches the pinned file read-only as
  `src`, and creates the five `main.<table>` views bound by a `TIMESTAMP` literal
  computed from `clock().astimezone(UTC)` — never a parameter DuckDB would render
  in its own session timezone, which is the host-`TZ` bug § The as-of cut names.
  One deviation from §4's literal snippet: `ctx.db` is `AsOfQueryExecutor`, not a
  bare `DuckDbQueryExecutor` — the class §4 sketches only calls its
  `connection_factory` once at construction and again on connection errors, so it
  cannot alone reconnect on `clock().date()` moving. `AsOfQueryExecutor` wraps one
  `DuckDbQueryExecutor` instance, rebuilding it via the same view-recreating
  factory the moment `execute_query` sees the bound date has moved, and satisfies
  the same `execute_query(query) -> QueryResult` contract (`ports.py`'s
  `ReadOnlyWarehouseQuery`), so P1b's consumers take it exactly as they would the
  bare class. Manually verified: the bound tracks `clock()` to the same UTC
  instant under `TZ=UTC`, `America/New_York` and `Asia/Tokyo`; a context built on
  a mutable clock closure picks up a later date on its next query with no
  explicit rebuild call.
- [x] T021 `assistant/cache.py`: `ResponseCache` keyed by the sha256 of the
  canonical request — URL plus sorted query parameters for Open-Meteo, `data[]`
  plus parameters for GR2L — as committed JSON under `eval/cache/`. A miss is
  filled live and recorded, **gated on the service canary matching**; a diverging
  canary and an unfillable miss are both hard failures carrying the unmatched
  request and a diff against the nearest captured request
  (`decisions.md § The response cache`). → T020
  Done. `ResponseCache` is agnostic of which service it fronts — `key_for` hashes
  whatever canonical-request mapping the caller builds via `sort_keys=True` JSON,
  so "sorted query parameters" falls out of canonicalization rather than needing
  the caller to pre-sort. `Canary(request, live_fetch)` reuses the same
  request-keyed storage for its own committed entry: `fetch()` checks it before
  filling any miss, records its first capture as the verification pass that
  captured it, and raises `CanaryMismatchError` (carrying a unified diff of
  committed vs. live) on divergence *before* the real request's `live_fetch` ever
  runs — no entry is recorded from an unverified service. `CacheMissError` covers
  both hard-failure shapes the row names: `allow_live=False` (replay: nothing may
  call out) and a `live_fetch` that raises; both carry the unmatched request and,
  when the cache holds any entries, a `difflib`-nearest committed request plus a
  unified diff against it. Manually verified all three exit behaviours: a
  put/fetch round trip returns identical data; a canary whose live response
  differs from its committed one raises without writing the pending entry; a
  disabled-live and a raising-live miss both raise `CacheMissError` naming the
  nearest captured request.
- [x] T022 `WeatherClient` protocol plus the cached Archive implementation
  wrapping `fetch_daily_weather`; move the module-level `httpx.AsyncClient`
  singleton behind it. The composite that adds the station half is T043. → T021
  Done, in `tools/weather_client.py`. `WeatherClient` is a `Protocol` with one
  method, `fetch(start_date, end_date) -> WeatherResult` — no location argument,
  since a `WeatherClient` is constructed once per rollout already bound to the
  site. `ArchiveWeatherClient` implements it, always forcing Open-Meteo's Archive
  backend (deterministic ERA5 reanalysis, the one Open-Meteo path worth caching)
  via a new `force_archive` flag on `fetch_daily_weather`, so it never calls
  `_choose_backend`'s wall-clock read at all — narrower than fixing that read
  everywhere, which is T031's job, not this one's. `fetch_daily_weather` also
  gained an optional `client: httpx.AsyncClient | None` parameter, defaulting to
  the existing module singleton (`tools/weather.py` and `tools/gr2l.py`, both
  outside this packet, are unaffected); `ArchiveWeatherClient` is the one caller
  that passes its own owned client, which is the "moved behind it" the row asks
  for. No canary here — `decisions.md` § The response cache and §5 both name the
  cache as load-bearing for GR2L only; for weather it is cost/speed, so
  `ArchiveWeatherClient.fetch` calls `cache.fetch()` with no `canary=`. Manually
  verified against a fake `httpx` transport: a second `fetch()` for the same
  window returns the cached `WeatherResult` and the transport records exactly one
  call.
- [x] T023 Route `run_gr2l` through the same cache and add the canary
  request/response hash as the service-version proxy. → T021
  Done, in `tools/gr2l_client.py`. `run_gr2l` gains an optional `cache:
  ResponseCache | None` keyword; the request is unchanged from before
  (`Gr2lRequest(data=rows, **parameters.model_dump())`), and its
  `model_dump(exclude_none=True)` — `data[]` plus every roof parameter — is the
  canonical request that goes into `cache.fetch(...)`, matching §5's "`data[]`
  plus parameters for GR2L" verbatim. `CANARY_REQUEST` is a fixed, arbitrary
  one-day probe (never real site data, so it can't collide with an actual case's
  key) sent through a `Canary` on every cached call; its first live response
  becomes the committed entry and every later miss re-verifies against it before
  recording anything new, exactly as `decisions.md` § The response cache
  requires. `cache=None` (the default) calls GR2L directly with no caching at
  all, so `gr2l.py`'s existing direct call — untouched, outside this packet — is
  unaffected. Manually verified against a fake POST client: a second `run_gr2l`
  call for the same rows/parameters issues zero HTTP calls; a service whose
  canary response changes between two cached calls raises
  `CanaryMismatchError` before the second call's own data request is ever sent.
- [x] T024 [P] `tools/swc.py`: take the executor from `ctx` instead of building a
  connection from global settings; the module stays pure and ADK-free. → T020
  Done. `_ExecutorHolder` / `_get_executor` are gone, and with them the
  `settings` and `create_duckdb_connection` imports: `latest_measured_swc`,
  `_record_bounds` and `_unavailable_message` all take
  `executor: ReadOnlyWarehouseQuery` as their **first, required** parameter — the
  port (`ports.py`), not `DuckDbQueryExecutor`, so `ctx.db`'s `AsOfQueryExecutor`
  satisfies it without swc.py naming a concrete class. No production default is
  left inside the module: the row's "instead of" is only real if the settings
  path cannot be reached from here, and a silent fallback to an unbounded
  connection is precisely what `decisions.md` § The construction seam rejects.
  The one production caller, `gr2l.py`'s `_resolve_seed`, gained the same
  parameter and `predict_green_roof_water_balance_tool` passes
  `get_duckdb_executor()` — warehouse's existing lazy singleton rather than a
  second one, which collapses the "independent second seam" `findings.md`
  § Codebase seams records at `swc.py:99-107` into one production executor. That
  call is the single expression T029/T066 will replace with `ctx.db`. Nothing
  else calls into `swc`, and `latest_measured_swc`'s own SQL is unchanged, so
  the bound is entirely the caller's binding. `uv run ruff check .` and
  `uv run pytest` clean — 103 passed, the same 15 pre-existing findings in
  `notebooks/`, `scripts/count_tokens.py` and `train_gepa.py`.
- [x] T025 [P] `tools/warehouse.py`: `make_query_database_tool(executor, clock)`
  returning a closure that preserves the exact name, signature and docstring of
  `query_database_tool` — the frozen instruction names the tool and ADK derives
  the declaration from the function. Keep the module-level tool as the production
  default bound to the settings executor and `site_now`. → T020
  Done. The former module-level `def` moved **inside** the factory verbatim, so
  the production default is now `make_query_database_tool(SETTINGS_EXECUTOR,
  site_now)` and name, signature and docstring are identical to a context-bound
  tool *by construction* rather than by a copy someone must keep in step — there
  is one construction path, which is also what T035c will assert for the other
  factories. Verified the text that actually reaches the model: ADK's
  `FunctionTool(...)._get_declaration()` over the closure yields
  `name='query_database_tool'` and a `description` byte-identical to the
  pre-refactor one (the docstring's deeper source indentation is removed by
  ADK's own dedent, so the declaration is unchanged), with `tool_context` still
  absent from the parameter schema. `__qualname__` is reset to
  `query_database_tool` as well — ADK reads only `__name__`, but a
  `<locals>`-qualified name would otherwise surface in logs and reprs.
  Two supporting changes the row does not name but the factory forces. First,
  `SETTINGS_EXECUTOR`: `make_query_database_tool` takes an executor *object*,
  the default is built while the module is still importing, and
  `DuckDbQueryExecutor` connects in `__init__` — so a bare
  `get_duckdb_executor()` there would have opened a DuckDB connection at import
  time, which nothing does today. `_SettingsExecutor` is a two-line
  `ReadOnlyWarehouseQuery` that forwards to the existing lazy singleton per
  query, keeping import-time behaviour exactly as it was. It is also what T026
  and T027 hand their production defaults. Second, `_validated_execute` and
  `_execute_and_serialize` now take the executor explicitly instead of reaching
  for `get_duckdb_executor()` at the bottom — the whole point of the seam — and
  `_validated_execute` also carries the `clock` through to T028's rewrite; it is
  threaded but not yet read in this commit. `tests/assistant/test_phase3_fr15.py`
  monkeypatches `_validated_execute`, so its two fakes gained the matching
  parameters; nothing else about that test changed. `uv run ruff check .` and
  `uv run pytest` clean — 103 passed, same 15 pre-existing findings.
- [x] T026 [P] `DuckDbExplainValidator(executor)`: take the executor as a
  constructor argument instead of importing the warehouse singleton
  (`dry_run.py:13`), so the sub-agent has exactly one DB seam. → T020
  Done. `dry_run.py` no longer imports `tools.warehouse` at all — the `__init__`
  takes a `ReadOnlyWarehouseQuery` and `avalidate` runs `EXPLAIN` through it.
  Worth stating why this seam is not cosmetic: `EXPLAIN` binds identifiers
  against a *catalog*, so a validator left on the unbounded connection would pass
  SQL the case's own executor is about to run against the as-of views — the two
  connections agree on names today, but the moment they stop agreeing the
  pipeline would hand the querier SQL it has already declared valid. The
  constructor argument is required, with no default, for the same reason T024
  keeps none: the failure mode `decisions.md` § The construction seam names is a
  silent fallback that still answers. The only production construction site,
  `agent.py`'s module-level `_PIPELINE`, now passes T025's `SETTINGS_EXECUTOR`,
  which keeps it lazy — T027 replaces that line with the per-context build.
  `uv run ruff check .` and `uv run pytest` clean — 103 passed.
- [x] T027 `build_text_to_sql_agent(executor, clock, description=None)`: rebuild
  the pipeline with a context-bound validator and querier and a fresh `Agent` with
  **byte-identical** frozen text; the module singleton becomes the production
  default built from it. → T025, T026
  Done. The factory builds a fresh `TextToSqlPipeline` whose validator is
  `DuckDbExplainValidator(executor)` and a fresh `Agent` whose tools are
  `make_query_builder_tool(pipeline)` and `make_query_database_tool(executor,
  clock)` — the same executor object in both DB seams, so the SQL the pipeline
  approves and the SQL that runs meet one catalog. `text_to_sql_agent =
  build_text_to_sql_agent(SETTINGS_EXECUTOR, site_now)`, so `bootstrap.py`, the
  root agent and the chat service are untouched and there is no second
  construction path.
  **Byte-identical, verified rather than asserted:** the frozen strings moved to
  module constants (`AGENT_NAME`, `AGENT_DESCRIPTION`, `STATIC_INSTRUCTION`,
  built once at import) and `sha256(name | description | static_instruction)` of
  the production agent is `ba967291…`, unchanged from the pre-refactor commit;
  the hash over both tools' `(__name__, __doc__)` pairs is `5295be11…`, likewise
  unchanged. `query_builder_tool` had to become a closure too — it captured the
  module-level `_PIPELINE`, which no longer exists — and its docstring is
  therefore indented one level deeper in source; that is invisible because
  CPython 3.13 strips a docstring's common leading whitespace at compile time,
  and ADK dedents besides. Nothing outside this module imported `_PIPELINE` or
  `query_builder_tool`.
  What is deliberately **not** per-context: the transpiler and the two
  `LlmSqlFixer`s are stateless configuration and are now module-level singletons
  shared by every build (§3.1's "transpiler, fixers, models and prompt strings
  are shared"). The model is the exception to object sharing — `_build_model()`
  runs per build, since a `LiteLlm` is cheap and two rollouts should not share a
  client; the *text* that pins it, `settings.text_to_sql_agent_model`, is the
  same, which is the sense in which §3.1 means shared. `description` defaults to
  `None` and falls back to `AGENT_DESCRIPTION`, so T029 can thread a candidate's
  text through without the default becoming a second copy of the wording.
  `uv run ruff check .` and `uv run pytest` clean — 103 passed.
- [x] T028 Extend the querier's sqlglot pass — today it parses only for the
  read-only guard and executes the original string, so it must now re-emit what it
  validated — to rewrite `CURRENT_DATE` and `now()`-family nodes to the literal
  `as_of`. Rewrite, never reject: the sub-agent that emits them is dateless by
  design. → T025
  Done. `_validated_execute` now runs `_pin_clock_functions(parsed, clock())` and
  executes `.sql(dialect="duckdb")` off the rewritten tree; the original string
  is no longer executed at all. **Re-emission was the task, not the rewrite** —
  the guard already parsed and then threw the tree away, so a transform without
  it would have passed every test that inspected the tree and changed nothing at
  the database.
  Coverage is by node type, not by spelling: `exp.CurrentDate` (which is also how
  sqlglot parses DuckDB's `today()`), `exp.CurrentTimestamp` /
  `exp.CurrentDatetime` / `exp.Localtimestamp`, `exp.CurrentTime`, plus the rest
  of the `now()` family — `now`, `get_current_timestamp`, `transaction_timestamp`,
  `current_localtimestamp`, `current_localtime` — which sqlglot 30.9 parses as
  `Anonymous` and which are matched by lowercased name.
  **Which instant each node gets**, since neither §5 nor `decisions.md` settles
  it and the two answers differ: a date node is pinned to `as_of`'s **site-local**
  calendar date, a timestamp/time node to the same instant in **UTC**. A date
  node names the day the case is about — the boundary the oracles group on — and
  pinning it to the UTC date would move "today" by a whole day for an `as_of`
  before 02:00 Berlin, where the site-local choice is off by at most the couple of
  hours by which midnight-UTC misses midnight-Berlin. A timestamp node is compared
  against the columns' naive UTC values, so it is normalized exactly as
  `connect_asof` normalizes the bound (`decisions.md` § The as-of cut). Verified
  at that edge: at `as_of = 2026-04-24 00:30+02:00`, `SELECT CURRENT_DATE, now()`
  executes as `CAST('2026-04-24' AS DATE), CAST('2026-04-23 22:30:00' AS
  TIMESTAMP)`.
  Two consequences worth recording. Re-emission normalizes every statement
  through sqlglot's generator (`count(*)` → `COUNT(*)`), which is harmless because
  the pipeline's transpiler already round-trips the builder's SQL through
  read=duckdb/write=duckdb before the querier ever sees it — the querier's input
  is sqlglot output already. And a generation failure returns the same
  `Failed to parse the SQL query.` error as a parse failure rather than falling
  back to the original string, since the fallback is precisely the silent no-op
  this task exists to remove. The FR15 session-state stash keeps the model's own
  `sql_executed` text; the rewrite is logged at debug with both strings.
  `uv run ruff check .` and `uv run pytest` clean — 103 passed.
- [x] T029 `make_*` tool factories closing over `ctx` for the built tools, plus
  `build_toolset(ctx, docstrings=None)` applying docstrings to the produced
  callables so they are candidate-addressable. The text2SQL entry is built per
  context, never the import-time singleton, with `description` threaded from the
  candidate docstrings. → T022, T024, T027
  Done. `make_weather_forecast_tool(ctx)` and `make_green_roof_balance_tool(ctx)`
  join T025's `make_query_database_tool`, each moving the former module-level
  `async def` inside verbatim, and `build_toolset` lives in a new
  `assistant/toolset.py` — it needs the sub-agent, both tool modules and
  `TextToSqlAgentTool`, so putting it in `context.py` (§4's sketch shows the
  signature there) would have made `context.py` import the tools that already
  import it. The toolset is `[TextToSqlAgentTool(sub_agent), green_roof, weather]`
  in that order, which is the order the service has always declared; declaration
  order is part of the prompt, so `TOOL_NAMES` fixes it rather than the caller.
  Each factory binds exactly what `ctx` owns and reads it **per call**:
  `ctx.clock()` for the day both wrappers resolve their window against (the
  `site_now()` reads T031 had just made explicit are now gone from the tool
  layer), and `ctx.db` for the GR2L seed — the single `get_duckdb_executor()`
  expression T024's note left marked for this task. Docstrings are applied to the
  produced callables by assignment (`tool.__doc__ = text`), which is safe only
  because the callable is that context's own closure; the sub-agent's entry is
  its outward `description`, not a docstring, and goes through T027's
  `description=` argument. An unknown key in `docstrings` **raises**: a candidate
  component that is silently dropped is scored as though it had been applied,
  which is exactly `decisions.md` § The optimizer entry point and the candidate
  surface's failure through a side door.
  **Byte-identical, verified rather than asserted:** ADK's
  `FunctionTool(...)._get_declaration()` over both closures yields the name,
  description and parameter schema of the pre-refactor module-level functions —
  compared against the docstrings parsed out of `git show HEAD:` for both files,
  not against a copy in the test — with `tool_context` still excluded. The
  sub-agent's `sha256(name | description | static_instruction)` is still
  `ba967291…`. `__qualname__` is reset on both closures, as T025 did.
  Two deliberate deviations. First, **the weather source is not `ctx.weather`
  yet**, though T022 built the protocol: the only implementation today is
  `ArchiveWeatherClient`, which forces Open-Meteo's Archive backend and therefore
  cannot answer any forecast window, so binding it here would break the
  production tool for exactly the questions the chat is asked most. T043's
  composite is what fills `ctx.weather`; this task binds the clock, which is the
  half that leaks real time into a case. Second, **production gets a context, not
  a `ScenarioContext(db_path=...)`**: `ScenarioContext.bound(clock=..., db=...,
  weather=..., cache=...)` is a new alternative constructor taking
  already-built collaborators, so `production_context()` can pair `site_now` with
  T025's lazy `SETTINGS_EXECUTOR` and still open no DuckDB connection at import —
  `__init__` builds an `AsOfQueryExecutor`, which connects immediately, and
  production has no case to be bounded by. Its `weather` is `None` on purpose, so
  a premature consumer fails loudly rather than reading the wrong source.
  `agents/root_agent/agent.py` consequently stops importing the two tool
  callables and passes `tools=build_toolset(production_context())`; the `Agent(…)`
  construction itself is T030's to replace. `uv run ruff check .` and
  `uv run pytest` clean — 115 passed, the same 15 pre-existing findings.
- [x] T030 `build_root_agent(instruction, docstrings, tools, model)` binding the
  per-invocation instruction provider as a closure over `ctx.clock`; the
  module-level `root_agent` becomes the production default built from it, so
  `bootstrap.py` and the frontend are untouched. → T029
  Done. `root_agent = build_root_agent(production_context())`; `bootstrap.py`,
  `routers/agent.py` and the frontend import the same name and are untouched.
  **The signature gained `ctx` as its first parameter** — plan §5's four
  arguments are all still there, defaulted, but the row's own requirement (the
  instruction provider is "a closure over `ctx.clock`") cannot be met by a
  function that never receives a context, and the toolset needs the same one.
  `_make_temporal_instruction(ctx)` returns the `InstructionProvider`, which calls
  `ctx.clock()` **at invocation time**, not at build time: a case's agent renders
  its frozen `as_of` and production's renders the advancing site clock through one
  code path, and `static_instruction` stays the byte-stable prefix it was for
  prompt caching. The `site_now` import leaves this module, so no wall clock is
  read anywhere under `agents/`.
  `instruction` defaults to `ROOT_INSTRUCTION` and `model` to a fresh `LiteLlm`
  per build (a client is cheap; two rollouts should not share one, while the id
  and decoding parameters that *pin* it stay the same — §3.1's sense of shared).
  `tools` defaults to `build_toolset(ctx, docstrings)`, and passing **both**
  `docstrings` and `tools` raises rather than preferring one: the docstrings
  would be dropped and the candidate silently truncated, which is T029's unknown-key
  failure arriving from the other side. Verified: a candidate instruction and a
  candidate docstring both reach the built agent, two builds share no objects, and
  a context frozen at 2026-03-15 renders that date in the instruction block while
  the production agent renders today's. `uv run ruff check .` and `uv run pytest`
  clean — 115 passed, same 15 pre-existing findings.
- [x] T031 Scenario clock end to end: `current_datetime_block` gains a required
  `now` parameter; `fetch_daily_weather` and its backend selection gain a
  **required** date argument with no wall-clock default; the `site_now` import
  leaves `weather_client`; `site.py`'s module docstring stops claiming the weather
  client takes its clock from there. → T020
  Done. `current_datetime_block(now: datetime)` and `_choose_backend(start_date,
  today: date)` both lost their internal `site_now()` reads outright — the
  parameter is genuinely required, no sentinel. `resolve_window` came along for
  the same reason (`site_now` had to leave the module entirely, and it was
  `resolve_window`'s other wall-clock read): its `today` keyword lost its
  `today or site_now().date()` fallback and is now required too — unflagged by
  the row's own text but implied by "the `site_now` import leaves
  `weather_client`," and every existing test already passed `today=` explicitly,
  so nothing broke. `fetch_daily_weather`'s `today` stayed **optionally**
  required: keyword-only, defaulting to `None`, but raising `TypeError` at the
  call site the moment `force_archive` is not set and `today` is still `None` —
  a plain required parameter would have forced `ArchiveWeatherClient` (T022,
  always `force_archive=True`) to invent a date it never uses, which is worse
  than the raise. Three production call sites lost their hidden wall-clock read
  as a direct consequence and needed a one-line change each to stay explicit
  instead of silent: `agents/root_agent/agent.py`'s `_temporal_instruction` now
  passes `now=site_now()`; `tools/weather.py` and `tools/gr2l.py` now compute
  `today = site_now().date()` once and thread it into both `resolve_window` and
  `fetch_daily_weather`. None of the three is on this row's own file list, but
  all three call the functions the row renames, and leaving them uncalled with
  the old signature was not an option. `site.py`'s docstring no longer claims
  `weather_client` reads its own clock; it now says the client "takes no clock of
  its own" and names the two explicit-argument call sites instead. One existing
  test called `current_datetime_block()` bare
  (`tests/assistant/test_temporal_context.py`); updated to pass `now=site_now()`.
  Manually verified `fetch_daily_weather(...)` with neither `today` nor
  `force_archive` raises `TypeError` before any HTTP call.
- [x] T032 [P] Set the ~6-step tool cap on the root agent. → T030
  Done, but **not as an `Agent` field, because there is none**: ADK bounds a run
  by *LLM calls*, in `RunConfig.max_llm_calls`, so `build_root_agent` cannot
  carry the cap at all. `agents/root_agent/agent.py` gains `MAX_TOOL_STEPS = 6`,
  `MAX_LLM_CALLS = MAX_TOOL_STEPS + 1` and `rollout_run_config()`, and **P6's
  `run_case` and P8's `predict_fn` must both call that one function** — a search
  that bounded its candidates differently from the measurement path would differ
  on the one thing that decides whether a shotgun candidate finishes at all.
  The arithmetic, written down rather than guessed: in a ReAct loop each tool
  step costs one model turn (the turn that emits the call) and the answer after
  the last tool result costs one more, so 6 steps is 7 calls. ADK increments the
  counter and raises when it *exceeds* the limit
  (`invocation_context.py:88-99`), so exactly 7 calls are allowed and the 8th
  raises. Two things the count excludes, both verified in ADK 2.3's source:
  `AgentTool.run_async` builds its **own** `Runner` with a default `RunConfig`
  (`agent_tool.py`), so the sub-agent's builder/query turns are bounded by their
  own 500 and one `text_to_sql_agent` call is one step here however many turns it
  takes inside; and a model call retried after a transport error counts again, so
  the bound is on calls issued, not on distinct steps taken.
  **Open question for P6a, recorded rather than decided here:** the cap *raises*
  (`LlmCallsLimitExceededError`) mid-run — ADK has no truncate-and-answer mode —
  so a candidate that loops produces an exception, not a bad answer. Whether that
  is a wrong answer, an abstention or a harness exclusion belongs with the rest of
  the error taxonomy (`decisions.md` § Tool errors and harness exclusion), which
  is T100/T101's. Not applied to the production chat path: `ag_ui_adk` does take a
  `run_config_factory`, but capping there turns a live user's long conversation
  into a 500, which is not what T036's "the prose answer path is unchanged"
  means. `uv run ruff check .` and `uv run pytest` clean — 115 passed.
- [x] T033 Pin the task LLM: `temperature=0` and the decoding seed through
  `litellm_extra()`, the served model id and endpoint recorded per run, and the
  response cache switchable — on inside the search, off on the measurement path
  (`decisions.md § Replication and the LLM cache`). → T021
  Done. `litellm_extra()` now carries `temperature` (0.0), `seed` (42) and the
  `caching` flag alongside the endpoint, which pins **all four** models this
  package builds — root agent, sub-agent, SQL builder, both fixers — by
  construction rather than at four call sites that could drift. Production
  decoding does change: those calls previously took the provider's default
  temperature, and now run greedy. That is §5's row, and the direction is toward
  reproducibility, but it is a live-behaviour change and not merely bookkeeping.
  The record and the switch live in a new `assistant/llm.py`, apart from
  `settings.py`, which holds values and not policy: `task_model_pin()` /
  `sub_agent_model_pin()` return served id + endpoint + decoding parameters (the
  `canary` slot is `eval/pins.json`'s, since capturing one means talking to the
  endpoint, which this module never does), and `configure_llm_cache(enabled)`
  installs or removes litellm's disk cache. **Off is the default**, because the
  measurement path is where a mistake is unrecoverable: three repeats behind a
  cache are one sample and two copies.
  Two things verified rather than assumed about the key, since
  `decisions.md`'s validity condition rests on them. litellm builds it from
  `ModelParamHelper._get_all_llm_api_params()`, which **does** include `tools` and
  `tool_choice` as well as `model`, `messages`, `temperature` and `seed` — checked
  directly, and then behaviourally: two requests differing only in a tool's
  `description` hash to different keys, as do two differing only in the seed. So
  a candidate cannot be scored on another candidate's responses. But `api_base` is
  **not** in the key, so the cache is namespaced by the endpoint here; without
  that, the same model id served from a second provider would replay the first
  provider's answers under a pin that claims to distinguish them.
  `uv run ruff check .` and `uv run pytest` clean — 115 passed.
- [x] T034 `eval/pins.json` and a `just pins` check covering plan §4's pin list,
  with the card-store and reflection-model entries stubbed until P4 and P8 fill
  them. → T033
  Done: `eval/pins.json`, `scripts/check_pins.py`, and a new `eval.just` imported
  by the `justfile` — the repo splits its recipes by area (`models`, `common`,
  `experiments`) and has no lint or test target to hang this off, so the testbed
  gets its own file, which is also where P6–P8's run recipes will go. Two
  recipes: `just pins` verifies, `just pins-write` re-pins deliberately.
  **Three states, not two**, which is what makes a file written before most of
  its artifacts exist checkable at all. *Pinned*: committed and recomputed alike.
  *Moved*: committed and different — the failure the check exists for; it prints
  both values and exits 1. *Unpinned*: committed as `null`, reported and never
  failed, because the artifact does not exist yet (the card store, the rules
  constants, `roofs.py`, the station derivation, the reflection model, the
  candidate prompt names) or because filling it needs a live capture no offline
  check can make (the GR2L and task-model **canary responses**). Seven pins are
  live today: the `water.duckdb` sha256, the GR2L roof presets, the GR2L canary
  *request*, the GR2L base URL, the task and sub-agent model records, and the
  dependency versions. Nine are open. When an unpinned slot becomes computable
  the report says so (`unpinned, ready: …`) rather than staying silent, and
  `--write` never overwrites a value it cannot derive, so a canary filled by a
  live pass survives the next re-pin.
  Three choices worth recording. The **base URL is pinned by its sha256**, not
  in clear: §5 asks for the URL, but the URL lives in `.env` and committing an
  internal host into the repository is a disclosure the pin does not need — the
  hash detects a move to another deployment just as well. The **dependency
  versions come from `uv.lock`**, not from the installed environment: the
  lockfile is the artifact committed beside a result, and an environment that has
  drifted from it is precisely what this pin should catch. And the **card-store
  digest covers paths as well as bytes**, since renaming a card is as much a
  change as editing one and there is no index file to hash instead.
  Verified end to end: `just pins` exits 0 against the committed file, and
  exits 1 naming `water_duckdb_sha256` with both values when that hash is moved
  by one byte. `uv run ruff check .` and `uv run pytest` clean — 115 passed,
  same 15 pre-existing findings.
- [x] T035a Tests for the context and the cache (**packet P1a**): `connect_asof`
  bounds every one of the five tables; the as-of cut is identical under at least
  two host `TZ` settings — nothing else in the pin set can detect a violation; a
  production-clocked context reflects a date change on the next query without a
  rebuild; cache round-trip, canary divergence and unfillable miss.
  → T020, T021
  Done: `tests/assistant/test_context.py` (9 cases) and `tests/assistant/test_cache.py`
  (8 cases). Every `connect_asof` bound is checked against an independent raw
  query over the pinned `data/water.duckdb` — never against another view — so
  the test can't pass by agreeing with its own implementation; the fixed `as_of`
  (2026-01-01) sits inside four tables' records and past `radiation`'s, exercising
  both a real truncation and a full pass-through in the same parametrized run. The
  `TZ` case is the one place a same-process assertion would have been too weak: a
  mid-run `os.environ['TZ']` write is not guaranteed to reach every
  timezone-aware code path without `time.tzset()`, so it spawns three fresh
  interpreters (`UTC`, `America/New_York`, `Asia/Tokyo`) and diffs their output
  instead. The rebuild test drives `ScenarioContext` through a mutable-clock
  closure across a date change and asserts the later query's bound moved with no
  explicit rebuild call, plus a companion white-box check that the connection
  object itself is untouched across two same-day queries. The cache tests cover
  round-trip (including key-order independence, since canonicalization is
  `ResponseCache`'s job), both unfillable-miss shapes (`allow_live=False` and a
  raising live fetch, the latter asserted to carry the nearest captured request),
  canary first-capture-then-verify, canary divergence blocking the pending entry,
  and that a hit never consults the canary at all. `uv run ruff check .` and
  `uv run pytest` both clean — 103 passed, 15 pre-existing unrelated `ruff`
  findings in `notebooks/`, `scripts/count_tokens.py` and
  `src/experiments/text2sql/train_gepa.py` untouched.
- [x] T035b Tests for the DB seams and the sub-agent (**packet P1b**): two
  contexts with different `as_of` running the same query concurrently — through
  the sub-agent path as well — each seeing its own bound; the T028 rewrite pinned
  by test; the context-bound query tool's name, signature and docstring identical
  to the module-level one. → T027, T028
  Done: `tests/assistant/test_db_seams.py`, 12 cases. The concurrency pair runs
  two `ScenarioContext`s (bounds 2026-01-01 and 2026-03-15, ~3,500 `outflow` rows
  apart) through `asyncio.gather` over **interleaved** calls — four each,
  alternating — first at the executor seam and then through the sub-agent path
  proper, `build_text_to_sql_agent(ctx.db, ctx.clock).tools[1]`, which is the
  packet's exit criterion. Expected counts come from an independent raw query
  against the pinned file rather than from a second view, as in T035a.
  The rewrite is pinned from the executor's side: a `SpyExecutor` records the SQL
  it is **handed**, so the assertions are about what reached the database, not
  about the parse tree — a tree-inspecting test would pass against exactly the
  no-op T028 removes. Cases: `CURRENT_DATE` → the site-local date literal; the
  five-spelling `now()` family → the UTC instant (11:00 Berlin in January
  becoming `10:00:00`, with the date node in the same statement keeping the
  site's day); the executed string differing from the original; a dateless
  `CURRENT_DATE - INTERVAL 7 DAY` query against the real as-of view returning the
  same non-zero count as its literal equivalent — under the wall clock that
  window is empty, so it can only pass if the tool used the context's clock; the
  clock read per call through a mutable closure; and the read-only guard still
  rejecting `DROP TABLE` with nothing reaching the executor.
  Both mutations were run to check the tests have teeth rather than assuming it.
  Executing `sql_query` instead of the re-emitted string — the exact silent no-op
  — fails 5 cases; binding the sub-agent's query tool to `SETTINGS_EXECUTOR`
  instead of the context's executor fails the sub-agent concurrency case alone,
  which is the one that would otherwise have been satisfied by an unbound
  singleton answering plausibly.
  The frozen-text cases compare a context-bound tool against the production
  default on `__name__`, `__qualname__`, `inspect.signature` and `__doc__`, and
  two built agents on name, description, `static_instruction` and both tool
  docstrings, while asserting the agents and their tools are distinct objects —
  §3.1's two halves in one test. T026's seam gets a direct case: two validators
  on two spies each run their own `EXPLAIN`. `uv run ruff check .` and
  `uv run pytest` clean — 115 passed, same 15 pre-existing findings.
- [x] T035c Tests for the factories and the pins (**packet P1c**):
  `build_toolset` produces independent toolsets for two `as_of` values in
  parallel; the production defaults are the factories' own output, not a second
  construction path; `just pins` fails on a moved hash. → T029, T030, T034
  Done: `tests/assistant/test_factories_and_pins.py`, 19 cases. The parallel case
  runs two toolsets' **sub-agent queriers** — `toolset[0].agent.tools[1]`, the
  deepest point a context's binding has to reach — through `asyncio.gather` over
  interleaved calls, four each, and checks both counts against an independent raw
  query on the pinned file (T035a/T035b's rule: never against a second view). It
  asserts up front that the two expected counts differ, so a fixture that stopped
  discriminating fails loudly instead of passing vacuously.
  Each binding is asserted from the **far side**, since a test that only walked
  the object graph would pass against a tool that holds a context and then reads
  a singleton anyway: the weather tool is checked by the `today` a faked
  `fetch_daily_weather` was handed (two contexts asking `past_days=1` get
  2025-12-31 and 2026-03-14), the GR2L tool by a spy executor recording that the
  seed query reached `ctx.db`, and the clock is shown to be read *per call* by
  moving a mutable closure between two calls on one tool.
  "No second construction path" gets a case with teeth of its own:
  `hasattr(weather_module, "get_weather_forecast_tool")` is asserted **false** —
  a module-level tool is exactly the unbound singleton this seam removes — plus
  `production_context()` being the same object every call, holding `site_now`
  and `SETTINGS_EXECUTOR` themselves, and `root_agent` matching a fresh
  `build_root_agent(production_context())` name for name and docstring for
  docstring while sharing no tool objects with it.
  Both docstring failure modes are covered from both sides: a candidate's text
  reaches the ADK *declaration* (not merely `__doc__`), an omitted component
  keeps the production wording, an unknown tool name raises, and
  `build_root_agent(docstrings=…, tools=…)` raises.
  **Five mutations were run to check the tests bite**, not assumed: binding the
  toolset's sub-agent to `SETTINGS_EXECUTOR` fails the parallel case alone;
  restoring `site_now()` in the weather wrapper fails the window case; seeding
  GR2L from `get_duckdb_executor()` fails the seed case; making `build_toolset`
  drop the docstrings fails the declaration case; and making `check_pins` never
  return 1 fails the moved-hash case. Each mutation fails exactly the case that
  claims that seam. `uv run ruff check .` and `uv run pytest` clean — 134 passed,
  same 15 pre-existing findings.
- [x] T036 Smoke-run the chat UI against the refactored service and confirm the
  prose answer path is unchanged. → T030
  Done, against the **live** model (`openai/qwen3.6-35b-a3b` on the configured
  endpoint) over the real `create_bootstrap` app — the real `root_agent`, ADK
  runner and `LiteLlm`, a throwaway SQLite session database, and the exact HTTP
  path the frontend drives: `POST /admin/users` → `POST /auth/login` →
  `POST /conversations` → `POST /` with an AG-UI `RunAgentInput` and a bearer
  token, reading the SSE stream. Three questions, one per path:
  a bare prose question ("what can you help me with?") streamed
  `RUN_STARTED … TEXT_MESSAGE_* … RUN_FINISHED` with **no tool call** and a
  full capability answer; "the highest soil temperature ever recorded" routed to
  `text_to_sql_agent`, which built and ran SQL against the warehouse and answered
  56.38 °C on 2025-07-02; and "how much rain fell in the past 3 days" called
  `get_weather_forecast_tool` and answered 4.8 mm over Aug 17–19 with the
  per-day breakdown. No `RUN_ERROR` on any of the three, and the conversation
  messages endpoint returned the persisted turns each time.
  Two couplings that this packet could have broken silently were checked in the
  persisted ADK events rather than inferred. **FR15 enrichment still fires**: the
  `text_to_sql_agent` function response carries `results` with `columns`/`rows`
  alongside `sql`/`reasoning`, which is what `TextToSqlAgentTool` merges from the
  querier's session-state stash — and the querier is now a per-context closure, so
  this is the proof that the state key survived the move. And **the tool name the
  frontend keys on is unchanged**: `web/components/ChatView.tsx` renders on
  `name: "text_to_sql_agent"`, and that is the name in the stream.
  Scope of the claim, stated exactly: this drives the service the chat UI talks
  to, not a browser. No file under `web/` changed in this packet, and the
  endpoints, payload shape and event types it consumes are the ones it consumed
  before. The one deliberate behaviour change on the production path is T033's:
  those calls now send `temperature=0` and the pinned seed where they previously
  took the provider's defaults.

**Exit:** two `ScenarioContext`s with different `as_of` run concurrently in one
process and neither sees the other's data or clock.

---

## Phase 2 — Tool completeness (weather + GR2L)

- [x] T040 Resolve `past_days` / `forecast_days` to absolute dates against
  `ctx.as_of` in both wrappers before any client call. Malformed windows — end
  before start, negative counts, unparseable dates — are `error` /
  `invalid_argument`; nothing else in window validity is an error. → T031
  Done. The resolution half was already in place from T029 (both wrappers call
  `resolve_window(..., today=…)` before any client), so what this row actually
  changed is **what counts as malformed**: `_MAX_PAST_DAYS = 92` and
  `_MAX_FORECAST_DAYS = 16` are deleted along with `_validate_count`'s `maximum`
  parameter, and the check is now type-and-sign only. Both caps were window
  *reach*, not window *form*: `decisions.md` § Window resolution and the scenario
  clock rejects a numeric back-window cap outright (no source imposes one), and
  the forward cap is a scope limit the wrapper reports as `not_available`
  (T041) — raising `InvalidWindowError` for either would hand the abstention
  metric a false positive on an answerable question. The three surviving
  malformed classes are exactly the row's: unparseable date, negative count,
  end-before-start (including the pair of zero counts that selects no days).
  Both wrappers now read `ctx.as_of.date()` rather than `ctx.clock().date()`.
  Identical value — `as_of` is the property over the same callable — but it is
  the name the row, §3.3 and §5 all use for the thing a window resolves against,
  and it reads per call exactly as before.
  Two agent-facing docstrings lost the stale `(0-92)` on `past_days`, in
  `weather.py` and `gr2l.py`; `forecast_days`' `(0-16)` stays, because after
  T041 that bound is real — it is just typed `not_available` instead of `error`.
  `uv run ruff check .` and `uv run pytest` clean — 137 passed, the same 15
  pre-existing findings, all in `notebooks/`, `scripts/count_tokens.py` and
  `src/experiments/`.
- [x] T041 Typed `not_available` on `get_weather_forecast_tool` for the **single**
  scope limit: a well-formed window whose end lies more than 16 days past
  `ctx.as_of`. No back-window cap, no coverage class, no cutoff class — the old
  three-class split is deleted, not narrowed (`decisions.md § Window resolution and
  the scenario clock`). Enforced against `ctx.as_of` and asserted by a harness
  test. → T040
  Done. `FORECAST_HORIZON_DAYS = 16` and `beyond_horizon(end_date, as_of)` in
  `weather_client.py`; the wrapper checks it between `resolve_window` and the
  fetch and returns `NotAvailableResult`. The check runs on the **resolved**
  window, so one rule binds both window forms — `forecast_days=18` and an
  explicit end date 17 days out abstain identically, and neither is a count
  comparison. There was no three-class split left to delete: T040 had already
  removed `_MAX_FORECAST_DAYS`, and the back-window and coverage classes never
  existed in code (the old `_choose_backend` cutoff is a *routing* read, not an
  abstention, and T045 deletes it).
  **Asserted here, not by a harness test**, because there is no harness yet —
  P6a builds it. Four tests in `test_weather_window.py`, whose scope this widens
  from `resolve_window` to "what window the tool accepts, and against what":
  the boundary is exact (`as_of + 16` succeeds, `as_of + 17` abstains); no
  source is reached on the abstaining call, so a `not_available` can never be a
  disguised fetch failure; the relative form is bound by the same check; and a
  400-day back window still succeeds, since only the forward side is bounded.
  The `as_of` used (2026-03-15) puts both sides of the boundary in the real
  past, so a wrapper reading a wall clock would pass neither test.
  Two docstring additions, both load-bearing for the agent rather than cosmetic:
  the `Returns` block now names the `not_available` branch and tells the agent to
  report it rather than retry with different arguments, and `forecast_days` says
  outright that nothing exists more than 16 days ahead. **`gr2l.py` is
  deliberately not given the same check** — the row names one tool, and T054's
  beyond-horizon test belongs to the packet that owns that wrapper's argument
  surface; `beyond_horizon` is written as a free function so wiring it there is
  one line.
  `uv run ruff check .` and `uv run pytest` clean — 141 passed, same 15
  pre-existing findings.
- [x] T042 `StationWeatherSource` in the pure layer: derive `DailyWeatherRow`s
  from `wetter` per `weather_tool.md` § Station source — per-field aggregation, the
  `tn` estimator, the `−7999` sentinel filter, UTC→Europe/Berlin **before**
  grouping, and complete days only. Served uncorrected: no calibration, no gap
  filling, no per-day fallback. → T020
  Done, in `tools/weather_station.py`. Two methods, both taking their data from
  the executor handed in at construction and nothing else: `daily_rows(start,
  end)` derives the window's complete days, `record_bounds()` reports the first
  and last complete day the *executor* can see. Splitting them that way keeps
  the coverage decision out of this class — T043's composite owns routing, this
  owns arithmetic. Values are served unrounded: the derivation is pinned, so a
  rounding step would be part of the pin, and `weather_tool.md` specifies none.
  **The day boundary is one expression, not one per site.** `site.py` gains
  `site_day_expr(column)`, the naive-UTC → `SITE_TIMEZONE` day conversion
  `decisions.md` § The day boundary requires be written once; the semantic
  layer, the plot tool and the oracles read the same helper as they land. Both
  queries here group on it, never on the raw column — the two groupings differ
  on 106 of the record's 482 days by up to 6.664 mm of rain, and 2025-04-20's
  22:00–23:30 UTC rain (6.664 mm) lands in Berlin's 2025-04-21, where the
  lysimeters recorded it.
  **The sentinel filter is a sign test, not an equality test, and this is a
  finding.** `findings.md` names `−7999`, and 69 rows carry it exactly — but the
  half-hourly values are themselves means of finer samples, so a half-hour that
  mixed sentinel and real readings lands anywhere between: the record holds
  `−7954.5`, `−3365.9`, `−54.99` and eleven others, 88 negative rows in all
  across 10 UTC days (18 of 48 on 2025-08-28). An equality filter would leave 19
  of them in, and one `−3365.9` among 48 samples puts that day's mean wind near
  `−70` m/s — a plausible-looking number in a required field. Wind speed cannot
  be negative, so `>= 0` is both the correct rule and a strict superset of the
  sentinel; it cannot discard a real reading. Counting only samples below
  `−1000` reproduces the "9 days" figure in the packet brief; the tenth day
  (2025-09-25) carries three contaminated half-hours in the hundreds.
  **Completeness is exactly 48 rows**, which under local-day grouping also drops
  the fall-back Sunday (2025-10-26 carries 50 half-hours, a genuine 25-hour
  day). A 25-hour day is as far from the specified count as a 23-hour one, and
  the alternative — a DST-aware expected count — is machinery no document asks
  for; the consequence is conservative, since an unserved day sends its window
  to the Archive whole rather than mixing provenance. One further exclusion the
  spec does not name but the sentinel filter creates: a day whose every wind
  sample is a sentinel has no `w`, and `DailyWeatherRow.w` is required, so the
  `HAVING` drops it. No day in the record hits it (the worst is 18 of 48), but a
  null in a required field is not a failure worth discovering at validation.
  Under this rule the record's complete days run **2025-01-02 → 2026-04-26**,
  477 of them; the two edge days are partial in local time (the record starts
  2025-01-01 00:00 UTC = 01:00 Berlin and ends 2026-04-27 09:00 UTC).
  Served uncorrected, as the row requires: no calibration factor for the ~26 %
  shortwave offset, though the pyranometer overlap makes it computable — which
  is precisely `decisions.md` § No fitted correction between the instrument and
  the oracle's case — no gap filling, and no per-day fallback.
  `uv run ruff check .` and `uv run pytest` clean — 141 passed, same 15
  pre-existing findings. Tests for the derivation are T055's.
- [x] T043 Composite `WeatherClient` at layer 2, constructed with the case's
  as-of executor: the station serves when the record covers the **whole** window,
  tested through the as-of view; everything else, including every window reaching
  past `as_of`, falls to Archive whole. Every window has exactly one provenance;
  the response echoes the source. → T042, T022
  Done. `CompositeWeatherClient` and `make_weather_client(db, cache)` in
  `weather_client.py`. Coverage is one arithmetic comparison over one query:
  `daily_rows` returns only complete days, so `len(rows) == (end - start).days +
  1` **is** whole-window coverage, and a partly covered window falls through
  with no second query and no per-day patching. Nothing tests a date against a
  hardcoded record span — the executor is the only thing consulted, so a case's
  as-of view is what decides, and a window past `as_of` finds nothing there and
  goes to Archive without a special case for the future.
  Both construction paths now fill: `ScenarioContext(...)` passes
  `make_weather_client` its own `db` and `cache` (the parameter, formerly
  `Callable[[Any, Any], Any]`, is now the named `WeatherClientFactory`, and
  `bound`'s `weather`/`cache`/`db` are typed too, via a `TYPE_CHECKING` import
  so `context.py` still pulls no httpx at import), and `production_context()`
  builds the same composite over `SETTINGS_EXECUTOR`. Both wrappers now read
  `ctx.weather`, and `fetch_daily_weather` leaves the tool layer entirely.
  Two consequences worth naming.
  **`ArchiveWeatherClient`'s cache became optional, and production passes
  `None`.** A committed entry is keyed on absolute dates, so on an advancing
  clock it would keep serving the *forecast* a window once returned after those
  same days had become observations — harmless for a frozen case, wrong for the
  running service. `cache=None` fetches live and records nothing, the same shape
  T023 gave `run_gr2l`.
  **`schemas.py` was touched, minimally and unavoidably.** `WeatherResult.backend`
  is now `source: Literal["station", "forecast", "archive"]` (T045 drops
  `"forecast"`). The row's "the response echoes the source" cannot be expressed
  otherwise — the field has to be able to say `station` — and "backend" was
  Open-Meteo's vocabulary for a thing that is now a mast on the roof. T046's
  deferral of the `elevation` removal to P2b stands untouched; this is a
  different field, and P2a/P2b are sequential, so there is no concurrent edit to
  conflict with. The agent-facing docstring follows the rename and now tells the
  agent to disclose a station-served window, which §3.3 requires of the answer
  and nothing else in the payload could support.
  Three tests in `test_factories_and_pins.py` stopped monkeypatching
  `fetch_daily_weather` — there is nothing there to patch — and now inject a
  `RecordingWeatherClient` through `ctx.weather`, which is a stronger assertion
  in the same shape T035c set: a wrapper that reached past its context would
  have passed the monkeypatched version. One test added: both construction paths
  produce a `CompositeWeatherClient` whose station half holds *that* context's
  executor.
  `uv run ruff check .` and `uv run pytest` clean — 142 passed, same 15
  pre-existing findings.
- [x] T044 Station windows bypass the response cache entirely — a pure function of
  the pinned DB, so record, replay and off are all no-ops there and a miss must not
  raise. The record's first and last complete day join `eval/pins.json`, read from
  the DB rather than hardcoded. → T021, T043, T034
  Done. The bypass is **structural, not a rule anyone has to remember**: the
  coverage test runs first and the Archive half — the only object here holding a
  cache — is reached only once it has failed, so a station window computes no
  key, records no entry and cannot raise a miss. Nothing is lost by it either;
  the station is a pure function of the pinned database, which
  `water_duckdb_sha256` already covers, so an entry would be a second copy of a
  thing already pinned.
  Making the three modes *expressible* is what this row actually added:
  `ArchiveWeatherClient` gains `allow_live`, threaded into `cache.fetch(...)`,
  and `make_weather_client(db, cache, allow_live=…)` passes it to the Archive
  half alone — the station has no channel to call out, so there is nothing to
  disable. **Off** is `cache=None` (T043, production's binding), **record** is a
  cache with `allow_live=True`, **replay** is `allow_live=False`. The fourth
  combination, replay with no cache, has nothing to replay from and raises at
  construction rather than degrading into a live call.
  The pin fills `station_derivation`, the slot §5 already named, with all four
  of the things §5 lists: a sha256 over the per-field aggregation, the day
  expression and the completeness-plus-sentinel predicate (`derivation_pin()` in
  `weather_station.py`), plus `first_complete_day` and `last_complete_day` read
  through `StationWeatherSource.record_bounds()` against `data/water.duckdb`
  itself. Reading them rather than transcribing `findings.md` is the point: a
  hardcoded span would keep passing after an ingest that moved the record's
  edges, and the DB hash cannot see the derivation at all. Committed values:
  **2025-01-02 → 2026-04-26**, unbounded by any `as_of` — this is the record's
  extent, not a case's view of it. `just pins` now reports 8 pinned, 8 unpinned,
  0 moved.
  `uv run ruff check .` and `uv run pytest` clean — 142 passed, same 15
  pre-existing findings. The replay assertion the row implies is T055's.
- [x] T045 [P] Delete the Forecast backend and `_FORECAST_PAST_LIMIT_DAYS`: with
  two sources chosen from the window, the third backend and its wall-clock cutoff
  have no caller. → T043
  Done. Gone: `FORECAST_URL`, `_FORECAST_PAST_LIMIT_DAYS`, `_choose_backend`, and
  `fetch_daily_weather`'s `force_archive` flag — the flag existed only to bypass
  the selector, so deleting the selector deletes its own workaround.
  **`fetch_daily_weather` lost its `today` parameter outright**, which is the
  real prize here. T031 could only make that argument conditionally required (a
  `TypeError` raised when `force_archive` was unset), because `_choose_backend`
  genuinely needed a date; with one endpoint the function has no use for one, so
  the wall-clock guarantee stops being a runtime check and becomes a property of
  the signature. `site.py`'s docstring is updated accordingly: the client "takes
  no clock of its own at all" now, rather than taking one as an explicit
  argument.
  `backend` also stops being threaded through `_transpose`, `_upstream_error`
  and `OpenMeteoError`, which each carried it only to name which endpoint had
  failed. `ARCHIVE_SOURCE` replaces the computed value at the one construction
  site, and `WeatherResult.source`'s literal narrows to
  `"station" | "archive"` — the pairing T043's rename set up, now that no code
  path can produce `"forecast"`.
  `_DEFAULT_FORECAST_DAYS` **stays**: despite the name it is the bare-call window
  ("the coming week"), a `resolve_window` concern with no connection to the
  retired endpoint.
  One stale mention left deliberately: `weather_tool.md` § Notes & limits names
  `_choose_backend` while explaining that the Forecast backend's 64-day reach is
  "now moot: the Forecast backend is retired". The sentence is explicitly
  historical and already states the outcome this task implements, and the file
  is outside this packet's edit set.
  `uv run ruff check .` and `uv run pytest` clean — 142 passed, same 15
  pre-existing findings; `just pins` unmoved.
- [x] T046 Remove `WeatherResult.elevation` from `schemas.py` — the client cannot
  know the surveyed height. The weather wrapper composes the site's own
  `latitude` / `longitude` / `elevation` into its agent-facing payload; consumers
  needing `hoehe_nn` take it from `site.py` explicitly. **Lands in packet P2b,
  not P2a**, though it is a weather change: `schemas.py` is otherwise T052's
  file, and one packet per file is what keeps the two halves of P2 sequential
  rather than conflicting. → T043
  Done. The field is gone from `WeatherResult`, and with it both places that
  filled it: `fetch_daily_weather` no longer reads Open-Meteo's `elevation` key
  at all, and the station half no longer restates `SITE_ELEVATION_M` (the import
  left `weather_client.py` with it). Neither *knew* the surveyed height — one
  reported a ~1 km cell, the other a value it had copied from `site.py` — so the
  removal takes out a field that was a guess on one path and a duplicate on the
  other.
  **The wrapper composes rather than overrides.** `weather.py` used
  `model_copy(update=…)`, which needs the field to exist to overwrite it; it now
  builds the payload with `model_dump()` and adds `elevation` beside the two
  coordinates it still overrides. The agent-facing shape is byte-identical to
  before — same three keys, same values — which is what keeps this a schema
  change and not a contract change.
  `GreenRoofBalanceResult` needed nothing: `gr2l.py` already took `hoehe_nn`
  from `site.py` directly (`resolve_roof_parameters(..., hoehe_nn=SITE_ELEVATION_M)`)
  and never read the weather result's copy, so "consumers take it from `site.py`
  explicitly" was already true of the only consumer there was.
  Three test doubles dropped the field from their `WeatherResult(...)` calls —
  the two `SpyArchive`s and `RecordingWeatherClient`. That they had to is the
  useful part: pydantic rejects the unknown keyword, so nothing can keep
  constructing a `WeatherResult` with an elevation it invented.
  `uv run ruff check .` and `uv run pytest` clean — 171 passed, same 15
  pre-existing findings; `just pins` unmoved at 8 pinned, 8 unpinned, 0 moved.
- [x] T047 Seed rule `seed_at = min(window_start, as_of)` in
  `swc.latest_measured_swc`, with staleness flagging beyond 7 days and the
  never-substitute-a-default rule (`not_available`, never a generic value). Every
  seeded component uses this one rule. → T024
  Done. `swc.seed_bound(window_start, as_of)` is the rule as a named function,
  and `latest_measured_swc` gained a **required keyword-only** `as_of` so no
  caller can seed without stating its cut. The staleness flag and the
  never-substitute rule were already in place from the original wrapper; what
  this row adds is the `as_of` half of the bound.
  **Why the rule lives here and not in the executor.** A case's `AsOfQueryExecutor`
  already hides post-cut rows, so for the tool path the second half is belt and
  braces. It is not redundant for the *other* caller: T110's oracles import this
  very function and may hand it an unbounded connection, and then the executor
  enforces nothing. Verified against the real DB with exactly that — an
  unbounded `DuckDbQueryExecutor` — where a window opening 2026-06-01 at `as_of`
  2026-03-10 12:00 Berlin seeds from the 11:00 UTC reading that day rather than
  from the record's true last reading on 2026-04-24.
  **The bound is an instant, compared as one.** `as_of` is converted to naive UTC
  inside `seed_bound`, not by the caller — `decisions.md` § The as-of cut's
  reason, since the `swc.timestamp` column is naive UTC and an aware value
  compared against it renders in the host's session timezone. `window_start`
  contributes its own last instant (`23:59:59.999999`), so a window opening today
  is still seeded from a reading taken earlier today; the retrospective check
  above returns the 23:30 reading with `age_days=0`.
  **Age stays measured against the window start**, which is what `SwcSeed.age_days`
  has always documented and what makes staleness disclose the right thing: the
  forecast case above is seeded at its cut and reports `age_days=83`,
  `is_stale=True` — the roof will have moved on by the time the window opens, and
  that is precisely what the answer has to say.
  Two smaller consequences. `MeasuredSwc` carries `seed_at`, so the rule is
  observable rather than inferred from which row came back — two cases differing
  only in `as_of` can show *why* they were seeded differently. And the
  no-seed message now names the seed bound's day rather than the window's; when
  `as_of` is the binding half the two differ, and the old wording sent the agent
  looking for a reading that does exist.
  `uv run ruff check .` and `uv run pytest` clean — 171 passed, same 15
  pre-existing findings. Tests for both halves are T054's.
- [x] T048 `forcings={"precip": {"2026-07-22": 50.0}}` applied to the fetched rows
  before the GR2L request: sparse, keyed by the row's own field names, validated
  against the window, echoed in the response for argument checking. → T040
  Done. `forcings: dict[str, dict[str, float]] | None` on the tool,
  `_normalize_forcings` / `_apply_forcings` beside it, and `forcings` echoed on
  `GreenRoofBalanceResult`. Since P2a the rows it overlays are the ones
  `ctx.weather` returned, so a counterfactual is applied to the same station or
  Archive forcing the standalone weather tool would report for that window.
  **The vocabulary is derived, not listed.** `FORCEABLE_FIELDS` is
  `DailyWeatherRow.model_fields` minus `Date`, so the "one vocabulary" the
  decision record asks for is enforced by construction rather than by a hand-kept
  list that a field rename could desynchronize silently. All seven value fields
  are forceable; `Date` is the key a forcing is addressed *by*.
  **Validation is genuinely pre-I/O**, against the resolved window and before
  `ctx.weather.fetch`, so a malformed counterfactual costs no upstream hop —
  which is also what makes it classifiable as `invalid_argument` in T052. Five
  faults are separated: an unknown field (echoing the valid list, as §3's
  `invalid_argument` rule requires), a day outside the window, an unparseable
  day, a non-numeric value, and a non-mapping shape.
  **Sparse means sparse in both directions** — an unnamed field keeps its fetched
  value on every day, an unnamed day keeps every field. Verified end to end:
  forcing `precip` and `tm` on one day of a seven-day station window leaves
  2025-06-11's `precip` at its measured 0.0 and moves only the named day.
  Two consequences the row does not spell out but the arithmetic requires.
  `_summarize` now runs over the **overlaid** rows, so a counterfactual's
  retention is against its own rain (56.92 mm total on the check above, not the
  6.92 mm actually measured) — summarizing the fetched rows would have reported
  retention against rain the model never saw. And a forced day the fetch did not
  return is reported rather than dropped: an override that silently does nothing
  is the one failure mode a sparse overlay can hide.
  `uv run ruff check .` and `uv run pytest` clean — 171 passed, same 15
  pre-existing findings. Tests are T054's.
- [x] T049 `evaluate_against_measured=True`: join the predicted `swc_pct` series to
  the `swc` as-of view and return mean and max |predicted − measured| in %θ over
  the overlap, plus the overlap window. → T024, T047
  Done. `swc.daily_mean_swc` is the measured series, `_compare_to_measured` the
  join, `MeasuredComparison` the payload, and `evaluation` a field on
  `GreenRoofBalanceResult` that is `null` unless the caller asked.
  **The comparison series is grouped by `site_day_expr`**, the same day boundary
  the station derivation and every oracle use. Grouping the raw UTC column
  instead would attribute part of every deviation to the boundary rather than to
  the model — the same failure `decisions.md` § The day boundary records for
  rain, arriving here through the join key instead of through a sum.
  **It reads `ctx.db`, so the comparison is bounded at the same cut as the seed.**
  A case cannot be scored against readings taken after its own `as_of`; that the
  join is on the day itself means the overlap is simply whatever both series
  hold, with no special case for the forecast tail.
  `_SENSOR_UNRELIABLE_FROM` applies here too: a flat-lined column is not a
  measurement to score a prediction against, and reusing the seed's own bound
  keeps one answer to "is this sensor trustworthy on this day".
  **Zero overlap is an outcome, not a failure.** §3.4's `not_available` triggers
  are the two roofs and a missing seed, and this is neither — a forecast window
  simply has nothing measured yet — so the run succeeds and `evaluation` carries
  `days: 0` with a `reason` instead of statistics. Reporting a deviation of 0.0
  over an empty overlap is the one answer that would be actively wrong.
  Verified against a hand computation: a seven-day station window with a
  deterministic ramped `Ssub` gives `mean 7.75` / `max 12.23` %θ, and the same
  numbers come out of `statistics.fmean` over `mm_to_theta_pct` applied in
  Python to the same days.
  `uv run ruff check .` and `uv run pytest` clean — 171 passed, same 15
  pre-existing findings. Tests are T054's.
- [x] T050 Bounded series in **both** wrappers: cap the daily series at 31 days,
  beyond which return the summary plus weekly aggregates and set a truncation flag.
  An absolute Archive window is otherwise unbounded. → T040
  Done. `tools/series.py` holds `MAX_SERIES_DAYS = 31`, `WEEK_DAYS`, and the two
  aggregations; `WeatherPeriod` / `RoofPeriod` carry them; both wrappers set
  `truncated` and empty `data` past the cap. One module for both, because "the
  same cap in both wrappers" is only true if there is one cap.
  **The bound is on the response, never on the computation.** GR2L still runs
  every day — the balance carries state day to day, so a capped simulation would
  be a *different* simulation — and the summary, the retention totals and T049's
  measured comparison are all still derived from the full series. Only what the
  model reads back is bounded.
  **Each field is aggregated the way that field is defined**, which for the
  weather row is the station derivation's own rule: `tx` the span's hottest day,
  `tn` its coldest night, `precip` and `gs` totals, the rest means. A weekly `tx`
  as a mean of daily maxima would be a number no instrument ever recorded. For
  the roof rows fluxes accumulate and states average, with `min_swc_pct` kept
  beside the mean because a mean water content hides exactly the day a drought
  question asks about, and day 1's null flux terms are skipped rather than read
  as zeros.
  **Fixed-size buckets from the window's first day, not ISO weeks** — an ISO
  bucketing makes the first and last bucket's length depend on which weekday the
  window opened, so two windows of equal length would summarize differently. The
  last bucket is short and reports its own `days` (a 32-day window gives four
  sevens and a four).
  The weather tool gained a whole-window `summary` too, since §3.3 asks for
  "summary statistics **plus** weekly aggregates" and `WeatherResult` had no
  summary at all; GR2L's `summary` already covered the window. It is the same
  function over one bucket, so the summary can never disagree with the weeks it
  summarizes. Both are `null` on an untruncated response, which keeps the
  common-case payload byte-identical to before this row.
  Verified at the boundary: 31 days returns 31 daily rows with `truncated: false`
  and no aggregates; 32 returns none, `truncated: true`, and five buckets.
  `uv run ruff check .` and `uv run pytest` clean — 171 passed, same 15
  pre-existing findings. Tests are T054's.
- [x] T051 Wetland out of scope (plan §2.4): add the wetland and its aliases to
  `NON_MODELLABLE_ROOFS`, delete `MM_ONLY_ROOFS` and the mm-only branch, leave the
  `wetland` preset in `ROOF_PRESETS` unchanged and unreachable so the preset pin
  does not move, and normalize `roof_type` **once at entry** instead of at one call
  site out of five. A test pins that `roof_type` is not a `Literal`.
  Done, all four halves. `NON_MODELLABLE_ROOFS` now carries two alias groups with
  **distinct reasons** — the gravel roof's "no substrate" and the wetland's own,
  which names both causes the specification gives (the %θ contract cannot
  describe a store whose sensor saturates below the ponding height, and that
  sensor has been dead since 2026-03-12). One shared reason string would have
  told a researcher asking about the wetland that it has no substrate layer,
  which is false.
  Wetland aliases mirror the gravel group's shape — database, German and
  abbreviated names: `wetland`, `wetland_roof`, `sumpf`, `sumpfdach`, `sumpf2`,
  `qwetland`. Verified: `wetland`, `Sumpf2` and `" QWetland "` all abstain with
  the wetland's reason.
  **`MM_ONLY_ROOFS` is gone and `_to_days` lost its `roof_type` parameter with
  it** — the second output shape existed only for the roof that no longer
  arrives, so the branch went rather than being kept for something unreachable.
  `swc_pct` stays optional purely for the null `Ssub` an older model build could
  return, and the schema descriptions that promised "null for the wetland" now
  say what is actually true.
  **Normalization moved to entry, and it was a real defect, not tidying.**
  `" Semi_Intensive "` used to pass the scope check (which lowercased) and then
  fail as an unknown roof type (which did not) — the same request answered
  `not_available` or `error` depending on which line read it. `roof_type =
  normalize_roof_type(roof_type)` runs once and every lookup below it — the scope
  table, the presets, the SH, the soil-moisture column, the echoed `roof_type` —
  uses the result. The echo is therefore the *resolved* type, which is what
  `gr2l_tool.md` already claimed.
  **The `wetland` preset is retained, unchanged and unreachable**, and there is a
  test asserting its exact values: they are pinned as `gr2l_roof_presets_sha256`,
  so deleting them would move a pin — and with it the comparability the GR2L
  canary exists for — in exchange for removing a branch no layer-1 call takes.
  `just pins` confirms it unmoved.
  The `roof_type` pin is asserted **twice, from both sides**:
  `typing.get_type_hints` says the annotation is `str`, and the ADK declaration
  ADK would actually send carries `{"type": "string"}` with no `enum` anywhere in
  it. The annotation check alone would miss a declaration built from a `Field`
  constraint or a hand-written schema, and the declaration is what the model
  reads — which is the whole reason §3.4 pins this.
  The agent-facing docstring follows the behaviour: three modellable roofs, both
  abstentions named, and an explicit instruction to *name the roof the user
  asked about* even when it is one of the two — without which the abstention
  family cannot be asked at all.
  `uv run ruff check .` and `uv run pytest` clean — 174 passed, same 15
  pre-existing findings; `just pins` unmoved at 8 pinned, 8 unpinned, 0 moved.
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
- [x] T055 Tests for the station path: per-field derivation against hand-computed
  values; a rain event straddling local midnight landing in the right Berlin day;
  sentinel exclusion from the wind mean; incomplete-day exclusion; routing at both
  record edges; partial coverage falling to Archive whole; a future window never
  resolving to the station; `source == "station"` echoed end to end through GR2L;
  and a retrospective station case completing in replay with **zero** cache entries
  and zero live calls. → T042, T043, T044
  Done, 29 tests in `tests/assistant/test_weather_station.py`, all nine clauses
  covered. Everything runs against the real pinned `data/water.duckdb`: the
  station *is* that file, and a fixture would pin an aggregation over rows this
  deployment never serves.
  **Hand-computed means hand-computed.** `_raw_half_hours()` pulls the raw
  half-hourly rows with no `GROUP BY` and no `AT TIME ZONE`, buckets them into
  Berlin days with `zoneinfo`, and `_hand_derive()` restates
  `weather_tool.md` § Station source in plain Python (`statistics.fmean`, `max`,
  `min`, `sum`). So the day boundary is checked against an independent
  implementation of the same rule rather than against a second copy of the
  query's own SQL — the T035b convention, applied to arithmetic instead of row
  counts. Agreement is to `rel=1e-12`; the two differ only by float summation
  order. Four days are checked field by field, chosen to be individually
  diagnostic: a quiet winter day where a plainly wrong field has nowhere to
  hide, the two worst sentinel days, and a high-summer day whose `gs` is an
  order of magnitude above the rest, so a radiation conversion off by 10³ cannot
  pass as plausible.
  **The sentinel test found the sign filter's real justification.** 2025-09-25
  carries six contaminated wind samples and **not one** equal to `−7999`; an
  equality filter — the value `findings.md` names — leaves all six in and puts
  that day's mean wind at about −799 km/h. That day is now a test of its own,
  next to 2025-08-28, where the unfiltered mean is about −10,418 km/h.
  The straddle test asserts the *migration*, not just the total: 2025-04-20's
  last four UTC half-hours carry 6.664 mm, and Berlin's 21st equals UTC's 21st
  plus exactly that. It also pins the consequence the decision record cares
  about — under UTC grouping the 20th is the wetter day, under Berlin grouping
  the 21st is, so the peak-day argmax genuinely moves.
  Routing is asserted from the far side throughout: a `SpyArchive` records the
  window it was handed, so "fell to Archive **whole**" is checked as one call
  for the entire span rather than inferred. Both record edges are inclusive
  (2025-01-02 and 2026-04-26 serve); one day either side falls through; so does
  a window containing 2026-03-29, the spring-forward Sunday inside the `as_of`
  band. `test_coverage_is_tested_through_the_as_of_view_not_the_record` is the
  mechanism test the packet brief asks for: at `as_of` 2026-03-15 11:00 both
  windows lie inside the *record* and only the earlier lies inside the *view*,
  so a coverage test written against the record's true end would serve both from
  the station and leak post-`as_of` observation into the second. The future
  window then needs no special case at all — past the cut the view is simply
  empty.
  The replay test asserts all three clauses (`status == success`,
  `source == station`, and `list(cache_dir.iterdir()) == []`) with
  `fetch_daily_weather` monkeypatched to raise, so "zero live calls" is proved
  rather than assumed. Its companion moves the same window one day past the
  record and asserts `CacheMissError` — without which the first test could pass
  against a client that simply never calls out.
  **`GreenRoofBalanceResult` gained `weather_source`**, which is what "echoed end
  to end through GR2L" requires: the run had no way to report its own forcing
  before. The test asserts the rows `run_gr2l` received are the station's own
  rows, so the field is the forcing rather than a label. Same `schemas.py`
  caveat as T043's, and for the same reason — T046 is still P2b's.
  **Verified by mutation, not just by passing.** Eight mutants of the
  implementation, each caught by at least three tests: grouping in UTC; the
  sentinel filtered by equality; `tn` as `min(Tmean)`; serving partial days;
  wind left in m/s; radiation left unconverted; partial coverage served by the
  station; and the station never serving at all.
  `uv run ruff check .` and `uv run pytest` clean — 171 passed, same 15
  pre-existing findings; `just pins` unmoved at 8 pinned, 8 unpinned, 0 moved.

- [ ] T056 **Owed by P2a.** Sync `weather_tool.md` to the weather behaviour that
  landed: drop "(planned)" from the station-source heading and the banner, state
  the **two** sources and that resolution is code's and never the agent's, the
  16-day horizon as the tool's single typed scope limit, the retired Forecast
  backend and its constant, and the derivation as built rather than as designed.
  The file already carries uncommitted D-number removals; finish that pass here
  rather than beside it. → T042, T043, T045
- [ ] T057 Sync `gr2l_tool.md` to what P2b lands: the wetland in
  `NON_MODELLABLE_ROOFS` and out of layer-1 scope, the `min(window_start, as_of)`
  seed rule with its staleness flag, `forcings` and `evaluate_against_measured`,
  the 31-day series cap, and the `invalid_argument` / `upstream` split. Its
  pending working-tree edits belong to this pass too. → T051, T052, T054

**Exit:** every §3.3 and §3.4 outcome is reachable and tested, no wrapper reads a
wall clock, and neither weather nor GR2L spec contradicts the code.

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

- [ ] T071 Sync `irrigation_tool.md` to what P3 lands: the millimetre balance and
  what the `100/SH_mm` rescaling moves, the decision-diff list as the disclosure
  it is, the reason-code ladder as implemented, and the R endpoint recorded as a
  deliverable **outside** this testbed rather than as pending work — its pending
  working-tree edits belong to this pass. → T065, T066, T067

**Exit:** the port reproduces the deployed controller before the unit fix, the
diff list exists, and the irrigation spec contradicts nothing in the code.

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
  it, and a diverging canary is a hard failure by design. **Pull forward — run it
  alongside P2b, not here.** It depends only on the canary format T023 fixes, it
  is the one task that can block a whole phase on something outside this
  repository, and discovering the service is unreachable during T116 costs a
  capture session rather than a five-minute check. Listed in P7 because that is
  where its output is consumed. → T023, T034
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

**101 tasks** across nine phases, 15 of them parallelizable, executed as **23
packets** — one session each, mapped above.

| Phase | Tasks | Parallelizable | Packets | Gates |
|---|---|---|---|---|
| P0 specification reconciliation | 11 | 6 | 1 | T010 blocks every ground-truth writer |
| P1 injection seam | 19 | 4 | 3 | blocks P2–P8 entirely |
| P2 tool completeness | 18 | 2 | 2 | blocks P5, P7 |
| P3 rules | 12 | 2 | 2 | blocks P4 |
| P4 cards | 5 | — | 1 | — |
| P5 plotting | 10 | 1 | 2 | — |
| P6 harness and pilot | 8 | — | 4 | **T107 freezes the testbed** |
| P7 oracles and generation | 7 | — | 5 | T115, pulled forward to P2b, gates capture |
| P8 optimizer | 10 | — | 3 | — |
| final | 1 | — | — | — |

**Parallel opportunities.** P0's documentation edits (T001–T005, T008) touch six
different files and run together. Inside P1, T024, T025 and T026 are independent
seams over the same context. P3 and P4 depend on P1 but not on P2, so the rules
and card track can run beside the weather and GR2L track; P5 needs both. Within
P2, T045 and T053 are independent cleanups — T046 is not, since `schemas.py` is
T052's file.

**The one irreversible edge** is T107. Two of its prerequisites — T105 and T106 —
edit text that is byte-stable afterwards, so neither can be deferred past the
gate.
