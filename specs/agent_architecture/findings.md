# Findings

Empirical, dated, verified-from-source facts about the codebase, the weather
data sources, and the pinned database. These are observations — what was
measured, what was read directly from source, what a live probe returned —
not intentions and not decisions. Nothing here explains why a choice was
made; see the architecture and decision documents for that.

Each entry states what was found, how it was verified, and when. An API
response, a library internal, or a live measurement can all change after the
fact; where the source text gave no date, this file says so explicitly
("unrecorded") rather than inventing one. Anything dated should be treated as
good as of that date and re-checked before being relied on much later.

## Codebase seams

**As-of view mechanism over the pinned DB.** `duckdb.connect(':memory:')` →
`ATTACH 'data/water.duckdb' AS src (READ_ONLY)` → `CREATE VIEW main.<table> AS
SELECT * FROM src.<table> WHERE timestamp <= :as_of` produces a view whose
`max(timestamp)` equals the bound, while `src.<table>` itself still reads
through to the live end of the file. Five tables need this wrapping:
`outflow`, `radiation`, `swc`, `tsoil`, `wetter`. No `*_raw` rename and no DB
rebuild are required; unqualified table names in text2SQL-generated SQL
resolve to `main`, i.e. to the bounded view.
*Verified:* directly against `data/water.duckdb`. *Date:* unrecorded.

**`DuckDbQueryExecutor` is already injectable, but injectability alone isn't
reachable.** `DuckDbQueryExecutor` takes a `connection_factory: Callable[[],
DuckDBPyConnection]` constructor argument (`agents/text_to_sql/executor.py:22`).
Three call sites build a connection from global settings today instead of
taking one in: `tools/warehouse.py`'s module-level singleton — which the
pipeline's `DuckDbExplainValidator` also imports (`text_to_sql/dry_run.py:13`)
— and `tools/swc.py:99-107`, an independent second seam. The executor sits at
the bottom of an import-time-frozen chain — executor → validator → pipeline →
tools → `Agent` → `AgentTool` — so a swappable `connection_factory` at the
bottom does not by itself make anything above it reconfigurable per call.
*Verified:* read from source at the cited files/lines. *Date:* unrecorded.

**The weather/GR2L clients are already parameterized; only the wrappers are
hard-wired.** `fetch_daily_weather` takes lat/lon as arguments; `run_gr2l`
takes `(rows, parameters)`; neither function imports ADK. Only the ADK
wrapper layer hard-wires `site.py`'s coordinates and a module-level singleton
`httpx.AsyncClient`.
*Verified:* read from source. *Date:* unrecorded.

**The scenario clock has exactly two production readers.**
`prompts/temporal.py:current_datetime_block()` and
`weather_client._choose_backend` (for its day-count cutoff) are the only two
call sites that read `site_now()` in production; nothing else calls it.
*Verified:* read from source. *Date:* unrecorded.

**The ADK static_instruction / instruction-provider split is already
exploited.** `root_agent` already sets both a `static_instruction` (a
byte-stable prefix, prompt-cache friendly) and a separate per-invocation
`instruction` provider.
*Verified:* read from source. *Date:* unrecorded.

**`AgentTool`'s name is the sub-agent's own name.** `TextToSqlAgentTool`
deliberately preserves the sub-agent's own name (`text_to_sql_agent`) as the
`AgentTool` name rather than renaming it, and trajectory scoring keys on that
name.
*Verified:* read from source (`text_to_sql_tool.py` docstring). *Date:*
unrecorded.

**No tests exist for `gr2l`, `weather`, or `swc`.** None of `tools/gr2l.py`,
`tools/weather.py`, `tools/swc.py` has existing test coverage.
*Verified:* read from the source tree. *Date:* unrecorded.

**`roof_type` is a plain `str` at the model tool's boundary.**
`predict_green_roof_water_balance_tool(roof_type: str, …)`
(`tools/gr2l.py:159-160`). The string is lowercased and looked up in
`NON_MODELLABLE_ROOFS` at `:237` and in `ROOF_PRESETS` at `:242`, so scope is
decided in code and nothing renders an enum into the function declaration: a
caller can still *name* a roof the tool does not model.
*Verified:* read from source. *Date:* 2026-08-19; re-read 2026-08-20.

**The wetland is still modellable in code.** `NON_MODELLABLE_ROOFS` covers gravel
aliases only — `gravel`, `gravel_roof`, `kies`, `kiesdach`, `kd`, `qgravel`
(`tools/gr2l_client.py:39-44`); `ROOF_PRESETS` carries a reachable `wetland`
entry (`:62-63`); `gr2l.py` routes that roof through `MM_ONLY_ROOFS`
(`tools/swc.py:51`, read at `gr2l.py:52,78`), which answers in millimetres with
`swc_pct=None` rather than declining; and the production `ROOT_INSTRUCTION` still
advertises "four roof segments: wetland, …"
(`agents/root_agent/agent.py:36`).
*Verified:* read from source. *Date:* 2026-08-19; re-read 2026-08-20.

**`weather_client` reads the wall clock in two places, and has no station path.**
`site_now` is imported at `tools/weather_client.py:23` and read at `:151`
(`today = today or site_now().date()`) and at `:197`, where `_choose_backend`
compares the window start against `site_now().date()` minus
`_FORECAST_PAST_LIMIT_DAYS = 92` (`:32`). Nothing in the module reads the
database.
*Verified:* read from source. *Date:* 2026-08-19; re-read 2026-08-20.

**The task LLM carries no decoding pins, and no reflection model exists.**
`settings.litellm_extra()` (`assistant/settings.py:79-86`) forwards `api_base`
and `api_key` and nothing else — no `temperature`, no `seed`. All four model
roles resolve to one served model (`.env:59-62`, `openai/qwen3.6-35b-a3b`), and
no reflection model is configured anywhere, though the pin list requires it to be
a second model distinct from the task model.
*Verified:* read from source and `.env`. *Date:* 2026-08-19; re-read 2026-08-20.

**The semantic layer states neither the collection area's value nor an alias
map.** Every efflux column in `tenants/green_roof/sensordata.py:16-21` is
described as "Outflow of the Lysimeter (with m² collection area) … (in liter)" —
the area is named without its number, so the model cannot convert litres to
millimetres from the schema it is given — and the file carries no alias map at
all: `Kies`, `KD` and `Kiesdach` appear only inside column names.
*Verified:* read from source. *Date:* 2026-08-19; re-read 2026-08-20.

**GR2L is served locally.** `.env:56` resolves
`WATER_ASSISTANT_GR2L_API_BASE_URL` to `http://localhost:8000/api-weinbau`, so
every capture pass over a model-bearing family needs that service running on the
capturing host and its canary committed.
*Verified:* read from `.env`. *Date:* 2026-08-19; re-read 2026-08-20.

**MLflow is already wired for the text2SQL experiments; its worker threads
drop ContextVars.** MLflow is already wired for the text2SQL experiments
(`src/experiments/`), including a `CostMeter`, and is the run-ledger sink in
use today. Its evaluation worker threads (`mlflow.genai.evaluate`) drop
ContextVars — this already broke `CostMeter`'s role attribution in the
text2SQL experiments.
*Verified:* observed directly in this repo's existing text2SQL experiment
runs. *Date:* unrecorded.

**litellm's response cache keys on the whole request, tool declarations
included.** `Cache.get_cache_key` (litellm 1.84.0) hashes every argument the
caller passed that appears in `ModelParamHelper._get_all_llm_api_params()` — 66
parameters, among them `messages`, `tools`, `tool_choice`, `model`,
`temperature`, `seed` and `response_format`. The off-the-shelf cache therefore
cannot serve one candidate's response to another candidate differing only in a
docstring, and a distinct seed yields a distinct key. A hand-rolled
"prompt-keyed" cache would have to reproduce that parameter set deliberately.
*Verified:* read from installed litellm source, plus a membership check over the
returned parameter set. *Date:* 2026-08-19.

## Optimizer internals

Installed versions where stated: **mlflow 3.13.0, gepa 0.1.1**.

**The optimizer entry point already ships its own GEPA adapter.**
`mlflow.genai.optimize_prompts(...)` with `GepaPromptOptimizer` wraps GEPA via
`MlflowGEPAAdapter` (`mlflow/genai/optimize/optimizers/gepa_optimizer.py:146+`),
which implements both `evaluate` and `make_reflective_dataset`.
*Verified:* read from installed mlflow source. *Date:* unrecorded.

**Candidate text is injected by a process-global patch.** Candidate text
reaches a running optimize_prompts iteration through a process-global patch of
`PromptVersion.template` (`optimize/optimize.py:292`), reverted in a `finally`
block; the patched value reaches the system only through a registry read
whose prompt name matches a candidate key.
*Verified:* read from installed mlflow source. *Date:* unrecorded.

**Records are evaluated in a `ThreadPoolExecutor`.** Records are evaluated in
a `ThreadPoolExecutor` (`optimize.py:329`, worker count controlled by
`MLFLOW_GENAI_EVAL_MAX_WORKERS`).
*Verified:* read from installed mlflow source. *Date:* unrecorded.

**Ground truth reaches scorers only through `expectations`; `inputs` is the
sole required column.** Ground truth reaches scorers only through the
`expectations` column (`optimize.py:296-299`); `inputs` is the sole required
column of a training record (`optimize/util.py:102-106`).
*Verified:* read from installed mlflow source. *Date:* unrecorded.

**The reflective dataset is built from trace spans plus
`Feedback.rationale`.** The reflective dataset GEPA reflects on is built from
MLflow trace spans plus `Feedback.rationale` (`gepa_optimizer.py:290-343`,
`util.py:180-188`) — not from the ADK event log. ADK spans do reach MLflow
traces, through `mlflow/tracing/otel/translation/google_adk.py`.
*Verified:* read from installed mlflow source. *Date:* unrecorded.

**A non-numeric scorer value requires an explicit `aggregation`.** A scorer
returning a non-numeric value raises unless an explicit `aggregation`
callable is supplied (`util.py:200-214`).
*Verified:* read from installed mlflow source. *Date:* unrecorded.

**MLflow ships no aggregation callable, and the silent default is the mean.**
The parameter is `aggregation: AggregationFn | None = None`
(`mlflow/genai/optimize/optimize.py:54`); the `weighted_objective` passed at
`:182` is defined at `:172` inside that same docstring example, so it is
illustrative text and not an export. Omitting the argument makes the objective
the **mean of the numeric scorer values** (`optimize/util.py:200-203`), which is
a silent reweighting rather than an error; a value that will not convert raises
instead (`:205-214`). `create_metric_from_scorers` (`util.py:135`) returns
`(aggregated_score, rationales, individual_scores)` (`:197,203`).
*Verified:* read from installed mlflow 3.13.0 source. *Date:* 2026-08-20.

