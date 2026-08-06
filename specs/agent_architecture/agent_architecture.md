# System architecture — green-roof water management assistant (thesis testbed)

Companion to [`questions.md`](./questions.md) (question-template catalog) and to
the two tool specs, [`gr2l_tool.md`](../../src/water_assistant_agent/assistant/tools/gr2l_tool.md)
and [`weather_tool.md`](../../src/water_assistant_agent/assistant/tools/weather_tool.md).
Implementation order lives in [`plan.md`](./plan.md).

The system is a **frozen testbed**: the thesis contribution is the evaluation of
prompt-optimization techniques, not the assistant. After the prerequisites in §9
land, only the items marked *optimizable* may change.

Every §3 entry carries a status tag against the implementation. §10 is the
decision log, and an entry earns its place there only by recording a **rejected
alternative** or an **evaluation-validity condition**: a decision whose mechanism
is fully stated in the body appears as a one-line pointer instead, and numbers
are retired rather than reused. The arbitration rule (D1) is: **the code wins on
mechanism, this document wins on evaluation properties.**

---

## 0 Status at a glance

| Component | Name the agent sees | Status |
|---|---|---|
| root agent | `root_agent` | **built** — not candidate-injectable yet (D6) |
| text2SQL sub-agent | `text_to_sql_agent` | **built** — no as-of views, not ctx-rebindable yet (D3, D19) |
| weather | `get_weather_forecast_tool` | **built** — live, uncached, no typed abstention, wall-clock backend cutoff, uncapped series, no station source (D4, D9, D11, D17, D26) |
| GR2L water balance | `predict_green_roof_water_balance_tool` | **built** — no `forcings` / `evaluate_against_measured`, uncapped series (D8, D9) |
| doc retrieval | `search_docs` | **to build** |
| irrigation rule | `calc_irrigation` | **to build** |
| plotting | `plot_timeseries` | **to build** — multi-source: DB · weather · GR2L (D14) |
| `ScenarioContext`, as-of views | — | **to build** (D3) |
| response cache (weather + GR2L) | — | **to build** (D4) |
| harness · scoring · oracles · cases | — | **to build** |

Three of six agent-facing tools exist. What exists is sound at the *client*
layer — pure, ADK-free `weather_client` / `gr2l_client` / `swc` seams behind thin
ADK wrappers — and that layering is what the rest of this document builds on.
What is missing is the *injection* seam (§4) that makes any of it replayable.

---

## 1 Design principles

1. **Three layers.** Agent-facing tools (small declarative arguments) →
   I/O resolution (`ScenarioContext`: DB as-of views, weather client, clock)
   → shared cores (`run_gr2l`, `swc`, rule functions). The agent sees only layer
   1; the harness injects at layer 2; oracles import layer 3 directly.
   **Layer 3 is not uniformly local**: GR2L runs as an external HTTP service, so
   `run_gr2l` is a thin client and an oracle that imports it issues the same
   request the tool does (§3.4). `rules_constants.py` and the irrigation rule
   remain pure local functions.
2. **Replay the world through a request-keyed cache, run everything downstream
   of optimized prompts live, pin the data live components read.** External
   world-state (weather, GR2L) is fetched live once and replayed thereafter from
   a committed cache keyed by the sha256 of the exact request (D4). Components
   whose inputs depend on the optimized instruction (text2SQL, retrieval, model
   chains) execute live against pinned data.
3. **No raw data through the LLM.** Tools return a summary plus a *bounded*
   series — never a database row dump, never an unbounded daily array. Anything
   requiring arithmetic over a long series happens inside a tool or core (D9).
4. **Single source of truth for rules.** `rules_constants.py` feeds the
   irrigation calculator, the oracles, and the rendered ops-manual sections.
   Agent-readable text and executable logic cannot drift apart.
5. **Disclosure fairness.** Every self-contained tool's docstring states what it
   fetches internally. Required in the handwritten baseline; optimized
   candidates may rewrite docstrings (they are part of the candidate).

```
┌ agent layer ────────────────────────────────────────────────────────┐
│ root_agent (ADK LlmAgent; instruction ← candidate)                  │
│  text_to_sql_agent · get_weather_forecast_tool ·                    │
│  predict_green_roof_water_balance_tool ·                            │
│  search_docs · calc_irrigation · plot_timeseries                    │
└───────────────┬─────────────────────────────────────────────────────┘
        ScenarioContext(as_of, db_asof, weather_client, http_cache)
┌ shared cores ─┴─────────────────────────────────────────────────────┐
│ run_gr2l (HTTP → external GR2L service, cached) ·                   │
│ daily weather (station rows from ctx.db · Open-Meteo, cached) ·     │
│ swc (θ ↔ mm, measured seed) · irrigation_decision ·                 │
│ rules_constants · bm25 index · pinned data/water.duckdb             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2 Root agent (Google ADK)

Built at `agents/root_agent/agent.py`. Today it is a module-level `Agent`
singleton with a literal `static_instruction` and three tools.

- `LlmAgent`, ReAct-style tool loop, hard cap ~6 tool steps. **Not yet set.**
- **Optimizable text components**: the `instruction` and the tool
  descriptions/docstrings — including the sub-agent's outward `description`,
  the text `AgentTool` presents to the root model (D19); the sub-agent's
  *internal* prompt stays frozen (§3.1). Everything else is frozen. Reaching
  them requires the factory refactor in D6 — module-level function docstrings
  are not addressable per candidate. Each component is **one registered MLflow
  prompt**, read per rollout inside `predict_fn`; that registry read is the only
  channel candidate text has (§6, D27), and one prompt per component — never a
  concatenated blob — is what keeps the surface separately mutable.
- Scenario clock: `as_of` reaches the model through the per-invocation
  instruction provider (`prompts/temporal.py`), which today renders
  `site_now()` — the wall clock. It must render `ctx.as_of` (D3), read through
  the context's clock *callable* (D18): production binds `site_now`, eval binds
  the case's frozen `as_of`, so there is one code path, not two — provided
  `as_of` is evaluated at read time, never captured at construction.
- **Answer contract** (catalog §1.1) — **eval-only**, carried by the candidate
  instruction:

  ```json
  {"status": "answered" | "not_available",
   "answer": <bool | number | "YYYY-MM-DD" | null>,
   "unit": "<L | mm | °C | pp | % | count | null>",
   "explanation": "<free text, unscored in phase 1>"}
  ```

  The agent contract's `status` is **two-valued**, and it is *not* the tool-level
  enum of §3.4/§3.6 (`success` | `not_available` | `error`). The abstention
  metric scores the **agent's** status; a tool's `not_available` is the upstream
  cause that makes it correct, not the thing measured — a tool may abstain
  correctly while the agent narrates around it and emits `answered`, which is
  exactly the failure the metric exists to catch. A tool `error` has no contract
  representation: `upstream` errors are handled harness-side, `invalid_argument`
  errors are the agent's to recover from and score normally (D16); a clarifying question has
  none either and is forbidden in the candidate (D15). Family H answers `null`
  (D14). Phase 1 scores status+answer; `explanation` is reserved for the phase-2
  judge. The production instruction keeps prose and clarification instead (D10).
- LLM config: pinned **dated** model version (never a floating alias),
  `temperature=0`, transparent cache keyed by exact prompt. **None of the three
  is configured today** — `settings.root_agent_model` defaults to the floating
  `openai/qwen3.6-35b-a3b` and `litellm_extra()` forwards only `api_base` /
  `api_key`.

## 3 Tools

### 3.1 `text_to_sql_agent` (sub-agent via `AgentTool`) — **built**, frozen
- Internal: completion (tuned prompt, **frozen**) → db querier → fixer loop,
  `settings.max_sql_retries` (default 3); attempt counts logged as diagnostics
  (`pipeline.py` returns `transpile_attempts` / `validate_attempts`).
- Reads the pinned DuckDB. **As-of views are not implemented** — the executor
  opens `data/water.duckdb` read-only with no time bound (D3).
- Semantic layer lives statically in its prompt: column descriptions, units,
  lysimeter areas, alias map (Kies/KD/QGravel ↔ gravel roof; DE↔EN bridging),
  join key and time resolution.
- Returns compact results (aggregates, small result sets), capped at 100 rows in
  `warehouse.query_database_tool`.
- The root agent sees the tool under the sub-agent's own name,
  **`text_to_sql_agent`** — that string, not `query_database`, is what
  trajectory scoring matches (D2). `query_database_tool` is the *inner* tool and
  never appears in a root-agent trajectory.
- **Frozen means the text, not the objects** (D19). Prompt, docstrings and
  pipeline structure are byte-stable, but the *object* is rebuilt per
  `ScenarioContext` by `build_text_to_sql_agent(executor, clock)`: its DB seam —
  `query_database_tool` *and* the pipeline's EXPLAIN validator, which both
  reach the module-level warehouse executor today — must bind to the case's
  as-of executor. Transpiler, fixers, models and every prompt string are
  shared; the DB binding and the injected clock (read by the querier's
  `CURRENT_DATE` rewrite, D19) are the only per-case state. The ctx-bound
  `query_database_tool` closure preserves the exact function name, signature and
  docstring — ADK derives the tool declaration from them and the frozen
  instruction names the tool — and the module singleton stays as the production
  default built from the same factory, mirroring D6.

### 3.2 `search_docs` — **to build**, BM25 v1
- `rank_bm25` over the corpus; deterministic; no LLM, no reranker.
- Corpus: ops-manual sections **rendered from `rules_constants.py`**
  (`#irrigation_rule`, `#heatwave_definition`, `#retention_target`,
  irrigation-priority logic §3.5), reference-ranges pages (normal/low/high per
  roof segment), sensor ReadMe, FAO-56 excerpts.
