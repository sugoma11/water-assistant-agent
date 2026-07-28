# Implementation Plan: thesis testbed for prompt optimization

Architecture: [`agent_architecture.md`](./agent_architecture.md) ·
Catalog: [`questions.md`](./questions.md)

Decisions referenced as **D1–D20** live in architecture §10 and are not restated
here. This file is the task-level order of work.

## Technical Context

**What exists.** `src/water_assistant_agent/assistant/` runs a FastAPI + AG-UI
service with a `root_agent` (ADK `Agent`, module-level singleton) wired to three
tools: `text_to_sql_agent` (`AgentTool` over a frozen text2SQL sub-agent),
`get_weather_forecast_tool`, `predict_green_roof_water_balance_tool`. The tool
layer is already cleanly split — ADK wrappers (`tools/gr2l.py`,
`tools/weather.py`, `tools/warehouse.py`) over pure, ADK-free clients
(`gr2l_client.py`, `weather_client.py`, `swc.py`, `site.py`, `schemas.py`). That
split is what makes the work below tractable: layer 2/3 barely moves, layer 1 is
rewritten as factories.

**Seams confirmed from source, not assumed:**

- **As-of views work without touching the pinned file.** Verified against
  `data/water.duckdb`: `duckdb.connect(':memory:')` →
  `ATTACH 'data/water.duckdb' AS src (READ_ONLY)` → `CREATE VIEW main.swc AS
  SELECT * FROM src.swc WHERE timestamp <= :as_of` yields `max(timestamp)`
  = the bound while `src.swc` still reads through to 2026-04-24. Five tables to
  wrap: `outflow`, `radiation`, `swc`, `tsoil`, `wetter`. No `*_raw` rename, no
  DB rebuild, and unqualified names in text2SQL-generated SQL resolve to `main`.
- **The DB executor is already injectable.** `DuckDbQueryExecutor` takes a
  `connection_factory: Callable[[], DuckDBPyConnection]`
  (`agents/text_to_sql/executor.py:22`), so an as-of connection drops in with no
  change to the pipeline. Three call sites reach one from global settings today:
  `tools/warehouse.py` — whose singleton the pipeline's `DuckDbExplainValidator`
  *also* imports (`text_to_sql/dry_run.py:13`) — and `tools/swc.py:99-107`, an
  independent second seam. All three route through the context (T014, T015,
  T023). Injectability alone is not reachability: the executor sits at the
  bottom of an import-time-frozen chain (executor → validator → pipeline →
  tools → `Agent` → `AgentTool`), which is why the sub-agent is rebuilt per
  context by a factory (D19, T024).
- **The weather/GR2L clients are already parameterized.** `fetch_daily_weather`
  takes lat/lon; `run_gr2l` takes `(rows, parameters)`; neither imports ADK.
  Only the *wrappers* hard-wire `site.py` and the module-level singleton
  `httpx.AsyncClient`. A `WeatherClient` protocol therefore wraps the
  fetch/transpose logic unchanged; the one signature change is T018's required
  `today` argument (D17), because `_choose_backend` *does* read a clock today.
- **The scenario clock has exactly two production readers today.**
  `prompts/temporal.py:current_datetime_block()` (`site_now()`) and
  `weather_client._choose_backend` (`site_now()` for the 92-day cutoff). Both
  become context-clock readers — `temporal.py` via the instruction provider,
  `_choose_backend` via an explicit `today` parameter, since the pure layer has
  no channel to `ctx` (D3, D17). Production binds `site_now` as the clock (D18);
  nothing else calls `site_now()`.
- **ADK instruction split is already exploited.** `root_agent` sets both
  `static_instruction` (byte-stable prefix, prompt-cache friendly) and a
  per-invocation `instruction` provider. The candidate instruction replaces
  `static_instruction`; the temporal block stays in the provider.
- **`AgentTool` name is the sub-agent's name.** `TextToSqlAgentTool` deliberately
  preserves it (`text_to_sql_tool.py` docstring, R3), so trajectory scoring keys
  on `text_to_sql_agent`.
