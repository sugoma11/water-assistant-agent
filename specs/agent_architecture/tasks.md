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
| **P3a** roof table, ET0, constants | T060–T062 | arch §1 principle 4, §3.5; `irrigation_tool.md § Units`, `§ Which extensive roof is which`; `findings.md § Not every roof is instrumented`, `§ The lysimeter collection area`, `§ External sources on this machine`; `GR2L_function.R:35-74` in the weinbau checkout | one roof table feeds `swc` and the presets with no value changes; ET0 matches the R routine term for term |
| **P3b** bucket, faithfulness, unit fix, tool | T063–T067, T070, T071 | `irrigation_tool.md` in full; `decisions.md § The irrigation calculator`, `§ No fitted correction between the instrument and the oracle`; `findings.md § External sources on this machine`; `smart_irrigation.py` | the port reproduces the deployed controller **before** the unit fix; the diff list exists and is committed |
| **P4** cards | T081 **first**, then T068, T069, T080, T082–T084 | arch §3.2; `decisions.md § Retrieval` | a lookup case in replay with zero cache entries and zero live calls; every drift test green |
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
  43/281 = 15.3 % (12.0 / 12.0 / 28.6 per split) — **superseded on the abstention
  line only** by plan §8's Q4, which made T24a's series spec a sampled variant and
  moved the count to 45/281 = 16.0 % (13.0 / 12.8 / 28.6); the ledger, the eight
  shares and everything else below stand as recorded. §4's abstention and
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
- [x] T052 `ErrorResult.error_type: "invalid_argument" | "upstream"` in
  `schemas.py`, plus a classification pass over every catch site: pre-I/O
  validation and malformed windows are `invalid_argument`; fetch, seed, GR2L,
  configuration and the wrapper catch-alls are `upstream`. The two populations are
  already separated by exception type, so this is tagging, not analysis.
  Done. `ErrorType` is a named alias in `schemas.py` and `error_type` is
  **required — no default**, which is what makes this a classification pass
  rather than a field: every one of the sixteen construction sites had to state
  which population it belongs to, and a seventeenth added later cannot default
  into the excluded one. Defaulting to `upstream` would have made every future
  argument fault silently unscored; defaulting to `invalid_argument` would have
  scored candidates for outages.
  Sixteen sites, thirteen in `gr2l.py` and three in `weather.py`. Five
  `invalid_argument`: unknown roof type, albedo range, `initial_soil_moisture_pct`
  range, malformed window (both wrappers), malformed `forcings`. Eleven
  `upstream`: the two `WeatherFetchError`s and two fetch catch-alls, an empty
  weather response, a forced day the source did not return, the seed read's
  catch-all, the measured-comparison read, `Gr2lConfigError`, and the GR2L
  catch-all.
  The row's premise held — the two populations really were already separated by
  exception type, and the tagging follows the `try` boundaries exactly. The one
  judgement call is the **empty weather response** and the **missing forced day**:
  neither is an exception, but both are the source failing to return what the
  window asked for, which is not something a different argument would fix.
  Two sites are deliberately **not** tagged. `SwcUnavailableError` and the two
  unmodellable roofs are `NotAvailableResult`, not errors at all — mixing a
  scope limit into either population is exactly what would make §7's
  false-abstention metric uninterpretable. And `warehouse.py` builds its own
  status dict inside the **frozen** sub-agent (§3.1); it is not one of the
  three-outcome tool wrappers, and P2b does not open that file.
  Both agent-facing docstrings now say what the two mean in behavioural terms —
  `invalid_argument` is correctable and retryable, `upstream` is to be reported
  as a system-side problem — since the agent sees the field and would otherwise
  have to guess whether to retry.
  Verified across all seven reachable paths with a weather client that raises:
  each returns the expected tag.
  `uv run ruff check .` and `uv run pytest` clean — 174 passed, same 15
  pre-existing findings.
- [x] T053 [P] Update the production `ROOT_INSTRUCTION`: three modellable
  segments, not four; the wetland joins the gravel roof as measured-only. → T051
  Done, two bullets in `agents/root_agent/agent.py`. The roof bullet names three
  modellable segments and both abstentions with their own reasons; the units
  bullet loses "the wetland roof reports millimetres only", which described an
  output shape T051 deleted.
  One sentence added beyond the row's text, and it is load-bearing: **"Still pass
  the roof the user asked about to the tool."** Without it the instruction reads
  as "do not call the tool for those two", and family I — the abstention asked in
  the gravel and wetland aliases — would be answered by the *agent's* prior
  instead of by the tool's typed `not_available`, which is the thing being
  scored. It is the instruction-level counterpart of T051's `str` annotation:
  both exist so the model can name a roof the tool declines.
  This is production text, so it is also a seed candidate for T120's registry;
  what it says about scope has to be true of the code as of this packet, which
  is why the row sits inside P2b rather than beside it.
  `uv run ruff check .` and `uv run pytest` clean — 174 passed, same 15
  pre-existing findings.