- Chunking by markdown headings with **stable section IDs** — these IDs are what
  `gold_docs` and recall@k reference.
- Returns top-k chunks with section IDs and scores; never "no results"
  suppression (abstention templates rely on plausible-but-wrong hits).
- Known scope limit (state in thesis): lexical matching; DE queries vs EN corpus
  is a measured gap or mitigated by a synonym map — decide before generation.
  Dense retriever (BGE-M3, exact search) kept as a drop-in **ablation arm**.

### 3.3 `get_weather_forecast_tool` — **built**, daily, live + cached (D4)
- Thin ADK wrapper (`tools/weather.py`) over `weather_client.fetch_daily_weather`,
  which is pure and ADK-free. The `WeatherClient` protocol and the cache seam are
  **to build**: today the wrapper imports the module-level function directly, so
  every rollout hits `api.open-meteo.com` (D3, D4).
- **Three sources, resolved from the window in code** (D26), in priority order:
  1. **Station** — derived from the pinned DB's `wetter` table through
     `ctx.db`, whenever the record covers the **whole** window.
  2. **Archive** — windows starting more than ~92 days before `as_of`.
  3. **Forecast** — everything else (`_choose_backend`).

  The station is preferred where it can serve the window outright, so every
  window keeps **one provenance**; a window the record covers only partly falls
  through to the existing Open-Meteo routing unchanged. This deliberately adds
  **no new `not_available` class** — D17's unservable-window cases (beyond the
  16-day horizon, before Archive coverage, spanning the Archive/Forecast cutoff)
  are still the whole of T18a. Deep history before the record starts is served
  by Archive exactly as today.

  The two Open-Meteo cutoffs are measured from a **required explicit `today`
  argument** — `ctx.as_of`, never the wall clock — so the chosen source, and
  with it the D4 cache key, is a pure function of the case (D17). The station
  boundary is not clock-relative at all: it is a property of the pinned
  `water.duckdb`, so it cannot drift between capture and replay.
  **Not implemented**: there is no station path, and `_choose_backend` reads
  `site_now()` today, which flips a recent-past window's backend ~92 real days
  after capture and rots the committed cache (D17). Archive values are stable
  upstream; forecast values are not, which is what the cache pins (D4).
- **The station path needs no cache, no network and no capture step.** It is a
  pure function of the pinned DB, which §5 already hashes, and it reads through
  the as-of views — so a retrospective weather window is time-bounded
  structurally, closing a leakage path Archive leaves open (Archive will happily
  return observations past a case's `as_of`). Cases inside the record are fully
  offline and immune to D17's recapture problem.
- **Daily resolution is pinned end to end** — cached responses, tool output,
  GR2L input rows, and any DB-meteo aggregation are one row per calendar day
  (Europe/Berlin). GR2L's native step is daily and it rejects sub-daily rows.
  Sub-daily questions ("in the next 6 hours") are phrased against whole days —
  the catalog still violates this in T09/T13 (D13).
- **Windows are resolved to absolute dates at layer 1** against `ctx.as_of`
  before the client is called (D7): `past_days` / `forecast_days` are convenient
  for the model but must never resolve against the wall clock, and an absolute
  window is what makes the cache key stable.
- **The daily series is bounded (D9)** on the same terms as GR2L's: capped at
  31 days, beyond which the tool returns summary statistics plus weekly
  aggregates and flags the truncation (T035). An absolute Archive window was
  the one remaining unbounded path; multi-month meteo aggregation is the
  database's job. **Not implemented**: neither wrapper caps the series today
  (T035).
- Output rows are **identical to GR2L's input row** (`DailyWeatherRow`: `Date`,
  `tm`, `tx`, `tn`, `rf`, `precip`, `w`, `gs`), so the two never need a mapping
  layer. `tx`/`tn` are required, not optional — FAO-56 Penman-Monteith derives
  the saturation-vapour-pressure term from them; field meanings and the two
  unit conversions (`w` in km/h, `gs` in J/cm²/day, both traced to GR2L's R
  core) are `weather_tool.md`'s. **This schema is frozen and implemented**
  (`schemas.DailyWeatherRow`, `weather_client._transpose`); the catalog's
  hourly fixture schema is stale (D13).
- **Station rows are derived, not stored** (D26). `wetter` is half-hourly and
  carries six of the seven fields; the per-field aggregation, the `tn`
  estimator, the `−7999` sentinel filter and the UTC→Europe/Berlin day
  boundary are specified in `weather_tool.md` § Station source. The derivation
  is fixed in code and part of the pinned surface (§5), because changing it
  moves every oracle — and a wrong day boundary shifts rain between days,
  desynchronising the series from the `outflow` and `swc` comparisons T19 is
  built on.
- **Three properties of the station data are measured scope limits, stated in
  the thesis rather than corrected** (D26): `tn` is **estimated** (no `Tmin`
  column; 0.25 °C mean / 0.48 °C p95 ambiguity against the naive alternative,
  inside FAO-56's tolerance — the one D12 objection that survives, and it is
  small), `gs` reads **~26 % below ERA5** (a gain offset, not noise — station
  forcing suppresses ET and biases modelled retention upward), and `precip`
  **undercatches** (~15 % on liquid days, ~50 % on frozen ones). The
  measurements, their decomposition and the pyranometer cross-check live in
  `weather_tool.md` § Station source; the consistency-over-accuracy argument
  that keeps them uncorrected is D26's.
- Requests beyond the 16-day horizon, before Archive coverage, or spanning the
  Archive/Forecast cutoff — servable by neither backend alone (D17) — must
  return a typed `not_available`, distinct from `error`: the basis of T18a.
  Unknown-variable questions (T18b) have no tool surface — the tool takes no
  variable argument and always returns the seven documented fields — so that
  abstention is the **agent's** to make, scored at the contract level with the
  docstring's variable list as its ground (D11). **Not implemented**: `weather.py` returns `ErrorResult` for
  every failure (D11).
- The tool returns the site's own `latitude`/`longitude`/`elevation` from
  `site.py`, never Open-Meteo's grid cell (`weather_tool.md` § The location is
  not a parameter). `WeatherResult` carries **no `elevation` field at all**
  (T037) — elevation is a site fact, not a weather output; GR2L's `hoehe_nn`
  is sourced from `site.py` by its own wrapper. Today the client still returns
  the grid-cell value under a description saying to use it as `hoehe_nn` — a
  trap for any oracle sharing the client; the fix is removal.
oi
### 3.4 `predict_green_roof_water_balance_tool` — **built**, layered GR2L
```python
def run_gr2l(rows, parameters) -> list[Gr2lResultRow]   # thin async HTTP client

async def predict_green_roof_water_balance_tool(
    roof_type,                       # wetland | non_irrigated_extensive |
                                     # irrigated_extensive | semi_intensive
    start_date=None, end_date=None,  # absolute window
    past_days=None, forecast_days=None,   # relative; resolved vs ctx.as_of (D7)
    initial_soil_moisture_pct=None,  # %θ; default: measured seed, see below
    albedo=None,                     # the one overridable physical parameter
    forcings=None,                   # TO BUILD: {"precip": {"2026-07-22": 50.0}}
    evaluate_against_measured=False, # TO BUILD: retrospective deviation stats
) -> dict
```
- Self-contained by default (docstring discloses internal fetching), overridable
  for counterfactuals. Internal fetches go through `ctx.weather` / `ctx.db` —
  same cache and as-of view the standalone tools see.
- **No `meteo_source` argument, and none is planned** (D12, narrowed by D26).
  The agent never names a weather source; the source is derived from the window
  in code, so the station path reaches GR2L through exactly the same
  `ctx.weather` seam the standalone tool uses — same rows, same units, same day
  boundary, one vocabulary. A retrospective GR2L run and a forecast run are
  therefore forced by different sources; the response echoes which, and the
  answer must disclose it whenever it is the station (§3.3's biases).
- `forcings` is keyed by the row's **own field names** (`precip`, `tm`, `tx`,
  `tn`) — one vocabulary with `DailyWeatherRow`, never `precip_mm` — and is
  sparse: unspecified days keep the fetched value (D8). It is echoed in the
  response for argument-checking.
- Returns `parameters`, `seed`, a bounded daily `data` series and a `summary`
  (`retention_mm`/`retention_pct`, `total_precip_mm`, `total_outflow_mm`,
  `min_substrate_storage_mm`, `min_swc_pct`, `drought_stress`,
  `retention_excludes_seed_day_runoff`). With `evaluate_against_measured`, adds
  mean/max |predicted − measured| in %θ over the window's overlap with the `swc`
  record (keeps the LLM out of bulk arithmetic in T19).
- Three outcomes: `success`, `not_available` (gravel roof; no seed available),
  `error` — the latter carrying `error_type: invalid_argument | upstream`
  (D16). This typed-abstention discipline is already implemented here (minus
  `error_type`) and is the model for §3.3.