- **No tests exist for `gr2l`, `weather`, or `swc`** — the three modules P1/P2
  refactor most heavily. Tests are written in the same phase, not after.
- **MLflow is already wired** for the text2SQL experiments (`src/experiments/`),
  including a `CostMeter`; it is the run-ledger sink. Its worker threads drop
  ContextVars, which is why D3 rules out a contextvar-scoped context.

**Constraints.** The production service and the CopilotKit frontend must keep
working throughout: `bootstrap.py` imports `root_agent` at module scope, and the
chat UI depends on the prose answer format (D10). Every refactor below is
additive at that boundary.

## Phases

Each task lists its file targets. `→ Tnnn` marks a hard dependency.

### P0 — Documentation reconciliation

| # | Task |
|---|---|
| T001 | ✅ Rewrite `agent_architecture.md` repo-true with status tags + decision log D1–D13 |
| T002 | Re-derive `questions.md` §1.4/§1.5 and the affected templates (D13): daily cache instead of hourly fixtures, self-contained model trajectories, registered tool names, whole-day phrasing for T09/T13, roof vocabulary instead of raw column names in T01. Do **not** patch case-by-case — re-derive the conventions section first, then sweep the catalog |
| T003 | Fold the P1/P2 behaviour changes back into `gr2l_tool.md` and `weather_tool.md` as they land (seed rule, typed `not_available`, window resolution, series cap). These specs are accurate today; they must not drift ahead of or behind the code |

### P1 — Injection seam and determinism

The blocking phase: no case is reproducible until this lands.

