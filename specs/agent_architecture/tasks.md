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

| Packet | Tasks | Sections to read | Exit |
|---|---|---|---|
| **P0** specification reconciliation | T001–T010 | all four core documents — cross-document consistency *is* the deliverable, so this one must not be split | no document references a file, section or decision id that does not exist |
| **P1a** context, cache, clock | T020–T023, T031, T035a | arch §4, §5 (as-of views, response cache); `decisions.md § The construction seam`, `§ The response cache`, `§ The as-of cut`, `§ Window resolution and the scenario clock`; `findings.md § Codebase seams` | as-of bound on all five tables; the cut identical under two host `TZ`s; cache round-trip, canary divergence, unfillable miss |
| **P1b** DB seams, frozen sub-agent | T024–T028, T035b | arch §3.1, §5 (generated-SQL bullet); `decisions.md § The construction seam`; `findings.md § Codebase seams` (executor injectability, `AgentTool` name) | two contexts' sub-agents run one query concurrently, each on its own bound; the `CURRENT_DATE` rewrite pinned |
| **P1c** factories, pins, smoke | T029, T030, T032–T034, T035c, T036 | arch §2, §5's pin list; `decisions.md § Model pinning`, `§ Replication and the LLM cache` | independent toolsets for two `as_of` values; `just pins` verifies; the chat UI unchanged |
| **P2a** weather: window, horizon, sources | T040–T045, T055 | arch §3.3; `weather_tool.md § Station source` + the two unit conversions; `decisions.md § Weather sources`, `§ Typed abstention`, `§ The day boundary`; `findings.md § Weather source measurements`, `§ Data record` | station derivation field by field; midnight-straddling rain in the right Berlin day; routing at both record edges; a station case in replay with **zero** cache entries and zero live calls |
| **P2b** GR2L: seed, counterfactuals, scope, taxonomy | T046–T054, and **T115 pulled forward** | arch §3.4 + §3's preamble; `gr2l_tool.md`; `decisions.md § GR2L argument surface`, `§ Bounded series`, `§ Tool errors and harness exclusion`; `findings.md § Data record` (QWetland) | T054's list green; `roof_type` pinned as `str`; a model case in replay issues no live call; the GR2L canary committed |
| **P3a** roof table, ET0, constants | T060–T062 | arch §1 principle 4, §3.5; `irrigation_tool.md § Units`, `§ Which extensive roof is which`; `findings.md § Not every roof is instrumented`, `§ The lysimeter collection area` | one roof table feeds `swc` and the presets with no value changes |
| **P3b** bucket, faithfulness, unit fix, tool | T063–T067, T070 | `irrigation_tool.md` in full; `decisions.md § The irrigation calculator`, `§ No fitted correction between the instrument and the oracle` | the port reproduces the deployed controller **before** the unit fix; the diff list exists |
| **P4** cards | T068, T069, T080–T084 | arch §3.2; `decisions.md § Retrieval` | a lookup case in replay with zero cache entries and zero live calls; every drift test green |
| **P5** plotting | T090–T097, T099 | arch §3.6; `decisions.md § Plotting`, `§ Bounded series` | a `model` + `weather` + `measured` plot issues no live call in replay |
| **P5f** frontend render | T098 | the existing tool-result component; arch §3.6's headless paragraph | the chat renders a plot from the stashed payload |
| **P6a** rollout, contract | T100, T101, T104 | arch §2 (contract), §6, §7's exclusion paragraph; `decisions.md § The answer contract`, `§ Tool errors and harness exclusion` | one case runs end to end; an injected `upstream` error marks `harness_error` where an `invalid_argument` does not; `parse_failure` reported apart from a wrong answer |
| **P6b** scoring | T102 | arch §7 in full, §6.1's `expectations` fields; `decisions.md § Trajectory scoring and routing probes`, `§ Plotting` (skip), `§ Retrieval` (card recall) | four metrics over fixture results, each with its edge case: unit normalization, binary trajectory, skip **and** coverage on both users, false abstention kept separate |
| **P6c** pre-freeze text, pilot oracles | T103, T105, T106, T011 | arch §3.1; `questions.md` §1.6 and the T01/T07/T09 entries | the semantic layer carries the area *value* and the alias map; three oracles reproduce hand-computed answers |
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
- [ ] T011 Standing task, closed at the freeze gate: keep `gr2l_tool.md`,
  `weather_tool.md` and `irrigation_tool.md` in step with P2 and P3 as behaviour
  lands — the seed rule, typed outcomes, window resolution, the series cap, the
  station derivation, the wetland's scope change. These specs are accurate today
  and must drift neither ahead of nor behind the code.
  **Open by design — it closes at the freeze gate, not in P0.** Phase 0 changed no
  tool behaviour, so the three specs needed no edit here; their standing state is
  that the wetland's scope change is stated *ahead* of the code, which plan §2.4
  makes deliberate (the code follows in T051) rather than drift. Checked at the
  P0 exit: every markdown link in the five specs and the three tool specs
  resolves, and every `decisions.md § …` citation names a heading that exists.

**Exit — met.** Every cross-document reference resolves (links and
`decisions.md § …` citations swept mechanically), and `questions.md` §4 is empty:
its six items are settled and recorded in the sections they govern, with the one
residual filed as an accepted risk in `decisions.md`. T011 stays open as the
standing task it is defined to be.

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
- [ ] T034 `eval/pins.json` and a `just pins` check covering plan §4's pin list,
  with the card-store and reflection-model entries stubbed until P4 and P8 fill
  them. → T033
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
- [ ] T035c Tests for the factories and the pins (**packet P1c**):
  `build_toolset` produces independent toolsets for two `as_of` values in
  parallel; the production defaults are the factories' own output, not a second
  construction path; `just pins` fails on a moved hash. → T029, T030, T034
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
- [ ] T046 Remove `WeatherResult.elevation` from `schemas.py` — the client cannot
  know the surveyed height. The weather wrapper composes the site's own
  `latitude` / `longitude` / `elevation` into its agent-facing payload; consumers
  needing `hoehe_nn` take it from `site.py` explicitly. **Lands in packet P2b,
  not P2a**, though it is a weather change: `schemas.py` is otherwise T052's
  file, and one packet per file is what keeps the two halves of P2 sequential
  rather than conflicting. → T043
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

**98 tasks** across nine phases, 15 of them parallelizable, executed as **23
packets** — one session each, mapped above.

| Phase | Tasks | Parallelizable | Packets | Gates |
|---|---|---|---|---|
| P0 specification reconciliation | 11 | 6 | 1 | T010 blocks every ground-truth writer |
| P1 injection seam | 19 | 4 | 3 | blocks P2–P8 entirely |
| P2 tool completeness | 16 | 2 | 2 | blocks P5, P7 |
| P3 rules | 11 | 2 | 2 | blocks P4 |
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
