# System architecture — green-roof water management assistant (thesis testbed)

Companion to [`questions.md`](./questions.md) (question-template catalog) and to
the two tool specs, [`gr2l_tool.md`](../../src/water_assistant_agent/assistant/tools/gr2l_tool.md)
and [`weather_tool.md`](../../src/water_assistant_agent/assistant/tools/weather_tool.md).
Implementation order lives in [`plan.md`](./plan.md).

The system is a **frozen testbed**: the thesis contribution is the evaluation of
prompt-optimization techniques, not the assistant. After the prerequisites in §9
land, only the items marked *optimizable* may change.

Every §3 entry carries a status tag against the implementation, and §10 records
the decisions taken where this document and the code specified different things.
The arbitration rule (D1) is: **the code wins on mechanism, this document wins on
evaluation properties.**

---

## 0 Status at a glance

| Component | Name the agent sees | Status |
|---|---|---|
| root agent | `root_agent` | **built** — not candidate-injectable yet (D6) |
| text2SQL sub-agent | `text_to_sql_agent` | **built** — no as-of views, not ctx-rebindable yet (D3, D19) |
| weather | `get_weather_forecast_tool` | **built** — live, uncached, no typed abstention, wall-clock backend cutoff (D4, D11, D17) |
| GR2L water balance | `predict_green_roof_water_balance_tool` | **built** — no `forcings` / `evaluate_against_measured` (D8) |
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
│ fetch_daily_weather (HTTP → Open-Meteo, cached) ·                   │
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
  are not addressable per candidate.
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
  `CURRENT_DATE` rewrite, D19) are the only per-case state.

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
- Backends resolve from the window: Archive for windows starting more than ~92
  days before `as_of`, Forecast otherwise (`_choose_backend`). The cutoff is
  measured from a **required explicit `today` argument** — `ctx.as_of`, never
  the wall clock — so the chosen URL, and with it the D4 cache key, is a pure
  function of the case (D17). **Not implemented**: `_choose_backend` reads
  `site_now()` today, which flips a recent-past window's backend ~92 real days
  after capture and rots the committed cache (D17). Archive values are stable
  upstream; forecast values are not, which is what the cache pins (D4). A window
  *spanning* the cutoff is servable by neither backend alone and returns a typed
  `not_available` (D17, §3.3 below).
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
  database's job.
- Output rows are **identical to GR2L's input row** (`DailyWeatherRow`), so the
  two never need a mapping layer:

  | Field | Meaning | Unit |
  |---|---|---|
  | `Date` | calendar day, `YYYY-MM-DD` | — |
  | `tm` | mean air temperature | °C |
  | `tx` | **max** air temperature | °C |
  | `tn` | **min** air temperature | °C |
  | `rf` | mean relative humidity | % |
  | `precip` | precipitation sum | mm |
  | `w` | mean wind speed | **km/h** |
  | `gs` | global radiation sum | **J/cm²/day** |

  `tx`/`tn` are required, not optional: FAO-56 Penman-Monteith derives the
  saturation-vapour-pressure term from them. `w` and `gs` are in the units GR2L's
  R core actually consumes (`u2 = w/3.6`, `Rs = gs × 0.01`) — these disagree with
  the labels on GR2L's Pydantic schema; trust the math, not the label. **This
  schema is frozen and implemented** (`schemas.DailyWeatherRow`,
  `weather_client._transpose`); the catalog's hourly fixture schema is stale
  (D13).
- Requests beyond the 16-day horizon, before Archive coverage, or spanning the
  Archive/Forecast cutoff (D17) must return a typed `not_available` — distinct
  from `error` — the basis of T18a. Unknown-variable questions (T18b) have no
  tool surface — the tool takes no variable argument and always returns the
  seven documented fields — so that abstention is the **agent's** to make,
  scored at the contract level with the docstring's variable list as its
  ground (D11). **Not implemented**: `weather.py` returns `ErrorResult` for
  every failure (D11).