- [x] T054 Tests for `gr2l`, `weather` and `swc`: %θ↔mm round-trip per roof;
  gravel and wetland → `not_available`; no trustworthy seed → `not_available`;
  stale-seed flag; seed-day retention flag; forcings application and echo;
  `evaluate_against_measured` arithmetic; beyond-horizon → `not_available`; each
  error site's `error_type`. → T041, T047, T048, T049, T051, T052
  Done, 83 tests in `tests/assistant/test_gr2l_tool.py`, every clause of the list
  covered, against the real pinned `data/water.duckdb` on the same T055
  reasoning: the seed and the measured comparison *are* that file. Only the two
  things outside the repository are replaced, and each by a **recording** double,
  so every claim about what reached the model is asserted from the far side.
  **Two production lines this row's own list required, both previously deferred
  *to* it.** T041's note hands over the horizon check explicitly — "`gr2l.py` is
  deliberately not given the same check … T054's beyond-horizon test belongs to
  the packet that owns that wrapper's argument surface" — so `beyond_horizon` is
  now wired into the GR2L wrapper between window resolution and the fetch, with
  its own `not_available` reason. And the packet's exit criterion ("a model case
  in replay issues no live call") is unreachable while the wrapper calls
  `run_gr2l` with no cache: T023 built the mechanism and left the call site
  alone as out-of-packet, and this is the packet that owns `gr2l.py`, so the hop
  now passes `cache=ctx.cache`. Production is unaffected — its context's cache is
  `None`, which is the same direct call as before.
  **The replay test records, then replays.** The first pass fetches through a
  fake service and commits both the data entry and the canary; the second runs
  with that service raising on any POST, and the two results are asserted
  *equal*. So the hit path is proved to issue nothing — including no canary,
  which is right: there is no service to have moved when nothing is asked of it.
  Its companion runs the identical setup with an empty cache and asserts the run
  fails, without which the first could be evidence about a service that is never
  called rather than about a cache that is hit. The weather half is the real
  composite over a station window, so that side needs no entry at all (T044).
  **Verified by mutation.** Twelve mutants, all caught: no cache on the model
  hop; `seed_bound` ignoring `as_of`; ignoring the window start; the summary over
  the fetched rows; the model run on the fetched rows; no horizon check; the cap
  off by one; a weekly `tx` as a mean of maxima; the comparison grouped in UTC;
  forcings not applied; the stale threshold never firing; argument faults tagged
  `upstream`.
  **Two of them survived the first pass, and both were real gaps.** The `as_of`
  half of the seed rule cannot be demonstrated through `ctx.db` at all — the
  as-of view has already hidden the later rows, so the test passed either way —
  which is exactly the oracle case T047 put the rule in the function for; there
  is now a test against an **unbounded** `DuckDbQueryExecutor`, and it is the one
  that fails when the `as_of` half is removed. And the cap tests asserted bucket
  edges, which hold under any per-field rule, so the aggregation itself is now
  asserted on values that differ: a weekly `tx` is the week's hottest day and
  day 1's null flux is skipped rather than read as a zero.
  Expected values are hand-computed in Python throughout. The measured
  comparison is joined against daily means bucketed into Berlin days with
  `zoneinfo` — no `GROUP BY`, no `AT TIME ZONE` — so the day boundary is checked
  against an independent implementation, and the seed against a raw
  `ORDER BY timestamp DESC LIMIT 1` read straight from the file.
  Nine `invalid_argument` sites and eight `upstream` ones are covered, with the
  argument faults additionally asserted to have reached **no** weather client and
  **no** model — which is what "before any I/O" means and the only way the
  classification is checkable rather than declarative.
  Two existing doubles gained `**kwargs` (`test_factories_and_pins`,
  `test_weather_station`) because the wrapper now passes `cache=`. That they had
  to is the useful part: a fake with a fixed arity is what noticed the call site
  had changed.
  **One gap left open and named, not fixed here:** `run_gr2l` has no `allow_live`
  channel, so in a replay pass a GR2L *miss* would call out live rather than
  raising, where the weather half raises `CacheMissError` (T044 gave
  `make_weather_client` that flag). Nothing in P2b's list asks for it, and P7d's
  T116 is where a capture pass would discover it; noted here so it is not
  rediscovered from scratch.
  `uv run ruff check .` and `uv run pytest` clean — 254 passed, same 15
  pre-existing findings; `just pins` unmoved at 8 pinned, 8 unpinned, 0 moved.
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
- [x] T057 Sync `gr2l_tool.md` to what P2b lands: the wetland in
  `NON_MODELLABLE_ROOFS` and out of layer-1 scope, the `min(window_start, as_of)`
  seed rule with its staleness flag, `forcings` and `evaluate_against_measured`,
  the 31-day series cap, and the `invalid_argument` / `upstream` split. Its
  pending working-tree edits belong to this pass too. → T051, T052, T054
  Done, all five, plus three new sections — "Counterfactual weather", "Comparing
  a run with the measurement", "The series is bounded" — because each argument
  needs a place to say *why* it behaves as it does, not just that it exists.
  The pending working-tree edits (the wetland-scope pass) went in with T051,
  which is where the behaviour they describe landed; a `git add -A` over the
  tools directory carried them a commit early. See the repair note below.
  **The routing paragraph was rewritten even though the row does not name it.**
  It still described the Open-Meteo Forecast backend and a "🔜 Planned" station
  source — both of which P2a shipped and retired respectively — and Phase 2's
  exit is that *neither* weather nor GR2L spec contradicts the code. It now
  states the two sources, that resolution is code's, that every window has one
  provenance, and the 16-day horizon as a `not_available`. `weather_tool.md` is
  T056's and was left alone.
  Three other things the file claimed that the code no longer does: the tool
  "calls Open-Meteo via `fetch_daily_weather`" (it goes through `ctx.weather`),
  the seed is "at or before the day the window opens" (now the `min` rule, with
  both halves' failure modes written out), and the outcomes list omitted
  `weather_source`, `forcings`, `truncated`/`weekly` and `evaluation`.
  The `roof_type`-is-not-a-`Literal` rule is now stated in the file itself, as a
  warning beside the signature. It was only in the architecture document, and
  this file is where someone editing the signature would look.
  `age_days` gets a sentence of its own: it is measured against the window start
  rather than the seed bound, which is what makes a forecast seeded at today's
  cut disclose how stale it *will* be. That distinction is invisible from the
  field name and was the one part of T047 a reader could get backwards.
  Anchors checked mechanically after the edit: 23 headings, 10 internal links,
  none dangling.
  **Repair, recorded here because it crosses tasks.** T051's commit also swept in
  the unrelated pending edits to `weather_tool.md` (T056's, owed by P2a) and
  `irrigation_tool.md` (P3's). Both files are restored to their pre-packet state
  in a follow-up commit and returned to the working tree as the uncommitted edits
  they were, so each still lands with the task that owns it.
  `uv run ruff check .` and `uv run pytest` clean — 254 passed, same 15
  pre-existing findings.

**Exit:** every §3.3 and §3.4 outcome is reachable and tested, no wrapper reads a
wall clock, and neither weather nor GR2L spec contradicts the code.

---

## Phase 3 — Rules, single source of truth

- [x] T060 `assistant/tools/roofs.py`: one table carrying each roof's canonical
  name, DE/EN labels, site ids, substrate height, lysimeter area, per-column
  plausibility bounds, alias set, and **its column in each of the five tables,
  absent where the roof is not instrumented**. Repoint `swc.ROOF_SWC_COLUMNS` and
  `gr2l_client.ROOF_PRESETS` at it with no value changes, so the pins are
  untouched. The catalog's sampling pools are read off this table. → T051
  Done. Five `RoofSegment` entries; both projections are now one-line
  comprehensions over `MODELLED_ROOFS`, and `gr2l_roof_presets_sha256` is
  byte-identical at `77e80a23…`.
  **The literal types in the presets are load-bearing, which the repoint nearly
  lost.** `Ssubmax` is `90` for the wetland and `16.0` for the non-irrigated
  extensive roof; canonical JSON writes those as `90` and `16.0`, so promoting
  either to a float moves the pin while leaving every `==` assertion green. The
  values are written with the types they were captured with, the fields are
  annotated `float` in the numeric-tower sense, and a test recomputes the sha256
  the way `check_pins.py` does — dict equality cannot see this, and the GR2L
  canary's comparability hangs off it.
  **Gravel is what separates "a roof" from "a modelled roof".** It was absent
  from `ROOF_SWC_COLUMNS` and from `ROOF_PRESETS` by two independently
  maintained key lists; it is now absent from both because its `gr2l` preset is
  `None` — no substrate, so no substrate-water state. Its `substrate_height_cm`
  is `None` for the same reason rather than `0.0`: a depth of zero is a number
  where there is no quantity. The wetland shows the other axis — it *has* a
  preset and is still declined at layer 1 — so modellability and layer-1 scope
  stay two questions with two answers.
  **The semi-intensive roof's absence is a missing key, not a null**, in
  `outflow` and `radiation` both, so a caller reading a column it does not have
  raises where it reads instead of carrying a `None` onwards. `roofs_with_column`
  makes P1/P1f fall out of the data: five roofs on `swc`/`tsoil`, four on
  `outflow`/`radiation`, and the pools §1.6 hand-lists are now derivable.
  `wetter` carries no roof column at all — it is the station, shared — which the
  module says explicitly so its absence is not read as a sixth gap.
  **`LYSIMETER_AREA_M2 = 1.0` is carried once and derived, not transcribed
  five times**: `lysimeter_area_m2` returns it exactly when the roof has an
  `outflow` column, so the uninstrumented roof cannot acquire a collection area
  by copy-paste. That is the whole content of "litres are millimetres, and no
  area factor exists anywhere".
  **The plausibility bounds are new values, so they are derived rather than
  chosen.** Floor = half the column's healthy minimum, which lands in the gap
  between a dead sensor and the driest real reading; `findings.md`
  § Validity-predicate specificity already measured that choice as insensitive
  over a sixfold range on `QWetland`. Ceiling = the store's physical saturation
  (60 %θ substrate, 100 %θ ponded mat, 20 %θ gravel). `QGravel` gets no floor —
  a roof with no substrate reads ~0 %θ honestly. Soil temperature takes one
  envelope for all five columns; outflow's floor is hard at zero and its ceiling
  is four times the wettest day in the record. Two DB-backed tests hold them to
  that: every healthy site-day mean is inside its bounds, and the wetland floor
  flags exactly the 43 dead days `findings.md` counts. New measurement recorded
  in `findings.md` § Data record.
  **`NON_MODELLABLE_ROOFS` was left alone**, since T060 names two repoints and
  that is a third; a test asserts every one of its keys resolves through the
  table's aliases, so the two lists fail loudly rather than drift. Repointing it
  belongs with a task that owns that file's behaviour.
  `roofs_version` pinned at `1.0` — the slot `check_pins.py` was written to fill
  as this packet landed.
  `uv run ruff check .` and `uv run pytest` clean — 281 passed, same 15
  pre-existing findings; `just pins` unmoved at 10 pinned, 6 unpinned, 0 moved.
- [x] T061 [P] `assistant/et_fao56.py`: FAO-56 Penman-Monteith ET0 at albedo 0.23,
  a verbatim port of `gr2l_model/R/GR2L_function.R` in the weinbau API checkout
  (`findings.md § External sources on this machine`), with the fixed
  `Pressure <- 100` kPa simplification at `:44` carried over deliberately and
  documented as a scope limit — under 1 % of ET0 at 142 m — so the two languages
  agree rather than each being right on its own terms.
  Done, `:35-71` line by line. `et0_terms` returns every intermediate the R
  computes, in the R's own order, so the port is checkable *term for term* rather
  than only at `ET_PM` — the exit criterion is a statement about the terms, and
  two compensating errors agree at the last line.
  **The fixed pressure is the smallest of four departures from FAO-56, not the
  only one.** `Gsc = 0.0820` is defined at `:59` and never used at `:65`, so
  `R_a` runs ~12× large; `es` at `:36` divides by 238 where eq. 11 has 237.3,
  while `Delta` at `:50` uses 237.3; and `Rnl` at `:67` takes fourth powers in
  °C, halving `tn**4` alone, where eq. 39 averages both in kelvin. Measured over
  four days spread across the year, the routine returns **1.17×–1.38×** a
  textbook FAO-56, the `Gsc` omission doing most of it — an inflated `Rso` pins
  the cloudiness factor at its −0.35 floor, so net longwave acts as a small gain
  instead of tracking cloud. The fixed pressure alone moves ET0 by at most
  0.10 % here (`gamma` 0.37 % high), which is the "under 1 %" the row asks for,
  now with the other three sized beside it. All four carried, none repaired, each
  asserted by a test so a later cleanup fails loudly. Recorded in `findings.md`
  § External sources on this machine.
  **Checked against the running endpoint, not only the checkout.** The checkout's
  `run_GR2L` takes no `albedo` argument and the service does, so they are
  different builds and "matches the R" was worth pinning down: four days posted
  at `albedo=0.2` come back within 4e-5 mm of the port, the response's own
  rounding. The four rows and their served `ET_PM` are committed as a fixture —
  irrigation is meant to run fully offline (§3.5), so the suite must not call the
  service, and a test that did would stop testing the port the day the container
  is down.
  **Albedo is the one deliberate difference and is therefore a parameter.** The R
  hard-codes `1 - 0.2`; ET0 is by definition the reference crop's at 0.23 (§3.5),
  which is the default here, and passing `GR2L_ALBEDO` reproduces the R exactly.
  A test holds albedo to entering through `Rn` alone.
  Two guard rails the R does not have: an albedo outside 0-1 is refused, matching
  `resolve_roof_parameters`, and a latitude with no sunset raises instead of
  letting `acos` of an out-of-range value produce a `NaN` that would propagate
  silently through a water balance. Unreachable at 51.35° N, but the core is
  importable by an oracle.
  `uv run ruff check .` and `uv run pytest` clean — 338 passed, same 15
  pre-existing findings; `just pins` unmoved at 10 pinned, 6 unpinned, 0 moved.
- [x] T062 `assistant/rules_constants.py`: per-roof wilting / dry / capacity /
  residual authored in the site's own units and converted **once** through
  `swc.theta_pct_to_mm`; hour-based horizons; the heat threshold; the outflow
  epsilon; both dose fields. Two constants have no deployed source and are flagged
  in-module as **eval policy** — the heatwave duration rule T08 counts against, and
  T12's retention target. One module, versioned, duplicated nowhere. → T060
  Done. The %θ values are `irrigation_tool.md` § Units' and the millimetres come
  out of `swc.theta_pct_to_mm` against `roofs.py`'s substrate height — 3.5 / 7.0 /
  15.4, 2.8 / 7.0 / 15.4, 15.0 / 24.0 / 33.0, residual 1.75 and 3.75 — matching
  that table exactly. A test asserts each millimetre value *is that function of
  that height* rather than a constant that happens to agree, which is the property
  a second conversion downstream would break.
  **Three provenances, labelled, because they are three different kinds of
  claim.** Most values are the deployed controller's, carried verbatim including
  the flat 22 %θ capacity that GR2L measures differently — a test pins that
  disagreement at 12.6 mm on the semi-intensive roof rather than letting it be
  noticed later as a bug. Two are eval policy with no deployed source. One, the
  per-roof dose in millimetres, is **owed by the site** and left `None`: it is
  policy the tool states rather than computes, so a plausible number invented here
  would be indistinguishable from the site's own once an answer quotes it. The
  deployed valve minutes (30 / 30 / 31) are what a disclosure can honestly say
  meanwhile, which is why "both dose fields" is one value and one hole.
  **Both eval-policy constants were chosen against the record, then fixed.**
  Three days at 24 °C marks 53 heatwave days across six months (1 / 4 / 12 / 15 /
  16 / 5); four days empties two of those months and a 30 °C threshold leaves the
  whole record with one, which would make T08's count zero in almost every
  sampled month. The retention target is 50 % — the figure a green-roof manual
  conventionally states — and the committed `t12_rain_events.md` shows it splits
  the 41 (event, roof) pairs 25/16 with 8 of 11 events carrying both classes, the
  headroom §2's T12 entry asks for. A test reads that table rather than trusting
  the number, so a target no longer supported by the record fails. Measurement in
  `findings.md` § Data record.
  **The horizons stay in hours and `horizon_rows` does the one conversion**, so
  48 h is 2 daily rows here and 48 hourly rows at the site. It raises on a step
  coarser than the horizon instead of returning zero rows: an empty window still
  produces a decision, and that decision would look like every other one.
  `rules_constants_version` pinned at `1.0`.
  `uv run ruff check .` and `uv run pytest` clean — 359 passed, same 15
  pre-existing findings; `just pins` unmoved at 11 pinned, 5 unpinned, 0 moved.
- [x] T063 `assistant/irrigation.py`: `simulate_store` / `summarize` /
  `irrigation_decision`, pure, no LLM. Preserves the deployed controller's
  semantics exactly — one store, the stress coefficient evaluated on the previous
  step, ET applied before the cap, seed-day initialization only, and the two
  window conventions — returning **bool plus reason code plus the fixed dose
  constant**, never a computed volume. → T062, T061
  Done, `_simulate_store` and `decide_substrate_roof` line for line. All four of
  `irrigation_tool.md` § The balance's deliberate defects are carried: the lag,
  the missing floor, the seed step's ET-less initialisation with its outflow from
  the *raw* initial value, and ET before the cap. `summarize` owns the two window
  conventions and is the only place hours become rows, through T062's
  `horizon_rows`.
  **The store's unit is a parameter, not a constant.** `Regime` has one member
  here — the deployed %VWC store with millimetres added to it — and the
  millimetre arm lands in T065, so the port and the fix are two commits with the
  golden series between them, which is the whole ordering this packet exists for.
  It also gives T067 its variable: the same window through two members with the
  forcing, the ET0 and the trigger levels held fixed.
  **The stress coefficient is the same algebra, not the same arithmetic.** The
  extraction writes it over fractions (`(θ/100 − 0.025)/(0.22 − 0.025)`); over %θ
  the factor of 100 cancels and the constants are the site's own. Measured
  against the controller over 200 steps on all three roofs: `ks` agrees to
  2.2e-16 per step and the store to 3.6e-15, with the decisions and the German
  strings' rungs identical. T064 pins it.
  **`water_limited=False` is dropped, not ported.** It is the extraction's
  open-water arm and belongs to the wetland's branch, which leaves with the roof
  (`NON_MODELLABLE_ROOFS`); a parameter no call site can set is a branch nothing
  tests. `first_outflow_date` is not carried either: it scans from index 0
  against `> 0`, so it can report the seed step's artifact as the day the roof
  fills. `refill_step` reads the refill window instead and is disclosure only —
  a message, never a decision.
  **One `DecisionFeatures` for both entry points**, so the comparison chain
  exists exactly once (`irrigation_tool.md` § The rule): a simulated series
  reduced by `summarize`, or a caller's three stated values reduced by
  `features_from_stated_values`, where the refill conjunct is the rain against
  the deficit to capacity through the same `OUTFLOW_EPSILON_MM`.
  `uv run ruff check .` and `uv run pytest` clean — 359 passed, same 15
  pre-existing findings.
- [x] T064 **Faithfulness before correctness**: a golden-series test asserting the
  port reproduces the deployed controller — `smart_irrigation.py`,
  `findings.md § External sources on this machine` — element for element *in its
  original unit regime*, landing **before** the unit fix, so every later
  difference is attributable to the fix rather than to the port. Commit the
  generated series as a fixture: the controller lives outside any repository, so a
  test that reads it at run time is a test that stops running the day that file
  moves. → T063
  Done. `scripts/capture_irrigation_golden.py` runs the controller once and
  commits what it produced; `tests/assistant/test_irrigation_golden.py` replays
  the numbers and never opens `~/Downloads`. The capture goes through
  `run_water_balance` and `decide_substrate_roof` — the composition the site
  actually runs — rather than the private bucket, so what is pinned is the
  controller's own wiring.
  **The windows are hourly, and that is the whole of the ladder's half of the
  claim.** At a one-hour step the horizons in hours and the controller's
  hard-coded `[:48]` / `[1:168]` are the same slice, so the two window
  conventions are compared rather than approximated; each window is 168 rows
  exactly, one refill horizon. The forcing is the pinned record's — Berlin-hour
  rain and mean air temperature from `wetter`, seeds from each roof's own `swc`
  sensor under `latest_measured_swc` — with the day's FAO-56 ET0 spread evenly
  over its 24 hours, the fixture's own convention, stated in the script and used
  nowhere else. It needs no defending: both implementations receive the identical
  numbers.
  **Three windows walk all five rungs, and a test asserts they still do.** Hot
  and dry (2025-06-28) fires rungs 1 and 5, hot and wet (2025-07-12) rungs 1 and
  2, and warm over April-wet roofs (2025-04-16) rungs 3 and 4 — the only window
  of the record's own that reaches the middle of the ladder, found by scanning
  every 168-hour window in the record for the two rungs the first two missed. Two
  degenerate cases carry the branches the controller has and the record does not:
  an empty window, and a `NaN` in the forcing.
  **Agreement is to 3.6e-15, not to the bit, and the reason is stated rather than
  tolerated.** The extraction writes the stress coefficient over fractions
  (`(θ/100 − 0.025)/(0.22 − 0.025)`) and the port writes it over %θ; the algebra
  is identical and the arithmetic is one bit apart — `ks` 2.2e-16, and over 168
  steps the store 1.8e-15 and the outflow 3.6e-15, against an asserted 1e-9. The
  decisions are exact, and the *rung* is checked against the controller's own
  German string rather than against my labelling of it: two rungs say yes and
  three say no, so the boolean alone would pass on the right answer for the wrong
  reason.
  A second test pins the constants the capture ran with against
  `rules_constants.py`, so a threshold that moves later fails as "the fixture is
  no longer evidence" rather than surfacing as a drifted decision.
  `uv run ruff check .` and `uv run pytest` clean — 382 passed, same 15
  pre-existing findings.
- [x] T065 Carry the balance into millimetres, rescaling each roof's response to
  rain by `100/SH_mm`. The deployed trigger levels are carried verbatim as site
  policy and are **not** re-derived (`decisions.md § No fitted correction between
  the instrument and the oracle`). → T064
  Done as a second `Regime` member and a flipped default — three lines of
  behaviour, because T063 put the unit behind an argument and T064 pinned the
  port with it. `Regime.MILLIMETRES` reads the millimetre properties
  `rules_constants.py` already converts once through `swc.theta_pct_to_mm`, and
  converts the seed the same way; the levels come out 3.5 / 7.0 / 15.4, 2.8 / 7.0
  / 15.4 and 15.0 / 24.0 / 33.0 — `irrigation_tool.md` § Units' table exactly, no
  second conversion anywhere.
  **The golden series still passes, unchanged**, because it names
  `Regime.PERCENT_THETA` rather than relying on a default. That is the ordering
  paying off: the deployed regime did not become unreachable when the corrected
  one became the default, so the port stays checkable against the controller and
  T067 has its second arm.
  **Nothing was re-derived.** The trigger levels a millimetre store is compared
  against are the same site constants, converted rather than re-fitted, and the
  flat 22 %θ capacity is still flat. Deep roofs get harder to move and shallow
  ones easier — 0.7× on the 7 cm roofs, 1.5× on the 15 cm — and the decisions
  that flip are T067's deliverable, not something to absorb here.
  `theta_pct_from_store` lands with it: millimetres stay internal and the surface
  speaks %θ (`agent_architecture.md` §3.5), so the tool has one way back rather
  than a conversion of its own.
  `uv run ruff check .` and `uv run pytest` clean — 382 passed, same 15
  pre-existing findings.
- [x] T066 `calc_irrigation` ADK tool and factory: self-contained by default with
  its own seed via `swc` and forcing via `ctx.weather`; supplying soil moisture,
  max temperature and forecast rain makes the call pure — no DB, no weather, no
  simulation. Gravel, wetland and seedless windows → `not_available`; three
  outcomes with `error_type`. → T063, T029, T043
  Done, `tools/irrigation.py` + `make_irrigation_tool(ctx)`, wired into
  `build_toolset` as the fourth tool. Three bindings read per call — `ctx.as_of`
  for the day, `ctx.weather` for the forcing, `ctx.db` for the seed — and no
  service behind any of it: no cache entry, no canary, and the only `upstream`
  sites are the weather fetch and the database.
  **The window takes no date arguments and is the refill horizon from today.**
  §3.5's signature has none, and it should not: this tool answers "should we
  water it now?", so the window is 7 daily rows from `ctx.as_of` — exactly
  `horizon_rows(REFILL_HORIZON_HOURS, step_hours=24)`, so the rule's own constant
  sizes the fetch rather than a literal week.
  **All three stated values or none.** A partly stated call is an
  `invalid_argument` naming what is missing, because the two paths differ in more
  than provenance: the modelled one spends part of the forecast rain on
  evaporation before the roof fills and the stated one takes the rain at face
  value, so a half-and-half call would produce a number no disclosure could
  describe. The stated branch is entered on all three being present, which also
  narrows them to `float` before either helper sees them.
  **Same scope, different sentence.** Membership is `NON_MODELLABLE_ROOFS` — one
  set for both water-balance tools — and the roof's identity comes from
  `roofs.resolve_roof`, so there is no third list here; the *wording* is this
  rule's own. GR2L declines the wetland because ponded storage has no
  water-content contract, and the rule declines it because it has no soil store
  to read a wilting point against, which is the answer a researcher asking about
  irrigation needs.
  **Millimetres stay internal.** The payload's `min_swc_pct` comes back through
  `Regime.theta_pct_from_store`, and the daily heat test reads `tx` — a measured
  daily maximum, the stated deviation from the site's hourly mean
  (`irrigation_tool.md` § Horizons and step).
  Smoke-run against the pinned database at `as_of` 2026-04-20: the seed reads
  29.77 %θ, above the deployed flat 22 %θ capacity, so the store caps on the seed
  step and the *seed-day outflow artifact does not set* `will_reach_capacity` —
  the refill window's offset doing exactly what it exists for. Seeds above the
  deployed capacity are common in this record and T067's table will show it.
  `uv run ruff check .` and `uv run pytest` clean — 382 passed, same 15
  pre-existing findings.
- [x] T067 Decision-diff harness: replay a historical window through **both** unit
  regimes with the same Python ET0, so unit handling is the only variable, and emit
  a markdown table of every date and roof where the irrigate decision flips, with
  the driving feature values. This is the evidence the site re-tunes against, and
  the bound on what the testbed's irrigation answers say about the deployed
  system. Commit the emitted table beside the script, the way T007's rain-event
  count is committed — the list is the deliverable, not the run. → T065
  Done. `scripts/irrigation_decision_diff.py` and the committed
  `specs/agent_architecture/irrigation_decision_diff.md`.
  **19 of 930 decisions flip — 2.0 %** — over the catalog's `as_of` band,
  2025-06-01 → 2026-04-24, 310 days × 3 roofs (18 band days uncoverable, all of
  them windows running past the station record's end). The millimetre balance
  irrigates on 8 the deployed one does not and declines on 11 it does. Both arms
  take the same station rows, the same `et_fao56` ET0, the same measured seed and
  the same trigger levels: `Regime` is the only argument that changes, which is
  what T063 put it there for.
  **Both arms are reported in %θ, and that is what makes the table readable.**
  The millimetre thresholds *are* the %θ ones converted, so expressed in %θ the
  two arms are compared against identical numbers and a flip is never a threshold
  moving — only a trajectory. The rescaling is visible directly: 1.43 %θ per mm
  on the 7 cm roofs against the deployed 1.0, and 0.67 on the 15 cm.
  **The dominant pair is `cooling_requested → refill_forecast`, 8 of 19**, all on
  the two extensive roofs: a shallow roof fills 1.43× faster in millimetres, so
  rain the deployed balance did not think would refill it now does, and the rule
  waits instead of watering. The semi-intensive roof moves the other way — three
  flips are `refill_forecast → cooling_requested` — because 0.67× makes the same
  rain insufficient. Deep roofs get harder to move and shallow ones easier, which
  is the physics the fix restores, seen in decisions.
  **The forcing is what the station recorded, not the forecast the site had.**
  Both arms see the identical rows, so forecast error is held out of the
  comparison rather than shared unequally between them.
  The number lives in that file and is not restated in `findings.md`: the table
  *is* the measurement, and a second copy is a second thing to keep true. T071
  cites it as the disclosure it is.
  `uv run ruff check .` and `uv run pytest` clean — 382 passed, same 15
  pre-existing findings.
- [x] T068 **Ordered after T081**, whose decision fixes this function's source
  list. `values_for(card_id)`: project `rules_constants.py` and `roofs.py` into
  each `provenance: rendered` card's `values:` / `applies_to:` / `not_applicable:`
  blocks, plus the drift test asserting the committed card equals it and a
  `just cards-check` that prints the correct block on failure. A test, not a
  writer — hand prose and generated values share one file. → T081, T062, T060
  Done in `knowledge/rendered.py`, six of the seven projections — T069 adds
  `roof_reference_ranges`. T081's answer held: two sources, no third shape.
  **The drift assertion is equality both ways, and that is what makes a card's
  silences enforceable.** `irrigation_dose` is the case that needs it: the
  per-roof depth is owed by the site, so `dose_mm` is rendered only where one
  exists and is absent from every roof today. A one-way assertion would let a
  card invent a plausible depth and pass, and an invented depth is
  indistinguishable from the site's own once an answer quotes it back.
  **Scope blocks are derived per card, which is the only reason they come out
  different.** The four irrigation cards take `ROOFS` minus `ROOF_RULES` — gravel
  and the wetland, each with the reason `NON_MODELLABLE_ROOFS` gives — while
  `retention_target` takes `roofs_with_column("outflow")` and excludes the
  *semi-intensive* roof instead, on instrumentation rather than substrate. A
  hand-listed exclusion would have got that wrong in one direction or the other.
  **Two splits worth recording.** Field capacity sits on `substrate_hydraulics`
  and not on `irrigation_threshold`: it is the store's property, not a trigger,
  and the split is also what keeps either card from being named after a single
  constant. And GR2L's per-roof storage stays out of the hydraulics card
  although `roofs.py` carries it — the flat capacity disagrees with it
  deliberately, and both numbers in one block would let an answer quote one as
  the other.
  **The eval-policy marker is per key**, listing inside `values:` which of them
  are this thesis's rather than the site's, because `heatwave_definition` mixes
  the two — the threshold is the deployed controller's and only the duration is
  ours. `rules_constants.py` asks for exactly this and it had nowhere to land
  until there was a card to land in.
  **Millimetres are rounded once, here**, or field capacity reaches a card as
  `15.400000000000001`. Safe only because nothing reads a number back out of a
  card — every consumer imports the constant — and the drift test compares
  rounded against rounded, so rounding is not a place drift can hide.
  `just cards-check` reports three states and never raises: drifted (prints the
  paste-ready block), missing, and — until T069 — no-projection, so one
  unfinished projection cannot hide the other six.
  `uv run ruff check .` clean; the suite is red by construction, see T082.
- [x] T069 [P] Derive the `roof_reference_ranges` values (normal / low / high per
  roof segment) from the `swc` record under the same drift discipline. → T068
  Done — three contiguous bands in %θ per roof, and **all four edges are
  imported**: the floor and ceiling are the roof's `swc` plausibility bounds
  (`roofs.py`, derived from the column's healthy range over the record) and the
  two interior cuts are the rule's dry threshold and field capacity. Nothing here
  is a new number, which is what keeps a reference range from becoming a fifth
  statement of the site's policy.
  **A roof needs both sources to have a band**, so the projection intersects them
  rather than assuming the rule's roofs are instrumented. Gravel and the wetland
  have an `swc` column — they are measurable and can be asked about — and no dry
  threshold to cut at, so they carry the exclusion instead of an empty band.
  **The bands are the site's policy, not the record's distribution, and the
  semi-intensive roof is where that shows.** Classified over the whole record it
  splits 211 low / 89 normal / 302 high of 602 site-days: half its days sit above
  a field-capacity edge that is the deployed flat 22 %θ where GR2L measures 30.4
  for that roof. Bands fitted to the record would have hidden the flat-capacity
  convention; these disclose it. Measured into `findings.md § Where the flat
  field capacity puts the semi-intensive roof`.
  **The record test asserts non-degeneracy, not agreement** — every band carries
  days of the actual record on every roof, so a `normal` band nothing falls in
  could not ship, while the record stays free to sit lopsidedly inside edges the
  site chose.
  `uv run ruff check .` clean; the suite is red by construction, see T082.
- [x] T070 Tests: the ladder's reason codes in priority order; the stated-value
  path issuing no I/O at all; `not_available` for both non-modellable roofs; unit
  conversion round-trips; and an irrigation case completing in replay with zero
  cache entries and zero live calls. → T066
  Done, 58 tests in `tests/assistant/test_irrigation_tool.py`, every clause of
  the row plus the error taxonomy's three outcomes.
  **Priority, not correctness.** Each rung is tested against a window that
  satisfies *every rung below it as well* — rung 1's row is below wilting **and**
  cold **and** refilling — so a ladder whose comparisons were individually right
  in the wrong order fails. A separate test pins the two boundaries the seed can
  land exactly on: `<=` at the wilting point, `>` at the dry threshold, both the
  controller's way round.
  **The unit fix is asserted as a conversion.** Each of the four millimetre
  thresholds is asserted to *be* the %θ one through `theta_pct_to_mm`, in both
  directions, and the rescaling is measured directly: one millimetre of rain with
  no ET moves the store 1.43 %θ on a 7 cm roof and 0.67 on the 15 cm one, against
  exactly 1.0 in the deployed regime whatever the depth. The payload's
  `min_swc_pct` is checked on the roof where a leaked millimetre value would show
  (8 %θ is 12 mm on the semi-intensive).
  **The stated path is asserted from the far side.** Both collaborators raise on
  use, so "no I/O" is proved rather than inferred from the answer being right —
  which is the whole of what T16b measures, since a call that quietly fetched
  would still return the right boolean.
  **The replay clause is a stated-value case, and the reason is worth recording.**
  A modelled call's window opens at `ctx.as_of` and runs a week forward, and the
  station is read *through the as-of view*, so the station can never cover it: the
  forcing falls to Archive whole and takes one cache entry. Verified — the
  modelled path in replay with an empty cache returns `upstream`. So T16b is the
  irrigation shape with zero cache entries, while T07 and T11 each replay from one
  weather entry, which is what T116 captures. `agent_architecture.md` §3.5's "no
  cache entry" is about the calculator, not about the forcing; T071 states that
  distinction in `irrigation_tool.md` rather than leaving it to be rediscovered.
  `uv run ruff check .` and `uv run pytest` clean — 440 passed, same 15
  pre-existing findings.
- [x] T071 Sync `irrigation_tool.md` to what P3 lands: the millimetre balance and
  what the `100/SH_mm` rescaling moves, the decision-diff list as the disclosure
  it is, the reason-code ladder as implemented, and the R endpoint recorded as a
  deliverable **outside** this testbed rather than as pending work — its pending
  working-tree edits belong to this pass. → T065, T066, T067
  Done, and the working-tree edits are committed with it — they were the D-id
  dereferencing and the wetland's exit from scope, both true of the code as of
  this packet.
  **A new section, *The decisions this moves*,** carries the disclosure: 19 of
  930, the dominant `cooling_requested → refill_forecast` pair and why the
  semi-intensive roof moves the other way, pointing at the committed table rather
  than restating it. § Units gains the rescaling as the two figures a reader can
  check — 1.43 %θ per mm at 7 cm, 0.67 at 15 cm, against a flat 1.0 in the
  deployed balance — and the fact that millimetres are what runs while the
  deployed regime stays reachable for the replay.
  **A *tool surface* section** the file did not have: the signature, why it takes
  no dates, the payload, all-or-none on the stated arguments, and the three
  `not_available` triggers.
  **Three corrections where the spec had drifted from the code**, which is what
  the packet exit asks for beyond the row's list. The ET0 bullet named the fixed
  pressure as *the* departure from FAO-56; T061 measured four, and the routine
  returns 1.17–1.38× a textbook FAO-56 — that is now stated with the sentence
  that makes it deliberate, since the site's thresholds were tuned against this
  convention's ET. The balance's pseudo-code still carried the `ks = 1.0`
  open-water arm, which left with the wetland. And "fully offline" needed
  splitting: the *calculator* is offline, and a modelled call still fetches a
  forcing that cannot be a station window, because its own window opens at the
  case's `as_of`.
  `first_outflow_date`'s omission is recorded in § What is dropped with its
  reason — it can name the seed step's artifact as the refill day — because it is
  the one dropped function whose absence is a correction rather than a scope cut.
  Left alone deliberately: `agent_architecture.md` §0's status table still reads
  "to build" for this row. It is stale for every landed packet — weather still
  says "no station source" after P2a, GR2L "no `forcings`" after P2b — so
  repairing this one row would make the table *more* misleading, not less. It
  wants one pass of its own.
  `rumdl check` clean on the file; `uv run ruff check .` and `uv run pytest`
  clean — 440 passed, same 15 pre-existing findings.

**Exit:** the port reproduces the deployed controller before the unit fix, the
diff list exists, and the irrigation spec contradicts nothing in the code.

---

## Phase 4 — Reference cards

- [x] T081 **Runs first in P4, before T068.** Decide `data_freshness`'s
  provenance (plan §8 Q1): either `static`, with the record dates carried by the
  station-derivation pin, or `rendered`, with `values_for()` gaining a pinned-DB
  source — `rendered` is defined against `rules_constants.py` and `roofs.py`, and
  this card's values are record dates, which is the whole question. Its drift test
  is meaningless until this is settled, and both T068's source list and T080's
  provenance field follow from it. Record the decision in `decisions.md § Retrieval`
  and close plan §8's Q1.
  Decided **`static`**, and the reason is §3.2's own last bullet: the card store
  is specified as a pure function of the files §5 hashes — no network, no cache
  entry, no `upstream` class — and a pinned-DB source inside `values_for()` is a
  database read in the middle of that promise. Letting only the *test* open the
  database is worse, not better: the card's guarantee would then rest on a file
  the store itself may not read.
  **What it buys is thin, and that is the argument.** A drift test compares a
  committed card against a source; here the source is a frozen database whose
  sha256 is already a pin, so the test re-checks what `just pins` checks, and
  fails in the same batch for the same reason.
  **What `static` costs is a mis-transcription**, which no pin catches — a wrong
  date authored on day one stays wrong. So the check moves rather than
  disappearing: the pin check already opens the database and already carries the
  station derivation, so it verifies the card's dates against the tables there.
  That obligation belongs to T082's pins half; it is written into `check_pins.py`
  with the card-store sha256, not into `knowledge/`.
  **The consequence T068 was waiting for:** `values_for()` has exactly two
  sources and no third shape to carry. The consequence for T080: the ten other
  cards keep their listed provenance and `data_freshness` joins `roof_directory`,
  `sensor_reference` and `et0_method` as the fourth `static` card — with the
  distinction that it is pinned *elsewhere* rather than unpinned, which its entry
  states.
  Landed in `decisions.md § Retrieval` (a rejected alternative and a validity
  condition fixing `rendered` at two sources), plan §4 and §8's Q1 both closed,
  and `agent_architecture.md` §3.2's static list corrected from three cards to
  four — that last one outside the task's letter, because leaving §3.2 naming
  three static cards would have made the decision a contradiction the moment it
  was recorded.
- [x] T080 Author `assistant/knowledge/cards/*.yaml` — **11 cards**, one file each,
  matching the §3.2 enum exactly: `irrigation_rule`, `irrigation_threshold`,
  `substrate_hydraulics`, `irrigation_dose`, `heatwave_definition`,
  `retention_target`, `roof_reference_ranges` (rendered); `roof_directory`,
  `sensor_reference`, `et0_method`, and `data_freshness` — **`static`**, per
  T081 — the four that carry no drift test.
  Hand-authored `text:` carrying **no numerals**; no card named after a single
  constant. `irrigation_rule` and `irrigation_threshold` must together answer T16a
  without the calculator, and `irrigation_threshold.not_applicable` must state the
  wetland's exclusion or T17b is silently answerable. → T081, T068, T069
  All eleven landed; T082's twelve failures are green and `just cards-check`
  reports seven of seven current. **Not one `values:` block was typed.** Each
  rendered card was assembled as hand-written head plus `values_for(id).as_yaml()`
  appended verbatim, so a card could not start out drifted and the first run of
  the drift test had nothing to catch — which is also the honest reading of that
  run: it confirms the paste, and only a later edit makes it a real check.
  **T17b's mechanism ended up split across the two authorships, and it had to
  be.** `not_applicable`'s wetland reason is generated, so the words "no
  soil-moisture threshold exists" could not be authored into the block; what the
  block states is *why* there is no substrate store. The prose therefore names
  the block and draws the conclusion from it — "a segment listed as not
  applicable below has no soil-moisture irrigation threshold at all… none to be
  inferred from the segments above". Stating the absence only in the block would
  have required editing `_NO_SOIL_STORE` in T068's `rendered.py`, which changes
  what six other cards say to change what one of them needed.
  **The no-numerals rule bit twice in places worth recording**, both resolved by
  naming the constant instead of quoting it: `et0_method` cannot write "FAO-56"
  and says "the published standard", and `sensor_reference` cannot write the wind
  sentinel and says "a large negative sentinel value". Neither loses anything a
  reader needs, which is evidence for the rule rather than against it.
  **`roof_directory.applies_to` is the one hand-typed roof list in the store.**
  Static, so no drift test holds it to `ROOFS`, and a sixth segment would leave
  it stale in silence. Accepted rather than fixed here: a projection for it means
  adding to `RENDERED_TOPICS`, which T081 fixed at seven.
  `data_freshness`'s ten dates were **read out of the pinned database and checked
  against the card**, not transcribed from `findings.md` — the mis-transcription
  T081 named as `static`'s only cost, closed on day one. The standing obligation
  is unchanged and still T082's: the pin check must re-verify them, because
  nothing in `knowledge/` will notice if the database is re-pinned.
- [ ] T082 `assistant/knowledge/store.py`: pydantic `Card` model, loader over the
  packaged YAML, `enum == card keys` test, the T068 drift test wired in, and the
  card-store sha256 into `eval/pins.json`. Pure, ADK-free, no I/O beyond the
  packaged files. Add `pyyaml` to `[project] dependencies`. → T080, T034
  **Part landed: the model, the loader, `enum == card keys`, and the no-numerals
  rule pulled forward from T084.** `pyyaml` is a direct dependency,
  `knowledge/cards/` ships empty. Outstanding: the card-store sha256 into
  `eval/pins.json` — which now also carries T081's obligation to verify
  `data_freshness`'s dates against the tables, since the pin check is where the
  database is already open.
  **The store-wide tests are driven by `CARD_TOPICS`, not by the files.**
  Parametrizing over what is on disk would make an empty store collect zero
  tests and report green — a vacuous pass on the very rule meant to catch a
  missing card. Driven by the vocabulary, an absent card fails and names itself.
  So this packet is red by construction until T080: 12 failures, 11 of them one
  per unauthored card plus `enum == card keys`, each naming T080.
  `extra="forbid"` earns its place on the T17b path: `not_applicible` would
  otherwise load as a card with no exclusions, and the probe would go silently
  answerable rather than erroring.
- [x] T083 `lookup_reference` ADK tool and factory: `topic: Literal[…]` in the
  **signature** so the vocabulary survives any docstring rewrite, `roof` filtering
  but never suppressing `not_applicable:`, the card returned **whole**, no
  roof-scoped `not_available`, and an unknown topic returning `invalid_argument`
  echoing the valid list. → T082, T029, T052
  Landed as `tools/reference.py` with `ReferenceCardResult` in `schemas.py`, and
  the tool is `TOOL_NAMES`' fifth entry — **appended, never inserted**, because
  declaration order is prompt text and reordering the four the service has run
  with would move every candidate's prompt for nothing a result could be
  attributed to.
  **The eleven names are written out in the signature rather than built from
  `CARD_TOPICS`.** T082 called that tuple "the twin, not the original"; a literal
  generated from it would leave the test that holds the two equal asserting a
  tautology, and the tuple would have become the original after all. Checked
  against ADK rather than assumed: the declaration carries the vocabulary at
  `parameters_json_schema.properties.topic.enum`, and a candidate `__doc__` moves
  `description` while leaving that schema untouched — which is the validity
  condition's actual mechanism and now a test rather than a claim.
  **The factory takes no `ctx`, and the absence is the guarantee.** Every other
  factory closes over a `ScenarioContext` because it reads a clock, an executor
  or a weather client; this one reads packaged files, so a `ctx` it never touched
  would be an invitation to reach for `ctx.db` later. Purity is structural
  instead of promised, which is exactly what the packet's exit criterion tests.
  **What `roof` filters had to be decided, because §3.2 says both "filter" and
  "returns the card whole".** It narrows `values:` to the named segment, and only
  where the block is roof-keyed *and* has an entry for it — four cards are, the
  rest are keyed by quantity or, in `data_freshness`'s case, by table.
  `applies_to:` and `not_applicable:` are never narrowed. That is not a
  convenience: T17b asks for the wetland's soil-moisture threshold, and narrowing
  to nothing would leave the exclusion with no rule beside it, deleting half of
  the probe. So a segment the card excludes gets the block whole, and
  `values_scoped_to_roof: false` says the filter found nothing rather than
  leaving the agent to infer it.
  **An unresolvable `roof` is `invalid_argument`, which is one clause past the
  row's text and is the reading that survives.** "A known topic returns the card
  whole" rules out a roof-scoped `not_available` — a *scope* answer — and an
  unparseable spelling is not one; ignoring it would serve every segment's
  numbers to a question that named one, with nothing in the result to say the
  filter never ran. `resolve_roof` already takes the German names, the site ids
  and the column names, so what reaches the error is a spelling nothing on the
  building answers to, and the error names the five.
  **A missing card is checked in the factory, not in the tool body.** §3.2 gives
  this tool no `upstream` class, so there is no honest error to return mid-call;
  the build-time check is where `store.py` already says a broken store belongs —
  at startup, never in a trajectory — and it is the runtime counterpart of
  `enum == card keys`.
  **One edit outside the row, in `ROOT_INSTRUCTION`**, on T053's precedent: a
  routing bullet for the reference route. Without it the instruction's "Otherwise,
  respond yourself" sends every documentary question to the model's own prior,
  which T16a scores as a failure by construction — the tool would be reachable in
  the declaration and unreachable in practice. The bullet also carries the
  read-the-exclusion instruction, since the abstention on T17a/T17b is the agent
  reading a card rather than relaying a typed status, and §2's point 5 covers only
  the latter.
  `test_an_unknown_docstring_key_raises` lost `lookup_reference` as its
  not-a-tool key and took `reference_lookup` instead — a plausible *misspelling*
  of a real tool, which is the realistic version of that failure now that every
  §3 tool but the plotter exists.
  `uv run ruff check .` and `uv run pytest` clean — 532 passed, same 15
  pre-existing findings.
- [x] T084 Tests: card round-trip and schema validation; `enum == card keys`;
  every rendered card's `values:` equal to `values_for(card_id)`; no numerals in
  any `text:`; the wetland exclusion surviving `roof="wetland"` on
  `irrigation_threshold` (T17b's mechanism); unknown topic naming the valid
  topics; and a lookup case completing in replay with zero cache entries and zero
  live calls. → T083
  **Four of the seven clauses were already covered and are not repeated.** Card
  round-trip, schema validation and `enum == card keys` are T082's in
  `test_knowledge_store.py`; the no-numerals rule is there too, pulled forward by
  the same packet; the rendered-card drift assertion is T068's in
  `test_knowledge_rendered.py`, beside the projection it asserts against. Giving
  any of them a second home would have let one copy rot while the other stayed
  green. The three that had no home landed in `tests/assistant/test_lookup_reference.py`
  — 136 tests, suite now 668.
  **The row's `enum == card keys` had an open first link, and closing it is what
  this file adds beyond the three clauses.** T082 compares `CARD_TOPICS` against
  the files; nothing compared it against the `Literal` the model actually reads.
  The chain is now `signature literal == CARD_TOPICS == card files`, and the
  first link is asserted through ADK's declaration rather than by reading the
  annotation back — the validity condition is a claim about what *the framework*
  renders, so reading the annotation would only have proved Python stored it. The
  candidate-rewrite case is asserted from the attacking side: a `__doc__` that
  says "valid topics: none, ask the database" moves `description` and leaves
  `parameters_json_schema` untouched.
  **Green on the first run means nothing until the tests are made to fail**, the
  same reading T080 gave its own first green run. Nine mutations, each caught by
  the test that names it: `not_applicable` filtered by roof; `values` narrowed
  even where the roof is excluded; an unresolvable roof passing silently; a topic
  dropped from the signature literal (three tests); the factory growing a `ctx`;
  the build-time store check removed; the unknown-topic error no longer echoing
  the list; a live HTTP call; and a warehouse read through a leaked context. The
  last two fail the replay test by name — "lookup_reference made a live HTTP
  call" — rather than as a timeout.
  **One asymmetry worth recording.** The dedicated T17b test does *not* catch a
  `not_applicable` filtered down to the asked roof, because the wetland's own
  reason survives that filter; it is `test_a_scoped_lookup_never_narrows_the_scope_blocks`
  over the whole roof × card product that catches it. The dedicated test is right
  to assert only what T17b needs, and the general rule needed its own test — but
  the probe would have been the weaker of the two had it been the only one.
  `data_freshness` earns its own test for the mirror-image reason: its `values:`
  is keyed by *table*, so it is the card that would be mis-filtered first if the
  roof-keyed check ever weakened from "every key is a roof" to "any key is".
  `uv run ruff check .` and `uv run pytest` clean — 668 passed, same 15
  pre-existing findings.

---

## Phase 5 — Plotting

- [x] T090 `tools/plot.py`: `plot_timeseries(series, start, end, kind)` with no
  `agg` argument, each `SeriesSpec` naming a **source** and a variable, never data.
  `measured` runs a fixed parameterized query against the as-of view over the five
  tables through a **closed vocabulary** of columns and aggregations; derived
  quantities exist there as flags, and area-normalized outflow is not one of them —
  the vocabulary relabels the unit rather than scaling the values. → T020, T029
  **The vocabulary is a data table keyed by `(table, variable)`, not by variable.**
  Two shortwave series from two different instruments — the station's `Rad_SW` and
  a mast's `SWdown` — are two different series, and the table is half of what the
  agent declares and half of what §7 scores, so it is part of the key rather than
  a field that follows from it. Twelve entries over the five tables.
  **No entry carries a per-roof column.** `ROOFS[roof].columns[table]` is the
  column and `roofs_with_column` is which roofs a table has, so the semi-intensive
  roof is undrawable in `outflow` and `radiation` here for the one reason it is
  absent there — no lysimeter, no mast — rather than because a second list
  remembered to omit it. `radiation` carries a mast *suffix* joined by
  `radiation_column`; only `wetter` names a column outright, because the station
  belongs to no roof. That is the whole of principle 4 applied to this table.
  **The flux/state flag is drawn on accumulation, not on the physics word.**
  W/m² is a rate reported *at* an instant, so irradiance is a state: summing 48
  half-hourly readings of it would report 48× the day's mean under a unit nobody
  uses. Rain and lysimeter outflow accumulate over the interval and sum.
  **Area-normalized outflow is absent by construction** — there is one `outflow`
  entry, it reports `mm`, and it applies no factor. At a 1 m² lysimeter a litre
  *is* a millimetre (`roofs.LYSIMETER_AREA_M2`), so the entry relabels and the
  values are the record's own; a second, "normalized" entry could only differ from
  it by the multiplication that constant exists to forbid.
  **Two `wetter` columns are deliberately outside it.** `Tmax` needs a `max` and
  `windspeed` needs the logger's sentinel filtered, and both rules already exist
  once, in the station derivation — which this tool reaches as a `weather` series,
  where they arrive as `tx` and `w` (T091). Serving them here under a sum-or-mean
  would name a mean of half-hourly maxima "the daily maximum".
  **`_AS_OF_TABLES` became `AS_OF_TABLES`.** The vocabulary's table list is
  filtered through it, so a variable over a table no as-of view bounds cannot be
  added and then read past a case's cut; a private name would have meant a second
  copy of the five. `test_context.py` follows the rename.
  `uv run ruff check .` and `uv run pytest` clean — 668 passed, same 15
  pre-existing findings.
- [x] T091 `weather` and `model` series resolve through `ctx.weather` and
  `run_gr2l` — the same clients, cache, window resolution and seed rule the
  standalone tools use. A `model` series accepts `initial_soil_moisture_pct`,
  `albedo` and `forcings` and echoes them for argument checking. No new DuckDB
  connection, no new HTTP client. → T022, T023, T040, T047, T048
  **"The same seed rule" is one function, not two that agree.** `gr2l.run_roof_model`
  is the shared core the plot's `model` series runs on, and every step of it is
  the step function the standalone tool already calls: `normalize_forcings`,
  `ctx.weather`, `_apply_forcings`, `_resolve_seed` under `swc.seed_bound`,
  `resolve_roof_parameters`, `run_gr2l(cache=ctx.cache)`. It deliberately does
  **not** resolve the window or check the roof's scope, because both are answered
  differently at each call site — one chart resolves one window for every series
  on it.
  **The standalone tool was not rewritten on top of it, and the equivalence is
  tested instead.** Its per-step `error_details` are pinned by name in a dozen
  tests ("Failed to fetch weather for the roof over…"), so folding them into the
  shared core would have moved the agent-facing text of a tool this packet does
  not touch. What could still drift is the *request*, so that is what is asserted:
  `test_the_plots_model_run_is_the_standalone_tools_run` records both callers at
  the one seam GR2L is reached through and compares the rows and the parameters
  whole. A seed bound, a forcing overlay or an elevation that moved on one side
  fails there and nowhere else.
  **`_normalize_forcings` became `normalize_forcings`** for T090's reason
  (`_AS_OF_TABLES` → `AS_OF_TABLES`): a second caller means a private name would
  have become a second copy of the window check, and the forcing has to be
  validated against the *chart's* window before anything is fetched.
  **`roof` is one field across the three sources**, not §3.6's table read
  literally as `roof` for `measured` and `roof_type` for `model`. Those are the
  two tools' own argument names; inside one `SeriesSpec` with `extra="forbid"`
  they would be two spellings of one selector, one of which is always an
  `invalid_argument` — and principle 4 has exactly one roof identity, resolved
  through `roofs.resolve_roof`, whose canonical names are already
  `NON_MODELLABLE_ROOFS`' and `ROOF_PRESETS`' keys.
  **The argument check became a pre-pass over the whole plot**, which is a
  behaviour change to T090's lazy per-series loop and the reason
  `test_a_rejected_series_costs_no_query` is now
  `test_a_rejected_plot_costs_no_query_at_all`. With only `measured` series the
  difference was one wasted query; with a live source it is a GR2L request and a
  *recorded cache entry* for a chart that comes back an error and is never read.
  A plot is one deliverable, so it is one decision.
  **One chart is one weather fetch and one run per roof-and-counterfactual.**
  Re-fetching across calls is cheap because both live sources replay from the
  cache (§3.6); asking GR2L twice for the identical request inside one chart is
  not re-fetching but a second capture of one answer, so a roof's moisture drawn
  against its own runoff is one simulation.
  **The forecast horizon is inherited, and it binds only the plots that have
  one.** The same `beyond_horizon` check on the same resolved window as §3.3 and
  §3.4 — an all-measured chart of a future window is simply empty, while a
  forecast or modelled one would be a chart of weather that does not exist.
  `uv run ruff check .` and `uv run pytest` clean — 708 passed, same 15
  pre-existing findings.
- [x] T092 Echo the **resolved spec** in full: source and variable per series, the
  resolved absolute range, the derived aggregation and resolution, unit and axis
  per series, gap and truncation flags, and any modelling arguments. → T091
  **Gaps and truncation are two flags because they are two facts**, and a reader
  needs them separately. `gaps` counts days of the requested window the series
  has no point for — a sensor outage, a table whose cover starts later, the seed
  day a modelled flux is not computed on — and is the hole a chart would
  otherwise be read straight across. `truncated` says the series *stops* before
  the window does, which is the record running out or the case's as-of cut, and
  is the one a "the last week of June" question can be silently wrong about.
  Both are read off the days the points actually have, so they mean the same
  thing at either resolution and from all three sources: a half-hourly stamp and
  a daily one begin with the same ten characters.
  **`stats` exists because the model is given no values at all.** A tool that
  returns a series can leave the summarizing to the reader; this one cannot, so
  what a caveat or a sanity check has to be built from is here — the count, the
  span, and min/max/mean. `total` follows the same accumulate-or-sample rule the
  operator does and is **absent on a state**: a window's rain has a total, a
  window's soil moisture does not, and reporting one would name a sum of
  half-hourly water contents a quantity.
  **These fields are echoed and not scored**, like unit and axis before them
  (`decisions.md` § Plotting): they are properties of the data, identical across
  candidates for one case, so scoring them would inflate every candidate
  equally. What they are for is the *answer* — `truncated` and a stale `seed` are
  exactly the disclosures §2's caveat rule already makes the agent responsible
  for passing on.
  **The shape is pinned as an exact key set**, not spot-checked. "In full" is the
  requirement, and a field silently dropped from the echo is a field the scorer's
  argument checks and the answer's caveats lose at the same moment, in a way no
  assertion about the fields a test happens to name would catch.
  `uv run ruff check .` and `uv run pytest` clean — 708 passed, same 15
  pre-existing findings.
- [x] T093 Return **no series to the model** — spec, summary statistics and
  `artifact_ref` only. → T092
  **The handle is content-addressed, not minted.** `plot_<sha256(resolved
  spec)[:16]>`, so the same chart over the same window always has the same
  handle and a chart over changed data has a different one. A UUID would have
  been the obvious choice and is the wrong one twice over: it puts a value in
  the model's context that differs between two replays of one case, which is
  precisely what §5 exists to prevent, and it would make
  `replayed == recorded` — the assertion the packet's exit criterion is
  written as — impossible to state.
  **"No series" is asserted by shape rather than by name.** The test checks that
  **no field of a series is a list of anything**, which a future field holding
  values cannot evade by being called something other than `points`. The one
  list in the payload is the list of series itself; `stats.points` is a count,
  and is the only place the word survives.
  `uv run ruff check .` and `uv run pytest` clean — 708 passed, same 15
  pre-existing findings.
- [x] T094 Force daily resolution on mixed plots: any plot combining a `model` or
  `weather` series with a `measured` one aggregates the measured series to
  Europe/Berlin calendar days with that variable's derived operator, and reports
  the resolution in the spec. → T090
  **Resolution is a property of the chart; the operator is a property of the
  variable.** So `plot_resolution` reads the *set of sources* once, before any
  series is fetched, and every measured series in that plot is then read at the
  resolution it returns — while each still aggregates by its own `sum` or `mean`.
  A per-series resolution would let one series be drawn at 48 points a day
  against another's one, which is not a chart.
  **The rule is stated as "all-measured keeps half-hourly", not as "a model or
  weather series forces days".** The two agree on today's three sources and
  disagree on a fourth: a source added later is daily until someone says
  otherwise, which is the safe direction — a half-hourly series drawn against a
  daily one is the failure, not the reverse. It also makes the `weather`+`model`
  plot with no measured series at all daily without a third branch.
  **`site_timestamp_expr` is new, and `site_day_expr` is now its `::DATE`.** The
  half-hourly branch has to stamp its points in Berlin too — a sample at 23:30
  local is stored as 21:30 or 22:30 UTC, and a chart labelling it with the
  previous day while the daily view counts it in the next is the misalignment
  §8 makes a reader responsible for noticing. One expression, two callers, per
  `decisions.md` § The day boundary's own reason for the first one.
  **Both branches bound the window by the site's day**, so a half-hourly series
  and the daily series it would be redrawn as cover exactly the same span; a
  half-hourly query bounded on the raw UTC column would have shifted its own
  edges by an hour or two against the daily one's.
  `uv run ruff check .` and `uv run pytest` clean — 668 passed, same 15
  pre-existing findings.
- [x] T095 Derive unit, axis assignment and aggregation operator in code from the
  variable — fluxes sum, states average — never model-chosen. → T092
  **The derivation is the vocabulary row, not a function beside it.** Unit and
  axis are columns of `MeasuredVariable` and the operator is a property over
  `quantity`, so there is no path that produces a series without them and none
  that lets a caller supply one: the tool signature carries no `agg`, no
  `aggregation`, no `unit` and no `resolution`, which is asserted rather than
  merely intended.
  **Axis is a grouping decision, so it is its own column rather than the unit
  restated.** Rain and lysimeter outflow share `water_depth_mm` across two
  tables; relative humidity and soil moisture are both percentages and do
  **not** share one, or a humidity of 70 % would set the scale an 18 %θ soil
  moisture is read against. Surface temperature in kelvin keeps its own axis
  away from the air's Celsius. Six identities for twelve variables.
  **These fields are echoed but not scored** (`decisions.md` § Plotting): they
  are constant across candidates and discriminate nothing, so scoring them would
  inflate every candidate equally. They are echoed anyway because a reader has to
  be able to see which operator ran over which column — which is also what makes
  the `outflow` row's `mm` legible as a relabel rather than a conversion.
  `uv run ruff check .` and `uv run pytest` clean — 668 passed, same 15
  pre-existing findings.
- [x] T096 Typed outcomes: a `model` series for the gravel roof or the wetland →
  `not_available`; a `measured` series for either stays valid. → T051
  **The reason is `NON_MODELLABLE_ROOFS`', not a third copy of it.** One set
  already carries both water-balance tools' scope (§3.4), and the plot declines
  in the same words for the same stated cause — "the gravel roof has no
  substrate layer… its measured sensor data can still be queried", which is
  exactly the sentence this tool then makes true by drawing that data.
  **The abstention is the plot's, not the series'.** A chart is one deliverable,
  so a legal rain series beside a gravel `model` series is not drawn either.
  Returning a half-chart would have the answer report a scope limit while the
  renderer had been handed something to draw under it — worse than drawing
  nothing, because the user would believe the picture.
  **`PlotScopeError` is deliberately not `PlotVocabularyError`.** An unreachable
  column is an argument fault the agent can correct and retry; an unmodellable
  roof is a well-formed request this deployment declines, and typing the first
  as an abstention too would make the false-abstention rate uninterpretable
  (`decisions.md` § Typed abstention). The two land on `invalid_argument` and
  `not_available` respectively, and both are decided before any I/O.
  **The check is on the canonical name, so every alias reaches it.** `Kiesdach`,
  `Sumpfdach`, `kd` and `qgravel` all resolve through `roofs.resolve_roof`
  first, which is what makes family I askable in German (§3.4's reason for
  keeping the roof a plain string rather than an enum).
  `uv run ruff check .` and `uv run pytest` clean — 708 passed, same 15
  pre-existing findings.
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
  **The measured half has landed** in `tests/assistant/test_plot_timeseries.py`
  (40 tests, suite now 708): the closed-vocabulary rejections, the mixed-plot
  daily aggregation, and the unit/axis/operator derivation. The four remaining
  clauses all need T091's two live sources or T096's scope trigger and are left
  for the packet that adds them — the `model` and `weather` source resolution,
  the gravel and wetland `not_available`, the full resolved-spec shape (T092
  adds the modelling arguments and the gap and truncation flags), and the
  no-live-call replay assertion.
  **Nothing here stands in for the sources that do not exist yet.** What a mixed
  plot is tested on is the *decision* it forces, which `plot_resolution` makes
  from the source names alone before anything is fetched — so the assertion is
  real today and does not have to be rewritten when the fetch behind it arrives.
  A double returning fake daily rows would have tested the double.
  **Every expected number is hand-computed from the raw rows**, `zoneinfo` for
  the boundary and `statistics` for the mean, never by running a second copy of
  the implementation's query — which would let the day boundary drift in both
  places at once and still pass. `test_utc_days_would_have_answered_differently`
  is the check that the boundary is load-bearing on the window the other tests
  use: group the same week in UTC and two of the seven daily rain totals move.
  **`test_a_rejected_series_costs_no_query` is the one white-box test**, and it
  earns it: "the fault is decided before any I/O" is a claim about what did
  *not* happen, and only a spy executor that recorded one query for the legal
  series and none for the illegal one can witness it.
  **`test_the_reported_outflow_is_the_litres_the_record_holds` is the
  no-area-factor assertion in its executable form.** The vocabulary test checks
  there is no second, normalized entry; this one checks the entry there *is*
  returns the record's own litres under the `mm` label. Multiplying by today's
  1.0 is a no-op no assertion could see, so what this pins is the day someone
  reads the area off a different lysimeter and writes the multiplication in.

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
- [x] T115 Stand up the GR2L service, record its served build, and commit the
  canary request/response hash to `eval/pins.json`. Capture cannot start without
  it, and a diverging canary is a hard failure by design. **Pull forward — run it
  alongside P2b, not here.** It depends only on the canary format T023 fixes, it
  is the one task that can block a whole phase on something outside this
  repository, and discovering the service is unreachable during T116 costs a
  capture session rather than a five-minute check. Listed in P7 because that is
  where its output is consumed. → T023, T034
  Done. `gr2l_canary_response_sha256` is
  `0c39f945a3f94073847202804d7e21e686dd8651b5ae81cb44bf4d04fbf20faa`, captured
  from the live service at the pinned base URL against the unchanged
  `CANARY_REQUEST` (`c24f571f…`, T023's).
  **The pin is a real model run, not an error body that returned 200** — worth
  checking, because a hash does not care what it hashes. The response carries
  `Ssub = 8.0`, exactly the `theta_01` the probe sends, so day 1 seeded the store
  it was told to; `Qdown`/`Qup`/`OUT` are null, the documented seed-day
  behaviour; and `ET = 1.045` is `ET_PM · kg · Ssub/Ssubmax` =
  `2.0901 × 1 × 8.0/16.0` to the digit. The canary therefore pins the model's
  arithmetic, not merely its availability.
  **The served build is that hash, and no version string was available to add.**
  The gateway's health payload carries an empty `tag` and its OpenAPI reports a
  static `1.0.0`, neither of which identifies a build — which is precisely why §5
  pins the service by base URL plus a request/response hash. Nothing was added to
  the pin list.
  **Machinery.** `gr2l_client.fetch_canary()` sends the probe through the same
  `_endpoint()` `run_gr2l` uses (extracted here), so a probe cannot vouch for a
  deployment other than the one real requests reach. `just pins-canary` captures
  it and **refuses to re-pin a hash that has moved**, printing both values — a
  moved canary is a hard failure by design, not something to overwrite quietly.
  `LIVE_ONLY_PINS` teaches `check()` that a captured pin is pinned rather than
  "now uncomputable"; without it, committing the first canary would have made
  `just pins` fail permanently. `just pins` now reports 9 pinned, 7 unpinned, 0
  moved.
  **Two live probes, same hash** — the service is deterministic across calls,
  which is the property the pin asserts and not one to take on faith.
  **The whole path exercised against the real service, once.** A station-forced
  seven-day window ran end to end — real `wetter` rows, real seed (4.2 %θ →
  2.94 mm), real GR2L — with `evaluate_against_measured` giving mean 1.57 %θ and
  max 2.65 %θ against the sensor record. The same window with
  `forcings={"precip": {"2025-06-12": 50.0}}` moved that day from 2.28 to
  15.62 %θ and produced 36.34 mm of runoff, so the **model** computed the
  counterfactual rather than the wrapper adjusting a baseline. Repeating the
  first request returned a byte-identical result and wrote no new entry: three
  cache files for canary + two distinct requests. Written to a scratch directory,
  never `eval/cache/` — that is T116's to commit.
  **One hazard found by standing the service up, and left for T116 to own.**
  `gr2l_client` keeps its `httpx.AsyncClient` in a module-level singleton, and
  httpx binds a connection pool to the loop it was created on. A process that
  drives cases through **separate `asyncio.run` calls** therefore fails on the
  second live GR2L call with `RuntimeError: Event loop is closed`, surfacing as
  an `upstream` error via `CacheMissError`. Observed directly: two `asyncio.run`
  calls, the first succeeding and the second failing at `run_gr2l`; both calls
  inside one loop succeed. It is invisible to this packet's tests (fakes) and to
  production (one loop for the process), and `fetch_canary` closes the client
  behind itself so the capture command is unaffected — but a capture or search
  pass that runs a loop per case would hit it on case two. `ArchiveWeatherClient`
  already owns its client per instance (T022); the GR2L half never got the same
  treatment. Not fixed here: no row in P2b's list covers it.
  `uv run ruff check .` and `uv run pytest` clean — 254 passed, same 15
  pre-existing findings.
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