**Day-1 seed: `seed_at = min(window_start, as_of)`** (D5). Never later than the
window opens — a March window seeded from a July reading is physically wrong —
and never later than `as_of`, which is the leakage bound. Today `swc.py` uses
`window_start` alone, which leaks whenever a case's window opens after its
`as_of`. Staleness flagging (`is_stale` beyond 7 days, with disclosure) and the
never-substitute-a-default rule (`not_available`, no generic `theta_01`) are
`gr2l_tool.md`'s (§ Where day 1's soil moisture comes from).

**GR2L is a remote service; oracles call it over HTTP too.** The model core is R,
deployed behind `POST {gr2l_api_base_url}/predict_gr2l` outside this repo — there
is no local implementation and we are not writing one. Oracles `import run_gr2l`
from the same client the tool uses. Rationale and the terms we accept:
- A local reimplementation would be a *second* model to keep in sync, and the
  oracle would then validate our port rather than the system under test.
- Determinism rests on three things instead of purity: the service is pinned by
  base URL plus a committed canary request/response hash (§5); GR2L is
  deterministic given `(rows, parameters)`; and responses are cached by exact
  request body, so a repeated rollout replays rather than re-requests.
- Cost, stated plainly: the first capture of any case needs network access. A
  cache miss while the service is down **fails the case** — it never substitutes
  a fallback. Service unavailability is a harness error, not an agent error, and
  is reported separately from scores.

**The tool speaks %θ; millimetres stay internal.** Implemented in `swc.py`; the
conversion and the per-layer unit contract are `gr2l_tool.md`'s (§ Units: %θ
in, %θ out). Retention/runoff stay in mm — fluxes, not states. **Wetland
exception**: its sensor saturates near 86 % θ, so the wetland reports
`swc_pct = null` in series and summary, and %-valued templates
(T09/T10/T21–T23) must not sample it. Its *seed* is still read and reported in
%θ (a lower bound whenever water is ponded) — the one place a wetland
percentage legitimately appears.

**Roof coverage — the gravel roof (`Kies`/`KD`/`QGravel`) is out of scope for
the model** (`gr2l_tool.md` § The gravel roof cannot be modelled). It remains
first-class for SQL, retrieval and plotting (families A, B, H); model-bearing
templates (D, G, part of E) must **not** sample it, and a modelling request
naming it is a legitimate `not_available`. Implemented as
`NON_MODELLABLE_ROOFS`, which also accepts the German/column aliases.

### 3.5 `calc_irrigation` — **to build**, deterministic decision rule
- **Returns a decision, not a volume** (D22): `irrigate: bool`, with the fixed
  dose stated alongside for disclosure. The dose is policy, not computation —
  the roof's 90th-percentile historical ET, a per-roof constant in
  `rules_constants.py` rendered into the ops manual like every other rule
  constant. No template asks for a per-case volume (T11 and T16b are bool).
- Docstring intent ("minimize runoff, avoid wilting, provide cooling")
  implemented as **fixed priority rules** — no LLM, no stochastic weights: never
  below wilting point → minimize expected runoff → pre-heat-day cooling.
  Constants and priorities from `rules_constants.py`, mirrored verbatim into the
  ops manual (T11 gold + phase-2 judge depend on this).
- Pure function; callable with stated values (T16b) or chain outputs (T11).
- **Input set unconfirmed — blocks P3 freeze** (D22, plan R7). Preliminary, from
  the algorithm's authors pending confirmation: SWC, precipitation, air
  temperature, radiation components, wind speed, air humidity. Until pinned,
  the signature, T11's gold chain (whether `get_weather_forecast_tool` joins
  it or the GR2L payload must echo the needed inputs) and the T11/T16b oracles
  stay unfrozen.

### 3.6 `plot_timeseries` — **to build**, self-contained, multi-source, spec-scored

```python
def plot_timeseries(
    series,          # list of SeriesSpec — each names a SOURCE, never data
    start, end,      # absolute window (relative forms resolved per D7)
    kind,            # line | bar | model_overlay | diff
                     # no agg argument: aggregation is DERIVED per variable (D20)
) -> dict            # resolved spec + summary stats + artifact_ref — NO series
```

- **The agent declares a source, never the data itself** (D14). Each `SeriesSpec`
  names where a series comes from and which variable to draw; the tool fetches it
  internally through the seams the other tools already use:

  | `source` | Fetch path | Selector fields | Bounded by |
  |---|---|---|---|
  | `measured` | `ctx.db` as-of view, fixed parameterized query | `table`, `column`, `roof` | as-of view (§5) |
  | `weather` | `ctx.weather` | `variable` (`DailyWeatherRow` field) | D7 window resolution |
  | `model` | `run_gr2l` through the shared client + cache | `roof_type`, `variable` (`GreenRoofDay` field), plus `initial_soil_moisture_pct` / `albedo` / `forcings` | D5 seed rule |

  Passing data *through* the model is forbidden — it is the row dump principle 3
  and D9 exist to prevent, and it degrades trajectory scoring into a
  float-transcription test (D14). Re-fetching is cheap because both live sources
  replay from the response cache (§5); in `replay` mode a plot never issues a
  live call.
- Self-contained on the same terms as §3.4: the plot tool inherits every leakage
  and determinism rule rather than restating them, **provided it resolves all
  three sources through `ctx`** and never opens its own DuckDB connection or
  `httpx` client (the mistake `swc.py` and both wrappers make today, §4).
- **Fixed parameterized query, no LLM SQL** — `measured` reaches the five as-of
  tables (`outflow`, `radiation`, `swc`, `tsoil`, `wetter`) through a closed
  vocabulary of columns and aggregations. Derived quantities (e.g.
  lysimeter-area-normalized outflow) must be *in* that vocabulary as a flag; they
  are not reachable by free SQL. State this scope limit in the thesis.
- **Returns no series at all** — only the resolved spec, summary statistics, and
  `artifact_ref`. The series' consumer is the renderer, not the LLM, so plotting
  is the one tool that satisfies D9 outright instead of being capped by it.
- **The resolved spec is the scored surface**: source and variable per series,
  resolved absolute range, the *derived* aggregation and resolution, unit and axis assignment
  per series, gap/truncation flags, and the modelling arguments echoed for any
  `model` series (argument-checkable exactly like §3.4's).
- **Daily resolution is forced for mixed plots.** `swc`/`wetter` are half-hourly;
  weather and GR2L are daily end to end (§3.3). Any plot combining a `model` or
  `weather` series with a `measured` one aggregates the measured series to
  calendar days (Europe/Berlin) with its variable's derived operator (D20) and
  reports the resolution in the spec.
- **Units, axes and aggregation are derived in code, never chosen by the
  model** (D20). Overlaying `precip` (mm/day) with `swc_pct` (%θ) needs two
  axes; fluxes aggregate by `sum`, states by `mean`; both assignments follow
  from the variable through the closed vocabulary, so the scored spec stays
  deterministic. There is no `agg` argument to fumble — the resolved spec
  echoes the derived operator per series instead.
- **Three outcomes, as in §3.4**: `success`, `not_available`, `error`. A `model`
  series for the gravel roof is `not_available` (`NON_MODELLABLE_ROOFS`), as is a
  `swc_pct` model series for the wetland (mm-only, §3.4); a `measured` series for
  either stays perfectly valid.
- Docstring: "fetches its own data; do not query the database separately for
  plotting" (basis of T24a's must-not).
- **Headless mode** — eval runs must not require the UI stack. Nothing renders
  server-side and no plotting library enters the Python dependency set. The
  series reaches the CopilotKit frontend **without passing through the model**
  (D20): the tool stashes it under a session-state key and the tool wrapper
  merges it into the tool-result event the chat replays — the proven
  `QUERY_RESULT_STATE_KEY` / FR15 pattern from `warehouse.py` — while the
  model-visible result stays spec + stats + `artifact_ref` (which names the
  stashed payload). Eval never reads the state key; rendering is an
  **unscored side effect**.

## 4 ScenarioContext — the single injection seam — **to build**

```python
class ScenarioContext:
    def __init__(self, clock, db_path, weather_client, http_cache):
        self.clock = clock                       # () -> datetime (D18)
        # Views: timestamp <= clock(). A view-recreating *factory*, not a bare
        # connection, because every reconnect must rebuild the views (D18).
        self.db = DuckDbQueryExecutor(
            connection_factory=lambda: connect_asof(db_path, clock)
        )
        self.weather = weather_client            # called with today=ctx.as_of (D17)
        self.cache = http_cache

    @property
    def as_of(self): return self.clock()

def build_toolset(ctx, docstrings=None) -> list[Tool]: ...   # make_* factories
```
- Per eval case the harness builds one context —
  `ScenarioContext(lambda: case.as_of, PINNED_DB, CachedOpenMeteo(...),
  ResponseCache(CACHE_DIR))` — and runs §6's rollout against it. Production
  builds the same object once at import with `clock = site_now` and the same
  cache (D18).
- **Explicit dependency injection, not contextvars and not monkeypatching**
  (D3). Contextvars are specifically ruled out: `mlflow.genai.evaluate` worker
  threads drop them, which already broke `CostMeter` role attribution in the
  text2sql experiments. `mock.patch` remains acceptable in oracle unit tests only.
- Three code paths bypass this seam today and must be routed through it:
  `swc.py` opens its own DuckDB connection from global settings, both tool
  wrappers import `fetch_daily_weather` directly, and the text2SQL sub-agent
  reaches the warehouse executor singleton from two places —
  `query_database_tool` and the pipeline's `DuckDbExplainValidator` (D19).

## 5 Determinism & pinning

| Component | Prompt layer | Computation | Data |
|---|---|---|---|
| root instruction + tool docstrings | **optimized** | live | — |
| `text_to_sql_agent` | frozen (tuned) | live | DB via as-of views |
| `search_docs` | n/a | live, deterministic | corpus snapshot + index, hashed |
| `get_weather_forecast_tool` | n/a | station: pure, offline · Open-Meteo: live once, then **cached** | ctx.db as-of views (station) · Open-Meteo, cache keyed by request |
| `predict_green_roof_water_balance_tool` | n/a | live, **remote HTTP**, cached | ctx.weather / ctx.db; pinned presets |
| `calc_irrigation` | n/a | live, pure | `rules_constants.py` |
| `plot_timeseries` | n/a | live, deterministic | ctx.db as-of views · ctx.weather · run_gr2l, all cached |
| LLM | — | T=0, pinned dated version, cached | — |

- Pins committed to the repo: `water.duckdb` sha256, corpus commit + index hash,
  `rules_constants.py` version, GR2L roof presets, **GR2L base URL + a canary
  request/response hash** (the service exposes no version string, so a canary
  that changes means the service changed), LLM model version string, **the
  reflection LM's dated version** (a second model, distinct from the task model —
  it shapes every proposal GEPA makes, so an unpinned reflector makes a run
  unrepeatable even with the task model fixed), **the candidate prompt names and
  seed versions** in the MLflow registry (D27), dependency lockfile (adk,
  litellm, mlflow, gepa, rank_bm25, duckdb — the optimizer entry point is
  `@experimental`, R8), and the **station derivation**
  (`weather_tool.md` § Station source: the per-field aggregation, the
  Europe/Berlin day boundary, the sentinel filter, and the station record's
  first/last complete day as read from the pinned DB) — the DB hash alone does
  not pin how rows are reduced to days.