- The tool returns the site's own `latitude`/`longitude`/`elevation`, never
  Open-Meteo's grid cell — a promise the *wrapper* keeps, since site geometry
  lives one layer up from the client (`site.py`). `WeatherResult` therefore
  carries **no `elevation` field at all** (T037): elevation is a site fact, not
  a weather output — GR2L's `hoehe_nn` is a roof parameter sourced from
  `site.py` by its own wrapper. Today the client still returns the grid-cell
  value under a description saying to use it as `hoehe_nn` — a trap for any
  oracle sharing the client; the fix is removal, because the client cannot know
  the surveyed height without breaking its reusability contract.

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
- **No `meteo_source` argument, and none is planned** (D12). Past windows are
  served by the Open-Meteo Archive backend, not by the `wetter` table. One
  weather source, one unit path, one cache key.
- Returns `parameters`, `seed`, a bounded daily `data` series and a `summary`
  (`retention_mm`/`retention_pct`, `total_precip_mm`, `total_outflow_mm`,
  `min_substrate_storage_mm`, `min_swc_pct`, `drought_stress`,
  `retention_excludes_seed_day_runoff`). With `evaluate_against_measured`, adds
  deviation metrics vs the `swc` record (keeps the LLM out of bulk arithmetic in
  T19).
- Three outcomes: `success`, `not_available` (gravel roof; no seed available),
  `error` — the latter carrying `error_type: invalid_argument | upstream`
  (D16). This typed-abstention discipline is already implemented here (minus
  `error_type`) and is the model for §3.3.

**Day-1 seed: `seed_at = min(window_start, as_of)`** (D5). Never later than the
window opens — a March window seeded from a July reading is physically wrong —
and never later than `as_of`, which is the leakage bound. Today `swc.py` uses
`window_start` alone, which leaks whenever a case's window opens after its
`as_of`. Beyond `swc.STALE_AFTER_DAYS` (7) the seed is flagged `is_stale` and the
answer must disclose it. When nothing trustworthy exists, the tool returns
`not_available` and never substitutes GR2L's generic `theta_01 = 20 mm` (which
sits above `Ssubmax` for three of the four roofs).

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

**The tool speaks %θ; millimetres stay internal.** Implemented in `swc.py`:
- `initial_soil_moisture_pct` is **%θ**; the tool converts with
  `S_mm = (θ%/100) × SH_mm` using the roof's own `SH` before calling `run_gr2l`.
- Outputs convert back the same way: `swc_pct` is the primary quantity, with
  `Ssub` mm alongside for the water balance.
- Retention/runoff (`OUT`, `retention_mm`) stay in mm — fluxes, not states.
- **Wetland exception.** Its store is a 17 mm fleece mat plus water ponded to the
  90 mm standpipe height; the sensor saturates near 86 % θ (≈14.7 mm), so above
  the mat θ is not recoverable from mm. The wetland reports `swc_pct = null` in
  the series and summary, and %-valued templates (T09/T10/T21–T23) must not
  sample it. Its *seed* is still read and reported in %θ (a lower bound whenever
  water is ponded) — the one place a wetland percentage legitimately appears.

**Roof coverage — the gravel roof is out of scope for the model.** GR2L models
`wetland`, `non_irrigated_extensive`, `irrigated_extensive`, `semi_intensive`.
The **gravel roof (`Kies`/`KD`/`QGravel`) has no preset and is not modellable** —
no substrate, so `SH` and the measured `Ssubmin`/`Ssubmax` do not exist. It
remains first-class for SQL, retrieval and plotting (families A, B, H).
Model-bearing templates (D, G, part of E) must **not** sample it; a modelling
request naming it is a legitimate `not_available`. Implemented as
`NON_MODELLABLE_ROOFS`, which also accepts the German/column aliases.

### 3.5 `calc_irrigation` — **to build**, deterministic decision rule
- Docstring intent ("minimize runoff, avoid wilting, provide cooling")
  implemented as **fixed priority rules** — no LLM, no stochastic weights: never
  below wilting point → minimize expected runoff → pre-heat-day cooling.
  Constants and priorities from `rules_constants.py`, mirrored verbatim into the
  ops manual (T11 gold + phase-2 judge depend on this).