**GEPA's Pareto front is over instances unless asked otherwise, and MLflow never
asks.** `gepa.optimize`'s `frontier_type` defaults to `"instance"`
(`gepa/api.py:53`, documented at `:135`, forwarded at `:398`), and
`GepaPromptOptimizer` builds its call as `self.gepa_kwargs | {…}` without ever
setting the key (`gepa_optimizer.py:349-359`), so candidate selection runs on the
scalar the `aggregation` callable returns. MLflow's adapter does forward
per-scorer values — `objective_scores=[result.individual_scores …]`
(`gepa_optimizer.py:194,206`) — and they reach the logs and the reflective
dataset, but the non-instance frontiers *raise* when an evaluator supplies none
(`gepa/core/state.py:210-215`), which is the shape of the code path nothing here
takes. A caller-supplied `frontier_type` inside `gepa_kwargs` would survive the
merge, the literal on the right-hand side not carrying that key.
*Verified:* read from installed mlflow 3.13.0 / gepa 0.1.1 source.
*Date:* 2026-08-19; re-read 2026-08-20.

**The text2SQL GEPA wiring is not reusable as-is.** `train_gepa.py:42,251`
uses `GepaPromptOptimizer` for a single registered prompt with a scorer over
a pure function. An agent testbed needs N+1 registered prompts and a
`predict_fn` that builds a per-case context — same entry point, different
candidate surface, so the existing wiring cannot be reused unmodified.
*Verified:* read from source. *Date:* unrecorded.

**The unused-prompt signal is a `logger.warning`, and it is the only one.**
`_build_eval_fn` collects the names its patched `template` property served into
a `used_prompts` set and, after the batch, logs
`"The following prompts were not used during evaluation: …"` at WARNING
(`optimize/optimize.py:337-344`). Nothing raises, nothing enters the returned
`EvaluationResultRecord`s, and nothing reaches the run's metrics: a component
left unread is invisible in the results and visible only in stderr, which is
why `harness/candidates.py` asserts on it instead.
*Verified:* read from installed mlflow 3.13.0 source. *Date:* 2026-08-25.