| # | Task |
|---|---|
| T010 | `assistant/context.py`: `ScenarioContext(clock, db_path, weather_client, cache)` where `clock: () -> datetime` and `as_of` is a property evaluating it (D18 — eval binds a frozen `lambda: case.as_of`, production binds `site_now` so the import-time singleton never goes stale past midnight) + `connect_asof(db_path, clock)` using the verified ATTACH/view mechanism over the five tables, re-evaluating `clock()` per connection; `ctx.db` is a `DuckDbQueryExecutor` over that view-recreating factory — a factory, not a bare connection, because the executor's reconnect path must rebuild the views (D19); the executor also reconnects when `clock().date()` has moved since connect — midnight rollover in production; frozen eval clocks never trigger it (D18) |
| T011 | `assistant/cache.py`: `ResponseCache` keyed by sha256 of the canonical request (URL + sorted params for Open-Meteo; `data[]` + parameters for GR2L), JSON-on-disk under `eval/cache/`, modes `record` / `replay` / `off`; a `replay` miss raises a typed `CacheMissError` carrying the unmatched request plus a diff vs the nearest captured request for the same endpoint (harness error, never a silent live call — the diff is what tells incomplete capture apart from candidate divergence, D16) → T010 |
| T012 | `WeatherClient` protocol + `CachedOpenMeteo` implementation wrapping `fetch_daily_weather`, supplying `today=ctx.as_of.date()` on every call (the clock yields a datetime, the client takes a date; D17); move the module-level `httpx.AsyncClient` singleton behind it → T011 |
| T013 | Route `run_gr2l` through the same cache; add the pinned canary request/response hash used as the service-version proxy → T011 |
| T014 | `tools/swc.py`: take the executor/connection from `ctx` instead of building one from global settings; keep the module pure and ADK-free → T010 |
| T015 | `tools/warehouse.py`: `make_query_database_tool(executor, clock)` returning a closure that preserves the exact name/signature/docstring of `query_database_tool` — the frozen sub-agent instruction names the tool and ADK derives the declaration from the function — with the module-level `query_database_tool` kept as the production default bound to the settings executor and `site_now` (D19; `clock` feeds the T025 rewrite — the executor deliberately does not carry it) → T010 |
| T016 | `make_*` tool factories closing over `ctx` for the three built tools, plus `build_toolset(ctx, docstrings=None)`; docstrings applied to the produced callables so they are candidate-owned (D6). The text2SQL entry is `TextToSqlAgentTool(build_text_to_sql_agent(ctx.db, ctx.clock, description=…))` built per context — never the import-time singleton — with `description` threaded from the candidate docstrings (D19) → T012, T014, T024 |
| T017 | `build_root_agent(instruction, docstrings, tools, model)`; binds the per-invocation instruction provider as a closure over `ctx.clock` (T018); keep module-level `root_agent` as the production default built from it, so `bootstrap.py` and the frontend are untouched → T016 |
| T018 | Scenario clock: `current_datetime_block` gains a required `now: datetime` parameter (the module has no channel to `ctx`; the provider closure built in T017 supplies `ctx.clock()`); `fetch_daily_weather`/`_choose_backend` gain a **required** `today: date` parameter (no wall-clock default — a caller that forgets it must fail, not silently leak) and the `site_now` import is deleted from `weather_client`; production supplies `site_now` through the context clock (D17, D18). Rationale is cache stability, not only leakage: a wall-clock cutoff flips a recent-past window's backend ~92 real days after capture, so `replay` misses a valid committed entry and a `record` re-run silently substitutes Archive observations for the forecast values the oracle was built on → T010 |
| T019 | Set the ~6-step tool cap on the root agent → T017 |
| T020 | Pin the LLM: dated model version in settings (no floating alias), `temperature=0` through `litellm_extra()`, prompt-keyed response cache → T011 |
| T021 | `eval/pins.json` + a `just pins` check: `water.duckdb` sha256, GR2L base URL + canary hash, model version, corpus/index hash (placeholder until P4), lockfile hash |
| T022 | Tests: `connect_asof` bounds every table; cache round-trip + `replay` miss raises; `build_toolset` produces independent toolsets for two different `as_of` values in parallel (the property contextvars would have broken) — including the text2SQL path: two contexts' sub-agents run the same `max(timestamp)` query concurrently and each sees its own bound (D19); a production-bound context (`clock=site_now`) reflects a clock-date change on the next query via the executor's reconnect-on-date-change path, without a context rebuild (D18); the T025 rewrite is pinned by test |
| T023 | `DuckDbExplainValidator(executor)`: take the executor as a constructor argument instead of importing the warehouse singleton (`dry_run.py:13`). EXPLAIN needs no as-of *correctness* (schema-only, and the views are schema-identical), but it rides the same `ctx.db` executor so the sub-agent has exactly one DB seam (D19) → T010 |
| T024 | `build_text_to_sql_agent(executor, clock, description=None)` in `agents/text_to_sql/agent.py`: rebuild the pipeline with a ctx-bound validator (transpiler, fixers and models shared), the two tools (querier via T015's factory), and a fresh `Agent` with byte-identical frozen text; the module singleton becomes the production default built from it, mirroring D6. `description` carries the candidate-owned outward tool description; `clock` threads to the querier factory for the T025 rewrite (D19) → T015, T023 |
| T025 | Wall-clock escape in generated SQL: extend the querier's existing sqlglot pass — which today parses only for the read-only guard and executes the original string, so it must now re-emit the SQL it validated — to rewrite `CURRENT_DATE` / `now()`-family nodes to the literal `as_of` (the clock arrives as the T015/T024 factory parameter) — rewrite, not reject, since the dateless frozen sub-agent that emits them cannot be optimized; production binds the context clock (D18), where the rewrite is a semantic no-op (D19) → T015 |

**Exit criterion:** two `ScenarioContext`s with different `as_of` run concurrently
in one process and neither sees the other's data or clock.

### P2 — Tool completeness (GR2L + weather)

| # | Task |
|---|---|
| T030 | Resolve `past_days`/`forecast_days` to absolute dates against `ctx.as_of` in both wrappers before any client call; validate 0–92 / 0–16 with outcomes split per D7/D16 — unservable windows (beyond horizon, before coverage, spanning the cutoff) → `not_available`; malformed arguments (negative counts, start > end, unparseable dates) → `invalid_argument` error → T018 |
| T031 | Typed `not_available` on `get_weather_forecast_tool`: beyond the 16-day horizon, before Archive coverage, and windows spanning the Archive/Forecast cutoff — servable by neither backend alone (D17) — the mechanism T18a scores; unknown-variable abstention (T18b) is agent-level, with no tool surface (D11) → T030 |
| T032 | Seed rule `min(window_start, as_of)` in `swc.latest_measured_swc` (D5) → T014 |
| T033 | `forcings={"precip": {"2026-07-22": 50.0}}` applied to the fetched rows before the GR2L request; sparse, validated against the window, echoed in the response for argument-checking (D8) → T030 |
| T034 | `evaluate_against_measured=True`: join the predicted `swc_pct` series to the `swc` as-of view, return mean/max |predicted − measured| in %θ plus the overlap window; wetland excluded (mm-only) → T014, T032 |
| T035 | Bounded series (D9): cap `data` at 31 days in **both** wrappers — GR2L and weather (an absolute Archive window is otherwise unbounded); beyond the cap return summary + weekly aggregates and set a truncation flag in the payload → T030 |
| T036 | Bug: normalize `roof_type` consistently in `tools/gr2l.py` — `NON_MODELLABLE_ROOFS` uses `.strip().lower()` at `:227` but the `ROOF_PRESETS` lookup at `:232` uses the raw string — as do `ROOF_PRESETS[roof_type]["SH"]` at `:279` and `resolve_roof_parameters` at `:292` — so `"Wetland"` returns `error` instead of resolving; normalize once at entry |
| T037 | Bug: `WeatherResult.elevation` carries Open-Meteo's grid-cell height while its field description says to use it as GR2L `hoehe_nn`; only the ADK wrapper overwrites it, so any oracle sharing the client gets the wrong value. Fix by **removal, not substitution** — the client cannot know the surveyed height (site geometry stays out of layer 2): drop `elevation` from `WeatherResult` in `schemas.py`; the weather wrapper composes site `latitude`/`longitude`/`elevation` into its agent-facing payload (§3.3's promise is the wrapper's to keep); consumers needing `hoehe_nn` take `SITE_ELEVATION_M` from `site.py` explicitly, as `gr2l.py:294` already does |
| T039 | `ErrorResult.error_type: "invalid_argument" \| "upstream"` in `schemas.py`; classify every error site (D16): the pre-I/O validation block in `gr2l.py` and malformed-date/window failures → `invalid_argument` (unservable-window declines stay `not_available` per the D7 split); fetch/seed/GR2L/config except-blocks and the catch-all in `weather.py` → `upstream`. Tests assert the classification per site |
| T038 | Tests for `gr2l`, `weather`, `swc` covering: %θ↔mm round-trip per roof, gravel → `not_available`, no-seed → `not_available`, stale-seed flag, wetland `swc_pct is None`, seed-day retention flag, forcings application, `evaluate_against_measured` arithmetic, out-of-horizon → `not_available` |

### P3 — Rules single source of truth

| # | Task |
|---|---|
| T040 | `assistant/rules_constants.py`: irrigation threshold(s) per roof, wilting point, heat-wave definition, retention target, priority order — one module, versioned, no duplication anywhere else |
| T041 | `assistant/irrigation.py`: `irrigation_decision(...)` as fixed priority rules (never below wilting point → minimize expected runoff → pre-heat-day cooling); pure, no LLM → T040 |
| T042 | `calc_irrigation` ADK tool + factory; callable with stated values (T16b) or chain outputs (T11) → T041, T016 |
| T043 | Renderer: `rules_constants.py` → ops-manual markdown sections with stable IDs (`#irrigation_rule`, `#heatwave_definition`, `#retention_target`, priority logic). Rendered, never hand-edited; a test asserts the rendered text matches the constants → T040 |
| T044 | Reference-ranges pages (normal/low/high per roof segment) derived from the `swc` record, same rendering discipline → T043 |

### P4 — Retrieval

| # | Task |
|---|---|
| T050 | Assemble `eval/corpus/`: rendered manual sections (T043), ranges pages (T044), sensor ReadMe, FAO-56 excerpts → T043, T044 |
| T051 | Heading-based chunker with **stable section IDs**; IDs are the contract `gold_docs` and recall@k reference — a test pins them → T050 |
| T052 | `retrieval/bm25_index.py`: `rank_bm25` build + query, index hash into `eval/pins.json` → T051, T021 |
| T053 | `search_docs` ADK tool + factory: top-k chunks with section IDs and scores, no "no results" suppression → T052, T016 |
| T054 | Decide and record the DE-query-vs-EN-corpus handling: measured gap or synonym map. Must be settled **before** case generation; the dense (BGE-M3) arm stays an ablation, not v1 |

### P5 — Plotting

Multi-source per D14, so this phase now sits downstream of P2 as well as P1: a
`model` or `weather` series rides the same window resolution, seed rule and cache
as the standalone tools. Nothing here re-implements a fetch path.

| # | Task |
|---|---|
| T060 | `tools/plot.py`: `plot_timeseries(series, start, end, kind)` — no `agg` argument (D20) where each `SeriesSpec` names a **source** (`measured` / `weather` / `model`) and a variable — never data (D14). `measured` runs a fixed parameterized query against the as-of view (no LLM SQL) over the five tables and a **closed vocabulary** of columns/aggregations; derived quantities (lysimeter-area normalization) are flags in that vocabulary, not free SQL → T010, T016 |
| T061 | `weather` and `model` series resolve through `ctx.weather` and `run_gr2l` — the same clients, cache and window resolution the standalone tools use; a `model` series accepts `initial_soil_moisture_pct` / `albedo` / `forcings` and echoes them for argument-checking. No new DuckDB connection, no new `httpx` client → T012, T013, T030, T032, T033 |
| T062 | Echo the **resolved spec** in-band — source + variable per series, resolved absolute range, the derived aggregation *and* resolution, unit + axis assignment per series, gap/truncation flags, modelling arguments. That is the scored surface; `artifact_ref` rendering is an unscored side effect |
| T063 | Return **no series to the model** — spec + summary stats + `artifact_ref` only (D9 satisfied outright, not capped) → T062 |
| T064 | Force daily resolution on mixed plots: `swc`/`wetter` are half-hourly, weather/GR2L are daily, so any plot combining a `model` or `weather` series with a `measured` one aggregates the measured series to calendar days (Europe/Berlin) with its variable's derived operator (D20) and reports the resolution → T060 |
| T065 | Derive unit, axis assignment **and aggregation operator** in code from the variable (mm/day vs %θ needs dual axes; fluxes → `sum`, states → `mean`); never model-chosen — there is no `agg` argument (D20) — so the scored spec stays deterministic → T062 |
| T066 | Typed outcomes: `model` series for the gravel roof → `not_available` (`NON_MODELLABLE_ROOFS`); `swc_pct` model series for the wetland → `not_available` (mm-only); `measured` series for either stays valid → T036 |
| T067 | Headless mode: eval runs must not import or require the UI stack. Nothing renders server-side — the tool returns spec + stats + `artifact_ref` to the model and stashes the series in session state for the tool wrapper to merge into the tool-result event (`QUERY_RESULT_STATE_KEY` / FR15 pattern, D20); the frontend draws from that payload, so **no Python plotting dependency** → T060 |
| T068 | Frontend: `useCopilotAction` render for `plot_timeseries` mirroring `TextToSqlResult.tsx`, reading the wrapper-enriched tool-result payload (D20), plus a charting dep (Recharts — the app is already React 19; `web/package.json` has none today) → T067 |
| T069 | Tests: source resolution per kind, closed-vocabulary rejection of unknown table/column, mixed-plot daily aggregation, axis/unit derivation, gravel and wetland `not_available`, resolved-spec shape pinned, and **no live call in `replay` mode** for a `model`+`weather` plot → T011 |

### P6 — Harness, scoring, pilot → FREEZE GATE

| # | Task |
|---|---|
| T070 | `harness/run_case.py`: case → `ScenarioContext` → `build_toolset` → `build_root_agent` → `Runner.run` → parse the answer contract → structured result with trajectory + diagnostics. Also scans the event log for root-level tool results with `status == "error"` and `error_type == "upstream"` and marks the case `harness_error`; `invalid_argument` errors leave the case scored, and the sub-agent's inner fixer-loop errors are not scanned (D16) → T017, T039 |
| T071 | Answer-contract parsing + the eval-only candidate instruction carrying it (D10); the production instruction is untouched. The candidate **must** carry an explicit no-clarification clause (D15) — the production instruction's clarification rule would otherwise leak in and fire on a paraphrase. A message parsing to neither status value is recorded as a `parse_failure` diagnostic, never as answer=0 |
| T072 | `harness/scoring.py`: answer (exact/tolerance, **skipped with coverage reported where the contract answer is `null`** — family H, D14), trajectory (unordered P/R/F1, extra-call penalty, must-nots, argument checks), retrieval recall@k, abstention accuracy **and** false-abstention rate reported separately, `harness_error` cases excluded from every aggregate and counted separately **per arm, broken down by source** (service, cache-miss reason, terminal sub-agent failure) (D16), diagnostics incl. `parse_failure` |
| T073 | Case schema (JSONL source of truth) + generated ADK evalset JSON from the same rows |
| T074 | Oracles for the three pilot templates only: T01 (SQL sum), T07 (rule chain), T09 (model chain, `import run_gr2l`) → T041, T013 |
| T075 | Harness assertions: T19-style windows end ≤ `as_of`; no live call in `replay` mode; roof pool respected per family |
| T076 | **Pilot run** on T01/T07/T09 with the handwritten instruction, 3 seeds, paired — then **freeze the testbed** (architecture §9.8). Everything after this point may change only the optimizable text |

### P7 — Oracles and case generation

| # | Task |
|---|---|
| T080 | Remaining oracles per family, sharing tool-chain code (architecture §1.1) |
| T081 | Template instantiation with the §1.6 generation filters: validity (sensor-gap rejection), balance (~50/50 on bool templates), abstention share 7–10 %. Family H draws from the roof pool of its **most demanding series** (D14); H's abstention cases (gravel `model_overlay`) are sampled deliberately from *outside* those pools, which govern answerable cases |
| T082 | EN + DE paraphrase generation; style pools disjoint between train and test; alias-heavy and colloquial German phrasings → T054 |
| T083 | Splits per §1.7: train ~80 / val ~50 / test_seen ~70 / test_unseen ~50 (holdout T17b, T18b, T22, T23, T26) |
| T084 | Cache capture pass in `record` mode over every case, then commit `eval/cache/`; re-run in `replay` mode and assert zero live calls → T011. Family H is part of the capture surface now that plots fetch weather and GR2L themselves (D14) — a plot case left uncaptured fails in `replay` exactly like a model case |

### P8 — Optimizers

| # | Task |
|---|---|
| T090 | Handwritten baseline candidate (instruction + docstrings), recorded as a first-class candidate |
| T091 | OPRO-style propose-and-keep-best (~100 lines) |
| T092 | GEPA adapter: per-example textual feedback from the ADK event log (trajectory diff vs gold, SQL errors, abstention outcome) |
| T093 | MIPROv2 arm, optional — DSPy twin or text2SQL-module-only |
| T094 | Run ledger to MLflow: candidate id, case id, model version, all pins, trajectory, tokens/cost → T021 |
| T095 | Statistics: 3 seeds per condition, paired evaluation on identical cases, paired bootstrap |

## Risks

- **R1 — Forecast-backend windows are unreproducible outside a capture
  deadline.** This covers future windows *and* recent-past windows served by the
  Forecast backend: the API only reaches ~92 real days back, so every such case
  must be captured while real time ≈ its `as_of`, and after that deadline the
  window cannot be re-fetched by anyone — the upstream world is gone. Losing the
  committed cache for such a case means regenerating the case with a fresh
  `as_of`, not re-running capture. Mitigation: `as_of` is part of the case
  identity; T084 runs promptly after generation; and D17's `as_of`-pinned
  backend choice makes replay time-invariant (the committed entry stays hittable
  forever) while making a post-deadline `record` attempt fail loudly at the API
  instead of silently substituting Archive observations for the forecast values
  the oracle was materialized from. Archive-backend windows carry no deadline:
  they are stable upstream and re-fetchable at any time.
- **R2 — GR2L service availability gates capture.** A cache miss with the service
  down fails the case by design. Mitigation: capture (T084) is one batch pass,
  well before any optimizer run; the canary hash detects a service change that
  would invalidate the committed cache.
- **R3 — The wetland's SWC sensor is unreliable from 2026-02-01** and the record
  ends 2026-04-24, so present-day wetland cases seed from months-old readings
  flagged `is_stale`. Mitigation: prefer `as_of` values inside the trustworthy
  record when sampling model-bearing cases; the staleness disclosure is itself
  scorable behaviour.
- **R4 — Refactoring three untested modules.** P1/P2 rewrite the wrappers around
  `gr2l`/`weather`/`swc`, which have no tests today. T022 and T038 are not
  optional and land in their own phases.
- **R5 — Frontend regression.** D6/D10 keep `root_agent` and the prose format
  intact, but T016–T019 and T024 touch modules the service imports. One smoke
  run of the chat UI after P1 closes.
- **R6 — A genuinely ambiguous paraphrase survives the §1.6 validity filter.**
  The candidate instruction forbids clarifying questions (D15), so the model is
  forced to guess and the case scores as wrong on an ambiguity the generator
  should have rejected. The filter tests *oracle* ambiguity (sensor gaps), not
  *linguistic* ambiguity introduced at paraphrase time (T082). Mitigation: spot-
  check the DE paraphrase pool for referential ambiguity before T083 splits;
  record the residual as a stated scope limit rather than a scored behaviour.

## Traceability

| Architecture item | Tasks |
|---|---|
| §4 ScenarioContext, D3 | T010, T014, T015, T016, T022 |
| §5 cache + pinning, D4 | T011, T012, T013, T020, T021, T084 |
| §5 as-of views / leakage | T010, T015, T032, T075 |
| §2 clock, D7, D17, D18 | T010, T012, T018, T022, T030, T031 |
| §2 LLM pinning, step cap | T019, T020 |
| §2 candidate injection, D6 | T016, T017, T071, T090 |
| §3.1 sub-agent ctx binding, D19 | T015, T022, T023, T024, T025 |
| §2 answer contract, D10, D15 | T070, T071, T072 |
| D16 error taxonomy | T039, T011, T070, T072 |
| §3.3 typed abstention, D11 | T031 |
| §3.4 seed rule, D5 | T032 |
| §3.4 forcings / evaluate, D8 | T033, T034 |
| §1.3 bounded series, D9 | T035 |
| §3.2 search_docs | T050–T054 |
| §3.5 calc_irrigation | T040–T044 |
| §3.6 plot_timeseries, D14, D20 | T060–T069 |
| §6 harness, §7 scoring | T070–T075, T094, T095 |
| §8 catalog amendments, D13 | T002 |
| Code bugs found in review | T036, T037 |

## Generated Artifacts

- `specs/agent_architecture/agent_architecture.md` (reconciled), this plan
- To be created: `assistant/context.py`, `assistant/cache.py`,
  `assistant/rules_constants.py`, `assistant/irrigation.py`,
  `assistant/retrieval/bm25_index.py`, `assistant/tools/{search_docs,plot}.py`,
  `harness/`, `eval/{corpus,cache,templates,oracles,cases}/`, `eval/pins.json`
- To be edited: `tools/{gr2l,weather,warehouse,swc,weather_client,gr2l_client,schemas}.py`,
  `agents/root_agent/agent.py`, `agents/text_to_sql/{agent,dry_run}.py`,
  `agents/text_to_sql/executor.py` callers,
  `prompts/temporal.py`, `settings.py`, `questions.md`, both tool specs,
  `web/components/ChatView.tsx` + `web/package.json` (plot render, T068)