- Pure function; callable with stated values (T16b) or chain outputs (T11).

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
        # Views: timestamp <= clock(). An executor over a view-recreating
        # *factory*, not a bare connection: the executor reconnects on IO
        # errors and whenever clock().date() has moved since connect
        # (midnight rollover in production; a frozen eval clock never
        # triggers it), a fresh in-memory connection has no views, and each
        # new connection re-evaluates the clock (D18, D19).
        self.db = DuckDbQueryExecutor(
            connection_factory=lambda: connect_asof(db_path, clock)
        )
        self.weather = weather_client            # called with today=ctx.as_of (D17)
        self.cache = http_cache

    @property
    def as_of(self): return self.clock()

def build_toolset(ctx, docstrings=None) -> list[Tool]: ...   # make_* factories
```
- Per eval case: `ScenarioContext(lambda: case.as_of, PINNED_DB,
  CachedOpenMeteo(...), ResponseCache(CACHE_DIR))` → `build_toolset` → root
  agent with candidate instruction → `Runner.run` → parse contract → score.
  Production: identical code, `clock = site_now`, same cache (D18).
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
| `get_weather_forecast_tool` | n/a | live once, then **cached** | Open-Meteo; cache keyed by request |
| `predict_green_roof_water_balance_tool` | n/a | live, **remote HTTP**, cached | ctx.weather / ctx.db; pinned presets |
| `calc_irrigation` | n/a | live, pure | `rules_constants.py` |
| `plot_timeseries` | n/a | live, deterministic | ctx.db as-of views · ctx.weather · run_gr2l, all cached |
| LLM | — | T=0, pinned dated version, cached | — |

- Pins committed to the repo: `water.duckdb` sha256, corpus commit + index hash,
  `rules_constants.py` version, GR2L roof presets, **GR2L base URL + a canary
  request/response hash** (the service exposes no version string, so a canary
  that changes means the service changed), LLM model version string, dependency
  lockfile (adk, litellm, rank_bm25, duckdb).
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
  assertion). The view bounds *rows*, not SQL time functions: `CURRENT_DATE` /
  `now()` in generated SQL evaluate against the process wall clock and resolve
  a wrong window inside a correctly bounded view, silently — the querier's
  sqlglot pass rewrites them to the literal `as_of` (D19).
- Residual LLM nondeterminism handled statistically: 3 seeds per condition,
  paired evaluation on identical cases, paired bootstrap.

## 6 Optimization harness (outer loop) — **to build**

```
def run_rollout(candidate, case):        # called by GEPA; the harness never loops
    ctx    = ScenarioContext(lambda: case.as_of, PINNED_DB, weather_client, cache)
    agent  = build_root_agent(instruction=candidate.instruction,
                              docstrings=candidate.docstrings,
                              tools=build_toolset(ctx, candidate.docstrings))
    result = runner.run(agent, case.question, state={"as_of": case.as_of})
    return score(result, case), feedback_text(result, case)   # §7 · ADK event log