- **Response cache** (D4): one mechanism, two callers. Keyed by the sha256 of the
  exact request (URL + sorted query params for Open-Meteo; `data[]` + parameters
  for GR2L), stored as committed JSON alongside the cases. It is a cost and
  availability measure *and* the replay mechanism for the two live dependencies
  the eval cannot inject away — keyed by request, not by case, so it never masks
  a changed request. Every input to the request — window resolution (D7) and
  backend choice (D17) — derives from `ctx.as_of`, so a key never depends on
  when the rollout runs. A miss in `replay` mode is a hard failure, never a live
  fetch.
- **Time-travel leakage**: `connect_asof` opens an in-memory DuckDB, attaches the
  pinned file read-only as `src`, and creates one `main.<table> AS SELECT * FROM
  src.<table> WHERE timestamp <= :as_of` view per table (`outflow`, `radiation`,
  `swc`, `tsoil`, `wetter`). Querier and plot tool resolve unqualified names in
  `main`, so they see only the as-of view and the pinned file is never modified —
  no `*_raw` rename, and the text2SQL semantic layer keeps the table names it
  already knows. The GR2L seed uses `min(window_start, as_of)` (D5); relative
  windows resolve against `as_of` (D7). T19 windows end ≤ `as_of` (harness
  assertion). The view bounds *rows*, not SQL time functions — the second
  escape, `CURRENT_DATE` / `now()` in generated SQL, is closed by the querier's
  sqlglot rewrite (D19c). Station weather reads the `wetter` view through the
  same executor, so it inherits the bound rather than restating it (D26) — the
  one weather path that cannot see past `as_of` at all.
- Residual LLM nondeterminism handled statistically: 3 seeds per condition,
  paired evaluation on identical cases, paired bootstrap.

## 6 Optimization harness (outer loop) — **to build**

GEPA is reached **through MLflow**, not driven directly (D27). MLflow owns the
`GEPAAdapter` — `MlflowGEPAAdapter` implements both `evaluate` and
`make_reflective_dataset` — so nothing here writes an adapter and there is no
`run_rollout(candidate, case)` signature: **GEPA owns the loop, MLflow owns the
adapter, and one rollout is one call of `predict_fn`.** What this repo writes is
`predict_fn`, the scorers, and the candidate surface in the prompt registry.

```python
def predict_fn(inputs: dict) -> dict:          # one case; MLflow calls it per record
    ctx  = ScenarioContext(lambda: inputs["as_of"], PINNED_DB, weather_client, cache)
    text = {name: mlflow.genai.load_prompt(uri).template   # patched to candidate text
            for name, uri in PROMPT_URIS.items()}          # inside optimize_prompts
    agent = build_root_agent(instruction=text.pop("root_instruction"),
                             tools=build_toolset(ctx, docstrings=text))
    return parse_contract(runner.run(agent, inputs["question"]))

mlflow.genai.optimize_prompts(
    predict_fn=predict_fn,
    train_data=cases,                          # {"inputs": …, "expectations": …}, §6.1
    prompt_uris=list(PROMPT_URIS.values()),    # one URI per optimizable component
    optimizer=GepaPromptOptimizer(reflection_model=…, max_metric_calls=…),
    scorers=[answer, trajectory, retrieval, abstention],   # §7
    aggregation=weighted_mean,                 # mandatory here, see §7
)
```

- **The candidate surface is the MLflow prompt registry.** `optimize_prompts`
  patches `PromptVersion.template` process-wide for the duration of a batch, so
  candidate text reaches the system **only** through a registry read whose prompt
  *name* matches a candidate key. A component that is never read is silently left
  un-optimized, and MLflow's "prompts were not used" warning is the only signal —
  hence the assertion in T096. Corollary: the patch is process-global, so exactly
  one candidate is evaluable per process at a time.
- **`predict_fn` builds everything per record.** It receives one `inputs` dict and
  constructs the context, the toolset and the agent from it. D6's `docstrings`
  argument is unchanged — only its *source* is new. MLflow evaluates records in a
  `ThreadPoolExecutor`, so §4's no-ambient-state property is not a nicety here but
  the precondition for the search to be correct at all: it is the same thread
  boundary that already dropped `CostMeter`'s ContextVars (D3).
- **Reflection reads MLflow traces and scorer rationales.**
  `MlflowGEPAAdapter.make_reflective_dataset` builds each component's reflective
  record from `{current_text, trace spans, score, inputs, outputs, expectations,
  rationales}`. The textual feedback GEPA reflects on is therefore whatever the
  scorers put in `Feedback.rationale` — trajectory diff vs gold, SQL errors,
  abstention outcome — not a separately assembled event-log digest. ADK spans
  reach those traces through MLflow's ADK OTel translation, so the event data is
  present; the *route* is the trace, not the log.
- **Run ledger**: `optimize_prompts` already logs per-iteration candidate text,
  per-scorer metrics and an eval-results table as artifacts. What T094 adds is the
  pins and case identity per rollout.
- **No ADK evalset** (D27). ADK's `AgentEvaluator` cannot be handed a constructed
  agent, and its metrics are args-exact trajectory plus ROUGE over the final
  message — neither §7's metrics nor D21's rule. Where ADK's own evaluators are
  wanted, MLflow wraps two of them as scorers fed from `expectations`, still
  without the file.

### 6.1 Case envelope

Cases are a **pretty-printed JSON array per split** (`eval/cases/{train,val,
test_seen,test_unseen}.json`), each element projecting **directly** onto MLflow's
`train_data` shape — the same rows feed the search and the §7 measurement run:

```jsonc
{"inputs":       {"question": "…", "as_of": "2026-03-14T08:00:00+01:00",
                  "case_id": "T09-0142", "template_id": "T09", "params": {…}},
 "expectations": {"status": "answered", "answer": false, "unit": null,
                  "answer_metric": "scored",        // "skipped" for family H (D14)
                  "tolerance": {"kind": "abs", "value": 0.1},
                  "expected_tool_calls": [{"name": "…", "args": {…}}],
                  "must_not_tools": ["…"], "gold_docs": [],
                  "argument_checks": [{"tool": "…", "path": "forcings.precip.2026-03-15",
                                       "op": "eq", "value": 50.0}],
                  "pins": {…}}}
```

The envelope is **fixed by the API, not chosen**: MLflow requires only `inputs`
per record and passes `expectations` verbatim to every scorer, and those are the
only two channels it delivers. Consequences:

- `inputs` carries everything `predict_fn` needs; `expectations` everything a
  scorer needs. There is no third place to put anything.
- **Materialized answers carry their pins.** D26's "provenance moves for free
  because no oracle is materialized yet" has a corollary: once one is, the case
  must record the surface it was materialized against — `water.duckdb` sha256,
  GR2L canary, station-derivation version, the resolved weather source — or a
  later change to any of them invalidates answers with nothing to detect it.
- `argument_checks` are **declarative**, so D21's argument conjuncts stay in data
  and the scorer never branches per template.
- `expected_tool_calls` keeps ADK's own key name: it costs nothing and keeps
  `mlflow.genai.scorers.google_adk.ToolTrajectory` available as a cross-check
  against our own trajectory scorer.
