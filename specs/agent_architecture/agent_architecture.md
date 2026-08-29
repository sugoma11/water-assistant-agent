# System architecture — green-roof water management assistant (thesis testbed)

What the system is, how it behaves, which guarantees it provides. Companions:
[`questions.md`](./questions.md) (the evaluation dataset — templates, conventions,
splits), [`decisions.md`](./decisions.md) (rejected alternatives and validity
conditions), [`findings.md`](./findings.md) (dated, verified measurements),
[`plan.md`](./plan.md) (implementation order), and the tool specs
[`gr2l_tool.md`](../../src/water_assistant_agent/assistant/tools/gr2l_tool.md),
[`weather_tool.md`](../../src/water_assistant_agent/assistant/tools/weather_tool.md),
[`irrigation_tool.md`](../../src/water_assistant_agent/assistant/tools/irrigation_tool.md).

The system is a **frozen testbed**: the thesis contribution is the evaluation of
prompt-optimization techniques, not the assistant. Once frozen, only the items
marked *optimizable* may change.

**Arbitration.** Where this document and the code disagree about a *mechanism*, the
code wins and this document is corrected; where they disagree about an *evaluation
property* — determinism, leakage, injectability, typed abstention, the addressable
optimizable surface — this document wins and the code changes. **Status lives in
one place**, §0's table; everything after it describes the target system in the
present tense.

---

## 0 Implementation status

**built** — matches this document · **partial** — exists, diverges · **to build** —
does not exist. The middle column is the registry of names the root model sees;
these strings are what trajectory scoring matches.