```
- Optimizer: **GEPA** with MLflow. GEPA owns the loop (no `propose()`/`update()`
  API); we implement its adapter: `evaluate` → `run_rollout` per case,
  `make_reflective_dataset` → per-example textual feedback from the event log
  (trajectory diff vs gold, SQL errors, abstention outcome)
- Run ledger per rollout: candidate id, case id, model version, all pins,
  trajectory, tokens/cost. MLflow is already wired for the text2SQL experiments
  and is the default sink.
- Cases stored as JSONL (source of truth) with generated ADK evalset JSON, so
  `AgentEvaluator` and the custom oracle harness read the same data.

## 7 Scoring (summary — details in catalog §1.2)

- Answer: exact / tolerance vs materialized oracle answer. **Skipped, not
  scored 0, where the contract answer is `null`** — family H's deliverable is an
  artifact, so the scorer reports answer-metric *coverage* alongside the score
  (D14, catalog §1.1). Comparing `null` against an oracle would cost H's ~6 %
  share of the suite for cases that were fully correct.
- Trajectory: unordered set P/R/F1 + mild extra-call penalty; hard must-nots
  where specified; **argument checks** for families D/G (`forcings` / `albedo` /
  `initial_soil_moisture_pct` present and plausible). Tool names in gold
  trajectories are the **registered** names (D2).
- Retrieval: recall@k, gold-query and agent-query variants.
- Abstention: accuracy on unanswerables **and** false-abstention rate, never
  aggregated. The **agent's** contract `status` is what is scored; a tool's
  `status='not_available'` is the upstream mechanism that makes abstaining
  correct (D11), not the measured quantity.
- Excluded from every aggregate above: cases with an `upstream` tool error or a
  `replay` cache miss, marked `harness_error` — counted separately per arm,
  with cache-miss reasons broken out. `invalid_argument` errors never exclude:
  the case stays in and scores through the normal metrics (D16).
- Diagnostics: fixer iterations, steps, tokens, latency, **`parse_failure`** —
  a final message that parses to neither status value. Reported apart from
  answer accuracy, so an optimizer degrading the output format is
  distinguishable from one degrading reasoning (D15).

## 8 Catalog amendments

The self-contained GR2L tool supersedes the catalog's pure-function trajectories
(`questions.md` §1.5 still describes `predict_soil_moisture(meteo_series,
initial_swc, roof, params)` and therefore adds `query_database` + `get_weather`
to every model chain — under the built tool those extra calls are *wrong* and
would be penalized):

| Template | Gold trajectory now |
|---|---|
| T09, T10 | `{predict_green_roof_water_balance_tool}` |
| T19 | `{predict_green_roof_water_balance_tool(evaluate_against_measured=True)}`; must-not `get_weather_forecast_tool`; `text_to_sql_agent` optional |
| T21 | `{predict_green_roof_water_balance_tool(forcings=…)}`; `get_weather_forecast_tool` optional (fetch-then-override valid) |
| T22 | `{predict_green_roof_water_balance_tool(albedo=…)}` |
| T23 | `{predict_green_roof_water_balance_tool(initial_soil_moisture_pct=…)}`; must-not `get_weather_forecast_tool` |
| T26 | union of composed calls, argument-checked |

`get_weather_forecast_tool`'s standalone identifiability rests on
T13/T14/T15b/T18; its distractor role strengthens in T19/T23.

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
   cache, model pinning. Blocks *everything*: no case is reproducible without
   it.
2. **GR2L / weather completeness** — `forcings`, `evaluate_against_measured`,
   typed `not_available` on weather, window-range validation, bounded series.
   Blocks D, G, and T18a (T18b is agent-level, no tool prerequisite — D11).
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

*Cancelled prerequisite:* deriving daily `tx`/`tn` from the half-hourly `wetter`
table. It existed only to serve `meteo_source="db"`, which is dropped (D12).

---

## 10 Decision log

**D1 — Arbitration rule.** Where this document and the code disagree about
*mechanism* (names, signatures, module paths, data sources), the code wins and
this document is rewritten. Where they disagree about an *evaluation property*
(determinism, leakage, injectability, typed abstention, addressable optimizable
surface), this document wins and the code changes. Those properties are the
validity conditions of the thesis claim.

**D2 — Tool names and arguments follow the code.** `text_to_sql_agent`,
`get_weather_forecast_tool`, `predict_green_roof_water_balance_tool`;
`initial_soil_moisture_pct` not `initial_swc`; a scalar `albedo` argument, not
`params={"albedo": …}` (flat scalars are also the right shape for ADK function
declarations — nested dicts measurably degrade tool-calling accuracy). Trajectory
scoring matches registered names throughout.

**D3 — The injection seam is explicit DI, not contextvars.** A contextvar-scoped
`ScenarioContext` would be far less invasive, but `mlflow.genai.evaluate` worker
threads drop ContextVars — already observed in this repo when `CostMeter` lost
its role attribution. Tools are built by `make_*` factories closing over `ctx`.

**D4 — Weather stays live Open-Meteo; determinism comes from a committed,
request-keyed response cache shared with GR2L.** No separate fixture format, no
`FixtureWeatherClient` file schema, no fixture generator: one cache mechanism
serves both live dependencies and is the *only* replay path. Archive responses
are already stable upstream, so the cache mainly pins forecast windows (a
forecast for a future date changes daily and is otherwise unreproducible). The
cache runs in `record` mode during capture and `replay` mode during evaluation,
where a miss fails the case loudly.

**D5 — Seed rule: `seed_at = min(window_start, as_of)`.** `≤ as_of` alone seeds a
retrospective March window from a July reading; `≤ window_start` alone (what the
code does today) leaks post-`as_of` sensor data into forecast cases. The minimum
satisfies both constraints and reduces to each rule in the case it was designed
for.

**D6 — Candidate injection via factories; the production singleton is
preserved.** `build_root_agent(instruction, docstrings, tools, model)` becomes
the real constructor; the module-level `root_agent` stays as a thin production default so
`bootstrap.py` and the CopilotKit frontend keep importing it unchanged. Tool
docstrings become candidate-owned by being applied to the factory-produced
callables.

**D7 — Relative windows are resolved to absolute dates at layer 1.**
`past_days`/`forecast_days` stay in the tool signatures (they help the model and
match both tool specs), but the wrappers convert them against `ctx.as_of` before
calling any client. This closes the wall-clock escape and makes the cache key
stable. Ranges (0–92 back, 0–16 ahead) are validated in code, not merely documented,
and validation outcomes split by cause (D16): a well-formed request no backend
can serve — beyond the 16-day horizon, before Archive coverage, spanning the
D17 cutoff — returns `not_available`, the genuine scope limit T18a scores; a
malformed argument — a negative day count, `start_date > end_date`, an
unparseable date — returns `error` with `error_type="invalid_argument"`,
never `not_available`, so agent fumbles cannot pollute the false-abstention
metric.

**D8 — `forcings` keyed by the row's own field names.** `{"precip": {"2026-07-22":
50.0}}`, not `{"precip_mm": …}` — one vocabulary with `DailyWeatherRow`.
Supported fields: `precip`, `tm`, `tx`, `tn`. Sparse; unspecified days keep the
fetched value. `evaluate_against_measured` returns mean/max |predicted −
measured| in %θ over the window's overlap with the sensor record.

**D9 — Principle 3 restated honestly, and enforced.** "Never row dumps" was
already violated by both built tools, which return an unbounded daily array. A
bounded model series *is* the tool's product and stays; the rule is that the
summary is always present and sufficient to answer, and the series is capped
(default 31 days, beyond which the tool returns the summary plus weekly
aggregates and says so in the payload). The cap binds **both** wrappers — GR2L
and weather alike (T035): an absolute Archive window was the one path still
able to return an unbounded array.

**D10 — The JSON answer contract is eval-only.** The production root instruction
serves a chat UI and answers in prose. The contract is part of the *candidate*
instruction, which is exactly the layer the optimizers own; the handwritten
baseline carries it verbatim. Note that "it would break the frontend" is *not*
the load-bearing reason — the frontend could render a contract card as
`TextToSqlResult.tsx` already does for SQL. The real reason is D15: production
needs a turn type the contract cannot express and the eval cannot exercise.

**D11 — Typed `not_available` is extended to the weather tool.** GR2L already
distinguishes scope limits from faults; T18a needs the same on weather (beyond
horizon, before Archive coverage, spanning the D17 cutoff). Without it the
abstention metric has no tool-level mechanism. Unknown-variable questions
(T18b) are the deliberate exception: the tool has no variable argument — it
always returns the seven documented fields — so no tool mechanism is possible
and the abstention is the agent's alone, scored via the contract `status` with
the docstring's variable list as its ground. The rejected alternative, a
`variables` selector validated against the seven fields, would change the tool
contract and the Open-Meteo request shape for the sake of one template family.

**D12 — `meteo_source` is dropped.** The tool always sources weather from
Open-Meteo (Archive for past windows). One source, one unit path, one cache key —
and the `wetter` table has no `Tmin` column, while its stored `Tmax` is a
per-interval max, so the DB path would have required a derivation step whose only
consumer was itself.

**D13 — `questions.md` must be re-derived, not patched.** It carries its own
contradictions with the built system: an *hourly* fixture schema missing `tx`/`tn`
(§1.4), the pure-function `predict_soil_moisture` and its inflated trajectories
(§1.5), sub-daily phrasing in T09/T13, raw column names as roof parameters in
T01, and `query_database` as the root-level tool name. §8 above supersedes the
model-chain rows; the rest is tracked as a task in `plan.md`.

**D14 — `plot_timeseries` is multi-source, and the agent declares a source, not
data.** The earlier spec bound the tool to the DB alone while already offering
`model_overlay` and `diff` as `kind` values — internally inconsistent, since
neither is expressible without a model series. Plots of GR2L output and weather
are in scope; the tool resolves them itself through `ctx.weather` and `run_gr2l`.
The rejected alternative is letting the agent pass the series in as an argument:
it drives the data through the LLM (the row dump principle 3 and D9 forbid), and
it turns family D/G-style argument checking into a test of whether the model
retyped forty floats correctly — non-deterministic, and measuring transcription
rather than tool selection. A session-state handle to a prior tool result (the
`QUERY_RESULT_STATE_KEY` pattern in `warehouse.py`) would avoid the re-fetch, but
makes a plot scorable only in the context of the turn before it and ends family
H's single-call property; it stays available as a chat-path optimization and is
not the eval mechanism. (D20 reuses the state *mechanism* as the render
transport, which is a different thing: it moves the series to the frontend,
never back into the model's context or another tool's arguments.) The re-fetch is cheap because both live sources replay
from the §5 cache. Consequences: H splits across the §8 roof pools, the tool
returns no series to the model at all, and P5 now depends on P1 *and* P2.
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
  abstention, or `parse_failure`; a self-repair costs only the extra-call
  trajectory penalty — proportionate, and it preserves the recovery behaviour an
  optimizer should be allowed to keep. Excluding these was exploitable: errors
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
  which the separate report makes visible.
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
impossibility from silent drift into a hard error. A window spanning the cutoff
is servable by neither backend alone and returns a typed `not_available` (§3.3),
joining D11's list.

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
text, not the objects.** The sub-agent's DB seam is welded in at import time:
`query_database_tool` executes against the module-level warehouse executor, and
the pipeline's `DuckDbExplainValidator` imports the same singleton
(`dry_run.py`) — so the as-of bound (§5) cannot reach either without rebuilding
the chain executor → validator → pipeline → tools → `Agent` → `AgentTool`.
`build_text_to_sql_agent(executor, clock, description=None)` does exactly that,
reassembling byte-identical prompt text around the case's `ctx.db`; the module
singleton remains as the production default built from it, mirroring D6. The
ctx-bound `query_database_tool` closure preserves the exact function name,
signature and docstring — ADK derives the tool declaration from them, and the
frozen instruction names the tool. Per-case state is *only* the DB binding:
transpiler, fixers, models and every prompt string are shared. The rejected
alternative was resolving `as_of` from ADK session state inside the tool
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
and T067 (the frontend draws) are reconciled by the mechanism `warehouse.py`
already proves works: the tool stashes the series under a session-state key and
the tool wrapper merges it into the tool-result event the chat replays
(`QUERY_RESULT_STATE_KEY` / FR15), while the model-visible result stays
spec + summary stats + `artifact_ref` — `artifact_ref` names the stashed
payload; nothing is rendered or stored server-side. Eval ignores the state key
entirely: the scored surface is the resolved spec (§3.6), so the transport adds
no scoring coupling. This is not the D14-rejected data handle — nothing
re-enters the model's context or another tool's arguments. Rejected
alternatives: a server-side artifact store (new HTTP endpoint plus a retention
story the testbed doesn't otherwise need) and ADK's artifact service (couples
the render path to plumbing the AG-UI/CopilotKit bridge may not forward).
Second half: a single plot-level `agg` cannot serve a mixed plot — `precip`
aggregates by `sum`, `swc_pct` by `mean` — so aggregation joins units and axes
as a **derived** property of the variable (T065): the closed vocabulary carries
the operator, the model never chooses it, and the resolved spec echoes the
derived operator per series, keeping it on the scored surface without a
model-owned field to fumble.
