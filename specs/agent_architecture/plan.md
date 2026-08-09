# Implementation Plan: thesis testbed for prompt optimization

Architecture: [`agent_architecture.md`](./agent_architecture.md) ·
Catalog: [`questions.md`](./questions.md)

Decisions referenced as **D1–D31** live in architecture §10 and are not restated
here; entries whose mechanism is stated in the architecture body are one-line
pointers there, so follow the section reference rather than expecting the
argument in §10. This file is the task-level order of work.

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
- **The optimizer entry point already exists and ships its own GEPA adapter**
  (installed: mlflow 3.13.0, gepa 0.1.1). `mlflow.genai.optimize_prompts(...)`
  with `GepaPromptOptimizer` wraps GEPA via `MlflowGEPAAdapter`
  (`mlflow/genai/optimize/optimizers/gepa_optimizer.py:146+`), which implements
  **both** `evaluate` and `make_reflective_dataset` — so T092 writes no adapter
  (D27). Five mechanics confirmed from source, each with a task consequence:
  - candidate text is injected by a **process-global patch of
    `PromptVersion.template`** (`optimize/optimize.py:292`, reverted in
    `finally`), reaching the system only through a registry read whose prompt
    *name* matches a candidate key → **T096**;
  - records are evaluated in a `ThreadPoolExecutor`
    (`optimize.py:329`, `MLFLOW_GENAI_EVAL_MAX_WORKERS`), so `predict_fn` must
    build its `ScenarioContext` per record with no ambient state → **T097**, and
    P1's exit criterion is exercised under a real executor;
  - ground truth reaches scorers **only** through `expectations`
    (`optimize.py:296-299`), and `inputs` is the sole required column
    (`optimize/util.py:102-106`) — this fixes the case envelope → **T004, T073**;
  - the reflective dataset is built from MLflow **trace spans plus
    `Feedback.rationale`** (`gepa_optimizer.py:290-343`, `util.py:180-188`), not
    from the ADK event log, so scorer rationales *are* the reflection signal →
    **T098**. ADK spans do reach MLflow traces
    (`mlflow/tracing/otel/translation/google_adk.py`);
  - a scorer returning a non-numeric value raises unless an explicit
    `aggregation` is supplied (`util.py:200-214`) — which family H's skipped
    answer metric requires → **T098**.