| Component | Name the agent sees | Status | Gap |
|---|---|---|---|
| root agent | `root_agent` | partial | module-level singleton, so instruction and docstrings are not addressable per candidate; step cap unset; floating model alias, no `temperature=0`, no prompt cache |
| text2SQL sub-agent | `text_to_sql_agent` | partial | executor not context-bound (both the inner query tool and the pipeline's EXPLAIN validator read the module singleton); no as-of views; no `CURRENT_DATE` rewrite; lysimeter areas and the alias map missing from the semantic layer |
| weather | `get_weather_forecast_tool` | partial | no station source, no source resolution, no `WeatherClient` seam or cache, so every rollout fetches live; no typed `not_available`; series uncapped; the client's wall-clock endpoint selection and its day-limit constant are deleted; `WeatherResult.elevation` is deleted |
| GR2L water balance | `predict_green_roof_water_balance_tool` | partial | no `forcings`, no `evaluate_against_measured`, no `error_type`; series uncapped; the seed reads `window_start` alone; `roof_type` is normalized at one call site and read raw at four others; **the wetland is still modellable** — `NON_MODELLABLE_ROOFS` holds gravel aliases only (`gr2l_client.py`), `ROOF_PRESETS` carries a reachable `wetland` entry routed through `swc.MM_ONLY_ROOFS` (`gr2l.py`), and `ROOT_INSTRUCTION` still advertises four modellable segments (`agents/root_agent/agent.py`) |
| reference lookup | `lookup_reference` | to build | card store, `enum == card keys` test |
| irrigation rule | `calc_irrigation` | to build | bucket model, ET0 core, `rules_constants.py`, `roofs.py`, decision-diff harness |
| plotting | `plot_timeseries` | to build | closed `measured` vocabulary, session-state handoff |
| `ScenarioContext`, as-of views | — | to build | three paths bypass the seam: `swc.py` opens its own connection, both tool wrappers import the fetch function directly, the sub-agent reaches the warehouse singleton from two places |
| response cache | — | to build | — |
| harness · scorers · oracles · cases | — | to build | — |
| pinned evaluation database | — | built | present and readable from this checkout; `radiation` is stamped an hour behind the other four tables (`findings.md`; `decisions.md` § The `radiation` timestamp offset) and the as-of cut is host-timezone dependent (`findings.md`; `decisions.md` § The as-of cut) |

What exists is sound at the *client* layer — pure, ADK-free `weather_client` /
`gr2l_client` / `swc` seams behind thin ADK wrappers. What is missing is the
injection seam (§4) that makes any of it replayable.

---

## 1 Design principles

1. **Three layers.** Agent-facing tools with small declarative arguments → I/O
   resolution (`ScenarioContext`: DB as-of views, weather client, clock) → shared
   cores (`run_gr2l`, `swc`, the irrigation bucket, rule functions). The agent sees
   layer 1, the harness injects at layer 2, oracles import layer 3 directly.
   **Layer 3 is not uniformly local**: GR2L runs as an external HTTP service, so
   `run_gr2l` is a thin client and an oracle importing it issues the same request
   the tool does (§3.4). The irrigation rule and its bucket model with ET0 stay pure
   and local — layer 3 carries a second water-balance core, not a second HTTP
   client. The R endpoint carrying that model into the weinbau API is a deliverable
   *outside* this testbed; no case's answer depends on it.
2. **Replay the world through a request-keyed cache, run everything downstream of
   optimized prompts live, pin the data the live components read.** External
   world-state is fetched once and replayed from a committed cache keyed by the
   sha256 of the exact request (§5); components whose inputs depend on the optimized
   instruction — text2SQL, retrieval, model chains — execute live against pinned
   data. **GR2L is the only component whose determinism the cache carries**, and the
   only one with a capture deadline.
3. **No raw data through the LLM.** Tools return a summary plus a *bounded* series,
   never a row dump or an unbounded daily array; arithmetic over a long series
   happens inside a tool or a core. The cap is §3.3's.
4. **Single source of truth for rules and identities.** `rules_constants.py` feeds
   the irrigation calculator, the oracles and the reference cards; the card
   rendering discipline is §3.2's. One table in `tools/roofs.py` carries each roof's
   canonical name, DE/EN labels, site ids, substrate height, lysimeter area, per-column
   plausibility bounds, aliases (`Kies`/`KD`/`QGravel` ↔ the gravel roof) and **its
   column in each of the five tables, absent where the roof is not instrumented** — the
   semi-intensive roof has neither a lysimeter nor a radiation mast (`findings.md`), so
   the catalog's sampling pools are read off this table rather than hand-listed. It is
   the only place those names exist — not `swc.ROOF_SWC_COLUMNS`,
   `gr2l_client.ROOF_PRESETS` or the docstrings. Every lysimeter collects 1 m², so
   outflow in litres is numerically millimetres and no area factor is ever applied.
5. **Disclosure fairness.** Every self-contained tool's docstring states what it
   fetches internally. Required of the handwritten baseline; candidates own the
   docstrings and may rewrite them.

```
┌ agent layer ────────────────────────────────────────────────────────┐
│ root_agent (ADK LlmAgent; instruction ← candidate)                  │
│  text_to_sql_agent · get_weather_forecast_tool ·                    │
│  predict_green_roof_water_balance_tool ·                            │
│  lookup_reference · calc_irrigation · plot_timeseries               │
└───────────────┬─────────────────────────────────────────────────────┘
   ScenarioContext(clock → as_of · db as-of views · weather · cache)
┌ shared cores ─┴─────────────────────────────────────────────────────┐
│ run_gr2l (HTTP → external GR2L service, cached) ·                   │
│ daily weather (station rows from ctx.db · Open-Meteo Archive) ·     │
│ swc (θ ↔ mm, measured seed) · irrigation bucket · et0_fao56 ·       │
│ irrigation_decision · rules_constants · roofs ·                     │
│ card store (packaged YAML) · pinned data/water.duckdb               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2 Root agent (Google ADK)

At `agents/root_agent/agent.py`, built per rollout by
`build_root_agent(instruction, docstrings, tools, model)`. `LlmAgent`, ReAct-style
tool loop, hard cap of ~6 tool steps. LLM config: `temperature=0` and one pinned
decoding seed — sent, but not assumed honoured (`decisions.md` § Model pinning);
a transparent cache keyed by the **full request**, messages and tool declarations
and model id and decoding parameters alike, held **on inside the search and off on
the measurement path**, where three repeats per condition are the replication (§7);
and the model **pinned by canary rather than by version** — the provider serves no
dated aliases, so the served model id and endpoint are recorded and a committed
prompt/response canary detects a provider-side swap, on GR2L's terms (§5).

- **Optimizable text**: the `instruction` and the tool descriptions/docstrings,
  including the sub-agent's outward `description` — the text `AgentTool` presents to
  the root model. The sub-agent's *internal* prompt is frozen (§3.1), as is
  everything else. Each component is **one registered MLflow prompt**, read per
  rollout inside `predict_fn`; that registry read is the only channel candidate text
  has (§6). Docstrings are addressable because §4's factories apply them to freshly
  produced callables. **One exception**: `lookup_reference`'s `topic` enum lives in
  the function *signature*, so a candidate can reword the guidance but cannot delete
  the agent's list of valid topics (§3.2).
- **Scenario clock.** `as_of` reaches the model through the per-invocation
  instruction provider (`prompts/temporal.py`), which renders `ctx.as_of` read
  through the context's clock *callable*: production binds `site_now`, evaluation
  binds the case's frozen `as_of`, one code path serves both, and `as_of` is
  evaluated at read time, never captured at construction.
- **Answer contract** — evaluation-only, carried by the candidate instruction:

  ```json
  {"status": "answered" | "not_available",
   "answer": <bool | number | "YYYY-MM-DD" | null>,
   "unit": "<L | mm | °C | pp | %θ | % | count | null>",
   "explanation": "<free text, unscored in phase 1>"}
  ```

  `status` is **two-valued** and is *not* the tool-level enum of §3. The abstention
  metric scores the **agent's** status; a tool's `not_available` is the upstream
  cause that can make it correct, not the thing measured. A tool `error` has no
  contract representation, and neither does a clarifying question: the candidate
  instruction forbids asking one, and a final message parsing to neither status is a
  `parse_failure` diagnostic (§7). Cases whose deliverable is a plot artifact
  (catalog family H) answer `null`. Phase 1 scores `status` and `answer`;
  `explanation` is reserved for the phase-2 judge. The production instruction keeps
  prose and clarification instead. Every case's `A:` field instantiates this object.

---

## 3 Tools

**Three outcomes, stated once for every tool below.** A tool returns `success`;
`not_available`, a genuine scope limit of the system and never a bad argument, with
each subsection listing its own triggers and no others; or `error` carrying
`error_type: invalid_argument | upstream`. `invalid_argument` is deterministic
argument validation performed before any I/O, echoing what would have been valid.
`upstream` covers HTTP, database and configuration failures — including a cache
miss the service cannot fill and a request whose canary has diverged, the two
cases §5's record-on-miss rule does not absorb. A miss that records is not an
error at all. §7 states what each does to a score.

Tool and argument names are the registered ones in §0's table. Arguments are flat
scalars — `albedo=0.2`, never `params={"albedo": 0.2}`.

### 3.1 `text_to_sql_agent` (sub-agent via `AgentTool`) — frozen

- Internal: completion (tuned prompt, **frozen**) → db querier → fixer loop,
  `settings.max_sql_retries` (default 3), attempt counts logged as diagnostics.
  Reads the pinned DuckDB through the case's as-of executor (§5); returns aggregates
  and small result sets, capped at 100 rows.
- The semantic layer is static and frozen, and lives in
  `tenants/green_roof/sensordata.py`, rendered into the **builder's** system prompt
  rather than into the sub-agent's own instruction, which carries no schema:
  column descriptions, units, the 1 m² lysimeter collection area, the `radiation`
  hour offset (`decisions.md`), the alias map (principle 4), join key, time
  resolution.
- The root agent sees the tool under the sub-agent's own name, `text_to_sql_agent`;
  `query_database_tool` is the *inner* tool and never appears in a root-agent
  trajectory.
- **Frozen means the text, not the objects.** Prompt, docstrings and pipeline
  structure are byte-stable; the object is rebuilt per case by
  `build_text_to_sql_agent(executor, clock)`, binding both DB seams — the inner
  query tool *and* the pipeline's EXPLAIN validator — to the case's as-of executor.
  Transpiler, fixers, models and prompt strings are shared; the DB binding and the
  injected clock (read by the querier's SQL rewrite, §5) are the only per-case
  state. The context-bound query tool preserves the exact function name, signature
  and docstring, from which ADK derives the tool declaration. A module-level
  singleton remains as the production default, from the same factory.

### 3.2 `lookup_reference` — exact card lookup

```python
async def lookup_reference(
    topic: Literal["irrigation_rule", "irrigation_threshold",
                   "substrate_hydraulics", "irrigation_dose",
                   "heatwave_definition", "retention_target",
                   "roof_reference_ranges", "data_freshness",
                   "roof_directory", "sensor_reference", "et0_method"],
    roof: str | None = None,     # filter; never suppresses `not_applicable`
) -> dict
```

- **No ranking, no query, no index, no network.** The agent names a topic; a Python
  function reads that card out of a packaged YAML store.
- **The store is 11 cards** — the eleven enum members above — baked into the image,
  one YAML file per card: `id`, `title`, `provenance`, a hand-authored `text:`
  block, and `values:` / `applies_to:` / `not_applicable:` blocks. Editing a card
  requires a rollout, which keeps the store inside the pinned surface (§5).
- **Prose carries no numerals; `values:` is test-bound to the constants.**
  `provenance: rendered` means the `values:` block equals what `rules_constants.py`
  and `roofs.py` produce, asserted by a test — not that the file is machine-written.
  Those two modules are the whole of what `rendered` is defined against. The
  `provenance: static` cards (`roof_directory`, `sensor_reference`, `et0_method`,
  `data_freshness`) have no constants behind them there and carry no drift test.
  `data_freshness` is the one whose values are pinned elsewhere rather than
  unpinned — its record dates come from the database hash and the
  station-derivation pin, and the pin check verifies them, because a card store
  that read the database would stop being the offline function this section's
  last bullet promises (`decisions.md § Retrieval`).
- **The enum lives in the signature; only the docstring is candidate-owned.** ADK
  renders `Literal[...]` into the function declaration as a schema `enum`, so the
  vocabulary reaches the model regardless of the docstring. A test pins
  `enum == card keys`.
- **Cards name subjects; no card is named after a single constant.**
- **A known topic returns the card whole**, `not_applicable:` included. There is
  **no roof-scoped `not_available`**: asking for the wetland's soil-moisture
  threshold returns the `irrigation_threshold` card with the extensive-roof rule and
  the wetland's exclusion side by side, and the agent reads the exclusion.
- `irrigation_rule` and `irrigation_threshold` together state the per-roof numbers
  well enough to answer an irrigation question *without* the calculator.
- `not_available` is unused — the store either has a topic or does not; an unknown
  topic is `invalid_argument` echoing the valid list.
- **Fully offline and deterministic**, a pure function of files §5 hashes: no
  network, no cache entry, no capture step, no `upstream` error class.

### 3.3 `get_weather_forecast_tool` — daily weather

A thin ADK wrapper over the pure, ADK-free `weather_client`. Layer 1 resolves the
window, layer 2 resolves the source, and the pure fetch function sees an absolute
window and one endpoint.

- **Two sources, chosen in code from the window and never named by the agent**,
  which names a window and a question and never a provenance: the **station**,
  derived from the pinned DB's `wetter` table through `ctx.db` whenever that record
  covers the **whole** window; **Archive**, Open-Meteo's ERA5 reanalysis, for
  everything else, including every window reaching past `as_of`. Coverage is tested
  **through the as-of view**, so a future window can never resolve to the station,
  and a window the record covers only partly falls to Archive whole. Every window
  has exactly one provenance. The response echoes the source, and an answer
  discloses it whenever it is the station.
- **Source resolution lives in a composite `WeatherClient` at layer 2**, constructed
  with the case's as-of executor (§4). It cannot live in the pure fetch function:
  the station path reads `ctx.db` and the pure layer has no channel to a context.
  Every consumer — the standalone tool, GR2L, the irrigation calculator, the plot
  tool — reaches weather through `ctx.weather` and sees the same rows, units and day
  boundary.
- **The station path needs no cache, no network and no capture step**: a pure
  function of the pinned DB, read through the as-of views. It is the one weather
  path that cannot see past `as_of` at all; Archive returns observations past a
  case's `as_of` if asked.
- **Windows resolve to absolute dates at layer 1** against `ctx.as_of` before any
  client is called. `past_days` / `forecast_days` stay in the tool signature but
  never resolve against a wall clock; the pure layer takes `start_date` / `end_date`
  as required arguments with no default, so a caller that forgets to resolve fails
  loudly.
- **Window validity has exactly two failure modes.** A malformed window — end before
  start, a negative relative count, an unparseable date — is `error` /
  `invalid_argument`. A well-formed window whose end lies more than 16 days past
  `as_of` is `not_available`. Nothing bounds how far back a window may reach:
  Archive serves decades, and the 31-day cap below bounds the *response* by
  truncating and summarizing rather than by refusing.
- **The horizon check is the sole bound on future weather and is load-bearing** —
  Archive serves day 400 past `as_of` without complaint. It is enforced in code
  against `ctx.as_of`, asserted by a harness test, and is the single scope limit this
  tool signals. Unknown-variable questions have no tool surface: the tool takes no
  variable argument and always returns the seven documented fields, so that
  abstention is the **agent's**, grounded in the docstring's variable list and
  scored at the contract level.
- **The daily series is bounded**: capped at 31 days, beyond which the tool returns
  summary statistics plus weekly aggregates and flags the truncation. Multi-month
  meteorological aggregation is the database's job.
- **Daily resolution is pinned end to end** — cached responses, tool output, GR2L
  input rows and every aggregation over the five tables are one row per calendar
  day, **Europe/Berlin**, converted from the columns' naive UTC through one shared
  helper (`decisions.md` § The day boundary). GR2L rejects sub-daily rows, so
  sub-daily questions are phrased against whole days.
- Output rows are **identical to GR2L's input row** — `DailyWeatherRow`: `Date`,
  `tm`, `tx`, `tn`, `rf`, `precip`, `w`, `gs` — so the two need no mapping layer.
  `tx` and `tn` are required, not optional. Field meanings and the two unit
  conversions (`w` in km/h, `gs` in J/cm²/day) are `weather_tool.md`'s.
- **Station rows are derived, not stored.** `wetter` is half-hourly and carries six
  of the seven fields; the per-field aggregation, the `tn` estimator, the `−7999`
  sentinel filter and the UTC→Europe/Berlin day boundary are `weather_tool.md`
  § Station source. The derivation is fixed in code and pinned (§5). Rows are served
  **uncorrected**; the three measured offsets — estimated `tn`, low `gs`,
  undercaught `precip`, quantified in `findings.md` — are stated scope limits (§8).
- The tool returns the site's own `latitude` / `longitude` / `elevation` from
  `site.py`, never a grid cell. `WeatherResult` carries **no `elevation` field**:
  elevation is a site fact, and GR2L's `hoehe_nn` comes from `site.py` through its
  own wrapper.

### 3.4 `predict_green_roof_water_balance_tool` — layered GR2L

```python
def run_gr2l(rows, parameters) -> list[Gr2lResultRow]   # thin async HTTP client

async def predict_green_roof_water_balance_tool(
    roof_type,                       # non_irrigated_extensive |
                                     # irrigated_extensive | semi_intensive
    start_date=None, end_date=None,  # absolute window
    past_days=None, forecast_days=None,   # relative; resolved against ctx.as_of
    initial_soil_moisture_pct=None,  # %θ; default: measured seed, see below
    albedo=None,                     # the one overridable physical parameter
    forcings=None,                   # {"precip": {"2026-07-22": 50.0}}
    evaluate_against_measured=False, # retrospective deviation statistics
) -> dict
```

- Self-contained by default, overridable for counterfactuals; internal fetches go
  through `ctx.weather` and `ctx.db`, and the response echoes which source served.
  **There is no `meteo_source` argument** — the agent never names a weather source.
- `forcings` is keyed by the row's **own field names** (`precip`, `tm`, `tx`, `tn`),
  one vocabulary with `DailyWeatherRow` and never `precip_mm`, and is sparse:
  unspecified days keep the fetched value. It is echoed for argument checking.
- Returns `parameters`, `seed`, a bounded daily `data` series (§3.3's cap) and a
  `summary`: `retention_mm` / `retention_pct`, `total_precip_mm`,
  `total_outflow_mm`, `min_substrate_storage_mm`, `min_swc_pct`, `drought_stress`,
  `retention_excludes_seed_day_runoff`. With `evaluate_against_measured` it adds
  mean and max |predicted − measured| in %θ over the window's overlap with the `swc`
  record.
- `not_available` triggers: the gravel roof and the wetland; a window with no
  trustworthy seed.

**Day-1 seed: `seed_at = min(window_start, as_of)`** — never later than the window
opens, and never later than `as_of`, which is the leakage bound. Staleness flagging
(`is_stale` beyond 7 days, with disclosure) and the never-substitute-a-default rule
(`not_available`, never a generic `theta_01`) are `gr2l_tool.md`'s. Every seeded
component uses this rule.

**GR2L is a remote service, and oracles call it over HTTP too.** The model core is
R, deployed behind `POST {gr2l_api_base_url}/predict_gr2l` outside this repo;
oracles `import run_gr2l` from the same client the tool uses. Determinism rests on
three things instead of purity: the service is pinned by base URL plus a committed
canary request/response hash (§5), GR2L is deterministic given `(rows, parameters)`,
and responses are cached by exact request body. The first capture of any case needs
network access, and a cache miss while the service is down **fails the case** rather
than substituting a fallback — a harness error, not an agent error (§7).

**The tool speaks %θ; millimetres stay internal** (`swc.py`; the conversion and the
per-layer unit contract are `gr2l_tool.md`'s). Retention and runoff stay in mm —
fluxes, not states. **Neither the gravel roof nor the wetland can be modelled**
(`gr2l_tool.md`): the gravel roof has no substrate store, and the wetland is a fleece
mat whose θ sensor saturates near 86 %θ, so GR2L's scope is the three substrate
roofs. Both stay first-class for SQL, retrieval and plotting, and a modelling request
naming either is a legitimate `not_available`, implemented as `NON_MODELLABLE_ROOFS`,
which also accepts principle 4's German and column aliases. **`calc_irrigation`
excludes the same two roofs** (§3.5), so one set carries both water-balance tools'
scope and lives in `roofs.py` beside the rest of each roof's identity (principle 4).
This is what the catalog's per-family roof sampling pools are derived from.

**`roof_type` is a plain `str` at the tool boundary and must stay one.** It is the
deliberate opposite of `lookup_reference`'s `topic` (§3.2): ADK renders a
`Literal[...]` into the function declaration as a schema `enum`, so typing this
argument would leave the agent unable to *name* an out-of-scope roof and would make
family I — the abstention asked in the gravel and wetland aliases — unaskable. Scope
is decided in code against `NON_MODELLABLE_ROOFS` after normalizing the argument
once at entry, and a test pins the annotation.

### 3.5 `calc_irrigation` — self-contained over its own bucket model

```python
async def calc_irrigation(
    roof_type,                   # non_irrigated_extensive |
                                 # irrigated_extensive | semi_intensive
    soil_moisture_pct=None,      # stated-value path: %θ
    max_temperature_c=None,      #   supplying these skips every fetch and
    forecast_precip_mm=None,     #   every simulation — the rule alone runs
) -> dict
```

- **Returns a decision, not a volume**: `irrigate: bool`, with the fixed dose stated
  alongside for disclosure. The dose is site policy, not computation — the roof's
  90th-percentile historical ET, a per-roof constant in `rules_constants.py`,
  carried into the ops manual with the deployed valve minutes beside it.
- **It runs its own bucket model, and that model is not GR2L.** The deployed
  controller's balance differs from GR2L's in seven respects, among them one store
  rather than two, a stress coefficient evaluated on the *previous* step, ET applied
  before the cap, and a flat 22 %θ field capacity where GR2L measures
  22.9 / 32.6 / 30.4. The two are separate cores. The physics, the per-roof
  parameter derivation, the reason-code ladder, the hour-based horizons and every
  deviation from the deployed controller are `irrigation_tool.md`'s.
- **The input set is soil moisture, precipitation, ET0 and air temperature.**
  Radiation, wind and humidity enter only through ET0. **ET0 is computed, not
  fetched**: `DailyWeatherRow` carries no `et0` and its schema is frozen, so a local
  FAO-56 Penman-Monteith core (`et_fao56.py`) computes it at **albedo 0.23**, the
  reference-crop value; roof-specific throttling is the stress coefficient's job.
- **Self-contained by default** on §3.4's terms and with the same disclosure
  discipline: the seed comes from `swc.latest_measured_swc` under the
  `min(window_start, as_of)` rule and the forcing from `ctx.weather`. Supplying the
  stated values instead makes the call pure — no DB, no weather, no simulation.
- **Millimetres internally; the site's own units at the surface.** Constants are
  authored as the site states them (%θ) and converted once through
  `swc.theta_pct_to_mm`.
- **The unit correction is measured, not absorbed.** The deployed controller adds
  millimetres of rain and ET to a store held in %VWC; carrying that balance into
  millimetres rescales each roof's response to rain by `100/SH_mm` — about 0.7× for
  the 7 cm extensive roofs, about 1.5× for the 15 cm semi-intensive. The deployed
  trigger levels are carried verbatim into `rules_constants.py` as site policy
  regardless. A **decision-diff harness** replays a historical window through both
  unit regimes with the same ET0, so unit handling is the only variable, and reports
  every date and roof where the irrigate decision flips. That list bounds what the
  testbed's irrigation answers say about the deployed system (§8); re-tuning against
  it is the site's call.
- **The wetland is out of scope.** The rule is built for a classical substrate roof —
  a soil store read in %θ — not for a ponded fleece mat, so it does not apply.
- `not_available` triggers: the gravel roof and the wetland — the whole of
  `NON_MODELLABLE_ROOFS`, which §3.4 shares and which now bounds both water-balance
  tools alike; a window with no trustworthy seed.
- **Everything runs local**, which makes irrigation cases fully offline: no cache
  entry, no canary in the agent path, no `upstream` error class, and an oracle that
  imports the very function the tool calls.

### 3.6 `plot_timeseries` — self-contained, multi-source, spec-scored

```python
def plot_timeseries(
    series,          # list of SeriesSpec — each names a SOURCE, never data
    start, end,      # absolute window (relative forms resolved as in §3.3)
    kind,            # line | bar | model_overlay | diff
                     # no agg argument: aggregation is DERIVED per variable
) -> dict            # resolved spec + summary stats + artifact_ref — NO series
```

- **The agent declares a source, never the data itself.** Each `SeriesSpec` names
  where a series comes from and which variable to draw; the tool fetches it through
  the seams the other tools use.

  | `source` | Fetch path | Selector fields | Bounded by |
  |---|---|---|---|
  | `measured` | `ctx.db` as-of view, fixed parameterized query | `table`, `column`, `roof` | the as-of view (§5) |
  | `weather` | `ctx.weather` | `variable` (a `DailyWeatherRow` field) | §3.3's window resolution |
  | `model` | `run_gr2l` through the shared client and cache | `roof_type`, `variable` (a `GreenRoofDay` field), plus `initial_soil_moisture_pct` / `albedo` / `forcings` | §3.4's seed rule |

  Passing data *through* the model is forbidden. Re-fetching is cheap because both
  live sources replay from the response cache; in `replay` mode a plot issues no
  live call.
- Self-contained on §3.4's terms, inheriting every leakage and determinism rule
  rather than restating them, **provided it resolves all three sources through
  `ctx`** and never opens its own DuckDB connection or HTTP client.
- **Fixed parameterized query, no LLM SQL.** `measured` reaches the five as-of
  tables (`outflow`, `radiation`, `swc`, `tsoil`, `wetter`) through a closed
  vocabulary of columns and aggregations; derived quantities exist in that vocabulary
  as flags and are not reachable by free SQL (§8). Area-normalized outflow is **not**
  one of them — the lysimeters collect 1 m², so litres are already millimetres and the
  vocabulary relabels the unit rather than scaling the values.
- **Returns no series at all** — the resolved spec, summary statistics and
  `artifact_ref`. The series' consumer is the renderer, not the LLM, so plotting
  satisfies principle 3 outright instead of being capped by it.
- **The resolved spec is echoed in full**: source and variable per series, the
  resolved absolute range, the derived aggregation and resolution, unit and axis per
  series, gap and truncation flags, and the modelling arguments of any `model`
  series. **Scored is the agent-supplied half** (§7) — series source, variable and
  roof, the resolved range, and any modelling arguments.
- **Units, axes and aggregation are derived in code, never chosen by the model**:
  fluxes aggregate by `sum`, states by `mean`, and unit and axis follow from the
  variable through the closed vocabulary. There is no `agg` argument; the resolved
  spec echoes the derived operator per series. **Daily resolution is forced for
  mixed plots** — `swc` and `wetter` are half-hourly while weather and GR2L are
  daily, so any plot combining a `model` or `weather` series with a `measured` one
  aggregates the measured series to calendar days (Europe/Berlin) with that
  variable's derived operator and reports the resolution in the spec.
- `not_available` triggers: a `model` series for the gravel roof or the wetland.
  A `measured` series for either stays valid.
  Docstring: "fetches its own data; do not query the database separately for
  plotting."
- **Headless.** Nothing renders server-side and no plotting library enters the
  Python dependency set. The series reaches the CopilotKit frontend **without
  passing through the model**: the tool stashes it under a session-state key and the
  wrapper merges it into the tool-result event the chat replays — the existing
  `QUERY_RESULT_STATE_KEY` pattern from `warehouse.py` — while the model-visible
  result stays spec, statistics and `artifact_ref`, which names the stashed payload.
  Evaluation never reads the state key; rendering is an **unscored side effect**.

---

## 4 ScenarioContext — the single injection seam

```python
class ScenarioContext:
    def __init__(self, clock, db_path, weather_client_factory, http_cache):
        self.clock = clock                       # () -> datetime
        # A view-recreating *factory*, not a bare connection: every reconnect
        # must rebuild the views.
        self.db = DuckDbQueryExecutor(
            connection_factory=lambda: connect_asof(db_path, clock))
        self.cache = http_cache
        # Both halves, in this order: the station reads the as-of views, Archive
        # reads the cache. A factory taking only `db` cannot build the composite.
        self.weather = weather_client_factory(self.db, self.cache)

    @property
    def as_of(self): return self.clock()

def build_toolset(ctx, docstrings=None) -> list[Tool]: ...   # make_* factories
```

- **Nothing the harness needs is a module-level singleton.** The harness builds one
  context per case — `ScenarioContext(lambda: case.as_of, PINNED_DB,
  make_weather_client, ResponseCache(CACHE_DIR))` — and runs §6's rollout against
  it; production builds the same object once at import with `clock = site_now`.
  `build_root_agent`, `build_toolset` and `build_text_to_sql_agent` are the real
  constructors, and the production singletons are thin defaults produced by them.
- **Explicit dependency injection — not contextvars, not monkeypatching.**
  `mock.patch` remains acceptable in oracle unit tests only.
- **Anything that would capture `as_of` at construction re-derives it per use.** The
  as-of executor re-checks `clock().date()` per query and rebuilds its connection
  when the date moves.

---

## 5 Determinism, pinning and leakage

| Component | Prompt layer | Computation | Data |
|---|---|---|---|
| root instruction + tool docstrings | **optimized** | live | — |
| `text_to_sql_agent` | frozen (tuned) | live | DB via as-of views |
| `lookup_reference` | n/a | pure, exact, **fully offline** | card store, hashed |
| `get_weather_forecast_tool` | n/a | station: pure, offline · Archive: live once, then **cached** | ctx.db as-of views (station) · Open-Meteo Archive, cache keyed **per day** |
| `predict_green_roof_water_balance_tool` | n/a | live, **remote HTTP**, cached | ctx.weather / ctx.db; pinned presets |
| `calc_irrigation` | n/a | pure, **fully offline** | `rules_constants.py` · `roofs.py` · ctx.db as-of views · ctx.weather |
| `plot_timeseries` | n/a | live, deterministic | ctx.db as-of views · ctx.weather · run_gr2l, all cached |
| LLM | — | T=0, seed pinned, pinned by served id + canary; cached in the search, **uncached on the measurement path** | — |

**Pins committed to the repo**: `water.duckdb` sha256; one sha256 over the card
store (`assistant/knowledge/cards/`; there is no index to hash);
`rules_constants.py` and `roofs.py` versions; the GR2L roof presets; the GR2L base
URL plus a canary request/response hash, the service exposing no version string; the
task LLM's served
model id, endpoint and decoding parameters (`temperature=0`, seed) **plus its own
request/response canary**, the provider offering no dated versions; the same three
for **the reflection LM**, a second model distinct from the task model, without which
a run is unrepeatable even with the task model fixed; **the same three again for each
of the text-to-SQL chain's three models** — the sub-agent that routes, the builder
that writes the query, and the fixer that repairs one the transpiler or the validator
rejected — since all three are live dependencies under undated aliases and each moves
family A's answers on its own, the fixer most quietly of all: it changes not what is
answered but what *fails*, and an unrescued query leaves the aggregates as an
`upstream` exclusion rather than as a wrong answer; the
candidate prompt names and seed versions in the MLflow registry; the dependency
lockfile (adk, litellm, mlflow, gepa, pyyaml, duckdb); and the **station derivation**
(`weather_tool.md` § Station source — the per-field aggregation, the day boundary,
the sentinel filter, and the station record's first and last complete day), which
the DB hash does not cover.

**Response cache — one mechanism, two callers, two granularities.** Keyed by the
sha256 of a canonical request: `data[]` plus parameters for GR2L, and for
Open-Meteo the **one-day** Archive request — URL plus sorted query parameters
over a single calendar day, a window being assembled from its days rather than
cached as itself. Entries are committed JSON stored beside the cases, keyed by
request rather than by case. Every input to a key derives from `ctx.as_of` —
window resolution and source choice both — so a key never depends on when the
rollout runs.
**A miss is filled live and recorded, gated on the service's canary matching**; a
diverging canary or an unreachable service is a hard failure (`decisions.md` § The
response cache). The strict-replay alternative was rejected because a cache
captured over the oracle's windows cannot cover the windows a *candidate* chooses,
and would teach the search to reproduce the baseline's arguments. What the cache is
load-bearing *for* is GR2L; for weather it is cost and speed, the station needing no
entry at all and Archive being re-fetchable indefinitely.

**That asymmetry is what the two granularities implement.** A window has two
degrees of freedom and is reachable through `past_days` / `forecast_days` as
readily as through dates, so the windows one `as_of` can produce are a plane —
289 of them inside the horizon — while the days they are drawn from are a list
of 33. Keying weather on the window meant a capture pass could warm only a line
through that plane and a candidate one day off gold's window was excluded; keyed
per day, every window it can resolve is assembled from entries a bounded sweep
has already committed. GR2L keeps the request key, having no day to decompose
into and being the component whose determinism this carries. **The two therefore
differ in replay as well**: on the measurement path GR2L replays strictly and a
miss is an `upstream` exclusion, while Archive fills and records a day the
capture pass did not reach, and the count of what it recorded is published per
arm beside the exclusion count (§7).

**Time-travel leakage** is closed on four fronts, all reading the same `as_of`:

- **As-of views.** `connect_asof` opens an in-memory DuckDB, attaches the pinned
  file read-only as `src`, and creates one
  `main.<table> AS SELECT * FROM src.<table> WHERE timestamp <= :as_of` view per
  table (`outflow`, `radiation`, `swc`, `tsoil`, `wetter`). The querier and the plot
  tool resolve unqualified names in `main`, so they see only the view, the pinned
  file is never modified, and the semantic layer keeps the table names it knows.
  Station weather reads the `wetter` view through the same executor. **`as_of` is an
  instant and `connect_asof` converts it to UTC** before it meets the columns' naive
  UTC timestamps; a timezone-aware bound passed through would be compared in the
  host's session timezone and cut the record differently per host (`decisions.md`
  § The as-of cut).
- **Generated SQL.** The view bounds *rows*, not SQL time functions, so the
  querier's sqlglot pass **rewrites** `CURRENT_DATE` and `now()` in generated SQL to
  the literal `as_of` before execution.
- **Seeds and windows.** The GR2L seed uses `min(window_start, as_of)` (§3.4);
  relative windows resolve against `ctx.as_of` at layer 1 (§3.3); windows for
  retrospective model-versus-measured comparison end at or before `as_of`, asserted
  by the harness.
- **Case time.** `as_of` is drawn from a band inside the measurement record and the
  wall clock never enters a case; the band and its sampling rules are the catalog's
  (`questions.md` §1.7). Its ceiling is **2026-04-24**, the last day of the sensor
  tables; the station record itself runs to 2026-04-27 (`findings.md`).

Residual LLM nondeterminism is handled statistically, on §7's terms.

---

## 6 Optimization harness (outer loop)

GEPA is reached **through MLflow**, not driven directly. MLflow owns the
`GEPAAdapter`, so nothing here writes an adapter and there is no
`run_rollout(candidate, case)` signature: **GEPA owns the loop, MLflow owns the
adapter, one rollout is one call of `predict_fn`.** This repo writes `predict_fn`,
the scorers, and the candidate surface in the prompt registry. The library internals
this rests on are recorded in `findings.md`.

```python
def predict_fn(inputs: dict) -> dict:          # one case; MLflow calls it per record
    ctx  = ScenarioContext(lambda: inputs["as_of"], PINNED_DB,
                           make_weather_client, cache)
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
    scorers=[answer, trajectory, card_recall, abstention],   # §7
    aggregation=aggregate_scores,              # ours; mandatory, see §7
)
```

- **`aggregation` is this repo's callable and is not optional.** MLflow ships none —
  the `weighted_objective` its docstring shows is an example, not an export
  (`findings.md` § Optimizer internals) — and omitting the argument makes the
  **mean of the numeric scorer values** the objective while any non-numeric value
  raises. Both of §7's skips are non-numeric by construction, so the callable is
  what makes them skips instead of an exception, and it is also what the optimizer
  selects on.

- **The candidate surface is the MLflow prompt registry.** Candidate text reaches
  the system **only** through a registry read whose prompt *name* matches a
  candidate key, so a component that is never read is silently left un-optimized and
  the harness asserts that every registered candidate prompt was read during an
  evaluation pass. The patch carrying that text is process-global: exactly one
  candidate is evaluable per process at a time.
- **`predict_fn` builds everything per record** — context, toolset and agent from
  one `inputs` dict. Records are evaluated in worker threads, so §4's
  no-ambient-state property is the precondition for the search to be correct at all.
- **Reflection reads MLflow traces and scorer rationales**, so the textual feedback
  GEPA reflects on is whatever the scorers put in `Feedback.rationale` — trajectory
  diff against gold, SQL errors, abstention outcome. ADK spans reach those traces
  through MLflow's ADK OTel translation: the route is the trace, not the event log.
- **Run ledger.** `optimize_prompts` logs per-iteration candidate text, per-scorer
  metrics and an eval-results table; the harness adds the pins and case identity per
  rollout.
- **No ADK evalset.** Where ADK's own evaluators are wanted, MLflow wraps them as
  scorers fed from `expectations`, without an evalset file.

### 6.1 Case envelope

Cases are a **pretty-printed JSON array per split** (`eval/cases/{train,
test_seen,test_unseen}.json`), each element projecting **directly** onto MLflow's
`train_data` shape — the same rows feed the search and §7's measurement run:

```jsonc
{"inputs":       {"question": "…", "as_of": "2026-03-14T08:00:00+01:00",
                  "case_id": "T09-0142", "template_id": "T09", "params": {…}},
 "expectations": {"status": "answered", "answer": false, "unit": null,
                  "answer_metric": "scored",        // "skipped" where answer is null
                  "tolerance": {"kind": "abs", "value": 0.1},
                  "expected_tool_calls": [{"name": "…", "args": {…}}],
                  "must_not_tools": ["…"], "gold_cards": [],
                  "argument_checks": [{"tool": "…", "path": "forcings.precip.2026-03-15",
                                       "op": "eq", "value": 50.0}],
                  "pins": {…}}}