**MLflow's prompt cache is a process-global singleton with no default TTL for a
version-keyed read.** `PromptCache` is a thread-safe singleton
(`mlflow/prompt/registry_utils.py:332-370`) keyed on name plus version-or-alias;
`load_prompt`'s `cache_ttl_seconds` defaults to `MLFLOW_ALIAS_PROMPT_CACHE_TTL_
SECONDS` (60 s) for an alias read and to `MLFLOW_VERSION_PROMPT_CACHE_TTL_
SECONDS` — unset, so **no expiry** — for a version read. Two consequences here.
Caching a `PromptVersion` is safe inside a search, because the candidate patch
replaces `template` on the *class* and a cached instance therefore still yields
candidate text. And a test suite that points the registry at a throwaway file
per test must clear the singleton, or the second test reading
`agent_root_instruction` version 1 gets the first test's text.
*Verified:* read from installed mlflow 3.13.0 source, and by the version-2 read
in `tests/harness/test_candidates.py`. *Date:* 2026-08-25.

**A prompt name admits alphanumerics, hyphens, underscores and dots — no
slashes.** `register_prompt(name="water_assistant/root_instruction")` raises
`MlflowException: Prompt name can only contain alphanumeric characters,
hyphens, underscores, and dots.`, so the candidate surface's namespacing is a
prefix (`agent_tool_<name>`) rather than a path.
*Verified:* registered against a local SQLite registry on mlflow 3.13.0.
*Date:* 2026-08-25.

## Weather source measurements

**Station vs ERA5 (Open-Meteo Archive) biases.** Against ERA5 (Open-Meteo's
Archive backend, a ~31 km reanalysis), the site's own station reads: `tm`
+0.69 °C, `tx` +1.05 °C (r = 0.99); `gs` (global radiation) ~26% low against
the site's own on-site pyranometers; `precip` undercatches by ~15% on
liquid-precipitation days and ~50% on frozen-precipitation days; the `tn`
estimator (derived — the station has no `Tmin` column) carries 0.25 °C mean /
0.48 °C p95 ambiguity against the naive alternative estimator.
*Verified:* measured comparison of the station record against ERA5 / the
site's own pyranometers. *Date:* unrecorded.

**Open-Meteo's Forecast backend's actual past reach is 64 days, not the
documented 92.** At the site, all seven GR2L weather variables are present
back to `today − 64` and null from `today − 65` onward; a `past_days=92`
request returns 93 rows, of which 28 are entirely null.
*Verified:* measured live against the Open-Meteo Forecast backend at the site
coordinates. *Date:* 2026-07-29.

**The two Open-Meteo backends disagree on the same past day.** For
2026-06-21, `precip` reads 0.00 mm from the Forecast backend vs 2.50 mm from
the Archive backend; for 2026-06-20, `tm` reads 24.4 °C (Forecast) vs 26.0 °C
(Archive).
*Verified:* same-day query against both backends directly, part of the same
investigation as the 64-day reach measurement above. *Date:* 2026-07-29.

**The Forecast endpoint refuses an old window outright; Archive serves it in
full.** The Forecast endpoint refuses a 2026-04-25 window outright
("Parameter 'start_date' is out of allowed range from 2026-05-09"), while the
Archive endpoint serves the same window completely.
*Verified:* live query against both backends at the site coordinates.
*Date:* 2026-08-10.

**Open-Meteo's Historical Forecast API: resolution and divergence from
Archive.** Open-Meteo's Historical Forecast API serves windows the plain
Forecast endpoint refuses, at 2.5 km grid resolution (vs Archive/ERA5's
31 km) from a grid cell closer to the site — but its values differ
materially from Archive: 1.70 mm (Historical Forecast) vs 8.70 mm (Archive)
precip on 2026-05-05. It archives model analyses, not lead-time forecasts:
its `*_previous_dayN` variants cap at 7 days and do not cover precipitation.
*Verified:* read from the Open-Meteo API and a direct comparison query.
*Date:* unrecorded.

## Data record

**Wetter (station) record: span, timezone, completeness, sentinel.** The
station record (`wetter` table) spans 2025-01-01 to 2026-04-27, is stored in
UTC with no DST, and has 479 of 481 possible days with a complete set of 48
half-hourly rows. Wind speed carries a `-7999` sentinel value that must be
excluded before averaging.
*Verified:* read from `data/water.duckdb`'s `wetter` table; the UTC/no-DST
claim was verified via season-invariant solar noon (a DST-shifted clock would
make the site's solar-noon timestamp drift by season; it does not).
*Date:* unrecorded.

**Data record end/start dates by table.** `swc`, `tsoil` and `outflow` end
2026-04-24; `wetter` ends 2026-04-27. `outflow` starts 2025-04-15.
`radiation` covers only 2025-03-01 to 2025-10-01. `wetter` reaches back to
2025-01-01. `swc` and `tsoil` reach back furthest, to 2024-07-23.
*Verified:* read from `data/water.duckdb`. *Date:* unrecorded; the
`swc`/`tsoil` start date was added 2026-08-17.

**Not every roof is instrumented in every table.** `swc` and `tsoil` carry all
five roof segments; `outflow` and `radiation` carry only four, and it is the
same four — the **semi-intensive roof has no lysimeter and no radiation mast**.
`outflow`'s six columns are `Kies_Efflux` (gravel), `Extensiv1_Efflux`
(irrigated extensive), `Extensiv2_Efflux` (non-irrigated extensive),
`Sumpf2_Efflux` (wetland), plus two small extensive-substrate test lysimeters
(`Zeitlysi_Efflux_x`, timer-irrigated; `Sensorlysi_Efflux_x`,
threshold-irrigated) that are not roof segments. `radiation` carries `ED1`
(irrigated extensive), `ED2` (non-irrigated extensive), `KD` (gravel) and `SD`
(wetland). A template reading an outflow or radiation column therefore cannot
sample the semi-intensive roof at all.
*Verified:* `information_schema.columns` over all five tables, read against
`sensordata.py`'s column descriptions. *Date:* 2026-08-19.

**The lysimeter collection area is 1 m², so outflow in litres is numerically
millimetres.** `sensordata.py` describes every efflux column as "Outflow of the
Lysimeter (with m² collection area) … (in liter)" without ever stating the
number; the site confirms 1 m². Cumulative sums corroborate it over the outflow
record (2025-04-15 → 2026-04-24, against 478.6 mm of station rain): gravel
365.9 L (0.76 of rainfall), irrigated extensive 190.7 (0.40, and it receives
irrigation on top of rain), non-irrigated extensive 143.8 (0.30), wetland 42.8
(0.09). Every ratio is below 1 and they rank as the roofs' storage does; a
0.5 m² area would put gravel runoff at 1.5× rainfall and 2 m² would give bare
gravel 62 % retention, neither of which is physical. So **1 L = 1 mm** on these
columns, and retention carries no area factor.
*Verified:* per-column sums over `outflow` against `sum(wetter.Rain)` over the
same window. *Date:* 2026-08-19.

**The pinned DuckDB is present and readable from this checkout.**
`data/water.duckdb` exists (13,119,488 bytes) and `.env:68`
(`WATER_ASSISTANT_DUCKDB_PATH`) resolves to it. Every measurement in this
section was taken by opening that file read-only from the repository root.
*Verified:* `duckdb.connect(..., read_only=True)` against the path in `.env`.
*Date:* 2026-08-17.

**Two whole-system lysimeter outages.** `outflow`, `swc` and `tsoil` drop out
*simultaneously* twice; `wetter` and `radiation` are unaffected by both. The
first removes 2025-10-02 to 2025-10-05 (4 days), bracketed by partial days
2025-10-01 (36 rows) and 2025-10-06 (8 rows). The second removes 2025-10-24 to
2025-11-27 (35 days), bracketed by 2025-10-23 (27 rows) and 2025-11-28 (25–27
rows depending on table). Inside the catalog's `as_of` band (2025-06-01 to
2026-04-24, 328 days) this leaves 39 days with no lysimeter row at all and 49
days missing or thin (fewer than 44 of 48 expected `swc` rows). The loss is
seasonally concentrated: October 16/31 usable days, November 2/30. Coverage of
a look-back window from a sampled `as_of` degrades accordingly — 83.5 % of band
days have a fully covered previous 2 days, 75.5 % a covered 7 days, and 52.5 %
a covered 30 days.
*Verified:* distinct-date gap scan and per-day row counts over all five tables
in `data/water.duckdb`. *Date:* 2026-08-17.

**Outages are missing rows, never NULLs.** Every column of all five tables has
a non-null count exactly equal to its table's row count: there is not one NULL
anywhere in the pinned database. A data-availability test written as a null
check therefore never fires; row presence is the only signal an outage leaves.
*Verified:* per-column `count(col)` against `count(*)` for all five tables.
*Date:* 2026-08-17.

**The `QWetland` sensor is dead from 2026-03-12, not degraded from
2026-02-01.** From 2026-03-12 to the end of the record (2026-04-24, 44 days)
`swc.QWetland` reads a flat ~0.00 %θ, with a daily mean below 0.5 %θ on 43 of
those days. Zero is physically impossible for a ponded fleece mat whose healthy
record spans 4–96 %θ (mean 70.5). The preceding weeks are sound, not
"unreliable": January–February show a coherent recharge from 4 %θ to a plateau
at 85.4–85.7 %θ — the documented ~86 %θ saturation — followed by drainage to
27 %θ by 2026-03-11.
*Verified:* daily min/mean/max and zero-counts of `swc.QWetland` over the whole
record. *Date:* 2026-08-17.

**The wetland `Ssubmin` preset was derived over a window 40 days short.**
`gr2l_tool.md` derives `Ssubmin = 1.3 mm` from the `QWetland` p1 of 7.554 %θ
computed over the record before 2026-02-01. Excluding only the genuinely failed
window instead — before 2026-03-12 — gives p1 = 6.309 %θ, i.e.
`Ssubmin = 1.07 mm`. The p99 is unaffected (86.189 vs 86.185 %θ). The preset is
pinned and the wetland is out of layer-1 scope, so the value stands and the
discrepancy is recorded rather than corrected.
*Verified:* `quantile_cont(QWetland, 0.01)` over both windows.
*Date:* 2026-08-17.

**`QWetland` also froze once at a plausible value.** The column holds exactly
77.160 %θ for 312 consecutive half-hourly rows (156 h), 2025-05-22 to
2025-05-28. A plausibility bound cannot see this episode; only a stuck-run test
can.
*Verified:* longest-identical-run scan per column. *Date:* 2026-08-17.

**`radiation` is stamped one hour behind the other four tables.** Lag-correlating
`wetter.Rad_SW` against `radiation.KD_SWdown` — two shortwave sensors at one
site, which should be simultaneous — peaks at r = 0.9975 with `radiation`
shifted −60 min, against r = 0.9049 at zero lag. The other tables share the
station clock: `wetter.Rain` against `outflow.Kies_Efflux` peaks at lag 0
(r = 0.772), and against the first difference of `swc.QEx1` at lag 0
(r = 0.344). `tsoil.TGravel` against `radiation.KD_TSFC` peaks at +90 min,
consistent with a ~30 min physical lag plus the same 60 min offset. A single
`as_of` literal therefore cuts `radiation` one hour looser than the other four
tables.
*Verified:* lag-correlation sweeps at 30 min resolution over the tables'
overlap in `data/water.duckdb`. *Date:* 2026-08-17.

**`radiation` is absent for most of the `as_of` band.** Its 2025-03-01 to
2025-10-01 coverage leaves 205 of the band's 328 days with no radiation row at
all, while `radiation` is one of the five tables reachable through
`plot_timeseries`' `measured` vocabulary and the text2SQL semantic layer.
*Verified:* per-day presence scan against the band. *Date:* 2026-08-17.

**A timezone-aware `as_of` is compared in the session timezone, so the as-of
cut is host-dependent.** The five tables' `timestamp` columns are naive
`TIMESTAMP` holding UTC. DuckDB renders a `TIMESTAMP WITH TIME ZONE` parameter
into the connection's session timezone — inherited from the host `TZ`, observed
here as `Europe/Berlin` — and compares that wall-clock reading against the naive
column. Measured against `wetter`: `2026-03-14T08:00+01:00` keeps rows through
08:00, while `2026-03-14T08:00Z` keeps rows through 09:00 and
`2026-03-14T08:00+05:00` keeps rows through 04:00. In summer the same shape
widens: `2025-07-15T08:00+02:00` keeps rows through 08:00 against
`2025-07-15T08:00Z`'s 10:00. So §6.1's `"as_of": "2026-03-14T08:00:00+01:00"`
cuts the UTC record at 08:00 UTC — one hour of future data in winter, two in
summer — and the same case file would cut an hour earlier on a host with
`TZ=UTC`. The `water.duckdb` hash cannot detect this.
*Verified:* `select max(timestamp) from wetter where timestamp <= ?` with naive,
UTC, `+01:00`, `+02:00` and `+05:00` parameters. *Date:* 2026-08-17.

**UTC and Europe/Berlin day groupings differ materially.** Grouping `wetter.Rain`
by the naive UTC date the column carries, against the Europe/Berlin calendar date
of the same rows, gives different daily totals on **106 of 482 days over the whole
record, by up to 6.664 mm**; inside the catalog's `as_of` band (2025-06-01 →
2026-04-24) it is **77 of 329 days, by up to 5.593 mm**. The two groupings differ
by one hour of rows in winter and two in summer, so rain falling in the last hours
of a UTC day is booked to the next Berlin day. That is enough to move a peak-day
argmax, flip a daily did-it-run-off boolean near a boundary, and shift a monthly
total at its edges.
*Verified:* daily `sum(Rain)` under both groupings over `data/water.duckdb`,
compared per day. *Date:* 2026-08-19.

**Spring-forward artifact and wind sentinel spread.** The two `wetter` days
short of 48 rows are the spring-forward Sundays 2025-03-30 and 2026-03-29, each
missing exactly the 02:00 and 02:30 rows. This is an ingest artifact rather than
local-time storage — the season-invariant solar noon above rules out a
DST-shifted clock — but the rows are missing regardless, so 2026-03-29 fails a
48-row complete-day test inside the band. The `-7999` wind sentinel appears on 9
days (2025-04-20/21, 2025-07-15, 2025-07-22, 2025-08-28, 2025-09-25,
2025-10-26, 2026-04-18/19), reaching 18 of 48 samples on 2025-08-28 and 15 on
2025-04-21.
*Verified:* per-day row counts and sentinel counts over `wetter`.
*Date:* 2026-08-17.

**Validity-predicate specificity, measured against the record.** A daily-mean
plausibility floor on `swc.QWetland` flags 43 days at every threshold from
0.5 %θ to 3.0 %θ, all inside the dead period, with zero false positives across
the whole record from 2024-07-23 — the threshold is insensitive over a sixfold
range. A stuck-run test at 24 identical consecutive samples (12 h) flags zero
days on `QEx1`, `QEx2`, `QIn` and all five `tsoil` columns over the band, and 53
on `QWetland`; the longest identical run on any healthy state sensor is 17
samples (8.5 h, `QEx1`). Both tests are unusable on fluxes and on `QGravel`:
`Sumpf2_Efflux` holds 0.000 for 6948 consecutive rows (3474 h, 2025-04-15 to
2025-09-09), and `QGravel` — a roof with no substrate — has a band median of
0.06 %θ. A daily-range floor is not a substitute for the plausibility bound:
catching 41 of the 44 dead `QWetland` days requires `range ≤ 0.5`, which
false-positives on 63 healthy days.
*Verified:* threshold sweeps of all three predicates over
`data/water.duckdb`. *Date:* 2026-08-17.

**What the record supports as a heatwave definition.** T08's duration rule is
authored eval policy, so the pinned station record is what bounds it. Counting a
heatwave day as one inside a run of consecutive Europe/Berlin days at or above
the deployed controller's 24 °C heat threshold: a **2**-day run marks 65 days
over 6 months, **3** days marks 53 over the same 6 (per month 1 / 4 / 12 / 15 /
16 / 5), and **4** days marks 44 over only 4. Raising the threshold to 30 °C
collapses it — 12 days over 3 months at a 2-day run, and 4 days in a single month
at 3 days, which would make T08's count zero in almost every sampled month.
`rules_constants.py` therefore carries 3 days at 24 °C, the definition that keeps
the question answerable across the most months without being satisfied by every
warm spell.
*Verified:* run-length scan over daily `max(Tmax)` from `wetter`, grouped by
Europe/Berlin day. *Date:* 2026-08-20.

**Per-column healthy ranges, the basis of `roofs.py`'s plausibility bounds.**
Over the whole record, half-hourly: `QGravel` 0.000–8.923 %θ (band median
0.034), `QEx1` 3.518–35.803, `QEx2` 1.038–25.846, `QIn` 3.981–33.566, and
`QWetland` 4–96.450 excluding the dead stretch from 2026-03-12. Soil temperature
spans −12.024 to 55.253 °C across all five columns, the extremes both on the
gravel roof. Half-hourly outflow tops out at 11.600 L (`Kies_Efflux`) and daily
totals at 25.800 L against a wettest station day of 22.933 mm — a lysimeter can
shed more than the gauge catches, which the undercatch measurements above
predict. Radiation shortwave reads a few W/m² *negative* at night on every mast
(minimum −8.204), so a plausibility floor at zero would reject healthy darkness.
The committed bounds take the floor at half the healthy minimum and the ceiling
at the store's physical saturation, which places every one of them outside these
ranges.
*Verified:* per-column min/percentile/max over `data/water.duckdb`, and site-day
means of the same columns re-checked against the committed bounds by
`tests/assistant/test_roofs.py`. *Date:* 2026-08-20.

**Where the flat field capacity puts the semi-intensive roof.** The
`roof_reference_ranges` card cuts each roof's plausible `swc` envelope at the
rule's dry threshold and field capacity (T069). Classifying site-day means over
the whole record by those bands: the irrigated extensive roof splits 106 low /
282 normal / 214 high of 602 days, the non-irrigated one 262 / 330 / 10, and the
semi-intensive roof **211 / 89 / 302** — it spends half the record above the
band edge. That edge is the deployed controller's flat 22 %θ capacity on all
three roofs, where GR2L measures 30.4 %θ for this one, so the lopsidedness is
the flat-capacity convention showing in the data rather than an unusually wet
roof. Bands fitted to the record would have hidden it; the card states the
site's policy and discloses this instead
(`decisions.md § No fitted correction between the instrument and the oracle`).
No day on any of the three roofs falls outside the committed plausibility
bounds.
*Verified:* site-day means per `swc` column over `data/water.duckdb`, classified
against the rendered band edges; asserted as non-degeneracy per band in
`tests/assistant/test_knowledge_rendered.py`. *Date:* 2026-08-21.

**Rejecting constant-valued days would delete the balance classes.** Per-day
`max = min` rejection over the band removes 217 of 289 `Kies_Efflux` days, 241
`Extensiv1_Efflux`, 245 `Extensiv2_Efflux`, 272 `Sumpf2_Efflux` and 162 of 328
`wetter.Rain` days — every one of them a legitimate dry day — while catching
only 26 of the 44 dead `QWetland` days.
*Verified:* per-day constant-value counts per column over the band.
*Date:* 2026-08-17.

**The band carries 11 rain events usable for a retention question.** Taking an
event as a maximal run of consecutive Europe/Berlin days with at least 0.2 mm of
station rain, extended by one drainage day, the `as_of` band holds 57 events, 15
of them at least 10 mm deep. Four of the 15 fall to `outflow` coverage (the two
lysimeter outages: 2025-07-15, 2025-10-04, 2025-10-23 and 2025-11-15, the last
with no outflow row at all), leaving **11 events and 41 (event, roof) pairs**
over P1f's four roofs; per roof it is 10 / 10 / 10 / 11. Retention leaves [0, 1]
on 3 further pairs — gravel on 2025-09-21 (−72.7 %) and both extensive roofs on
2026-02-21 (−13.4 %, −6.6 %) — where outflow exceeds the gauge's rain, which the
undercatch measurements above predict for frozen days. Across the 11 events, 8
carry both a high and a low retention over their four roofs at any target from
50 % to 70 % (9 at 40 %), and the pairs split 25 above / 16 below at 50 %.
*Verified:* `scripts/count_t12_rain_events.py` against `data/water.duckdb`,
output committed at `specs/agent_architecture/t12_rain_events.md`.
*Date:* 2026-08-20.

**Zero-outflow days dominate the band.** Daily outflow sums are exactly zero on
217 of 289 days for the gravel roof (75.1 %), 241 (83.4 %) for `Extensiv1`, 245
(84.8 %) for `Extensiv2` and 272 (94.1 %) for `Sumpf2`, leaving 17 non-zero
wetland days in the whole band.
*Verified:* daily `sum(col)` per outflow column over the band.
*Date:* 2026-08-17.

**T05's unique-peak pool is 28 of 44 whole roof-months, and 2 of them are the
wetland's.** A peak-outflow question needs a month whose daily maximum is
attained on exactly one day; a tie gives the question two defensible answers and
§1.6 discards the draw. Over the 11 calendar months lying wholly inside the
outflow record (2025-05 → 2026-03), the gravel roof has a unique peak in all 11,
the irrigated extensive roof in 8, the non-irrigated in 7, and **the wetland in
2** — 2025-09 and 2026-02. Every one of the 11 months has at least one roof that
qualifies, so nine disjoint months across train and test_seen are reachable; what
is not free is the roof axis, since the wetland can appear in T05 at most twice
in the whole suite and both of its months are then forced. Ties are not rare
noise here: they are the zero-outflow finding above seen from the other side, an
entirely dry month tying at 0.0 on every day in it.
*Verified:* per-(roof, month) daily `sum(col)` over `outflow` through
`site_day_expr()`, counting days attaining the month's maximum; whole months
only.
*Date:* 2026-08-24.

**T24a's new oracle reproduces the two pilot cases T107 measured over, key for
key.** Those two were hand-instantiated through `make_pilot_cases.py`'s
`oracle: False` branch, which copied `status` off the template; T110's oracle
derives it from §3.6's trigger instead. Run over the committed `inputs` at each
case's own `as_of`, it returns the same `status`, `answer`, `unit` and `pins` for
both — so the pilot's 48 rollouts were scored against the status this packet now
computes, and nothing in T107's numbers moves. The branch is left standing rather
than switched over, because regenerating the pilot artifacts is what *would* move
them.
*Verified:* `t24a_plot_request` against `eval/cases/pilot.json`'s two T24a
records, in `tests/eval/test_oracles.py`.
*Date:* 2026-08-24.

**No forward-looking weather window can resolve to the station, at any `as_of`.**
The composite serves from the station only where the record covers **every** day
asked for, tested through the as-of view — and a forward window starts on the
case's own day, which that view has truncated at the `as_of` instant. So it is
short of its 48 rows, the completeness rule drops it, and the window falls to the
Archive whole. Measured at `as_of` 08:00, 12:00 and 23:00 on 2026-03-10: the
station serves 0 of 1, 0 of 3 and 0 of 7 days on `forecast_days` windows at every
one of the three, and 7 of 7 on the preceding week. `record_bounds()` through the
same view ends on 2026-03-09 — the day before the cut, never the cut's own day.
Two consequences. Every family C case, and every seed-bearing forward window
(T07's refill week, T09's horizon), carries `weather_source: ["archive"]` and is
**never** stamped with `station_derivation`; the derivation pin therefore covers
only the retrospective windows — T15a's SQL route reads the column directly, and
T24a(ii)'s completed month is the one plot that reaches the station. And capture
is unavoidable for all of them: a station window needs no cache entry, an Archive
window does.
*Verified:* `StationWeatherSource(ctx.db).daily_rows` over
`make_case_context(as_of)` for three `as_of` hours × three horizons, against the
same call on the preceding week.
*Date:* 2026-08-24.

**T15a and the station weather path return the same number.** Over
2026-04-13..19, T15a's `sum(Rain)` grouped by the site's day gives 29.257 mm, and
`StationWeatherSource.daily_rows` over the same window returns seven complete
days summing to 29.257 mm of `precip`. `decisions.md § Trajectory scoring and
routing probes` accepts a real cost on this template — a candidate answering
correctly through the weather tool scores 0 on trajectory — and that cost is only
worth accepting if the two routes really are equivalent, which had been asserted
and not measured. The equality holds on a *fully covered* window and only there:
the derivation serves a day only with all 48 of its rows and drops the rest,
where the SQL route sums whatever is present. §1.6's coverage predicate is what
keeps a sampled window on the side where they agree.
*Verified:* `t15a_past_rain` against `StationWeatherSource(ctx.db).daily_rows`
over one `make_case_context` at `as_of` 2026-04-20, in
`tests/eval/test_oracles.py`.
*Date:* 2026-08-24.

**Family D against the live model, hand-checked — re-taken on the post-fix
build.** At `as_of` 2026-04-20 12:00 on the non-irrigated extensive roof, T10
over three days runs 2026-04-20..2026-04-22 seeded at 20.14 %θ (the `QEx2`
reading at 10:00, inside the cut) and returns substrate storage 14.10 / 11.1883 /
8.2852 mm, so the minimum is `8.2852 / 70 × 100 = 11.84 %θ` on the 22nd — the
conversion the tool's own `_to_days` performs at `SH = 7 cm`, checkable without
re-running the model. T19 over seven complete past days runs
2026-04-13..2026-04-19 seeded at 13.69 %θ, with daily absolute deviations of
1.04, 0.671, 2.051, 2.899, 3.917, 3.755 and 4.174 pp: mean `18.507 / 7 = 2.644 →
2.64 pp`, largest 4.17 pp. The two windows resolve to **different sources** — the
forward one to the Archive, the retrospective week to the station — so T10 stamps
three pins and T19 four.
**The pre-fix numbers were 12.33 %θ and 2.6 pp**, against storage of 14.10 /
11.4089 / 8.6292 mm and deviations of 1.04 / 1.081 / 1.381 / 2.029 / 2.997 /
4.975 / 4.664. Both moved in the direction the ET change predicts: the new build
evapotranspires more, so the roof dries faster and every modelled minimum falls.
They are recorded because the difference between the two is the clearest
statement of what re-pinning the canary cost.
*Verified:* both oracles through `make_case_context(as_of, allow_live=True)`
against the live GR2L service and the pinned database, one event loop, a scratch
cache directory emptied between the two runs. *Date:* 2026-08-24 (both).

**T08's month pool does not survive its own boundary guard; a period pool does.**
A heatwave run crossing the edge of the window the question names gives the count
two defensible values — the days the window contains against the days the record
marks — so the oracle computes both and discards the draw when they differ. Over
the ten calendar months the band completes, that refuses **three** (June 2025 at
11 against 12, August at 14 against 16, September at 4 against 5), leaving seven,
of which six answer zero and only July 2025 is non-zero at 15. Seven is short of
the nine disjoint draws §1.7 needs and a pool of six zeros is not a count
question. Sliding windows restore both: of the 315 fourteen-day windows in the
band, **287 are accepted and 82 of those answer non-zero** (max 13); at seven days
it is 293 of 322 accepted with 58 non-zero, at ten days 287 of 319 with 69. T08
therefore samples `{period}`, which also makes it T02's card-grounded twin.
*Verified:* `heatwave_days` over every sliding window of each length in
2025-06-01 → 2026-04-24, against daily `max(Tmax)` from `wetter` grouped by the
site day. *Date:* 2026-08-24.

**T25's two gold routes read a different yesterday, and the gap is 0.63 °C.**
Source resolution is whole-window (`CompositeWeatherClient`), so the two-call
route asks for yesterday alone and is served by the station, while the
single-call route asks `past_days=1, forecast_days=2` and falls to the Archive
whole — the record holds no future. At `as_of` 2026-04-20 the station reports
12.57 °C for 2026-04-19 and the Archive 13.20 °C for the same day, against a
tomorrow of 11.90 °C. Both routes answer "no" here, but a tomorrow anywhere in
(12.57, 13.20] would have them disagree, and the catalog calls both routes gold —
so the case would score a candidate wrong for a route it was told to take. The
oracle computes both readings and refuses the draw when they differ; this is the
first template in the catalog where the *route* rather than the window is what
makes an answer two-valued.
*Verified:* `t25_tomorrow_warmer_than_yesterday` through
`make_case_context(2026-04-20 12:00, allow_live=True)`, its three fetches logged
with their resolved sources. *Date:* 2026-08-24.

**Family E's card-grounded answers, hand-checked against the record.** T12 over
2025-07-12..2025-07-14 (the event's window, drainage day included) reads 14.688 mm
of station rain: the gravel roof sheds 11.400 mm for a retention of 0.2239 and the
irrigated extensive roof sheds nothing for 1.0, so at the 0.50 target the same
event answers **no** for one roof and **yes** for the other — the class balance
the target was chosen for. Over 2026-02-17..2026-02-19 the irrigated roof sheds
9.900 mm of 14.178 mm for 0.3017. All three reproduce
[`t12_rain_events.md`](./t12_rain_events.md)'s table (22.4 %, 100.0 %, 30.2 %) to
its stated precision, which is the check that matters: the report is generated by
a standalone script and the oracle shares no code with it, so agreement is two
independent readings of one record rather than one reading twice. T08 over July
2025 counts 15 heatwave days out of 20 days at or above 24 °C — the 1st–7th is a
run of seven, the 18th–25th a run of eight, and the 10th, 11th, 13th, 14th and
27th are isolated or paired and count for nothing.
*Verified:* both oracles through `make_case_context(2026-04-24 23:00)` against the
pinned database. *Date:* 2026-08-24.

**T16a's answer depended on a roof its question did not name, on 27 % of draws.**
The irrigation trigger levels are per segment — 10 %θ dry on both extensive roofs
against 16 %θ on the semi-intensive — so a question stating a soil moisture and a
rain total without naming a roof can have two answers. Over a grid of 900 draws
(soil moisture 2.0–24.0 %θ in 0.5 steps × five temperatures × four rain totals)
the three roofs disagree on **240**, and the disagreement spans the whole
4.5–16.0 %θ range rather than sitting at one edge. The catalog's own stated
values were inside it: at 12 %θ, 28 °C and 2 mm the two extensive roofs answer
*no* (`sufficient_moisture`) and the semi-intensive answers *yes*
(`cooling_requested`). T16a's sketch now names a roof, as T16b's already did.
*Verified:* `irrigation_decision` over `features_from_stated_values` for the three
roofs across the grid. *Date:* 2026-08-24.

**Family F reaches all five rungs and agrees with the tool on each.** On the
irrigated extensive roof: 4.0 %θ with heat is `below_wilting_point` (rung 1,
irrigate); 12.0 %θ at 15 °C is `no_heat_no_stress` (rung 2); 12.0 %θ at 30 °C is
`sufficient_moisture` (rung 3); 8.0 %θ at 30 °C with 20 mm forecast is
`refill_forecast` (rung 4) and with 2 mm is `cooling_requested` (rung 5,
irrigate). Each draw was run through both oracles and through `calc_irrigation`'s
stated path on one context, and the three agree on the boolean and on the reason
code every time. The rung-4 boundary is the roof's deficit to capacity: at
15.4 mm capacity and 8.0 %θ (5.6 mm) the deficit is 9.8 mm, so 20 mm refills and
2 mm does not.
*Verified:* `t16a_manual_on_stated_values`, `t16b_calculator_on_stated_values` and
`make_irrigation_tool(ctx)` over the same eight draws. *Date:* 2026-08-24.

**The served GR2L accepted `albedo` and ignored it — fixed 2026-08-24, and the
fix moved the whole ET routine. SUPERSEDED as a blocker; kept because it is what
the T22 guard was built against.** Six albedos — 0.0, 0.05, 0.2, 0.5, 0.8, 1.0 — posted with an
otherwise identical request return **one byte-identical response**: same `ET_PM`
(8.5882 mm/day on the probe window), same `Ssub` series, same everything. The
argument does reach the request — `resolve_roof_parameters` sets
`parameters.albedo` to each value and the client sends it — so this is the
service discarding it, not the wrapper dropping it. That the parameter is
load-bearing in the physics is checkable against our own port of the same R
convention: `et0_for_row` over that window gives 10.0276 / 8.5882 / 6.4291 /
2.8306 mm at albedo 0.0 / 0.2 / 0.5 / 1.0, a 3.5× range, and the served value
equals the local one at **0.2** exactly — the figure the R checkout hard-codes at
`:68`. The newer build added `albedo` to the API surface without wiring it into
the ET routine.

Consequences, and they are the reason this is recorded rather than filed:
`t22_albedo_override` would return the un-overridden prediction on every draw, so
the answer metric would be satisfied by a candidate that never passed the
argument at all — the one failure a counterfactual template cannot absorb. The
oracle therefore raises `InertOverrideError`, measured per draw against the
roof's own default rather than hard-coded, so T22 begins materializing unchanged
on the day the service wires the parameter up. T26(i) composes `albedo` with a
forcing and stays answerable, because the rain still moves it; half of its claim
is not measurable meanwhile, and (iii) is the variant the compositional headline
should rest on. `forcings` and `initial_soil_moisture_pct` are unaffected — both
demonstrably move the model.
*Verified:* six `run_gr2l` calls over one four-day forcing at
`theta_01 = 14.0 mm`, responses compared as canonical JSON; `et_fao56.et0_for_row`
over the same rows at four albedos. *Date:* 2026-08-24.

**The albedo fix landed, and it carried a second change nobody asked for.** The
same six-albedo sweep now returns **six distinct responses**, and `ET_PM` tracks
albedo with the slope our port predicts — the served-minus-local difference is
*constant in albedo* (0.4489 mm/day across 0.0 → 1.0 on a July window), which is
what says the albedo term is now applied identically on both sides. But that
constant is new, and it is not constant across days: before the fix, served
`ET_PM` at albedo 0.2 matched our port to 4e-5 on every day tried; it is now
higher by 0.4489 / 0.3573 / 0.1883 / 0.2958 / 0.4328 mm/day on five test days.
So the fix did not only wire the parameter through.
*Verified:* six `run_gr2l` calls per day over five days. *Date:* 2026-08-24.

**The second change is the `Rnl` Kelvin fix, and it is diagnosed exactly rather
than inferred.** `GR2L_function.R` in the checkout has itself moved: one upstream
commit, `3e7405a` "add albedo, open_water flag to the GR2L model", changed four
things at once —

1. `albedo` and `open_water` became arguments and `Rn <- Rs * (1 - albedo)`
   replaced a hard-coded `(1 - 0.2)`. This is the requested fix.
2. **`Rnl` moved to absolute temperature**, `(tx^4 + tn^4 / 2)` →
   `((tx + 273.16)^4 + (tn + 273.16)^4) / 2`, which corrects two faults in one
   line: the Celsius fourth powers *and* a precedence slip that had been halving
   `tn^4` alone instead of averaging the pair.
3. `Rs` was rewritten from two statements into one, with no change to the
   arithmetic.
4. `et_factor` / `open_water` gives an open-water surface the potential rate.

Transcribing only (2) on top of our port reproduces the served `ET_PM` **to
within 5e-5 — the service's own rounding — on all five test days**, including the
four `tests/assistant/test_et_fao56.py` holds as a fixture. So the endpoint's ET
is now the checkout's ET again, and the offset is entirely that one line.

**The earlier reading of the direction, recorded here on 2026-08-24, was wrong
and is corrected.** It said more ET "rules out the Celsius `Rnl`, whose
correction would lower it". That is true in a textbook FAO-56 and false here, for
the reason the `Gsc` entry below already gives: the missing solar constant leaves
`Ra` 12.2× too large, which pins the cloudiness factor `1.35·(Rs/Rso) − 0.35` at
−0.2506 on this window — *negative*, so `Rnl` enters `Rn = Rs(1−α) − Rnl` as a
**gain** rather than a loss. Enlarging the Stefan-Boltzmann term ~6400× therefore
raises `Rn` and raises ET. The two bugs interact, and reasoning about either
alone gives the wrong sign.

**The port followed the R, and the correction is worth 22 % of ET0 and four
decisions.** At the site's direction `et_fao56.py` now computes `Rnl` the way
`3e7405a` does, and the endpoint and the port agree again to within the service's
rounding: the four fixture days re-captured live read 6.0318 / 0.5819 / 2.4564 /
8.1012 mm against a port giving 6.031849 / 0.581907 / 2.456362 / 8.101166, all
within 5e-5. The previous capture read 5.6745 / 0.3936 / 2.1606 / 7.6684.

Over the 2149 station days the band covers, ET0 rises everywhere: **median
1.223×**, range 1.067× to 5.682× — the tail being winter days where the old value
was near zero, so a large ratio is a small absolute change. The effect on the
decision the tool actually returns is much smaller than that, because the ladder's
top rung is a temperature test and its third is a threshold most days clear:
replaying every band day through both ET conventions with everything else held
fixed flips **4 of 921 roof-days (0.4 %)** — 3 on the irrigated extensive roof,
1 on the semi-intensive, 0 on the non-irrigated. Families E's T07 and T11 are
what move; family F does not, because the stated path takes no ET0 at all.
The decision-diff report was regenerated against the new ET and its headline
moves from 19 to **20 of 930 flips** (2.0 % → 2.2 %), still comparing the two unit
regimes against each other rather than against this change.
*Verified:* `run_roof` over the band at both ET conventions, same station rows,
same seeds, same thresholds; the four fixture days re-POSTed to the endpoint;
`scripts/irrigation_decision_diff.py` re-run. *Date:* 2026-08-24.

**Nothing in the repository pins the ET routine, and this change proves it
matters.** `rules_constants_version` covers the trigger levels and `roofs_version`
the roof identities, but the ET core is in neither, and no pin recomputes it — so
a change that moved every irrigation answer in the suite would have passed
`just pins` silently. It was caught here only because it was made deliberately.
The four irrigation templates are as pin-exposed as the model families were before
the GR2L canary existed; a `et_fao56_version` or a hash over the module would
close it. Not added here — the pin list is architecture §5's and adding to it is
a specification change — but recorded so the gap is a decision rather than an
oversight.
*Verified:* read from `eval/pins.json` and `scripts/check_pins.py`.
*Date:* 2026-08-24.

**Two of the four departures are fixed upstream and two survive**, so the R is
closer to FAO-56 without being it: `Gsc` is still defined and never used
(`Ra` 505.00 against eq. 21's 41.41), and `es` still divides by 238 where eq. 11
has 237.3 (−0.451 % on `es`), alongside the fixed `Pressure <- 100` kPa where
eq. 7 gives 99.63 at this site.
*Verified:* the upstream commit read from
`/home/shpilevo/work/ufz/weinbau-api-v1-internal/gr2l_model` (submodule
`3e7405a`, branch `feat/smart_irrigation_algo_func`); a transcription of the new
`Rnl` line over our port compared against five live `run_gr2l` responses.
*Date:* 2026-08-24.

**The GR2L canary moved with it, and was deliberately re-pinned.**
`0c39f945…` → `c8f51c82…`, with the canary's `ET_PM` going 2.0901 → 2.2879 and its
`ET` 1.045 → 1.144. The response is still a real model run rather than an error
body that returned 200: `ET = ET_PM · kg · Ssub/Ssubmax = 2.2879 × 1 × 8.0/16.0 =
1.14395`, to the digit, and `Ssub` is still the `theta_01` the probe sends. Two
consequences worth stating plainly. **`just pins` does not catch this** — the
canary is a `LIVE_ONLY_PIN`, reported as "captured live" and never re-fetched, so
the offline check said "0 moved" throughout; only `pins-canary` sees it. And
**the test suite does not catch it either**, by design: every oracle test drives a
stub, which is what makes them hermetic, so all 1089 passed against a service that
had changed underneath them. The canary is the only thing in the repository that
was ever going to notice.
*Verified:* `fetch_canary()` against the pinned base URL, twice; the arithmetic
checked by hand against the probe's own `theta_01`. *Date:* 2026-08-24.

**What the moved build invalidates, listed rather than cleaned up.** Eleven GR2L
responses in `eval/cache/` were recorded from the old build, and three committed
pilot cases (`T09-0001`…`0003` in `eval/cases/pilot.json`) carry the old canary in
their pins — so T107's pilot measurement of T09 was taken against a model that no
longer exists. Nothing here was deleted: the cache is T116's to own and the
committed cases are the record of a measurement that really was taken. A replay
of those three is still internally consistent (the cache holds the old build's
answers and never reaches the service), but its pins no longer match the
repository's, which is precisely the mismatch `expectations.pins` exists to make
visible. `check_pins.py --accept-moved` prints this list every time it re-pins.
*Verified:* the re-pin run's own output. *Date:* 2026-08-24.

**The list was acted on at capture, and the staleness was measured rather than
inferred.** T116 re-probed the service first: it still serves
`c8f51c82fe5f8602577831c84cc8ad7aa31ca5143cc57bcf5014c44491a0edf7`,
byte-identical to the pin, so the build has not moved a second time and
`--accept-moved` was neither needed nor used. The eleven entries were then
**discarded rather than merged**, and what justifies discarding them is a direct
comparison: two of the committed GR2L requests were re-issued verbatim against
the current service and **every day of both responses differs**. On the 28-day
February window the substrate store diverges from 0 mm on day 1 to 1.1 mm by day
28 (`Ssub` 9.024 → 7.9268), so the error compounds through the store and is not
a constant offset a reader could correct for. Day 1's `Ssub` is identical in
both, because it is the seed the request states, and only its `ET` moves — which
is the ET-routine diagnosis above, confirmed on real windows rather than on the
single-day canary. The eighteen Open-Meteo Archive entries were kept: ERA5
reanalysis has not moved, and the two populations separate cleanly on request
shape (`data[]` plus the roof parameters against a URL plus a query mapping),
with nothing ambiguous between them.
*Verified:* `fetch_canary()` against the pinned base URL; the requests of
`b6af72e3…` (3 days) and `dfff2a5a…` (28 days) re-issued through `_post_gr2l`
and compared row by row. *Date:* 2026-08-25.

**The stale canary lived in the cache as well as in the pin file, and that copy
is the one that bites.** `eval/cache/c24f571f…json` held the old build's canary
response (`ET = 1.045`), so `ResponseCache._verify_canary` would have compared a
live `1.144` against it and raised `CanaryMismatchError` at the first miss —
stopping the capture pass before it recorded anything. The gate is worth
understanding in both directions: `just pins` compares the *pin file*, and the
response cache compares its *own committed entry*, and only the second one runs
during a capture.
*Verified:* the removed `c24f571f…` entry's own bytes, against the live canary
response. *Date:* 2026-08-25.

**The suite's answers were never the old build's, and this was checked rather
than argued.** `expectations.pins.gr2l_canary` is read out of `eval/pins.json`
at generation time, so the stamp records which build the generator *believed* it
was using — it is not evidence about the response that was actually served, and
a generation run replaying a stale local cache would stamp the new hash over old
numbers. So `just cases-check` was run with `.generation-cache` **deleted**,
forcing every model answer to come live from the current build: the committed
`train`/`test_seen`/`test_unseen` files came back byte-identical. T116's capture
pass then re-answered all 281 cases one by one and found the committed answer
every time. Two independent confirmations, neither of which relies on the stamp.
*Verified:* `fetch_canary()`; two committed requests re-issued through
`_post_gr2l`; `scripts/generate_cases.py --check` from a cold cache;
`scripts/capture_cache.py`. *Date:* 2026-08-25.

**Discarding the eleven took part of the pilot's replay surface with it, and the
part is smaller than it first looks.** They were the prewarm
`scripts/prewarm_pilot_cache.py` recorded for `eval/cases/pilot.json` — the
script T116 has since retired — so the internal consistency noted above — "a
replay of those three is still internally consistent" — no longer holds.
Measured rather than assumed: **11 of the 14 pilot cases still replay, and 4
miss** — `T09-0001`…`0003`, whose windows were among the eleven, and
`T24a-0002`, the model overlay needing the 28-day February 2026 run. The three
`T07` cases replay, which is independent confirmation that the irrigation chain
never reaches GR2L; `T01`, `T06`, `T17a`, `T18a` and `T24a-0001` never touched
the cache at all.

**The pilot's gold answers did not move, which is a narrower exposure than a
stale pin suggests.** All three T09 answers were recomputed against the current
build and are **unchanged** — `True` at min 12.25 %θ against a threshold of 20,
`False` at 13.38 against 10, `False` at 8.95 against 5. The series underneath
moved, but these are booleans and no minimum sits near its threshold (margins
7.8, 3.4 and 4.0 pp), so the answer class survives the model change. What is
genuinely stale is the **provenance**: the three cases stamp
`gr2l_canary: 0c39f945…` where the repository pins `c8f51c82…`, and T107's 48
recorded rollouts had the *agent* calling the old build, so its own minima came
from it. Those rollout booleans very probably land the same way for the same
margin reason, but that is an inference and only a re-run would settle it —
which is why it is stated rather than claimed. T107's headline results do not
depend on GR2L in any case: T17a's 3/3 failure is a contract-encoding fault in
the root instruction, and the coverage and recall ratios are structural.

**The pilot is left as it stands, and this is the decision rather than a
deferral.** Nothing downstream reads the file: P8's search and measurement load
`train.json` as `train_data` and report on the two test splits, no P8 row
mentions the pilot, and the one test that reads it needs no cache because T24a's
oracle resolves scope without fetching. The pilot was P6's freeze gate, it
passed that gate, and the four defects it caught are fixed in frozen code. So
re-running it would re-measure a gate rather than measure anything, and
capturing its 4 missing entries without re-running would pair a current cache
with an old-canary stamp. Both were declined. What a later reader should take
from this row is that the 4 misses and the stale stamp are known and priced, not
an oversight to repair.
*Verified:* the eleven entries' request shapes against the prewarm script's
windows; all 14 pilot cases replayed against the committed cache with
`httpx.AsyncClient.send` blocked; the three T09 oracles re-answered live into a
scratch cache. *Date:* 2026-08-25.

**T22 materializes against the fixed build, and the override is monotone.** On
the non-irrigated extensive roof at `as_of` 2026-04-20, tomorrow's predicted soil
moisture is 15.46 / 17.39 / 18.44 %θ at albedo 0.05 / 0.6 / 0.9 against a default
of 0.2 — rising with albedo, which is the direction the physics requires: a
higher albedo reflects more energy away, so less of it drives evapotranspiration
and the roof stays wetter. The guard that refused every draw before the fix now
passes without any change to it, because it was written to compare the override
against the roof's own default rather than to assert a known-bad service. T26(i)
moves with it: its window minimum goes 13.99 → 16.38 %θ at albedo 0.6, so the
variant that was half inert now composes two live axes.
*Verified:* `t22_albedo_override` and `t26_composed_override` through
`make_case_context(as_of, allow_live=True)` against the live service.
*Date:* 2026-08-24.

**GR2L forgets a counterfactual seed, and how fast depends on the weather.**
T23's override only means something while the run still remembers it. Sweeping a
5 %θ against a 20 %θ seed on the non-irrigated extensive roof and reading the
window's last day: at `as_of` 2026-04-20, a wet week, the two agree from **d = 2**
onward (both 21.7 %θ, the store saturated), differing only at d = 1 by 5.83 pp;
over a dry August window they separate by 9.52 / 5.94 / 3.79 / 1.27 pp at
d = 1 / 2 / 3 / 5 and converge on the 1.29 %θ floor by **d = 7**; over a drier
October window they still differ by 5.14 pp at **d = 10**. Re-measured on the
post-fix build and the shape is unchanged — the same convergence days, magnitudes
a little smaller — which is what makes the per-draw probe the right mechanism
rather than a constant chosen from one sweep. So there is no safe
horizon to write into the template: the same *d* is informative in October and
vacuous in April. `t23_state_override` measures it per draw instead, running a
probe seed at whichever end of the roof's own `Ssubmin`/`Ssubmax` range is
further from the stated value and refusing the draw when the two land on the same
last day.
*Verified:* `modelled_run` with `initial_soil_moisture_pct` at both seeds, three
`as_of` days × six horizons, against the live service. *Date:* 2026-08-24.

**Family G's forcing reaches the model, checked on the answer rather than on the
request.** At `as_of` 2026-04-20 the non-irrigated extensive roof's three-day
baseline minimum is 11.84 %θ (T10). Forcing 50 mm onto 2026-04-21 moves the
series to 20.14 / 18.14 / 13.43 %θ and produces **48.1 mm of runoff on the forced
day**, so the minimum becomes 13.43 %θ — the model computed the counterfactual,
rather than the wrapper adjusting a baseline afterwards. T26's cross-roof variants
over the same 30 mm forcing separate the roofs cleanly: irrigated extensive ends
at 22.78 %θ against non-irrigated's 13.43, and semi-intensive at 25.63 against the
same 13.43. (Pre-fix the same three were 13.99, 23.39 and 25.94; the runoff figure
is unchanged, since it is set by the forcing and the store's capacity rather than
by ET.)
*Verified:* `t21_forced_rain_minimum` and `t26_composed_override` against the live
service through `make_case_context(..., allow_live=True)`, one event loop.
*Date:* 2026-08-24.

**The alias map and the scope table are not the same vocabulary, and three of
T27's own stated spellings fall in the gap.** `roofs.py`'s alias map is what
§1.6 means by "natural language the semantic layer's alias map covers";
`NON_MODELLABLE_ROOFS` is what both water-balance tools match a normalized
argument against. Posting every spelling of the two excluded roofs to
`predict_green_roof_water_balance_tool` and to `calc_irrigation`: twelve reach
`status='not_available'` from both, and three do not.

| Spelling | `resolve_roof` | in scope table | both tools return |
|---|---|---|---|
| `SD` | wetland | **no** | `error` / `invalid_argument` |
| `das Kiesdach` | **none** | no | `error` / `invalid_argument` |
| `the gravel roof` | **none** | no | `error` / `invalid_argument` |

Two distinct causes. `SD` is a wetland alias `roofs.py` carries and the scope
table omits. The other two are multi-word: `normalize_roof_type` strips and
lowercases and does nothing else, so no phrase containing an article or a noun
reaches either table. `das Kiesdach` was **named verbatim in T27's params line**
and `the gravel roof` is §1.6's own example of covered natural language.

Why it matters beyond tidiness: the tool docstring instructs the agent to "name
the roof the user actually asked about", so passing the question's own spelling
is the behaviour the prompt asks for — and it converts a scope limit into an
`invalid_argument`, which `decisions.md § Typed abstention` keeps apart precisely
because one is a fumble the candidate can correct and the other is not. Both
outcomes look like "did not answer", so the substitution is silent, and the
false-abstention rate is computed against the gold set that would carry it. T27's
pool is now the intersection, read off the two tables by the oracle rather than
listed. Over that pool the oracle and the tools agree on all **36** combinations
(12 spellings × 3 variants), with no fetch of any kind on any of them.
*Verified:* both tool factories over every alias of the two roofs at `as_of`
2026-04-20 through a replay-cache context, against
`t27_non_modellable_roof`. *Date:* 2026-08-24.

**T19 is the only GR2L window in the catalog that can reach the station.** It
follows from the forward-window finding above rather than being a separate
measurement: every other model template (T09, T10, T21, T22, T26) resolves a
window starting on the case's own day, which the as-of view has truncated, so all
of them fall to the Archive whole. T23's window is retrospective at its start but
runs *to* the case's day and therefore reaches the same truncated day, so it
falls too. That leaves T19's completed look-back as the one model template whose
pins can carry `station_derivation`, and it did in the check above.
*Verified:* the pin sets returned by the family D and G oracles at `as_of`
2026-04-20 12:00. *Date:* 2026-08-24.

**The three validity predicates separate the record's real faults from its real
health, on the record's own named windows.** Run as §1.6 states them — coverage
at ≥44 of 48 rows for a point query and ≥95 % with no gap over 24 h for a period,
per-column bounds off `roofs.py`, a 24-sample run test on state columns only —
against the windows the entries above name. **Rejected:** every day of the dead
`QWetland` stretch on bounds (2026-03-12 first, and 03-20 / 04-01 / 04-24
sampled); the 2025-05-22…28 episode frozen at 77.160 %θ on frozenness *and not*
on bounds, which is the whole reason that predicate exists; all eight sampled
days of the two whole-system outages; and all four bracket days at 36 / 8 / 27 /
25–27 rows. **Accepted:** the wetland's healthy January recharge, `QGravel`'s
honest near-zero July, all three substrate `swc` columns and all five `tsoil`
columns over August, four healthy days spread across the band, the spring-forward
Sunday at 46 rows, and `Sumpf2_Efflux`'s zero run — 4780 identical consecutive
rows inside the band — which is rejected by nothing. Zero false positives on
every healthy window tried.
*Verified:* `tests/eval/test_generation.py` (48 tests) against
`data/water.duckdb` through a replay-cache context at `as_of` 2026-04-24 23:00.
*Date:* 2026-08-24.

**The 44-row point floor is what the bracket days need, and 24 would not do.**
The four dates bracketing the outages carry 36, 8, 27 and 25–27 rows: a floor
anywhere from 37 to 48 rejects all four, and one at 24 admits three of them. The
four rows of slack below 48 are spent on the spring-forward Sunday, which is
missing exactly its 02:00 and 02:30 rows as an ingest artifact and is a usable
day. So the floor is insensitive across 37–44 and its lower edge is set by the
27-row day rather than chosen.
*Verified:* per-day row counts of `swc.QEx1` on the four bracket dates and of
`wetter` on 2026-03-29. *Date:* 2026-08-24.

**A seed anchored at `as_of` instead of at `min(window_start, as_of)` clears a
window that seeds from a hole.** Measured on the shape T19 and T23 draw: at
`as_of` 2025-12-01 a window opening 2025-11-10 seeds inside the 35-day lysimeter
outage and its seed day carries no row at all, while the `as_of` day itself is
healthy and passes. The two requirements name different days and only the earlier
one is rejected, so the anchor is load-bearing rather than a restatement of the
cut.
*Verified:* `seed_requirement` at both anchors through the 2025-12-01 as-of view.
*Date:* 2026-08-24.

**T04's wet class is 1 in 6.3 of the pool, and rejection sampling turns that into
50/50.** Over the band's 289 outflow days across P1f's four roofs, 184 of 1156
(roof, day) pairs carry a non-zero daily sum — gravel 74, irrigated extensive 49,
non-irrigated 44, wetland 17 — so the minority class needs ~6.3:1 oversampling,
which is §1.6's "roughly 6:1" measured rather than estimated. With the quota
capping each class at `ceil(m/2)`, train lands exactly 2/2 on 40 consecutive
seeds and test_seen 2/3, at a mean 3.8 draws per accepted case and a worst
observed 12.8.
*Verified:* per-(roof, day) sums over `outflow` for the prior, and 40 seeded runs
of the T04 draw loop for the balance. *Date:* 2026-08-24.

**Drawing `as_of` before the window skews a retrospective pool badly enough to
break balance.** The first draw loop picked a band day and then a parameter
window behind it, which weights each window by how many band days can still see
it — burying the late band under the early one. On T04 that thinned the wet class
from the record's 1-in-6.3 to **1 in 25**, and the loop spent 13.5 draws per
accepted case against 3.8 after the fix. Windows are therefore drawn first and
the cut sampled from the days that can see them, which leaves both uniform. The
forward families are unaffected and still draw `as_of` first, because their
window is defined relative to it.
*Verified:* the same T04 loop under both orders, comparing
`answered`-draw class counts. *Date:* 2026-08-24.

**A whole-catalog generation pass lands the stated sizes and the stated
abstention share, with the predicates doing visible work.** 273 of §1.7's 281
cases materialize — train 100/100, test_seen 125/125, test_unseen 48/56 — in 387
draws, a 70.5 % acceptance rate. Abstentions land at **13 / 100, 16 / 125 and
16 / 56**, i.e. 13.0 % / 12.8 % / 28.6 % and 16.0 % overall, exactly §1.6's
figures, with nothing in the generator steering toward them. All ten bool
templates balance: 2/2 in train, 2/3 or 3/2 in test_seen, 4/4 in the holdout. The
142 rejections split 71 balance, 59 coverage, 9 oracle, 2 frozenness, 1
plausibility. The last three are the interesting ones — they are not synthetic:
T24a drew the wetland's `swc` for March 2026 and was rejected on **both** bounds
and frozenness (the dead stretch), and for May 2025 on frozenness alone (the
77.160 %θ episode). The nine oracle refusals are T110's four expected shapes plus
T05's tie and T22's default-albedo guard: T05 2, T25 2, T20 2, T08 1, T22 1,
T23 1.
*Verified:* one `eval.generation.instantiate.generate()` pass over 31 templates
at seed 20260824, live GR2L and Archive, cache written to a scratch directory.
*Date:* 2026-08-24.

**T20 is the catalog's hardest template to fill, and the cause is its own
boundary guard rather than the data.** It took 38 rejections for 8 instances —
more than twice the next template — of which 36 are balance and 2 are the
oracle's refusal to answer a draw whose heatwave run continues past the window's
far edge. A forecast horizon of 3–7 days qualifies as a heatwave rarely enough
that the "yes" class is the minority, and the guard removes some of the draws
that would have supplied it. It fills at 4/4 all the same, so this is a cost
rather than a limit.
*Verified:* per-template rejection counts from the same pass. *Date:* 2026-08-24.

**T26's cross-roof variants could not be emitted, and the answer contract is why
it was the oracle that moved.** `_t26_cross_roof` answered **the winning roof's
canonical name**, and `eval/schema/case.schema.json` admits an `answer` that is a
boolean, a number, an ISO-day string (`^\d{4}-\d{2}-\d{2}$`) or null, so
`"semi_intensive"` matched none of the four and **T26(ii) and T26(iii) produced a
valid oracle answer that no case file could carry**. T111 recorded this as two
frozen surfaces disagreeing. It is not symmetric: the **root instruction states
the same three shapes to the candidate** — "`true` or `false` for a yes/no
question, a bare number for a quantity, `"YYYY-MM-DD"` for a date"
(`harness/contract.py`) — so a roof name was a shape the agent was never told it
could return. Two surfaces agreed and the oracle was the outlier, which is what
ruled out widening the schema: the answer vocabulary lives in
`EVALUATION_ROOT_INSTRUCTION`, the search's own starting point, so widening it
would make a gold answer's reachability a function of candidate text.
**Repaired at T114** — the comparison is answered over the ordered pair, `detail`
still records the winner, and the suite went 276 → 281
(`decisions.md § A comparison is answered as a boolean over an ordered pair`).
*Verified:* the oracle over all three variants against a forcing-aware stub,
asserted both ways round on one draw; a hand-built envelope validated against the
committed schema. *Date:* 2026-08-24, repaired 2026-08-25.

**The German paraphrase pool was spot-checked before splits were cut, and it
found two defects and no ambiguous reference.** The check covered all **136
German surfaces** — 38 question shapes × 2 registers per side, over 12 draws of
each shape, so every value pool and every roof the shape can draw appears in it.
Four classes were looked for. *Referential ambiguity*: none. The site has five
roofs and two of them are extensive, so "das Dach" and "das Extensivdach" name
nothing, and the plural "die beiden Extensivdächer" resolves only where the pair
*is* the subject (T03, T06, T24b) — every other German surface names its roof
through the declined label. *Grammar*: two defects, both found by reading rather
than by a rule, and both now checked mechanically. Family F's German opened on
the roof — "das unbewässerte Extensivdach liegt bei 8.0 %θ …" — a German sentence
beginning in lower case; and T24a's colloquial pair register wrote "von dem
Kiesdach" where German contracts to "vom Kiesdach". The first cannot be repaired
after rendering, because an English surface may legitimately open on
`non_irrigated_extensive`, so the fix is capitalized forms in `german_forms` and
a test that every German surface opens with a capital. *Underspecification*:
every parameter a shape draws appears in every surface of it, so no German
paraphrase asks a question its oracle answers a parameter of. *Raw column names*:
none, which is the load-bearing half for T27 and T24a(iii), whose `{alias}`
parameter is drawn from spellings half of which are database columns.
*Verified:* `tests/eval/test_paraphrases.py`, 24 tests over 6 816 renderings of
the 272 surfaces (3 408 of them German), plus a printed read-through of all 136
distinct German ones. *Date:* 2026-08-24.

**The catalog's own German example for T15a is a redenotation, and is not used.**
§2 T15a offers "Wie viel hat es letzte Woche geregnet?" as its DE example, while
the template's parameter is an explicit `{past_period}` window — so that sentence
names a different set of days on every reading. It is the backward twin of the
"nächste Woche" T15b's own note forbids and that `decisions.md § Forward horizons
are counted in days` rules out. The paraphrase pool states the window instead,
and `names_calendar_period` refuses any surface whose calendar-relative wording
differs from the canonical's — which keeps families F's "coming week", where the
week is a *stated value* rather than a sampled window. Two smaller catalog
examples are stale in the same way and were left as illustrations: T24a's DE
example names "die beiden Extensivdächer", a pair variant (i) can no longer draw
since its `swc` pair must contain the gravel roof or the wetland, and T27's drops
the `{d}` horizon the entry itself repaired in.
*Verified:* the rule applied to the catalog's example strings, in
`tests/eval/test_paraphrases.py`. *Date:* 2026-08-24.

**A whole-catalog pass under the split cut holds every size, every balance and
every disjointness — and the cut cost the sampler nothing.** 273 cases in 400
draws (68.3 % accepted, against 70.5 % uncut): train 100/100, test_seen 125/125,
test_unseen 48/48 with T26 held out of the pass, abstentions 13 / 16 / 16 and all
ten bool templates balanced (2/2 in train, 2/3 or 3/2 in test_seen, 4/4 in the
holdout). **`parameter_overlaps` is empty**: no template shares a value of any
sampled parameter between train and test_seen. Language lands 50/50, 63/62 and
24/24. The `as_of` evidence is three numbers per split and each separates a
stripe from a cut: **longest consecutive run 1** in all three, 11 calendar months
touched in all three, and spans of 2025-06-04 → 2026-04-24 (train), 2025-06-02 →
2026-04-19 (test_seen), 2025-06-09 → 2026-04-23 (test_unseen) — the whole band on
each side, from 56, 68 and 40 distinct days. Rejections split 77 balance, 49
coverage, 20 oracle, 1 frozenness.
*Verified:* one `eval.generation.instantiate.generate()` pass over 31 templates
at seed 20260824, striped pools, live GR2L and Archive, cache written to a
scratch directory. *Date:* 2026-08-24.

**A stripe taken over two different lists is not a stripe over the value, and the
disjointness report found it in T113's own output.** T24a draws its `{month}`
from `outflow`'s eleven complete months on the flux variant of its measured pair
and from `swc`'s twenty on the state one. Striping each list by position put
`2025-07` at index 2 of the first (even → train) and index 11 of the second
(odd → test_seen), so **one template's own parameter overlapped between the
splits** while every other parameter in the catalog was clean. The stripe is now
taken over the widest month list and narrowed to the table's own months
afterwards, which makes a month's side a property of the month rather than of
which template asked for it. The defect was invisible to the sampler — each pool
was internally disjoint — and visible only in the emitted parameters, which is
why `GenerationRun.report()` carries `parameter_overlaps` and why the exit
criterion is asserted over cases as well as over draws.
*Verified:* the overlap was reported by a full pass before the fix and is empty
after it; `tests/eval/test_splits.py` carries the regression. *Date:* 2026-08-24.

**T12's eleven qualifying events do cover 4 + 5 disjointly, as the catalog
claims.** The 15 candidate windows stripe 8 to train and 7 to test_seen; of the
11 that qualify, 6 land in train's stripe and 5 in test_seen's — exactly the
margin `t12_rain_events.md` predicts, with no event drawn by both splits and both
classes reachable on each side (2/2 and 2/3). This is the one cut that could have
failed on the data rather than on the code, since the event pool is the record's
and qualification is decided by the filter and the oracle rather than by the
sampler.
*Verified:* an offline generation pass over T12 in `tests/eval/test_splits.py`.
*Date:* 2026-08-24.

**The emitter is byte-stable across two runs, and the suite is the whole 281.**
`scripts/generate_cases.py` run twice into two directories produced
byte-identical files — `train.json` `d66cc411…`, `test_seen.json` `a0d0a016…`,
`test_unseen.json` `a4c67c89…` on both passes — and `--check` against the
committed files exits 0. All three splits are exactly `templates × m`: 100 / 125
/ 56, **zero shortfalls**, 413 draws (68.0 % accepted). `parameter_overlaps` is
empty, `as_of` is inside each split's own stripe on every case, abstentions land
13 / 16 / 16, and the languages land 50/50, 63/62 and 28/28. The three files load
through MLflow's own `_convert_eval_set_to_df` and `validate_train_data` with
columns `['expectations', 'inputs']`, so the search and the measurement run pass
them straight in as `train_data`.
*Verified:* two full passes at seed 20260824 against the live GR2L and Archive,
diffed and hashed; `tests/eval/test_emit.py`. *Date:* 2026-08-24, re-taken
2026-08-25 after T26's repair.

**Repairing T26 moved the holdout file and nothing else, which is what a
holdout-only template should do.** `train.json` and `test_seen.json` kept their
hashes across the change — `d66cc411…` and `a0d0a016…` before and after — and
only `test_unseen.json` moved, `6a725e71…` → `a4c67c89…`. Worth recording
because it is the emitter's own claim under test: a change confined to one
template appears as a change to one file, so a diff in `eval/cases/` localizes
what moved instead of being a whole-suite rewrite.
*Verified:* `sha256sum` before and after. *Date:* 2026-08-25.

**T26's three variants were three labels over two probes, and the albedo is what
now separates them.** `_sample_t26` gave the `albedo` override to variant (i)
alone, so (ii) "override plus cross-roof comparison" and (iii) "the same
comparison with no `albedo` anywhere" drew identical parameters, took the same
code path in `_t26_cross_roof`, carried the same gold set and — after T112 —
rendered from the same sketch. Nothing distinguished them but the string in
`params["variant"]`, which made (iii)'s stated purpose vacuous: it exists to
contrast with a double-transfer variant and had none to contrast with. (ii) now
carries the override across the pair, applied to **both** runs so the roof stays
the only difference, and the three variants are three shapes with three sketches.
The emitted holdout carries 3 / 3 / 2 with 6 of the 8 carrying an albedo, and
T26 balances 4/4 — it is a balanced template as of this repair, since every
variant answers a boolean.
*Verified:* draws over all three variants asserted to differ in `a` and in shape;
the emitted `test_unseen.json`. *Date:* 2026-08-25.

## External sources on this machine

Paths outside this repository, recorded here rather than in the plan because a
checkout location is an environment fact, not a repo-relative reference. Both are
inputs to P3 and to nothing else.

**The weinbau API, carrying GR2L's R implementation, is checked out at
`/home/shpilevo/work/ufz/weinbau-api-v1-internal`.** `gr2l_model/R/GR2L_function.R`
(129 lines) holds the FAO Penman-Monteith ET routine that `et_fao56.py` is a
verbatim port of, including the fixed `Pressure <- 100` kPa simplification at
`:44`, and `run_GR2L` itself. `gr2l_model/api/plumber.R` is the endpoint that
serves `POST /predict_gr2l`, the service the response cache's canary is pinned
against.
*Verified:* read from the checkout. *Date:* 2026-08-20.

**The R's ET routine departs from FAO-56 in four places, and returns 1.17× to
1.38× a textbook FAO-56 because of it.** Besides the fixed `Pressure <- 100` kPa
at `:44`, `GR2L_function.R` defines the solar constant `Gsc = 0.0820` at `:59`
and never uses it, so `R_a` at `:65` comes out 1/0.082 ≈ 12× too large; `es` at
`:36` divides by `238 + tm` where FAO-56 eq. 11 has 237.3, though `Delta` at
`:50` does use 237.3; and `Rnl` at `:67` takes `tx^4 + tn^4 / 2` in **degrees
Celsius**, where eq. 39 averages both fourth powers in kelvin. The `Gsc`
omission dominates: an inflated `R_a` inflates `Rso`, which pins the cloudiness
factor `1.35·(Rs/Rso) − 0.35` near its −0.35 floor, so net longwave becomes a
small constant *gain* instead of a loss that tracks cloud cover. Over four days
spread across the year at Leipzig's geometry and albedo 0.23, ET0 comes out
5.522 / 0.388 / 2.107 / 7.450 mm against a textbook FAO-56's 4.737 / 0.301 /
1.523 / 6.008 — ratios of 1.17, 1.29, 1.38, 1.24. The fixed pressure is the
smallest of the four by far: `gamma` runs 0.37 % high and ET0 at most 0.10 %.
None of this is corrected in `et_fao56.py`, deliberately: the site's irrigation
thresholds were tuned against this convention's ET, and the port exists so the
two languages agree rather than each being right on its own terms.
*Verified:* `et_fao56.py` against a second transcription of the R and against a
textbook FAO-56 written for the comparison; pressure sensitivity by re-running
the same days with eq. 7's 99.63 kPa. *Date:* 2026-08-20.

**The deployed endpoint's ET routine matched the checked-out one until
2026-08-24, when the albedo fix moved it. SUPERSEDED — the four served values
below are still the fixture `tests/assistant/test_et_fao56.py` holds the port to,
and they are still what that build served; they are no longer what the endpoint
serves.** The
checkout's `run_GR2L` takes no `albedo` argument and the service does, so the
running build is newer than `/home/shpilevo/work/ufz/weinbau-api-v1-internal`.
Posting four days to `POST /predict_gr2l` at `albedo=0.2` — the value the
checkout hard-codes at `:68` — returns `ET_PM` of 5.6745 / 0.3936 / 2.1606 /
7.6684, which `et_fao56.py` reproduces to within 4e-5 mm, i.e. to the four
decimals the response rounds to. Whatever else moved between the two builds, the
ET routine did not. Those four rows and their served values are committed as a
fixture in `tests/assistant/test_et_fao56.py`.
*Verified:* one live POST to the endpoint in `.env:56`, the same one the GR2L
canary is pinned against. *Date:* 2026-08-20.
**Re-checked 2026-08-24 after the albedo fix: the equality no longer holds**, by
0.19–0.45 mm/day depending on the day (see the two albedo entries above). The
endpoint and our port have diverged **because the R moved and the port did
not** — `3e7405a` corrected the `Rnl` line — so what this entry now records is a
port that is faithful to the *previous* checkout. The four served values above
are still exactly what our port produces, which is why the fixture test still
passes; they are simply no longer what the endpoint serves.

**Nothing measured has moved on the irrigation side, and that is a fact about
scope rather than luck.** `et_fao56.py` is reached only by `calc_irrigation`,
which runs it locally, so families E and F answer exactly as before and the
decision-diff harness's baseline is untouched. GR2L computes its own ET
server-side and never calls the port, so the families that moved (D, G, and H's
model overlay) moved through the service alone. The open question this leaves is
not a defect but a choice — whether the port follows the R to the corrected
`Rnl` — and it is recorded as such in
`decisions.md § No fitted correction between the instrument and the oracle`,
because re-porting would move four templates' gold answers and the site's trigger
thresholds were tuned against the old convention's ET.

**`GR2L_function.R` ends in a top-level demo block, lines 112–129, and
`plumber.R:11` sources the file** — so building a random 365-day data frame,
running `run_GR2L` over it and printing the head happens on **every container
start**. (An earlier note placed the block at `:124-141`; that range is wrong for
the file as it stands.) Nothing in this repository depends on it, and the block is
outside the request path, so it costs startup time rather than correctness.
*Verified:* read from the checkout. *Date:* 2026-08-20.

**The deployed irrigation controller is at
`/home/shpilevo/Downloads/smart_irrigation.py`**, an extraction of the site's own
`temp/smart_irrigation.py` — the source the bucket model, the reason-code ladder
and the trigger constants are ported from, and the reference the faithfulness
golden test replays against before the unit fix. `irrigation_tool.md` already
names this path in its header.
*Verified:* file present at that path. *Date:* 2026-08-20.

## Landed fixes

**Weather window resolution fix.** Before this fix, Open-Meteo defaulted
`forecast_days` to 7, so a bare `past_days=5` request returned twelve days
total (five past days, today, and six forecast days), and `DailyWeatherRow`
carried no observed/forecast marker — so history questions were sometimes
answered partly from predictions, and GR2L simulated forecast days as if they
were observations. The fix maps both window forms (`past_days`/
`forecast_days`) to one absolute `(start, end)` pair before any client call;
`fetch_daily_weather` now takes `start_date`/`end_date` as required
arguments, with its `past_days`/`forecast_days` parameters removed so the bug
cannot be reintroduced from a caller. Backend routing was left unchanged by
this fix.
*Verified:* bug reproduced and fix landed against the live Open-Meteo API;
tests added at `tests/assistant/test_weather_window.py`.
*Date:* 2026-07-29.