- Per-template constants (tolerance, must-not set, gold docs) live in the template
  YAML and are **copied into each case** at generation, so a scorer reads one
  record and never needs a second file.
- **Array, not JSONL, and generated rather than authored.** The format is free —
  MLflow accepts a list of dicts, so nothing upstream privileges either — and an
  indented array is chosen for review: a JSONL diff reports one changed line per
  case without saying which field moved, while an indented one diffs per field,
  which is what a committed ground truth needs. Two conditions make that hold:
  the file is **emitted, never hand-edited** (same discipline as the rendered ops
  manual — a hand-fixed case silently breaks its derivation from the template and
  its `pins` stamp), and the generator emits **deterministically** — fixed key
  order, cases sorted by `case_id`, `indent=2`, trailing newline — or every
  regeneration produces a diff that is pure noise.

## 7 Scoring (summary — details in catalog §1.2)

- Answer: exact / tolerance vs materialized oracle answer. **Skipped, not
  scored 0, where the contract answer is `null`** — family H's deliverable is an
  artifact, so the scorer reports answer-metric *coverage* alongside the score
  (D14, catalog §1.1). Comparing `null` against an oracle would cost H's ~6 %
  share of the suite for cases that were fully correct.
- Trajectory: **binary per case** (D21) — 1 iff every gold tool was called, no
  must-not tool was called, and the **argument checks** for families D/G pass
  (`forcings` / `albedo` / `initial_soil_moisture_pct` present and plausible);
  else 0. No partial credit and no extra-call penalty: calls beyond the gold
  set are free, and their per-arm mean is a diagnostic, not a score. Tool names
  in gold trajectories are the **registered** names (D2).
- Retrieval: recall@k, gold-query and agent-query variants.
- Abstention: accuracy on unanswerables **and** false-abstention rate, never
  aggregated. The scored quantity is the **agent's** contract `status`, on §2's
  terms.
- Excluded from every aggregate above: cases with an `upstream` tool error or a
  `replay` cache miss, marked `harness_error` — counted separately per arm,
  with cache-miss reasons broken out. `invalid_argument` errors never exclude:
  the case stays in and scores through the normal metrics (D16). **This exclusion
  is a property of the measurement path, which is ours; the search path inside
  `optimize_prompts` has no exclusion channel and is protected structurally
  instead (D28).**
- Diagnostics: fixer iterations, steps, tokens, latency, **mean extra calls
  per arm** (tools called beyond the gold set — unscored under D21, but the
  efficiency signal must stay visible), **`parse_failure`** —
  a final message that parses to neither status value. Reported apart from
  answer accuracy, so an optimizer degrading the output format is
  distinguishable from one degrading reasoning (D15).

**Mechanism** (D27). Each metric above is one MLflow `Scorer` returning a
`Feedback`. Two things ride on that shape:

- **`Feedback.rationale` is the search signal, not decoration.** MLflow forwards
  it into GEPA's reflective dataset (§6), so what a scorer *says* about a failure
  is what the reflector reads. A scorer that returns a bare float optimizes
  blind.
- **Per-scorer values stay separate all the way into GEPA**, which receives them
  as `objective_scores` and can hold a Pareto front over them — the four metrics
  drive selection without being pre-blended into one number.
- **An explicit `aggregation` callable is mandatory, not optional.** It receives
  the raw scorer outputs and is where "skipped, not 0" is implemented for family
  H's answer metric (D14): MLflow otherwise raises on a non-numeric scorer value,
  which is exactly what a legitimately abstaining answer scorer returns. Coverage
  is reported beside the score.

## 8 Catalog amendments

The self-contained GR2L tool supersedes the catalog's pure-function trajectories
(`questions.md` §1.5 still describes `predict_soil_moisture(meteo_series,
initial_swc, roof, params)` and therefore adds `query_database` + `get_weather`
to every model chain — under the built tool those tools leave the *gold set*: a
candidate that skips them must not fail recall, and under D21 a candidate that
still calls them pays nothing unless a must-not says otherwise):

| Template | Gold trajectory now |
|---|---|
| T09, T10 | `{predict_green_roof_water_balance_tool}` |
| T11 | **reframed bool** (D22): `{predict_green_roof_water_balance_tool, calc_irrigation}` expected; final chain pending the D22 input confirmation (whether `get_weather_forecast_tool` joins it) |
| T19 | `{predict_green_roof_water_balance_tool(evaluate_against_measured=True)}`; must-not `get_weather_forecast_tool` (a `text_to_sql_agent` cross-check is a free extra call, D21) |
| T21 | `{predict_green_roof_water_balance_tool(forcings=…)}` (a prior `get_weather_forecast_tool` fetch is a free extra call — fetch-then-override valid, D21) |
| T22 | `{predict_green_roof_water_balance_tool(albedo=…)}` |
| T23 | `{predict_green_roof_water_balance_tool(initial_soil_moisture_pct=…)}`; must-not `get_weather_forecast_tool` |
| T26 | union of composed calls, argument-checked |