```

The envelope is **fixed by the API, not chosen**: `inputs` and `expectations` are
the only two channels MLflow delivers (`findings.md`). Consequences:

- `inputs` carries everything `predict_fn` needs, `expectations` everything a scorer
  needs; there is no third place to put anything.
- **`as_of` is an instant**, written in the site's own offset-bearing form and
  normalized to UTC by the seam that consumes it, never by the caller
  (`decisions.md` § The as-of cut).
- **Materialized answers carry their pins** — `water.duckdb` sha256, the GR2L
  canary, the station-derivation version, the resolved weather source.
- `argument_checks` are **declarative**, so per-template argument conjuncts stay in
  data and the scorer never branches per template. They address **tool arguments
  only**, never the tool result, which fixes the plotting family's scored surface to
  the agent-supplied half of the spec. The `op` vocabulary carries `eq`, `set_eq` (a
  series selection is a set match) and `present` (a candidate-chosen value that must
  merely exist and be plausible).
- **A `path` addresses one call's arguments, and a `*` segment collects one field
  across a list.** `series.*.roof` is the roofs of every series that carries one,
  which is how "a set match over the selection" is written without inventing an
  order the request does not have: an index would impose one, and `set_eq` over
  `series` itself compares whole declarations and so fails a candidate that added
  an optional key it was entitled to add. Four properties, all load-bearing —
  members lacking the field are **skipped**, so a `weather` series with no roof
  does not break a roof check; the wildcard is **list-only**, `forcings` being a
  mapping; `set_eq` over it **collapses duplicates**, so it constrains *which*
  values were drawn and never how many series carry them, cardinality being the
  `series.*.source` check's to constrain; and collecting nothing is an **absence**
  for `present`, which would otherwise be a check no call could fail.
- **Checks sharing a call are satisfied by one call.** Ungrouped checks on one
  tool describe a single call and are scored against a single call — some call to
  that tool must satisfy all of them. Existential over calls, because a fumble
  repaired on a later call costs nothing (`decisions.md § Trajectory scoring and
  routing probes`); universal over the group, because per-check search lets a run
  assemble a pass out of fragments — one call with the right roofs and the wrong
  variable, another with the right variable and the wrong roofs, and no correct
  call anywhere. That is the whole scored surface of the plotting family, and on
  T26(i) it would let the compositional holdout pass by setting `albedo` on one
  call and `forcings` on another without ever composing them. Where a template
  genuinely needs *two* calls to one tool — T26(ii)'s cross-roof comparison — an
  optional `group` splits the checks, and each group is then satisfied
  independently.
- Two schema validators, not conventions: **`gold_cards` non-empty implies
  `lookup_reference` in `expected_tool_calls`**, and **`answer_metric: "skipped"`
  implies `answer: null`**.
- `expected_tool_calls` keeps ADK's own key name, which keeps MLflow's ADK
  `ToolTrajectory` scorer available as a cross-check against ours.
- Per-template constants — tolerance, must-not set, gold cards — live in the
  template YAML and are **copied into each case** at generation, so a scorer reads
  one record and never a second file.
- **Array, not JSONL, and generated rather than authored.** An indented array diffs
  per field where JSONL diffs per line. The file is **emitted, never hand-edited**,
  and the generator emits **deterministically** — fixed key order, cases sorted by
  `case_id`, `indent=2`, trailing newline.

---

## 7 Scoring and reporting

Four metrics, each one MLflow `Scorer` returning a `Feedback`. The catalog supplies
the per-template inputs they read (`A:`, `Traj:`, `Must-not:`, `Cards:`,
`tolerance`); the behaviour below is the measuring apparatus and lives here.

- **Answer** — exact match or tolerance against the materialized oracle answer,
  compared **after unit normalization**; `unit` is a first-class `expectations`
  field for that reason. The normalization table is small and fixed: `L` and `mm`
  are the same quantity on the lysimeter columns, whose collection area is 1 m²
  (`findings.md`), and `%` and `%θ` are one unit. Nothing else converts — a
  millimetre answer to a percentage-point oracle is wrong, not rescalable.
- **Trajectory** — **binary per case**: 1 iff every gold tool was called, no listed
  must-not tool was called, and the argument checks pass; else 0. No partial credit
  and no extra-call penalty — calls beyond the gold set are free, so a template
  discriminates routing **only** through its must-not set. Gold tool names are the
  registered names in §0. The argument checks carry the model-bearing families
  (`forcings` / `albedo` / `initial_soil_moisture_pct` present and plausible) and
  the plotting family, whose entire scored surface they are: series source, variable
  and roof plus the resolved `start` / `end`. Aggregation, unit and axis are **not**
  scored — derived in code from the variable, they are constant across candidates.
  The resolved spec is still echoed in-band and diffed as a diagnostic.
- **Card recall** — one number, `|gold_cards ∩ retrieved_cards| / |gold_cards|` over
  the union of every `lookup_reference` call in the run. Graded, so it gives the
  partial credit the binary trajectory cannot. There is no gold-query / agent-query
  split: an exact lookup has no query to substitute, so only the agent's card
  selection is measurable.
- **Abstention** — accuracy on unanswerable cases **and** the false-abstention rate,
  never aggregated into one number. The scored quantity is the **agent's** contract
  `status`, on §2's terms.

**Skipped, not scored 0.** Two metrics have a legitimately undefined case: the
answer metric where the contract answer is `null` (a plot deliverable), and card
recall where `gold_cards` is empty. Both return a non-numeric value that the
explicit `aggregation` callable turns into a skip, and both report **coverage**
beside the score — one mechanism with two users, implemented once in `aggregation`.

**Harness exclusion.** A case whose run hit an `upstream` tool error is marked
`harness_error`: excluded from every aggregate above and counted per candidate arm,
with reasons broken out. `invalid_argument` errors never exclude — the case stays in
and scores through the normal metrics, so an unrecovered fumble surfaces as a wrong
answer or a false abstention and a self-repair costs nothing. **This exclusion is a
property of the measurement path.** The search path inside `optimize_prompts` has no
exclusion channel and is protected by construction instead: `train_data` is
pre-filtered to fully captured cases, a cache miss on a window the candidate chose
records rather than fails (§5), and only an unreachable service or a diverging
canary still scores 0 — with the per-arm counts of both residual failures and newly
recorded entries published beside the results. Diverging failure counts between arms
mean the run is repeated; diverging record counts mean the arms explored different
argument space, which is reported, not repaired. **Both counts are now published
on the measurement path too**: since weather records there rather than excluding
(§5), a condition that reached windows the other did not shows it as recorded
entries instead of as exclusions, and reading one without the other would read
the repair as a difference between the arms.

**Diagnostics**, reported and never scored: fixer iterations, steps, tokens,
latency, **mean extra calls per arm**, and **`parse_failure`** — a final message
parsing to neither status value, reported apart from answer accuracy so an optimizer
degrading the output format stays distinguishable from one degrading reasoning.

**Two properties ride on the `Scorer` shape.** `Feedback.rationale` is the search
signal, not decoration: MLflow forwards it into GEPA's reflective dataset. And
per-scorer values stay separate through logging and into that reflective dataset —
but **not** into selection. GEPA's Pareto front is over *instances* unless
`frontier_type` says otherwise, and MLflow never sets it, so **selection runs on the
one scalar the `aggregation` callable returns** (`findings.md` § Optimizer
internals). The four metrics therefore reach reflection unblended and selection
blended; a metric that must steer selection has to earn its weight inside
`aggregation`, and no metric is protected from being traded against another there.

**Reporting rules.**

- 3 repeats per condition at one pinned seed with the LLM cache **off**, paired
  evaluation on identical cases. They are near-replicates at temperature 0: they
  shrink per-case noise, they do not multiply *n*, and the unit of analysis stays the
  template. The cache is off because a prompt-keyed hit would return the first
  repeat's bytes to the other two, and the seed is not assumed to control what
  varies — the repeats measure residual nondeterminism rather than suppress it
  (`decisions.md` § Replication and the LLM cache).
- The paired bootstrap resamples **`template_id`, not `case_id`**; errors cluster by
  template, and case-level resampling reports intervals several times too narrow.
- The train number is the **selection score**, never "training accuracy": the final
  candidate is selected on those same instances.
- The two generalization gaps are reported **separately** — train → test_seen
  catches memorized constants and phrasing overfit; test_seen → test_unseen catches
  template overfit, to which test_seen is blind by construction.
- test_unseen's trajectory result is a **per-template win/loss table**, never an
  accuracy with an interval.
- Budget and hyperparameters are pre-registered before any test run, all method
  debugging happens on train, and every arm is reported on test — never "best of".

---

## 8 Scope limits

What the testbed cannot measure, disclosed in the thesis rather than engineered away.

- **Forecast-facing questions are answered from reanalysis.** Every window past a
  case's `as_of` is served by Archive, so the agent reasons under perfect foresight.
  The testbed measures routing, arithmetic and abstention on future-facing
  questions; it says nothing about how the assistant handles forecast uncertainty.
- **Station data is served uncorrected.** `tn` is estimated (the station has no
  `Tmin` column), `gs` reads low against the site's own pyranometers, and `precip`
  undercatches, more severely on frozen days; the measurements are in `findings.md`.
  The radiation offset is a gain error, not noise: station forcing suppresses ET and
  biases modelled retention upward. Answers disclose the source when the station
  serves.
- **A window spanning `as_of` mixes provenances** — station on the past side,
  Archive on the future side, with a measured temperature offset between them
  (`findings.md`), so such a window carries a source discontinuity.
- **The plotting vocabulary is closed.** `measured` series reach the five tables
  through a fixed set of columns and aggregations, and a derived quantity is
  available only if it exists there as a flag; plotting covers less of the database
  than free SQL does.
- **The `radiation` table carries a known one-hour timestamp offset, uncorrected.** It
  is stamped an hour behind the other four (`findings.md`), and the pinned file is
  **not** rebuilt to fix it: an ingest correction moves the `water.duckdb` hash and
  invalidates every captured response and materialized answer downstream of it. So one
  `as_of` literal cuts that table an hour looser than the rest, and a `radiation`
  series drawn beside another table's is misaligned by two half-hourly rows. The
  offset is stated in the semantic layer and disclosed here rather than removed. Its
  reach is small in any case — `radiation` covers only 2025-03-01 → 2025-10-01, so
  most of the `as_of` band has no row at all.
- **The task and reflection models are pinned by canary, not by version.** The
  provider serves no dated aliases, so a run records the served model id, endpoint and
  decoding parameters, and detects a provider-side swap only through a committed
  request/response canary — after the fact, on the next run that exercises it. A swap
  landing between two arms of one comparison is what this cannot prevent; diverging
  canaries mean the comparison is repeated. The decoding **seed is sent but not
  verifiably honoured** on these endpoints, so greedy decoding is the only
  determinism actually claimed, and §7's three repeats measure what survives it
  rather than removing it.
- **The semi-intensive roof has no lysimeter and no radiation mast**, so no runoff,
  retention or radiation question is asked about it (`findings.md`, `questions.md`
  §1.8). That narrows coverage rather than any claim: the roof still carries the
  soil-moisture and temperature families and every water-balance family.
- **Scope limits are measurable per roof, not per tool.** The gravel roof and the
  wetland are outside GR2L and `calc_irrigation` alike, so no case rewards
  distinguishing *which* tool a roof is out of scope for. The suite measures that
  modellability is family-dependent — measured questions answer, water-balance
  questions abstain — and nothing finer.
- **Effect sizes.** The suite detects large differences — on the order of 20
  percentage points on test_seen answer accuracy — and not small ones. Errors cluster
  by template, so instances of the same template asymptote and only more templates
  move the ceiling; test_unseen's seven templates support description, not
  inference. This is a limit of a suite this size, not a defect more instances would
  repair.
- **Ambiguity residual.** The candidate may not ask a clarifying question, which is
  safe only because the generation filter discards cases whose oracle is ambiguous.
  A *linguistically* ambiguous paraphrase can survive that filter; the model is then
  forced to guess and scores as wrong on a case that was never cleanly answerable.
- **Irrigation thresholds were tuned against uncorrected dynamics.** The deployed
  trigger levels are kept verbatim as site policy, so the corrected model's behaviour
  around them does not necessarily reproduce the decisions the deployed controller
  actually made. §3.5's decision-diff list is the disclosure, and it bounds what the
  testbed's irrigation answers say about the real deployed system.
- **The R deliverable can drift.** The bucket model and its decision ladder exist
  twice, in Python and in R, with no shared CI. The committed canary detects drift
  after the fact on the next run that exercises it; it cannot prevent it, and a stale
  canary is indistinguishable from an unchanged implementation. No case's answer
  depends on the R side, so drift there is a defect in that artifact, not in the
  evaluation.

---

## 9 Repository layout

The three-layer separation is achieved inside the package, so there is no top-level
`src/core/`, `src/clients/`, `src/harness/` tree:

```
src/water_assistant_agent/assistant/
  agents/root_agent/   agent.py (candidate injection) · text_to_sql_tool.py
  agents/text_to_sql/  frozen sub-agent: agent · pipeline · executor · fixers
  tools/               gr2l.py · weather.py · warehouse.py        (ADK layer 1)
                       gr2l_client.py · weather_client.py         (pure layer 2)
                       swc.py · site.py · schemas.py              (pure layer 3)
                       gr2l_tool.md · weather_tool.md ·
                       irrigation_tool.md                         (tool specs)
  prompts/             temporal.py (scenario clock) · agent_instructions.py
  settings.py          WATER_ASSISTANT_* pydantic-settings
  [to build]  context.py · cache.py · rules_constants.py · irrigation.py
              et_fao56.py
              knowledge/store.py · knowledge/cards/*.yaml   (card store)
              tools/{lookup,plot,irrigation}.py · tools/roofs.py
data/water.duckdb      (sha256 to pin)
eval/          [to build] schema/ templates/ oracles/ cases/ cache/ · pins.json
specs/agent_architecture/   agent_architecture.md · questions.md · decisions.md ·
                            findings.md · plan.md
```

Implementation order, prerequisites and blocking edges are `plan.md`'s.