- **The text2SQL GEPA wiring is not reusable as-is.** `train_gepa.py:42,251` uses
  `GepaPromptOptimizer` for a single prompt with a scorer over a pure function;
  the agent testbed needs N+1 registered prompts and a `predict_fn` that builds a
  per-case context. Same entry point, different candidate surface.

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
| T004 | **Pin the ground-truth envelope before the T002 sweep.** `eval/schema/case.schema.json` + `eval/schema/template.schema.json`, plus the projection rule template YAML → case JSON. The envelope is **dictated by MLflow, not designed** (architecture §6.1, D27): `inputs` carries what `predict_fn` needs (`question`, `as_of`, `case_id`, `template_id`, `params`); `expectations` carries everything a scorer needs (`status`, `answer`, `unit`, `answer_metric`, `tolerance`, `expected_tool_calls`, `must_not_tools`, `gold_cards`, `argument_checks`, `pins`) because those are the only two channels the API delivers. `argument_checks` declarative (`{tool, path, op, value}`) so T072 never branches per template; `expected_tool_calls` keeps ADK's key name so `mlflow.genai.scorers.google_adk.ToolTrajectory` stays available as a cross-check. Validators: every tool name is a **registered** name (D2); `answer_metric: "skipped"` implies `answer: null`; **`gold_cards` non-empty implies `lookup_reference` in `expected_tool_calls`** (D32 — card recall is scored, so a gold card on a template that never looks one up scores 0 on a correct run; an empty `gold_cards` *skips* the metric rather than scoring 0); per-template constants are copied into each case, never referenced across files. **Why before T002:** T002 is the first writer of ground truth, and D14/D16/D21/D23/D24/D25 each changed a field shape — an unpinned schema means deriving it twice and drifting once, which is how `questions.md` §1.4/§1.5 went stale in the first place |
| T002 | Re-derive `questions.md` §1.4/§1.5 and the affected templates (D13): daily cache instead of hourly fixtures, self-contained model trajectories, registered tool names, whole-day phrasing for T09/T13, roof vocabulary instead of raw column names in T01 (whose pool also omits the semi-intensive roof §8 includes), the §3 coverage matrix. Do **not** patch case-by-case — re-derive the conventions section first, then sweep the catalog. Already reconciled ahead of the sweep, do not re-litigate: T18a/T18b relabel (D23), binary trajectory legend (D21), T11/T16b bool reframing (D22), the §1.1 D16 wording, the T16 symmetric must-nots + T04's model-tool must-not (D24), T24b's pp-gap answer (D25). Still to settle in the sweep: T16a-vs-T16b phrasing cue (D22 — now load-bearing: D24's must-nots fail the un-cued route) and the T25 measured-past routing convention (open). Every §1.1/§1.2 field the sweep touches must land in T004's `expectations` envelope, and §1.4 (hourly weather fixtures) is **deleted, not re-derived** — the request-keyed cache replaced it (D4) and cases carry no cache reference at all, since the key is the request → T004 |
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
| T018 | Scenario clock: `current_datetime_block` gains a required `now: datetime` parameter (the module has no channel to `ctx`; the provider closure built in T017 supplies `ctx.clock()`); `fetch_daily_weather`/`_choose_backend` gain a **required** `today: date` parameter (no wall-clock default — a caller that forgets it must fail, not silently leak) and the `site_now` import is deleted from `weather_client`; production supplies `site_now` through the context clock (D17, D18). Rationale is cache stability, not only leakage: a wall-clock cutoff flips a recent-past window's backend ~92 real days after capture, so `replay` misses a valid committed entry and a `record` re-run silently substitutes Archive observations for the forecast values the oracle was built on. Also update `site.py`'s module docstring, which says the weather client takes its clock from there → T010 |
| T019 | Set the ~6-step tool cap on the root agent → T017 |
| T020 | Pin the LLM: dated model version in settings (no floating alias), `temperature=0` through `litellm_extra()`, prompt-keyed response cache → T011 |
| T021 | `eval/pins.json` + a `just pins` check: `water.duckdb` sha256, GR2L base URL + canary hash, model version, **card-store sha256** (one hash over `assistant/knowledge/cards/`; placeholder until P4 — there is no index to hash, D32), lockfile hash |
| T022 | Tests: `connect_asof` bounds every table; cache round-trip + `replay` miss raises; `build_toolset` produces independent toolsets for two different `as_of` values in parallel (the property contextvars would have broken) — including the text2SQL path: two contexts' sub-agents run the same `max(timestamp)` query concurrently and each sees its own bound (D19); a production-bound context (`clock=site_now`) reflects a clock-date change on the next query via the executor's reconnect-on-date-change path, without a context rebuild (D18); the T025 rewrite is pinned by test |
| T023 | `DuckDbExplainValidator(executor)`: take the executor as a constructor argument instead of importing the warehouse singleton (`dry_run.py:13`). EXPLAIN needs no as-of *correctness* (schema-only, and the views are schema-identical), but it rides the same `ctx.db` executor so the sub-agent has exactly one DB seam (D19) → T010 |
| T024 | `build_text_to_sql_agent(executor, clock, description=None)` in `agents/text_to_sql/agent.py`: rebuild the pipeline with a ctx-bound validator (transpiler, fixers and models shared), the two tools (querier via T015's factory), and a fresh `Agent` with byte-identical frozen text; the module singleton becomes the production default built from it, mirroring D6. `description` carries the candidate-owned outward tool description; `clock` threads to the querier factory for the T025 rewrite (D19) → T015, T023 |
| T025 | Wall-clock escape in generated SQL: extend the querier's existing sqlglot pass — which today parses only for the read-only guard and executes the original string, so it must now re-emit the SQL it validated — to rewrite `CURRENT_DATE` / `now()`-family nodes to the literal `as_of` (the clock arrives as the T015/T024 factory parameter) — rewrite, not reject, since the dateless frozen sub-agent that emits them cannot be optimized; production binds the context clock (D18), where the rewrite is a semantic no-op (D19) → T015 |

**Exit criterion:** two `ScenarioContext`s with different `as_of` run concurrently
in one process and neither sees the other's data or clock.

### P2 — Tool completeness (GR2L + weather)

| # | Task |
|---|---|
| T030 | Resolve `past_days`/`forecast_days` to absolute dates against `ctx.as_of` in both wrappers before any client call; validate 0–92 / 0–16 with outcomes split per D7/D16 — unservable windows (beyond horizon, before coverage, spanning the cutoff) → `not_available`; malformed arguments (negative counts, start > end, unparseable dates) → `invalid_argument` error → T018. **Partly landed early (2026-07-29), see T030a; what remains here is the `ctx.as_of` clock (still `site_now()`) and the `not_available`/`invalid_argument` split (still one flat `error`).** |
| T030a | ✅ **Landed 2026-07-29** — resolution itself, pulled forward because it was fixing a live bug, not preparing for the seam. `weather_client.resolve_window` maps both window forms to one absolute `(start, end)` pair; both wrappers call it before the client, and `fetch_daily_weather` now takes `start_date`/`end_date` as **required** arguments (its `past_days`/`forecast_days` parameters are gone, so the bug cannot be reintroduced from a caller). **The bug:** Open-Meteo defaults `forecast_days` to 7, so a bare `past_days=5` returned *twelve* days — five past, today, and six forecast — and `DailyWeatherRow` has no observed/forecast marker, so history questions were answered partly from predictions and GR2L simulated forecast days as observations. Resolution follows Open-Meteo's own split (`past_days` = complete days ending yesterday; `forecast_days` counts from today; neither = the old 7-day default, now explicit). Errors: new `InvalidWindowError` (pre-I/O, message names the argument) and `WeatherFetchError`/`OpenMeteoError`/`IncompleteWeatherError`, which carry Open-Meteo's own `reason` to the agent instead of "Failed to fetch weather" — e.g. *"Parameter 'start_date' is out of allowed range from 1940-01-01"*. Backend routing deliberately **unchanged**. Tests: `tests/assistant/test_weather_window.py` |
| T030b | **Bug found while landing T030a — Forecast's past reach is ~64 days, not the documented 92.** Measured at the site 2026-07-29: all seven GR2L variables present back to `today − 64`, null from `today − 65`; `past_days=92` returns 93 rows of which 28 are entirely null. `_choose_backend`'s `_FORECAST_PAST_LIMIT_DAYS = 92` therefore routes 65–92-day-back windows to Forecast, where they die in `_transpose` ("returned no data") even though Archive serves them completely (verified through today). Fix is to lower the cutoff — but note the two backends **disagree on the same past day** (2026-06-21 precip: 0.00 mm Forecast vs 2.50 mm Archive; 06-20 tm 24.4 vs 26.0 °C), so the cutoff is an accuracy decision about which source the recent past comes from, not a routing detail. Decide it with T031/D17 rather than by tuning a constant; an empirical reach can drift, so prefer a cutoff that fails to Archive → T031. **D26 does not fix this**: the station record ends 2026-04-27, before the Forecast backend's recent-past window begins, so the 65–92-day band is still Open-Meteo's to route |
| T031 | Typed `not_available` on `get_weather_forecast_tool`: beyond the 16-day horizon, before Archive coverage, and windows spanning the Archive/Forecast cutoff — servable by neither backend alone (D17) — the mechanism T18a scores; unknown-variable abstention (T18b) is agent-level, with no tool surface (D11) → T030 |
| T031a | **Station source (D26).** `StationWeatherSource` in the pure layer: given an as-of DuckDB executor and an absolute window, derive `DailyWeatherRow`s from `wetter` per §3.3's table — `avg(Tmean)`, `max(Tmax)`, `min(2·Tmean − Tmax)` for the missing `tn`, `avg(RH)`, `sum(Rain)`, `avg(windspeed) × 3.6` **excluding the `−7999` sentinel**, `sum(Rad_SW) × 1800 / 10⁴`. Convert UTC → Europe/Berlin **before** grouping (the table is UTC with no DST — verified via season-invariant solar noon; grouping on `timestamp::date` shifts rain across days and desynchronises T19's comparison against `outflow`/`swc`). Emit only **complete** days (48 half-hourly rows; the record has 479/481 complete). Data quality is served **uncorrected** — no calibration, no gap filling, no per-day fallback (D26) → T010 |
| T031b | Route to the station in `_choose_backend` when the record covers the window **entirely**; partial coverage falls through to the existing Archive/Forecast rules unchanged, so provenance stays single-valued and no new `not_available` class appears (D26). The record's first/last complete day is read from the pinned DB, not hardcoded, and joins the §5 pins. `WeatherResult.backend` (and GR2L's echo) gains `station` so provenance is visible to the agent, the oracle and the trajectory scorer → T031a, T012 |
| T031c | Bypass the D4 response cache for station windows — it is a pure function of the pinned DB, so `record`/`replay`/`off` are all no-ops there and `replay` must **not** raise `CacheMissError` for a window the station serves. Test: a retrospective case inside the record completes in `replay` mode with **zero** cache entries and zero live calls → T011, T031b |
| T031d | Tests for T031a–c: per-field derivation against hand-computed values, the UTC→Berlin boundary (a rain event straddling midnight local lands in the right day), sentinel exclusion from the wind mean, incomplete-day exclusion, station-vs-Open-Meteo routing at both record edges, partial coverage falling through to Archive, and `backend == "station"` echoed end to end through GR2L |
| T032 | Seed rule `min(window_start, as_of)` in `swc.latest_measured_swc` (D5) → T014 |
| T033 | `forcings={"precip": {"2026-07-22": 50.0}}` applied to the fetched rows before the GR2L request; sparse, validated against the window, echoed in the response for argument-checking (D8) → T030 |
| T034 | `evaluate_against_measured=True`: join the predicted `swc_pct` series to the `swc` as-of view, return mean/max |predicted − measured| in %θ plus the overlap window; wetland excluded (mm-only) → T014, T032 |
| T035 | Bounded series (D9): cap `data` at 31 days in **both** wrappers — GR2L and weather (an absolute Archive window is otherwise unbounded); beyond the cap return summary + weekly aggregates and set a truncation flag in the payload → T030 |
| T036 | Bug: normalize `roof_type` consistently in `tools/gr2l.py` — `NON_MODELLABLE_ROOFS` uses `.strip().lower()` at `:227` but the `ROOF_PRESETS` lookup at `:232` uses the raw string — as do `ROOF_PRESETS[roof_type]["SH"]` at `:279` and `resolve_roof_parameters` at `:292` — so `"Wetland"` returns `error` instead of resolving; normalize once at entry |
| T037 | Bug: `WeatherResult.elevation` carries Open-Meteo's grid-cell height while its field description says to use it as GR2L `hoehe_nn`; only the ADK wrapper overwrites it, so any oracle sharing the client gets the wrong value. Fix by **removal, not substitution** — the client cannot know the surveyed height (site geometry stays out of layer 2): drop `elevation` from `WeatherResult` in `schemas.py`; the weather wrapper composes site `latitude`/`longitude`/`elevation` into its agent-facing payload (§3.3's promise is the wrapper's to keep); consumers needing `hoehe_nn` take `SITE_ELEVATION_M` from `site.py` explicitly, as `gr2l.py:294` already does |
| T039 | `ErrorResult.error_type: "invalid_argument" \| "upstream"` in `schemas.py`; classify every error site (D16): the pre-I/O validation block in `gr2l.py` and malformed-date/window failures → `invalid_argument` (T030a already separates these two populations by exception type — `InvalidWindowError` vs `WeatherFetchError` — so classification is a tagging pass over existing catch sites, not new analysis) (unservable-window declines stay `not_available` per the D7 split); fetch/seed/GR2L/config except-blocks and the catch-all in `weather.py` → `upstream`. Tests assert the classification per site |
| T038 | Tests for `gr2l`, `weather`, `swc` covering: %θ↔mm round-trip per roof, gravel → `not_available`, no-seed → `not_available`, stale-seed flag, wetland `swc_pct is None`, seed-day retention flag, forcings application, `evaluate_against_measured` arithmetic, out-of-horizon → `not_available` |