`get_weather_forecast_tool`'s standalone identifiability rests on
T13/T14/T15b/T18a (T18b's gold set is empty, D23); its distractor role
strengthens in T19/T23.

**Roof sampling per family (§3.4).** `{roof}` is drawn from two pools, because
the model covers fewer segments than the database:

| Family | Pool |
|---|---|
| A (pure SQL), B, H with `measured` series only | gravel · irrigated ext. · non-irrigated ext. · semi-intensive · wetland |
| D, G, model-bearing E (T07, T11, T26), **H with a `model` series** | irrigated ext. · non-irrigated ext. · semi-intensive |
| %θ-valued answers (T09, T10, T21–T23), **`swc_pct` model series in H** | as above, **minus wetland** |

H therefore splits across all three pools rather than sitting in the first (D14):
a plot is only as modellable as its most demanding series. A `model_overlay`
naming the gravel roof is a legitimate `not_available` — a free abstention case
in a family that otherwise has none, sampled deliberately from *outside* the
pool table (the pools govern answerable cases; T081).

- `initial_soil_moisture_pct` and every soil-moisture answer are in **%θ**; no
  template asks the agent for millimetres of substrate storage. Retention/runoff
  templates stay in mm/L.
- A modelling question about the **gravel roof** is an abstention case, not an
  error: gold `status = not_available`. Worth one deliberate template family
  alongside T17b — the gravel roof is the most-sampled roof in family A, so the
  model must learn that its availability is family-dependent.
- Question phrasing uses the **agent's** roof vocabulary
  (`non_irrigated_extensive`, …) or natural language mapped to it by the alias
  map — not raw column names (`Extensiv1`, `Sumpf2`), which the catalog still
  uses in T01 (D13).

## 9 Repository layout & prerequisites

Actual layout (the same three-layer separation is achieved inside the package,
so there is no top-level `src/core/`, `src/clients/`, `src/harness/` tree):

```
src/water_assistant_agent/assistant/
  agents/root_agent/   agent.py (candidate injection, D6) · text_to_sql_tool.py
  agents/text_to_sql/  frozen sub-agent: agent · pipeline · executor · fixers
  tools/               gr2l.py · weather.py · warehouse.py        (ADK layer 1)
                       gr2l_client.py · weather_client.py         (pure layer 2)
                       swc.py · site.py · schemas.py              (pure layer 3)
                       gr2l_tool.md · weather_tool.md             (tool specs)
  prompts/             temporal.py (scenario clock) · agent_instructions.py
  settings.py          WATER_ASSISTANT_* pydantic-settings
  [to build]  context.py · cache.py · rules_constants.py · irrigation.py
              retrieval/bm25_index.py · tools/{search_docs,plot}.py
data/water.duckdb      (sha256 to pin)
eval/          [to build] corpus/ cache/ templates/ oracles/ cases/
specs/agent_architecture/   agent_architecture.md · questions.md · plan.md
```

Blocking order before data generation (see `plan.md` for the task-level plan):
1. **Injection seam** — `ScenarioContext`, as-of views, `make_*` factories
   (root tools **and** the text2SQL sub-agent, D19), scenario clock, response
   cache, model pinning, and the prompt-registry entries that carry the candidate
   surface (D27 — a component that is not a registered prompt cannot be
   optimized). Blocks *everything*: no case is reproducible without it.
2. **GR2L / weather completeness** — `forcings`, `evaluate_against_measured`,
   typed `not_available` on weather, window-range validation, bounded series,
   **the station source and its daily derivation** (D26). Blocks D, G, and T18a
   (T18b is agent-level, no tool prerequisite — D11).
3. `rules_constants.py` + `calc_irrigation` + rendered manual + ranges pages —
   blocks B, E, F.
4. `search_docs` + corpus + stable section IDs — blocks B, E, F recall metrics.
5. `plot_timeseries` — blocks H. Now downstream of prerequisite 2 as well: its
   `model` and `weather` series ride the same window resolution, seed rule and
   cache as the standalone tools (D14).
6. Lysimeter areas in the semantic layer — unblocks T12.
7. Alias map + typo fixes — blocks paraphrase generation.
8. Harness on 3 pilot templates (T01, T07, T09) with the handwritten
   instruction — then **freeze**.

*Reinstated prerequisite (was cancelled under D12):* deriving daily rows from
the half-hourly `wetter` table. It was dropped with `meteo_source="db"`; D26
brings it back as the retrospective source — not as an agent-facing selector,
and now covering all seven fields rather than `tx`/`tn` alone.

---

## 10 Decision log

**D1 — Arbitration rule.** Where this document and the code disagree about
*mechanism* (names, signatures, module paths, data sources), the code wins and
this document is rewritten. Where they disagree about an *evaluation property*
(determinism, leakage, injectability, typed abstention, addressable optimizable
surface), this document wins and the code changes. Those properties are the
validity conditions of the thesis claim.

**D2 — Tool names and arguments follow the code** (names in §0 and §3). One shape
choice is not merely nominal: `albedo` is a scalar argument, not
`params={"albedo": …}`, because flat scalars are the right shape for ADK function
declarations and nested dicts measurably degrade tool-calling accuracy.

**D3 — The injection seam is explicit DI, not contextvars.** Argued in §4.
Rejected: a contextvar-scoped `ScenarioContext` — far less invasive, but
`mlflow.genai.evaluate` worker threads drop ContextVars, already observed in this
repo when `CostMeter` lost its role attribution.

**D4 — Weather stays live Open-Meteo; determinism comes from a committed,
request-keyed response cache shared with GR2L** (§5). Rejected: a separate
fixture path — `FixtureWeatherClient`, a file schema, a fixture generator. One
mechanism serves both live dependencies and is the *only* replay path. Archive
responses are already stable upstream, so what the cache really pins is forecast
windows: a forecast for a future date changes daily and is otherwise
unreproducible.

**D5 — Seed rule: `seed_at = min(window_start, as_of)`.** Argued in §3.4. Each
one-sided rule fails a case the other handles: `≤ as_of` alone seeds a
retrospective March window from a July reading, and `≤ window_start` alone (what
the code does today) leaks post-`as_of` sensor data into forecast cases.

**D6 — Candidate injection via factories; the production singleton is
preserved.** `build_root_agent(instruction, docstrings, tools, model)` becomes
the real constructor; the module-level `root_agent` stays as a thin production default so
`bootstrap.py` and the CopilotKit frontend keep importing it unchanged. Tool
docstrings become candidate-owned by being applied to the factory-produced
callables.

**D7 — Relative windows are resolved to absolute dates at layer 1** (§3.3), and
their **validation splits by cause** (D16) — the part that carries evaluation
weight. `past_days`/`forecast_days` stay in the signatures because they help the
model and match both tool specs, but they never reach a client. Ranges (0–92
back, 0–16 ahead) are validated in code, not merely documented. A well-formed
request no backend can serve — beyond the 16-day horizon, before Archive
coverage, spanning the D17 cutoff — returns `not_available`, the genuine scope
limit T18a scores; a malformed argument — a negative day count,
`start_date > end_date`, an unparseable date — returns `error` with
`error_type="invalid_argument"`, never `not_available`, so agent fumbles cannot
pollute the false-abstention metric.

**D8 — `forcings` is keyed by `DailyWeatherRow`'s own field names.** Spec in
§3.4: one vocabulary, so a counterfactual argument and the row it overrides never
need a translation table between them.

**D9 — Principle 3 restated honestly, and enforced.** "Never row dumps" was
already violated by both built tools, which return an unbounded daily array. The
resolution is not to drop the series — a bounded series *is* the tool's
product — but to require that the summary alone is always sufficient to answer,
and to cap the series (§3.3) in **both** wrappers, GR2L and weather alike
(T035): an absolute Archive window was the one path still able to return an
unbounded array.

**D10 — The JSON answer contract is eval-only.** The production root instruction
serves a chat UI and answers in prose. The contract is part of the *candidate*
instruction, which is exactly the layer the optimizers own; the handwritten
baseline carries it verbatim. Note that "it would break the frontend" is *not*
the load-bearing reason — the frontend could render a contract card as
`TextToSqlResult.tsx` already does for SQL. The real reason is D15: production
needs a turn type the contract cannot express and the eval cannot exercise.

**D11 — Typed `not_available` is extended to the weather tool** (§3.3). GR2L
already distinguishes scope limits from faults; without the same on weather the
abstention metric has no tool-level mechanism there at all. Rejected: a
`variables` selector validated against the seven fields, which would change the
tool contract *and* the Open-Meteo request shape for the sake of one template
family — and would hand T18b a tool surface it is deliberately meant to lack,
since that abstention is the agent's alone (D23).

**D12 — `meteo_source` is dropped** (narrowed by D26; the surviving half is the
load-bearing one). No agent-facing weather-source selector: the agent names a
window and a question, never a provenance. What has *not* survived is "one
source, one unit path, one cache key" — D26 adds the station as a second source,
resolved in code. D12's supporting data argument (no `Tmin`, a per-interval
`Tmax`) was re-measured and holds only weakly: the `tn` estimator's ambiguity is
0.25 °C (§3.3).

**D13 — `questions.md` must be re-derived, not patched.** It carries its own
contradictions with the built system; §8 supersedes the model-chain rows and
plan T002 enumerates the rest. The method is the decision: re-derive the
conventions section first, then sweep the catalog — case-by-case patching is how
the contradictions accumulated.

**D14 — `plot_timeseries` is multi-source, and the agent declares a source, not
data.** The earlier spec bound the tool to the DB alone while already offering
`model_overlay` and `diff` as `kind` values — internally inconsistent, since
neither is expressible without a model series; the resolved spec is §3.6.
Rejected: letting the agent pass the series in as an argument. It drives the data
through the LLM (principle 3 and D9 forbid it), and it turns family D/G-style
argument checking into a test of whether the model retyped forty floats
correctly — non-deterministic, and measuring transcription rather than tool
selection. Also rejected: a session-state handle to a prior tool result (the
`QUERY_RESULT_STATE_KEY` pattern in `warehouse.py`), which would avoid the
re-fetch but makes a plot scorable only in the context of the turn before it and
ends family H's single-call property; it stays available as a chat-path
optimization and is not the eval mechanism. (D20 reuses the state *mechanism* as
the render transport, which is a different thing: it moves the series to the
frontend, never back into the model's context or another tool's arguments.)
Consequences: H splits across the §8 roof pools, the tool returns no series to
the model at all, and P5 now depends on P1 *and* P2.
The same argument applies at the *output* end: family H answers `null` rather
than echoing a `plot_spec` into the final message (catalog §1.1) — otherwise a
transcription slip in a perfectly-executed call scores as a wrong answer. §7's
answer metric is skipped for those cases, not scored 0.

**D15 — Clarifying questions are out of contract in eval, retained in
production.** The contract's `status` is two-valued and both values end the
turn, so a clarifying question ("which January did you mean?") has no
representation: it would parse as a completed answer with `answer: null`. This
is safe in eval only because the catalog's §1.6 validity filter *discards*
sampled parameters whose oracle is ambiguous — no case should warrant one. It is
therefore mandatory that the candidate instruction **explicitly forbid** asking
one ("answer under your best interpretation; never ask a question back"): the
production instruction's own clarification rule would otherwise leak into
candidates and fire on a DE paraphrase or an alias-heavy phrasing, producing an
unparseable turn scored as a wrong answer. Production keeps clarification, which
is why D10's split stands — a unified contract would need a
`needs_clarification` status that no case exercises, so no optimizer would ever
receive signal on it, and the shipped behaviour would be the one branch the
thesis never measured. Residual scope limit: a genuinely ambiguous paraphrase
that survives the filter forces the model to guess (plan R6).

**D16 — Tool `error` splits by cause; only unavoidable errors are harness
exclusions.** Criterion: *exclude what the candidate could not have avoided and
the harness cannot reproduce; score what the candidate deterministically
causes.* `ErrorResult` gains `error_type: "invalid_argument" | "upstream"`.

- **`invalid_argument`** — deterministic argument validation, all of it pre-I/O
  (unknown `roof_type`, out-of-range `albedo` /
  `initial_soil_moisture_pct`, malformed dates, negative or inverted windows —
  D7 draws the line against the *unservable* windows that stay
  `not_available`). **Never excludes.** The case is
  scored normally: an unrecovered failure surfaces as a wrong answer, false
  abstention, or `parse_failure`; a self-repair costs nothing at all under
  D21 — the retried tool is in the gold set and extra calls are free — which
  preserves the recovery behaviour an optimizer should be allowed to keep.
  Excluding these was exploitable: errors
  on hard cases would leave the denominator and *raise* a candidate's average.
- **`upstream`** — HTTP/DB/config failures and `CacheMissError`. Any occurrence
  marks the case `harness_error`: excluded from every aggregate, counted
  separately **per candidate arm** (a systematic rate difference between arms is
  itself a finding, not noise to hide). Deliberately presence-based, unlike
  `invalid_argument`: after an upstream error the world no longer matches the
  pinned case, so even a "recovered" answer is not a measurement of that case.
