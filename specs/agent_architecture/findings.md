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

**The deployed endpoint's ET routine still matches the checked-out one.** The
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