### P3 — Rules single source of truth

The deployed controller (`~/Downloads/smart_irrigation.py`, extracted from the
site's `temp/smart_irrigation.py`) is now in hand, which retires R7 and adds
T045–T049. Its model is **not** GR2L (D29) and it runs local (D30), so this
phase gained a second water-balance core but lost every network dependency.

| # | Task |
|---|---|
| T045 | `assistant/tools/roofs.py`: one roof table — canonical name, DE/EN labels, EGR1/EGR2/IGR/WGR site ids, `swc` column, `SH_cm`, lysimeter area (1 m²), valve presence, alias set. Repoint `swc.ROOF_SWC_COLUMNS` and `gr2l_client.ROOF_PRESETS` at it with **no value changes**, so §5's pins are untouched. Subsumes T036 — normalization happens once, at the table |
| T046 | `assistant/et_fao56.py`: FAO-56 Penman-Monteith ET0, a verbatim port of `GR2L_function.R:35-74` at **albedo 0.23** (reference crop, not the roof's — throttling is the stress coefficient's job). The R source's fixed `Pressure = 100 kPa` is carried over deliberately and documented as a scope limit (<1 % of ET0 at 142 m) so the two languages agree |
| T040 | `assistant/rules_constants.py`: per-roof wilting / dry / capacity / residual **authored in %θ and kg, converted to mm once** through `swc.theta_pct_to_mm` and `kg / area_m²`; horizons in hours (48 / 168); heat threshold; outflow epsilon; **both dose fields** — D22's p90-ET (unset until derived) and the deployed valve minutes. Plus **two constants with no deployed source, flagged in-module as eval policy**: the heatwave *duration* rule (the controller carries only `HEAT_THRESHOLD_C`, no consecutive-day concept) that T08 counts against, and T12's retention target. One module, versioned, no duplication anywhere else — every card's `values:` block is test-bound to it (T052) → T045 |
| T041 | `assistant/irrigation.py`: `simulate_store` / `summarize` / `irrigation_decision`, pure, no LLM, no numpy. Fixed priority rules (never below wilting point → minimize expected runoff → pre-heat-day cooling) returning **bool + reason code + the fixed dose constant** (D22), never a computed volume. The two `will_reach_capacity` adapters (modelled outflow; stated rain total) keep the ladder single. Preserves the original's semantics exactly — stress coefficient from the *previous* step, no lower floor, seed-day initialisation only, SWC window including index 0 and outflow window excluding it → T040, T046 |
| T047 | **Faithfulness before correctness.** A golden-series test asserting the port reproduces `smart_irrigation.py` element-for-element *in the original %θ/kg mode*, landing **before** the unit fix — so every later difference is attributable to the fix rather than the port → T041 |
| T042 | `calc_irrigation` ADK tool + factory, self-contained per §3.5 (own seed via `swc`, own forcing via `ctx.weather`); the stated-value path does no I/O at all (T16b). Three outcomes with D16 `error_type`; gravel and seedless windows → `not_available` → T041, T016, T031a, T031b |
| T048 | Decision-diff harness (D31): replay a historical window from the pinned DuckDB and the station source through **both** unit regimes with the *same* Python ET0, so unit handling is the only variable; emit a markdown table of every date/roof where the decision flips, with the driving feature values. This is the evidence for whether the site re-tunes → T047, T031a |
| T049 | **Cross-repo, non-evaluation** (`~/work/weinbau-api-v1`): `R/et_fao56.R` extracted from `GR2L_function.R` **gated on the GR2L canary**; `R/GreenRoofSWB_function.R`; `POST /predict_greenroof_swb` on the *existing* `gr2l_model` container; gateway route + schema + `allowed_predict_endpoints` `Literal` + auth-matrix cases; `docs/greenroof_swb_tool.md`; the irrigation canary into `eval/pins.json` marked non-evaluation. All thresholds arrive in the request — no site policy in R. Also guard or delete the top-level demo block at `GR2L_function.R:124-141`, which runs on every container start because `plumber.R` sources the file → T041 |
| T043 | **Card values bound to the constants** (D32, replaces the markdown renderer): a `values_for(card_id)` function projecting `rules_constants.py` + `roofs.py` into each `provenance: rendered` card's `values:` / `applies_to:` / `not_applicable:` blocks, plus the drift **test** asserting the committed card equals it. A test, not a writer — hand prose and generated values share one file, so byte-level rendering would need a format-preserving YAML writer for no added guarantee. Ship a `just cards-check` that prints the correct block on failure. `irrigation_rule` + `irrigation_threshold` must together answer T16a without the calculator (D24), and `irrigation_threshold.not_applicable.wetland` must state that no soil-moisture threshold exists or the card silently answers T17b → T040, T045 |
| T044 | `roof_reference_ranges` card (normal/low/high per roof segment) derived from the `swc` record, same drift-test discipline; carries the wetland's lysimeter *level* threshold in kg → T043 |

### P4 — Reference cards

Rewritten under D32. The heading-based chunker (**T051**) and the DE/EN
retrieval decision (**T054**) are **deleted, not deferred** — an exact lookup has
no chunk boundaries and no lexical query — and their IDs are retired rather than
reused. **T052** is repurposed in place, from the BM25 index to the card-store
loader; **T050** and **T053** keep their slots with new content.

| # | Task |
|---|---|
| T050 | Author `assistant/knowledge/cards/*.yaml` — ~9 cards, one file each: `id`, `title`, `provenance`, hand-authored `text:` **carrying no numerals**, and `values:` / `applies_to:` / `not_applicable:`. `provenance: rendered` — `irrigation_rule`, `irrigation_threshold`, `substrate_hydraulics`, `irrigation_dose`, `data_freshness`, `heatwave_definition`, `retention_target`, `roof_reference_ranges`; `provenance: static` (no constants behind them, exempt from the drift test) — `roof_directory`, `sensor_reference`, `et0_method`. **No card may be named after a single constant** — cards name subjects, or absence becomes inferable from the topic enum and T17a degenerates (D32) → T043, T044 |
| T052 | `assistant/knowledge/store.py`: pydantic `Card` model + loader over the packaged YAML (`pyyaml`; hatchling already ships package data for `src/water_assistant_agent/`), an `enum == card keys` test, the T043 drift test wired in, and the card-store sha256 into `eval/pins.json`. Pure, ADK-free, no I/O beyond the packaged files → T050, T021 |
| T053 | `lookup_reference` ADK tool + factory: `topic: Literal[…]`, `roof: str \| None`. The **enum lives in the signature**, not the docstring — ADK renders `Literal` into the function declaration as a schema `enum` (verified against the installed `google-adk`), so a candidate can reword the guidance but cannot delete the vocabulary and silently disable the route (D6, D32). Returns the card **whole**, `not_applicable:` included; `roof` filters but never suppresses that block. Unknown topic → `error` with `error_type="invalid_argument"` echoing the valid list (recoverable, free under D21). **No roof-scoped `not_available`** — it would collapse T17b into T18a → T052, T016, T039 |
| T055 | Tests: card round-trip and schema validation; `enum == card keys`; every `provenance: rendered` card's `values:` equals `values_for(card_id)`; no numerals in any `text:` block; unknown topic returns `invalid_argument` naming the valid topics; the wetland exclusion survives `roof="wetland"` on `irrigation_threshold` (T17b's mechanism); a lookup case completes in `replay` mode with **zero** cache entries and zero live calls (fully offline, D32) → T053 |

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
| T070 | `harness/run_case.py`: case → `ScenarioContext` → `build_toolset` → `build_root_agent` → `Runner.run` → parse the answer contract → structured result with trajectory + diagnostics. Also scans the event log for root-level tool results with `status == "error"` and `error_type == "upstream"` — plus, keyed on **tool name**, any `text_to_sql_agent` result that parses as a dict with `status == "error"` (the sub-agent's payloads carry no `error_type` and its terminal result is often prose; prose is never an error, D16/D24 review) — and marks the case `harness_error`; `invalid_argument` errors leave the case scored, and the sub-agent's inner fixer-loop errors are not scanned (D16) → T017, T039. **This is the same rollout T097's `predict_fn` performs** — write it once here and have `predict_fn` call it, or the search and the measurement run diverge on the one code path that must be identical |
| T071 | Answer-contract parsing + the eval-only candidate instruction carrying it (D10); the production instruction is untouched. The candidate **must** carry an explicit no-clarification clause (D15) — the production instruction's clarification rule would otherwise leak in and fire on a paraphrase. A message parsing to neither status value is recorded as a `parse_failure` diagnostic, never as answer=0 |
| T072 | `harness/scoring.py`: answer (exact/tolerance, **skipped with coverage reported where the contract answer is `null`** — family H, D14), trajectory (**binary per case, D21**: gold ⊆ called ∧ no must-not called ∧ argument checks; no partial credit, no extra-call penalty), **card recall** (`|gold_cards ∩ retrieved| / |gold_cards|` over the union of every `lookup_reference` call; one number, no gold-query/agent-query split; **skipped with coverage reported where `gold_cards` is empty**, reusing D14's mechanism — D32), abstention accuracy **and** false-abstention rate reported separately, `harness_error` cases excluded from every aggregate and counted separately **per arm, broken down by source** (service, cache-miss reason, terminal sub-agent failure) (D16), diagnostics incl. `parse_failure` and mean extra calls per arm (D21). Structure it as **per-case metric functions plus an aggregation layer**: T098 wraps the same per-case functions as MLflow `Scorer`s for the search, while the aggregates (exclusions, coverage, per-arm breakdowns) stay here, on the measurement path (D28) → T004 |
| T073 | Case files as the source of truth, emitted through T004's schema: one **pretty-printed JSON array per split** (`eval/cases/{train,val,test_seen,test_unseen}.json`) of `{"inputs": …, "expectations": …}` elements, loadable **directly** as MLflow `train_data` by both the search (T092) and the measurement run — one format, no projection step. Array over JSONL for reviewability (a JSONL diff hides which field changed); the emitter must be **deterministic** — fixed key order, cases sorted by `case_id`, `indent=2`, trailing newline — and the files are generated, never hand-edited, or a case silently loses its derivation from the template and its `pins` stamp. **No ADK evalset** (D27): dropped, not deferred. Oracle materialization stamps `expectations.pins` with the surface the answer was computed against (DB sha256, GR2L canary, station-derivation version, resolved weather source), so a later pin change invalidates loudly instead of silently → T004, T021 |
| T074 | Oracles for the three pilot templates only: T01 (SQL sum), T07 (`import irrigation_decision` — local, no HTTP, D30), T09 (model chain, `import run_gr2l`) → T041, T013. **T07's phrasing must be settled in T002 first**: its gold set moved to `{calc_irrigation}` (§8) while its wording still cues the docs route, and T076 cannot freeze a pilot whose route is ambiguous |
| T075 | Harness assertions: T19-style windows end ≤ `as_of`; no live call in `replay` mode; roof pool respected per family |
| T076 | **Pilot run** on T01/T07/T09 with the handwritten instruction, 3 seeds, paired — then **freeze the testbed** (architecture §9.8). Everything after this point may change only the optimizable text → T077, T078 |
| T077 | Lysimeter collection areas in the text2SQL semantic layer (architecture §9.6, catalog §4.2): per-roof areas in the frozen sub-agent's prompt, unblocking T12 and L↔mm conversions — must land before the T076 freeze because the sub-agent text is byte-stable thereafter |
| T078 | Semantic-layer alias map + typo fixes (architecture §9.7, catalog §4.3): DE/EN roof aliases (Kies/KD/QGravel ↔ gravel, DE↔EN bridging) — blocks paraphrase generation (T082) and must precede the T076 freeze for the same reason |

### P7 — Oracles and case generation

| # | Task |
|---|---|
| T080 | Remaining oracles per family, sharing tool-chain code (architecture §1.1). Each writes its result into the case's `expectations` and stamps `expectations.pins` (T073) → T004 |
| T081 | Template instantiation with the §1.6 generation filters: validity (sensor-gap rejection), balance (~50/50 on bool templates), abstention share 7–10 %. Family H draws from the roof pool of its **most demanding series** (D14); H's abstention cases (gravel `model_overlay`) are sampled deliberately from *outside* those pools, which govern answerable cases |
| T082 | EN + DE paraphrase generation; style pools disjoint between train and test; alias-heavy and colloquial German phrasings. **No longer blocked on a retrieval-side DE/EN decision** (retired T054): under D32 a German question reaches a card through the model's choice of `topic`, so the gap is measured as routing accuracy on DE paraphrases rather than mitigated in a retriever → T078 |
| T083 | Splits per §1.7: train ~80 / val ~50 / test_seen ~70 / test_unseen ~50 (holdout T17b, T18b, T22, T23, T26) — emitted as one T073 case file per split → T073 |
| T084 | Cache capture pass in `record` mode over every case, then commit `eval/cache/`; re-run in `replay` mode and assert zero live calls → T011. Family H is part of the capture surface now that plots fetch weather and GR2L themselves (D14) — a plot case left uncaptured fails in `replay` exactly like a model case |

### P8 — Optimizer (GEPA only)

**One optimizer, two arms.** GEPA is the only search method (architecture §6);
the comparison is *handwritten baseline vs GEPA-optimized candidate* on identical
cases, not a bake-off between optimizers. OPRO and MIPROv2 are dropped — no
propose-and-keep-best loop, no DSPy twin, no text2SQL-module-only arm. T091 and
T093 are retired, not renumbered, so the IDs below stay stable.

**Reached through MLflow, not driven directly** (D27): the entry point is
`mlflow.genai.optimize_prompts` + `GepaPromptOptimizer`, MLflow supplies the
`GEPAAdapter`, and this phase writes only `predict_fn`, the scorers and the
prompt-registry candidate surface. T096–T099 are new; the four together replace
what the retired single-task "GEPA adapter" was.

| # | Task |
|---|---|
| T090 | Handwritten baseline candidate (instruction + docstrings), **registered as prompt versions in the MLflow prompt registry** — it is simultaneously the reference arm and GEPA's `seed_candidate`, so the two cannot drift apart (D27). Recorded as a first-class candidate; every GEPA result is paired against it → T096 |
| T092 | Wire the search: `harness/optimize.py` calling `mlflow.genai.optimize_prompts(predict_fn=…, train_data=cases, prompt_uris=…, optimizer=GepaPromptOptimizer(reflection_model=…, max_metric_calls=…), scorers=…, aggregation=…)`. **No `GEPAAdapter` is written** — MLflow's `MlflowGEPAAdapter` owns `evaluate` and `make_reflective_dataset`, GEPA owns the loop, one rollout is one `predict_fn` call (D27). Run in `replay` mode over a pre-filtered `train_data` (D28) → T096, T097, T098, T099 |
| T096 | Candidate surface in the prompt registry (D27): register the root instruction and **one prompt per optimizable tool docstring**, including the sub-agent's outward `description` (D19) — never one concatenated blob, since `components_to_update` granularity is what lets GEPA mutate a single docstring. Prompt names + seed versions join `eval/pins.json`. **Assert no "prompts were not used during evaluation" warning**: a component never read is silently frozen while appearing optimizable, and that warning is the only signal → T017, T021, T090 |
| T097 | `predict_fn(inputs) -> dict`: build `ScenarioContext(lambda: inputs["as_of"], …)`, read candidate text via `load_prompt(uri).template` (patched to candidate text during optimization), then `build_toolset(ctx, docstrings=…)` → `build_root_agent` → `Runner.run` → parse the contract. Per record, **no ambient state** — MLflow evaluates records in a `ThreadPoolExecutor`, the same boundary that dropped `CostMeter`'s ContextVars (D3). D6's `docstrings` argument is unchanged; only its source is new. Test: two records with different `as_of` evaluated concurrently *through `predict_fn`*, each seeing its own bound — T022's property under the real executor → T016, T017, T022, T096 |
| T098 | Scorers as MLflow `Scorer`s, one per §7 metric (answer, trajectory, **card recall**, abstention), each returning a `Feedback` whose **`rationale` is the GEPA reflection signal** — trajectory diff vs gold, the cards fetched vs the cards wanted, SQL errors, abstention outcome (D27); a float-only scorer optimizes blind. Per-scorer values reach GEPA as `objective_scores`, so the metrics drive Pareto selection un-blended. An explicit **`aggregation` callable is mandatory**: it receives the raw scorer outputs and implements "skipped, not 0" for family H's answer metric (D14) **and for card recall on empty `gold_cards`** (D32) — one mechanism, two users; MLflow would otherwise reject the non-numeric value → T072, T004 |
| T099 | `harness_error` at search time (D28): pre-filter `train_data` to cases with complete capture; run the search in `replay` mode so upstream errors are rare by construction; a `harness_error` scorer contributes 0 **and** reports the per-arm count, so a polluted search is a stated fact rather than silent noise. The D16 exclusion itself stays in T072's measurement path, which produces the numbers the thesis claims rest on → T072, T084 |
| T094 | Run ledger to MLflow: candidate id, case id, model version, all pins, trajectory, tokens/cost. `optimize_prompts` already logs per-iteration candidate text, per-scorer metrics and an eval-results table; this adds the pins and case identity per rollout → T021 |
| T095 | Statistics: 3 seeds per condition (baseline, GEPA), paired evaluation on identical cases, paired bootstrap. Runs on the **measurement** path (T072), not on `optimize_prompts`' internal scores |

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
- **R7 — retired.** `calc_irrigation`'s input set was unconfirmed; the deployed
  controller resolves it (soil moisture, precipitation, ET0, air temperature —
  radiation, wind and humidity enter only through ET0). T041/T042 and the
  T11/T16b oracles are unblocked; architecture §3.5 and D22 record the
  resolution. What survives is not a blocker but T002's phrasing sweep for T07,
  whose "according to the operations manual" wording now collides with T16a.
- **R9 — the thresholds were tuned against the defective dynamics.** The
  deployed trigger levels (5/10, 4/10, 10/16 %θ) were set against a model that
  added mm to a %θ store, so the mm fix moves them relative to the dynamics by
  `100/SH_mm` per roof (D31). T048 measures the decision flips; whether to
  re-tune is the site's call, not ours, and until they do, the answers this
  system gives can differ from what the roof's own controller did that day.
  Mitigation is disclosure, not correction — the deviation list in
  `irrigation_tool.md` is part of the thesis's scope limits.
- **R10 — cross-language drift.** The bucket and the ladder exist in both
  `irrigation.py` and the R endpoint (D30), and nothing links the two
  repositories' CI. The committed canary detects drift on the next run that
  touches it; it does not prevent drift, and a stale canary is indistinguishable
  from an unchanged service. Mitigation: the canary is checked in the same pass
  as GR2L's, and D30 makes explicit that no case's answer depends on the R side,
  so drift is a deliverable defect rather than an evaluation defect.
- **R11 — extracting `et_fao56.R` touches a canary-pinned file.** T049 refactors
  `GR2L_function.R`, whose request/response hash is a §5 pin. Mitigation: the
  extraction is gated on the canary being unchanged, and the stated fallback is
  a standalone copy for the new model with GR2L left untouched — three ET
  implementations instead of two, which is worse DRY but zero risk to a pinned
  surface the whole suite rests on.
- **R8 — the optimizer entry point is `@experimental`.**
  `mlflow.genai.optimize_prompts` and `GepaPromptOptimizer` are marked
  experimental (mlflow 3.13.0), and candidate injection rests on an internal
  monkeypatch of `PromptVersion.template` — a minor-version bump can move it, and
  the failure mode is quiet: prompts stop being patched and the search optimizes
  nothing while still reporting scores. Mitigation: the mlflow and gepa versions
  join the §5 pins and the lockfile hash (T021); T096's "no unused prompt"
  assertion fails loudly the moment the patch stops reaching a component; and
  D27's rejected alternative (a) — a hand-written `GEPAAdapter` over
  `gepa.optimize` — stays available at the cost of re-implementing MLflow's
  per-iteration logging.
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
| §3.3 station source, D26 (D12 narrowed) | T031a–T031d, T012, T011 |
| §3.4 seed rule, D5 | T032 |
| §3.4 forcings / evaluate, D8 | T033, T034 |
| §1.3 bounded series, D9 | T035 |
| §3.2 lookup_reference, D32 | T050, T052, T053, T055 |
| §3.5 calc_irrigation | T040–T049 |
| §3.5 own bucket model, D29 | T041, T046, T047, T002 |
| §3.5 local execution, D30 | T041, T042, T049 |
| §3.5 unit fix + kept thresholds, D31 | T040, T047, T048 |
| §3.6 plot_timeseries, D14, D20 | T060–T069 |
| §6 harness, §7 scoring | T070–T075, T094, T095 |
| §6/§6.1 optimizer entry point + case envelope, D27 | T004, T073, T090, T092, T096, T097, T098 |
| D28 `harness_error` at search time | T099, T072, T084 |
| §8 catalog amendments, D13 | T002 |
| §7 binary trajectory, D21 | T072, T002 |
| §3.5 irrigation decision, D22 | T040–T042, T002 (R7 retired) |
| T18 relabel + empty gold, D23 | T002, T031 |
| D24 routing must-nots, D25 T24b answer | T002, T070 (catalog already patched) |
| §9.6/§9.7 semantic-layer prerequisites | T077, T078 |
| Code bugs found in review | T036, T037 |

## Generated Artifacts

- `specs/agent_architecture/agent_architecture.md` (reconciled), this plan
- To be created: `assistant/context.py`, `assistant/cache.py`,
  `assistant/rules_constants.py`, `assistant/irrigation.py`,
  `assistant/et_fao56.py`,
  `assistant/tools/roofs.py`, `assistant/tools/irrigation_tool.md`,
  `assistant/knowledge/{store.py,cards/*.yaml}`,
  `assistant/tools/{lookup,plot,irrigation}.py`,
  `harness/` (incl. `run_case.py`, `scoring.py`, `optimize.py`, `scorers.py`),
  `eval/{cache,templates,oracles,cases,schema}/`, `eval/pins.json`
- To be added to `[project] dependencies`: `pyyaml` (the card store ships in the
  production service). `rank_bm25` is **not** added — D32 removed the need before
  it was ever installed
- To be edited: `tools/{gr2l,weather,warehouse,swc,weather_client,gr2l_client,schemas}.py`,
  `agents/root_agent/agent.py`, `agents/text_to_sql/{agent,dry_run}.py`,
  `agents/text_to_sql/executor.py` callers,
  `prompts/temporal.py`, `settings.py`, `questions.md`, both tool specs,
  `web/components/ChatView.tsx` + `web/package.json` (plot render, T068)