- The frozen sub-agent's inner `query_database_tool` errors stay inside its
  fixer loop and are not scanned; only a **terminal** `text_to_sql_agent`
  failure counts, classified `upstream` (frozen-component fault). Its per-arm
  count is still a delegation-quality signal — the candidate owns the phrasing —
  which the separate report makes visible. Detection is harness-side, keyed on
  the **tool name**, because the sub-agent's payloads carry no `error_type`
  (T039 touches only the `schemas.py` `ErrorResult` the gr2l/weather wrappers
  return) and its terminal result is often LLM prose: a root-level
  `text_to_sql_agent` result that parses as a dict with `status == "error"`
  *is* the terminal failure; a prose or success result is never one and scores
  normally. Nothing is added to the production payload.
- A `replay` cache miss is `upstream` but dual-cause: incomplete capture
  (harness fault) or a legitimate candidate call diverging from the captured
  pattern (an extra baseline run, a differently chosen albedo).
  `CacheMissError` therefore carries the unmatched request plus a diff against
  the nearest captured request for the same endpoint, and the harness reports
  miss *reasons* per arm: candidate-caused misses bias scores toward the capture
  candidate's call pattern and must be visible (interacts with R1/T084).
- `not_available` keeps its meaning — a genuine scope limit. Bad arguments are
  never folded into it: the false-abstention metric would otherwise mix agent
  fumbles with wrong declines, two behaviours with different fixes. The agent is
  still never asked to tell "the world is broken" apart from "I cannot answer
  that".

**D17 — Weather backend choice is a pure function of the case clock;
`fetch_daily_weather` takes a required `today` argument.** T018 cannot be
implemented as "the client reads `ctx.as_of`": `_choose_backend` lives in the
pure layer, and D3 gives that layer no channel to a context. Instead the client
gains a required `today: date` keyword — **no wall-clock default**, so a caller
that forgets it fails immediately rather than silently leaking the wall clock —
and the `site_now` import is deleted from `weather_client`. The cutoff *policy*
stays in the client, next to the URLs it selects between; only the clock is
injected. The load-bearing consequence is cache stability, not leakage: the D4
key hashes URL + params, and a wall-clock cutoff flips a recent-past window from
Forecast to Archive ~92 real days after capture. From then on `replay` misses a
committed entry that is sitting in the cache and perfectly valid, and — worse — a
`record` re-run *succeeds* against Archive, substituting observed values for the
forecast values the case's oracle was materialized from: a silently moved ground
truth. With `today = as_of` the request is time-invariant (replay hits forever)
and a post-deadline recapture fails loudly at the API instead (R1). Nothing makes
a forecast-backend window recapturable after its deadline; `today` converts that
impossibility from silent drift into a hard error.

**D18 — The context clock is a callable; only eval freezes it.** The earlier §4
sketch stored `as_of` as a construction-time datetime. Production builds its
context once, at import, next to the `root_agent` singleton (D6) — a frozen
`as_of` there reintroduces the midnight-staleness bug `temporal.py` exists to
avoid, on every reader T018 adds. `ScenarioContext` therefore holds
`clock: () -> datetime` and exposes `as_of` as a property that evaluates it.
Eval binds `lambda: case.as_of` — frozen per case, which is correct there;
production binds `site_now`. This is what makes §2's "one code path" claim
actually true: identical readers, differing only in the callable they were
constructed with. Corollary: any component that captures `as_of` at construction
instead of reading the clock (as-of connection factories included) must
re-evaluate it per use, or it is wrong in production. Concretely, the as-of
executor checks `clock().date()` on every query and rebuilds its connection —
the reconnect path it already has for IO errors — when the date has moved
since connect; a frozen eval clock never triggers it, so eval and production
run identical code (T010, T022).

**D19 — The frozen text2SQL sub-agent is rebuilt per context; frozen means the
text, not the objects.** Mechanism in §3.1: the DB seam is welded in at import
time, so binding it per case means rebuilding the chain executor → validator →
pipeline → tools → `Agent` → `AgentTool` around byte-identical prompt text. The
rejected alternative was resolving `as_of` from ADK session state inside the tool
(`tool_context.state["as_of"]` does cross the `AgentTool` boundary — the
`QUERY_RESULT_STATE_KEY` mechanism proves it): far less code, but a missing key
silently falls back to the unbounded view — the exact leakage the testbed
exists to prevent, failing quietly — and it couples the ADK-free layer 2/3 to
ambient stringly-keyed state, the shape D3 already rejected. Explicit
construction makes an unbound case a loud error before any rollout runs.
Corollaries: (a) the EXPLAIN validator needs no as-of *correctness* — EXPLAIN
reads schema, and the views are schema-identical to the base tables — but it
rides the same `ctx.db` executor so the sub-agent has exactly one DB seam;
(b) the sub-agent's outward `description` — the text `AgentTool` presents to
the root model, i.e. the surface the candidate's routing is optimized
against — is candidate-owned like every other tool docstring (§2), while
everything inside the sub-agent stays frozen; (c) the as-of view bounds *rows*,
not SQL time functions — `CURRENT_DATE` / `now()` in generated SQL evaluate
against the process wall clock and silently resolve a wrong window inside a
correctly bounded view, so the querier's existing sqlglot pass rewrites those
nodes to the literal `as_of`. Rewrite, not reject: the sub-agent that emits
them is dateless by design (date resolution is the root agent's job, and a
candidate that forwards relative phrasing leaves the frozen builder no clock to
resolve against), so rejection would penalize behaviour nothing can optimize.
In production the rewrite binds the same clock (D18) and is a semantic no-op.
The clock reaches the rewrite as an explicit factory parameter —
`make_query_database_tool(executor, clock)`, `build_text_to_sql_agent(executor,
clock, …)` — because the executor deliberately does not carry it; and the pass
must now re-emit the SQL it validated, where today `warehouse.py` parses only
for the read-only guard and executes the original string.

**D20 — The plot series reaches the frontend through session state, never the
model; aggregation is derived, not an argument.** T063 (no series to the model)
and T067 (the frontend draws) are reconciled by the transport in §3.6 — the
mechanism `warehouse.py` already proves works. Eval ignores the state key
entirely: the scored surface is the resolved spec, so the transport adds no
scoring coupling. This is not the D14-rejected data handle — nothing re-enters
the model's context or another tool's arguments. Rejected
alternatives: a server-side artifact store (new HTTP endpoint plus a retention
story the testbed doesn't otherwise need) and ADK's artifact service (couples
the render path to plumbing the AG-UI/CopilotKit bridge may not forward).
Second half: a single plot-level `agg` cannot serve a mixed plot — `precip`
aggregates by `sum`, `swc_pct` by `mean` — so aggregation joins units and axes
as a **derived** property of the variable (T065): the closed vocabulary carries
the operator, the model never chooses it, and the resolved spec echoes the
derived operator per series, keeping it on the scored surface without a
model-owned field to fumble.

**D21 — Trajectory scoring is binary; there are no optional calls and no
extra-call penalty.** The catalog's "unordered set P/R/F1 + mild extra-call
penalty" was doubly undefined: the penalty had no magnitude, and the two clauses
double-count — with gold sets of one to three tools, one extra call inside the
F1 already costs up to a third of the score, which is not "mild", while a
penalty outside the F1 charges the same call twice. Replaced by a per-case
binary: **trajectory = 1 iff gold ⊆ called and called ∩ must-not = ∅** (the
family D/G argument checks fold in as further conjuncts), else 0. Consequences,
accepted deliberately: (a) `(opt)` is deleted from the vocabulary — with no
penalty, an optional call and an unlisted call are indistinguishable, so T21's
fetch-then-override and a T19 DB cross-check are free automatically; (b) empty
gold sets are well-defined (T18b, D23); (c) a shotgun candidate that calls
every tool scores perfect trajectory on templates without a must-not —
mitigated because the coverage matrix makes every tool a must-not distractor
somewhere (a blanket policy hard-fails those templates — D24 closed the former
`predict_…`/`calc_irrigation` gaps in that claim), the ~6-step cap bounds
the excess physically, and mean extra calls per arm joins the §7 diagnostics so
efficiency stays visible without being scored.

**D22 — `calc_irrigation` returns a decision, not a volume** (§3.5). The deployed
algorithm answers "irrigate?" as a bool and the dose is fixed policy, so there is
no per-case volume computation and no numeric volume
template survives: **T11** becomes the *predictive* twin of T07 ("does the
{roof} roof need irrigation **tomorrow**, per the standard rule?" — T07 runs on
measured SWC, T11 on the model's prediction), and **T16b** becomes the same
bool on stated values through the calculator (`{calc_irrigation}`), pairing
with T16a's docs-only route (`{search_docs}`) on identical inputs — the a/b
pair now probes docs-vs-calculator routing, and the two questions' phrasing
must cue the route (settle the wording in T002). The rule's unconfirmed input
set — which leaves this template pair, the calculator's signature and its
oracles unfrozen — is tracked as §3.5 and plan R7.

**D23 — The catalog's T18a/T18b assignment was swapped; this document's labels
are canonical.** `questions.md` had T18a = unknown variable and T18b = beyond
horizon; this document and T031 use the reverse, and the reverse is what is
meant. Canonical: **T18a = window-unservable** (beyond the 16-day horizon,
before Archive coverage, spanning the D17 cutoff — the typed tool-level
`not_available` of D11), train-eligible, gold `{get_weather_forecast_tool}`.
**T18b = unknown variable**, agent-level with no tool surface (D11), holdout —
and its gold set is **empty** under D21: the abstention is grounded in the
docstring's variable list, so a verification call proves nothing the agent's
context lacks and is neither required nor penalized; the abstention metric
alone carries T18b's signal. The holdout therefore tests generalization from
tool-signaled abstention (T18a: call → typed `not_available` → abstain) to
abstention with no tool signal at all — the harder direction. The rejected
alternative, requiring the call as "evidence-based abstention", contradicted
D11's own ground and penalized the agent that correctly trusts the documented
contract.

**D24 — Routing probes name the wrong route explicitly; every tool gets a real
distractor slot.** Under D21 an unlisted call is free, so a template
discriminates routing only through its must-not set — the gold set alone
cannot: a candidate that takes both routes satisfies it. As the catalog stood,
`calc_irrigation` was unlisted on T16a and `search_docs` on T16b, so a
candidate calling both on every irrigation question scored trajectory 1 on both
halves and the a/b pair probed nothing; `predict_green_roof_water_balance_tool`
was unlisted on *every* template, falsifying the §3-matrix claim ("every tool
appears at least once as a distractor") that D21's shotgun mitigation leans on.
Resolved: (a) the T16 pair carries **symmetric must-nots** — T16a (docs route)
forbids `calc_irrigation`, T16b (calculator route) forbids `search_docs` —
restoring the probe in both directions. Accepted risk, stated plainly: a
candidate that looks the rule up before calculating fails T16b even though the
behaviour is defensible, which makes the T002 phrasing cue load-bearing — the
wording must make the intended route unambiguous. (b) The model tool gains its
one distractor slot on **T04** (measured past outflow — a recorded fact for
which a simulation is the epistemically wrong source), making the matrix claim
true and closing the model-tool gap in D21's mitigation.

**D25 — T24b answers a single pp-gap.** The catalog left T24b's answer shape
undefined ("numeric ×2 or pp-gap — define one"), and "numeric ×2" was never
expressible at all: the §1.1 contract's `answer` is a single scalar. T24b is
rephrased to the mean soil-moisture **difference** between the two extensive
roofs over the month (pp, T03's tolerance convention), which fits the contract
unchanged, has an exact oracle, and keeps both roofs in the question — so the
T24a/T24b presentation-verb minimal pair still shares its information need.
Rejected alternatives: extending the contract to numeric arrays (touches §1.1,
the T071 parser and the T072 scorer for one template) and narrowing to a single
roof (breaks the twin, since T24a plots both).

**D26 — The retrospective weather source is the site's own station, not ERA5;
it is served uncorrected, and its biases are scope limits.** Open-Meteo's
Archive backend is ERA5 — a ~31 km reanalysis, not observation — while the
facility runs its own instrument on the roofs it is being asked about. Where the
record covers a window outright it is now the source (§3.3); Archive keeps deep
history and Forecast keeps the future.

*The evaluation properties that justify it*, which are the reason this is a D-entry
and not a one-line pointer:
- **Retrospective cases become fully offline.** The station path is a pure
  function of a file §5 already hashes, so it needs no D4 cache entry, no
  `record` pass, and no network. D17's central hazard — a forecast window that
  cannot be recaptured after its deadline, and an Archive re-capture silently
  substituting observations for the forecast values an oracle was built on —
  simply does not arise inside the record.
- **Leakage closes structurally.** Station weather reads the as-of view, so it
  cannot see past `as_of`. Archive can and does.
- **Provenance stays single-valued per window.** The station serves only windows
  it covers entirely; partial coverage falls through to the existing Open-Meteo
  routing. No new `not_available` class, no per-day source switching, no mixed
  series — which also means no new abstention template and no change to T18a.
- **The timing is the cheapest it will ever be.** No oracle is materialized yet,
  so provenance moves for free; after generation it would invalidate the suite.

*The accuracy claim, stated honestly.* The station is unambiguously better for
temperature (tm +0.69 °C, tx +1.05 °C vs ERA5 at r = 0.99 — the rooftop and
urban signal a 31 km cell cannot carry) and is the conceptually correct wind
forcing (GR2L computes `u2 = w/3.6` and wants 2 m wind; Open-Meteo serves 10 m,
a positive ET bias `weather_tool.md` already flags). It is **not** better
everywhere: `gs` reads ~26 % low against its own on-site pyranometers, and
`precip` undercatches by ~15 % on liquid days and ~50 % on frozen ones (§3.3).
The justification adopted is therefore **not** "measurements beat a model" — for
radiation and precipitation that claim is false at this site. It is
**consistency with the validation data**: the lysimeters whose `outflow` and
`swc` records the model is scored against (T19) sat under this gauge and this
pyranometer, so forcing GR2L from the same instruments makes prediction and
measurement commensurable, where ERA5-forcing-versus-lysimeter-truth mixes two
worlds. The thesis states both biases and their direction (station forcing
suppresses ET and biases modelled retention upward).

*Rejected, deliberately, on the user's decision:* calibrating `gs` against the
`radiation` table's pyranometers over their 2025-03 → 2025-10 overlap; taking
`gs` from Open-Meteo while everything else comes from the station; and falling
back to ERA5 on days the gauge cannot see frozen precipitation. Each would
improve a number at the cost of a derived, non-reproducible correction sitting
between the instrument and the oracle — and the per-day variants would break the
single-provenance property above. Uncorrected station data is reproducible from
the pinned DB by anyone; a fitted factor is not.

*Residual limits to carry:* the record spans 2025-01-01 → 2026-04-27, so
2024-H2 retrospectives have `swc`/`tsoil` but no station weather and fall to
Archive; the record ends before the Forecast backend's recent-past window
begins, so T030b's 64-vs-92-day routing bug is **not** resolved by this change;
and `tn` is estimated rather than measured.

**D27 — GEPA is reached through MLflow; the candidate surface is the prompt
registry; the ADK evalset is dropped.** The entry point is
`mlflow.genai.optimize_prompts(predict_fn, train_data, prompt_uris, optimizer=
GepaPromptOptimizer(…), scorers, aggregation)`. Mechanism in §6.

*Rejected alternatives:*
- **A hand-written `GEPAAdapter` driving `gepa.optimize` directly.** MLflow
  already ships `MlflowGEPAAdapter` implementing both required methods *and* the
  per-iteration logging (candidate text, per-scorer metrics, eval tables) that we
  would otherwise re-write against the sink the text2SQL experiments already use.
  Writing our own buys nothing and duplicates a moving part. It stays the fallback
  if the experimental API moves (R8).
- **ADK's `AgentEvaluator` + `.evalset.json`.** Three independent disqualifiers:
  candidate text has no channel into an agent looked up by module path, so every
  iteration would score the production singleton; its trajectory metric is
  args-exact with no must-not concept, which cannot express D21; and its answer
  metric is ROUGE over the final message, which cannot express §7's
  tolerance/skip/abstention split. The format exists for the **inverse loop** —
  capture-replay regression of a *fixed* agent against a recorded run, where the
  agent is the constant and the file is the fixture — and it binds the agent by
  module path precisely because of that assumption. Its resemblance to our ground
  truth (question, expected response, expected tool calls) is a coincidence of
  shape.
- **One concatenated prompt holding all optimizable text.** It would deny GEPA
  per-component mutation (`components_to_update` is the granularity) and
  attribute every reflective observation to a single blob, collapsing the
  addressable optimizable surface that D1 names as a validity condition.

*Evaluation-validity conditions this pins:*
- Candidate text reaches the system **only** through a registry read inside
  `predict_fn` whose prompt name matches a candidate key. A component that is
  never read is silently frozen while appearing optimizable — the failure is
  invisible in the results and visible only in MLflow's "prompts were not used"
  warning, which is why T096 asserts on it.
- The reflective signal is `Feedback.rationale` (§7). Scorer prose is part of the
  search machinery; a float-only scorer optimizes blind.
- The case envelope is `inputs` / `expectations` (§6.1) because those are the only
  two channels MLflow delivers — the ground-truth format is therefore dictated,
  not designed.
- The injection patch is process-global for the duration of a batch: one
  candidate per process, and any other in-process reader of those registered
  prompts sees candidate text.

**D28 — `harness_error` exclusion is a measurement-path property; the search path
is protected by construction, not by masking.** D16 excludes `upstream` errors and
`replay` cache misses from every aggregate. Inside `optimize_prompts` that is not
expressible: a `predict_fn` exception is swallowed into a string output and scored
normally, and GEPA consumes one float per record with no exclusion channel
(`score` may be `None` only when there are no scorers at all, and GEPA sums it).

*Rejected:* scoring such cases **0** — it penalizes a candidate for an outage and
biases selection toward whichever candidate happened to run while the service was
up. Also rejected: **neutral fill at the batch mean** — it preserves the
denominator while shrinking the effective *n*, hiding the loss rather than
reporting it.

*Adopted:* the exclusion stays in **our** scoring (§7), which produces the
numbers the thesis claims rest on. The search is protected structurally instead:
it runs in `replay` mode over a cache T084 asserts complete, and station-served
windows need no network at all (D26), so an upstream error during search is rare
*by construction* rather than handled after the fact. The residual is made
visible — a `harness_error` scorer contributes 0 **and** its per-arm count is
reported, so a search polluted by outages is a stated fact instead of silent
noise in the fitness signal. Corollary: `train_data` is pre-filtered to cases
with complete capture, so a case that cannot replay never enters the search.
