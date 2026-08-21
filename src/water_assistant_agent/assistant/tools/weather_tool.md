# Weather Tool — the site's station, and Open-Meteo's Archive

> Spec for the weather source, in **two layers** — read the one you need:
>
> 1. **[Agent-facing tool](#agent-facing-tool)** — `get_weather_forecast_tool`.
>    Answers questions about **the weather itself**. Takes a date window and
>    nothing else.
> 2. **[The two sources](#the-two-sources)** — `weather_client.py`. The
>    composite that decides which source answers, the
>    [station derivation](#station-source) over the `wetter` table, and
>    `fetch_daily_weather`, which builds the Open-Meteo query, transposes the
>    response and applies the two unit conversions. It is the layer that knows
>    about coordinates and Open-Meteo's parameter names.
>
> **The agent never chains this tool into [GR2L](./gr2l_tool.md).** GR2L's tool
> calls the same layer-2 client itself, for exactly the window it is simulating —
> that is what makes it self-contained. Nothing below is a step towards a roof
> simulation, and no part of layer 2 belongs in an agent-facing docstring.
>
> Open-Meteo is a free HTTP API, **no API key required**.

## What it does

Returns **daily** weather for a single lat/lon point — past (observed/reanalysis)
and/or future (forecast) — with exactly the variables GR2L needs: mean/max/min
temperature, mean relative humidity, precipitation sum, mean wind speed, and
shortwave-radiation sum.

Two sources, same shape of response, and **no argument selects them**:

| Source      | Where it reads                                  | Serves                          |
| ----------- | ----------------------------------------------- | ------------------------------- |
| **Station** | `wetter` in `data/water.duckdb`, through the caller's as-of view | any window the record covers **entirely** — real instruments at the roofs' own height, no network, no cache entry |
| **Archive** | `https://archive-api.open-meteo.com/v1/archive`  | everything else: deep history, partial station coverage, and every window reaching past the present |

The rule is **whole-window or nothing** (`CompositeWeatherClient`): a window the
station covers only partly falls to the Archive *whole* rather than being
stitched together, so every window carries exactly one provenance and the
station's measured biases stay one property of one source. The result reports
which under `source`.

Open-Meteo's **Forecast** backend is retired and no window routes to it — see
[Why the Forecast backend is gone](#why-the-forecast-backend-is-gone).

Because GR2L runs at **daily** resolution (see its spec), this tool is queried at
daily resolution — one coordinate for the whole facility, since every roof
segment shares the same building and therefore the same weather.

## 📍 The location is not a parameter

All roof segments sit on one building, so **location is a property of the
deployment, not an argument**. Neither the agent-facing tool nor its callers pass
coordinates: `site.py` holds them, and the layers below import them.

| Constant | Value | Used for |
| -------- | ----- | -------- |
| `SITE_LATITUDE`    | `51.353484` | Open-Meteo `latitude`, GR2L `lat` |
| `SITE_LONGITUDE`   | `12.432152` | Open-Meteo `longitude`, GR2L `long` |
| `SITE_ELEVATION_M` | `142.0` m   | GR2L `hoehe_nn` |

The **surveyed 142 m overrides the `elevation` Open-Meteo returns**: that value is
the height of the API's ~1 km model grid cell, which in a city can be off by tens
of metres. Never read `hoehe_nn` off a weather response — the tool replaces the
response's `elevation` with the site value before returning (`latitude` and
`longitude` likewise echo the site, not the grid cell), and GR2L's own request
takes all three straight from `site.py`.

`fetch_daily_weather` is the one function that still takes a coordinate, so the
client stays reusable at another site. To move the deployment, change `site.py`.

## Agent-facing tool

> Layer 1 — what the LLM sees. Implemented in `weather.py`.

```python
async def get_weather_forecast_tool(
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
) -> dict
```

| Argument | Required | Meaning |
| -------- | -------- | ------- |
| `start_date` / `end_date` | one of the two pairs | Explicit window, `YYYY-MM-DD`. Both dates, or neither |
| `past_days` / `forecast_days` | one of the two pairs | Relative window, whole days ≥ 0. **Neither carries an upper bound as an argument** — see below |

The two forms are exclusive — passing one of each is an argument error. A relative
window is resolved to absolute dates **before** the client is called (see
[Relative windows are resolved first](#relative-windows-are-resolved-first)).

**How far a window may reach is not an argument fault.** Nothing bounds
`past_days`: the station is bounded by the record it covers and the reanalysis
reaches back decades, so a window that runs off the front simply resolves to the
source that holds it. Forward, the 16-day horizon is checked against `ctx.as_of`
*after* resolution and reported as `not_available` — a scope limit the agent
should pass on, never an argument to correct and retry
(`decisions.md` § Typed abstention).

Not arguments, deliberately: **latitude/longitude/elevation** (pinned in
`site.py`) and the **source** (resolved in code from the window, see above).

Returns `status='success'` with the site's `latitude`/`longitude`/`elevation`,
the `timezone`, the `source` that answered (`station` / `archive`), and `data` —
one **already transposed and unit-converted** daily row per day, in GR2L's row
shape. A window longer than 31 days comes back `truncated: true` with an empty
`data`, replaced by a `summary` over the whole window and `weekly` aggregates:
the cap is applied at layer 1, on what the model reads back, and never in the
client, since GR2L consumes the same rows in full (architecture §3.3).

Two non-success outcomes, and the difference is load-bearing:
`status='not_available'` with a `reason` for the forward horizon, and
`status='error'` with `error_details` **and an `error_type`** —
`invalid_argument` for a malformed window (correctable, retryable),
`upstream` for a fetch that failed or a cache miss replay cannot fill (report it;
the harness excludes these and scores the `invalid_argument` ones,
architecture §7).

### When the agent should call it

Call it when the user asks about **the weather itself** — "will it rain this
week?", "how warm was last May?". Windows resolve automatically, and so does the
source:

- **Recent past** ("last 30 days") → the **station**, whenever the record covers
  every day of it. This is the common case inside the `as_of` band, and an
  answer should say the numbers came from the site's own instruments.
- **Historical** ("summer 2023") → **Archive**, which reaches back decades where
  the station record starts 2025-01-01.
- **Forward-looking** ("next week") → **Archive**, and the as-of view is why.
  The station's `wetter` view is bounded at the case's `as_of` and has nothing
  past it, so a forward window always fails the whole-window test. Archive then
  answers it as **reanalysis**: a case's `as_of` sits in the real past, so
  "tomorrow" for the case is a day that has already happened, and Archive holds
  it. Beyond **16 days** past `as_of` the tool stops rather than serving more of
  the same — nothing upstream refuses those days, which is the whole reason the
  limit lives in this code (`FORECAST_HORIZON_DAYS`).

### Relative windows are resolved first

`weather_client.resolve_window` turns any relative window into an absolute
`(start, end)` pair in the wrapper, before the client runs. This is not tidiness —
Open-Meteo defaults `forecast_days` to **7**, so a bare `past_days=5` returns
**twelve** days: the five past ones, today, and six forecast days. Nothing in a
transposed `DailyWeatherRow` marks which is which, so "how much rain fell last
week?" silently answered with a week of predictions mixed in, and GR2L simulated
them as observations.

| Arguments | Resolved window |
| --------- | --------------- |
| `past_days=P` | `today − P` … `today − 1` — P complete past days, **no forecast tail** |
| `forecast_days=F` | `today` … `today + F − 1` |
| both | `today − P` … `today + F − 1` |
| neither | `today` … `today + 6` (Open-Meteo's old default, now explicit) |

`today` is **`ctx.as_of`'s date, read per call** — never this module's wall
clock — so a case frozen at its `as_of` and production's advancing site clock
take the same path (architecture §4). `resolve_window` requires it as an
argument and has no fallback: a caller who forgets it fails there rather than
silently resolving a relative window against the host's real date.

Malformed windows (both forms at once, a half-given explicit window, an
unparseable date, a **negative** count, `start > end`, or counts selecting no
days) raise `InvalidWindowError` and become an `invalid_argument` error result
**before** any fetch, with a message naming the argument to fix. A count that is
merely *large* is not malformed — see the horizon note under
[the tool](#agent-facing-tool).

Do **not** call it as a step towards a green-roof simulation. The roof tool
fetches the weather for its own window; calling this one first is redundant,
floods the context with daily arrays, and invites re-doing conversions that have
already been applied. See [GR2L § Who fetches the weather](./gr2l_tool.md#who-fetches-the-weather).

## The two sources

> Layer 2 — `weather_client.py`. Its callers are the tool wrappers, never the
> agent. `CompositeWeatherClient` decides which source answers (whole-window or
> nothing, see [What it does](#what-it-does)); the station half is specified
> under [Station source](#station-source) and the Open-Meteo half below.

### Open-Meteo client

> `weather_client.fetch_daily_weather`. Everything below describes the upstream
> HTTP API.

#### Request (GET, query params)

| Param              | Required | Value sent                                                                     |
| ------------------ | -------- | ------------------------------------------------------------------------------ |
| `latitude`         | yes      | `SITE_LATITUDE` — passed by the caller, not chosen here                         |
| `longitude`        | yes      | `SITE_LONGITUDE` — likewise                                                     |
| `daily`            | yes      | `temperature_2m_mean,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,precipitation_sum,wind_speed_10m_mean,shortwave_radiation_sum` |
| `timezone`         | yes\*    | `auto` (resolves to `Europe/Berlin` at this site). Required whenever `daily` is used |
| `wind_speed_unit`  | yes      | **`kmh`** — see wind note below (this is also the Open-Meteo default)           |
| `start_date`/`end_date` | yes | `YYYY-MM-DD` window, always sent — the client takes no relative form           |

`forecast_days` / `past_days` are **never sent**. They are agent-facing arguments
only; `resolve_window` converts them upstream, so `fetch_daily_weather` cannot
inherit Open-Meteo's implicit seven-day forecast tail.

`temperature_unit` defaults to `celsius` (what GR2L wants) — leave unset.

#### Response (JSON)

The **upstream** shape. Neither tool returns this to the agent — the client
transposes and converts it first.

```jsonc
{
  "latitude": 52.52,
  "longitude": 13.42,
  "timezone": "Europe/Berlin",
  "elevation": 38.0,                       // grid-cell height — DISCARDED, see above
  "daily_units": { "shortwave_radiation_sum": "MJ/m²", "wind_speed_10m_mean": "km/h", ... },
  "daily": {
    "time":                    ["2026-07-06", "2026-07-07", "2026-07-08"],
    "temperature_2m_mean":     [17.2, 18.5, 17.1],
    "temperature_2m_max":      [19.6, 20.0, 22.2],
    "temperature_2m_min":      [13.8, 17.1, 11.7],
    "relative_humidity_2m_mean":[76,  78,   61],
    "precipitation_sum":       [3.20, 3.40, 0.00],
    "wind_speed_10m_mean":     [ ... km/h ... ],
    "shortwave_radiation_sum": [6.68, 5.62, 25.77]   // MJ/m²
  }
}
```

The `daily` object is **column-oriented** (parallel arrays, index-aligned to
`time`). `weather_client._transpose` turns it into the **row-oriented** shape
GR2L's `data[]` uses — one row per day — so both tools already return rows and
the agent never transposes anything.

## Mapping Open-Meteo → GR2L

Applied inside the client. One row per index `i` of the `daily` arrays:

| GR2L field | From Open-Meteo daily variable | Conversion | Unit into GR2L |
| ---------- | ------------------------------ | ---------- | -------------- |
| `Date`     | `time[i]`                      | as-is                 | date string |
| `tm`       | `temperature_2m_mean[i]`       | as-is                 | °C |
| `tx`       | `temperature_2m_max[i]`        | as-is                 | °C |
| `tn`       | `temperature_2m_min[i]`        | as-is                 | °C |
| `rf`       | `relative_humidity_2m_mean[i]` | as-is                 | % |
| `precip`   | `precipitation_sum[i]`         | as-is                 | mm |
| `w`        | `wind_speed_10m_mean[i]`       | request in **km/h**   | km/h — see note ⚠️ |
| `gs`       | `shortwave_radiation_sum[i]`   | **× 100**             | J/cm²/day — see note ⚠️ |

Site geometry comes from `site.py`, not from the response: `SITE_ELEVATION_M` →
GR2L `hoehe_nn`, `SITE_LATITUDE`/`SITE_LONGITUDE` → GR2L `lat`/`long`. (The
response's own `elevation` is the grid-cell height — discarded, see
"[The location is not a parameter](#-the-location-is-not-a-parameter)".)

### ⚠️ Two unit conversions that are easy to get wrong

Five of the seven fields map **1:1** with Open-Meteo (`tm`, `tx`, `tn` in °C;
`rf` in %; `precip` in mm) — no conversion. Only **wind** and **radiation**
differ. Both are traced through GR2L's actual math in
`gr2l_model/R/GR2L_function.R` (line numbers below); the gateway's Pydantic
schema documents the same two conversions in its field descriptions
(`api_gateway/prediction_models/gr2l.py:13-14`), so schema and core agree:

1. **Wind (`w`) → request in `kmh` (no arithmetic).**
   R line 42: `df$u2 <- df[[w]] / 3.6`. The factor 3.6 is exactly the km/h→m/s
   conversion, so GR2L expects `w` in **km/h**. → Request Open-Meteo wind as
   `wind_speed_unit=kmh` and pass it straight through.
   *(Do **not** request `ms`; GR2L would divide an m/s value by 3.6 and under-read
   wind by 3.6×. The same trap sits on the `wetter` table's `windspeed` column,
   documented in m/s — it would need ×3.6 before reaching GR2L. Nothing feeds it
   there today: `meteo_source="db"` is dropped — see `decisions.md` § Weather sources.)*
   Physical caveat (not a unit error): Open-Meteo wind is at **10 m**; FAO
   Penman-Monteith assumes **2 m** and GR2L applies no height correction — a small
   positive bias in ET.

2. **Radiation (`gs`) → multiply by 100.**
   R line 56: `df$Rs <- df[[gs]] / 8.64 / 1e+06 * 86400`. The first factor is
   J/cm²/day → W/m² (`1 J/cm²/day = 1/8.64 W/m²`), the rest is W/m² → MJ/m²/day;
   net `Rs = gs × 0.01` MJ/m²/day. So GR2L expects `gs` in **J/cm²/day**.
   Open-Meteo's `shortwave_radiation_sum` is **MJ/m²/day**, and
   `1 MJ/m² = 100 J/cm²`. → **`gs = shortwave_radiation_sum × 100`.**

Both conversions are **verified against the R source arithmetic** (deterministic,
not a guess). Historical note, because earlier revisions of this file said the
opposite: the gateway schema and the R comments once labelled these fields "m/s"
and "W/m²", contradicting the core. Both were corrected upstream and now name the
conversion explicitly. The arithmetic never changed — only the labels caught up.
Still worth a one-off ET sanity-check on first integration to catch any surprise
in the model service wrapping the R code.

## Verified: I tried it out ✅

Live call (2026-07-06), Berlin, `wind_speed_unit=ms` to inspect units. It is
against the **Forecast** endpoint because that is what this deployment used at
the time; it is kept as the record of where the unit facts below were measured,
not as a description of current routing (see
[Why the Forecast backend is gone](#why-the-forecast-backend-is-gone)). Both
endpoints return the same `daily` shape and the same `daily_units`.

```bash
curl "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41\
&daily=temperature_2m_mean,temperature_2m_max,temperature_2m_min,\
relative_humidity_2m_mean,precipitation_sum,wind_speed_10m_mean,\
shortwave_radiation_sum&wind_speed_unit=ms&timezone=Europe/Berlin&forecast_days=3"
```

Real response confirmed:

- All seven daily variables returned (incl. `relative_humidity_2m_mean` and
  `wind_speed_10m_mean`, which the public docs page omits from its list).
- `daily_units`: temps `°C`, RH `%`, precip `mm`, radiation `MJ/m²`, wind `m/s`
  when `wind_speed_unit=ms` (→ use `kmh` for GR2L, per note above).
- Response carried `elevation: 38.0` m and echoed the resolved timezone.

## Notes & limits

- **Timezone is mandatory** with `daily` — daily aggregation is timezone-dependent.
- **The forward horizon is this code's, not Open-Meteo's.** Archive answers a day
  400 past a case's `as_of` without complaint — from its side that day is still
  the real past — so `FORECAST_HORIZON_DAYS` is what stops a future-facing window
  being served as though it were observed. Nothing bounds the backward side.
- **One point per call**, and one point is all this deployment needs: every roof
  segment is on the same building, so one fetch serves them all. (Open-Meteo does
  accept comma-separated coordinates for multi-site use.)
- **Free tier:** no key, but fair-use rate limits apply; responses are cached per
  request (architecture §5), and a station window never reaches the cache at all.
- Source docs: <https://open-meteo.com/en/docs>

## Why the Forecast backend is gone

`https://api.open-meteo.com/v1/forecast` was the second Open-Meteo backend and is
**retired, not merely unused** — `ARCHIVE_URL` is the only endpoint in
`weather_client.py`. Three measurements, and each on its own would have been
survivable:

- **Its past reach is 64 days, not the documented 92.** Measured at the site on
  2026-07-29: all seven GR2L variables present back to `today − 64`, null from
  `today − 65`; `past_days=92` returns 93 rows of which **28 are entirely null**.
  A 65–92-day window was accepted by the API, routed to Forecast, and then failed
  in `_transpose` with "returned no data" — an `upstream` error on a window
  Archive covers completely.
- **It refuses a window as old as a case's `as_of` outright.** The `as_of` band
  sits further back than its reach, so the endpoint most cases would have wanted
  it for is the one it declines.
- **The two backends disagree on the same past day.** Forecast serves model
  output, Archive reanalysis. At the site, 2026-06-21 precipitation: **0.00 mm**
  (Forecast) vs **2.50 mm** (Archive); 06-20 mean temp 24.4 vs 26.0 °C. For a
  water balance that is not rounding noise — which source serves "the recent
  past" is an accuracy decision, not routing.

With the station serving every window the record covers, there was nothing left
for it to answer that Archive cannot (`findings.md` § Weather source
measurements, `decisions.md` § Weather sources).

## Station source

Windows the site's own record covers **entirely** are served from the `wetter`
table in `data/water.duckdb` (record span **2025-01-01 → 2026-04-27**) instead
of Open-Meteo: real instruments at the roofs' own height instead of a ~31 km
ERA5 cell, no network, no response cache. Partial coverage falls through to the
Archive whole, so every window keeps exactly one provenance.

Implemented in `weather_station.py` as `StationWeatherSource`, which reads
through **the caller's executor and nothing else** — a case passes `ctx.db`,
whose `wetter` view is bounded at that case's `as_of`. Coverage is therefore
tested *through the as-of view*, and a window reaching past the cut can never
resolve here: the leakage closure is structural rather than a check someone has
to remember. Nothing in the class reads a clock, a setting or a connection of
its own.

### Derivation: half-hourly `wetter` rows → `DailyWeatherRow`

`wetter` is half-hourly and carries six of the seven fields. The derivation is
fixed in code and part of the pinned surface (architecture §5) — changing it
moves every oracle, and `water.duckdb`'s hash cannot see it move, which is why
it is hashed separately as `station_derivation`:

| Field | Derivation from `wetter` |
|---|---|
| `tm` | `avg(Tmean)` |
| `tx` | `max(Tmax)` |
| `tn` | `min(2·Tmean − Tmax)` — **estimated**, there is no `Tmin` column |
| `rf` | `avg(RH)` |
| `precip` | `sum(Rain)` |
| `w` | `avg(windspeed) × 3.6` — m/s → km/h, excluding the `−7999` sentinel |
| `gs` | `sum(Rad_SW) × 1800 / 10⁴` — W/m² half-hourly → J/cm²/day |

**The sentinel filter is a sign test, not an equality test.** `findings.md` names
`−7999` and 69 rows carry it exactly — but the half-hourly values are themselves
means of finer samples, so a half-hour mixing sentinel and real readings lands
anywhere between (`−7954.5`, `−3365.9`, `−54.99`). Filtering on equality would
leave those in, and one `−3365.9` among 48 samples puts a day's mean wind near
`−70` m/s. Wind speed cannot be negative, so the sign is the filter and it cannot
discard a real reading.

**A day is served only if it is complete**, and completeness is exactly two
conditions: **48 half-hourly rows**, and at least one non-sentinel wind sample.
Days that are not complete are simply **absent** from the result — never
partially derived — so the composite's coverage test is just "did I get a row per
day asked for". The record holds 479 of its 481 whole days complete; both
shortfalls are spring-forward Sundays (2025-03-30 and 2026-03-29, the latter
inside the catalog's `as_of` band). Grouped in local days the rule also drops the
fall-back Sunday, which carries 50 half-hours: a 25-hour day is as far from the
count as a 23-hour one, and the alternative is a DST-aware expected count nothing
in the specification asks for. Either way the day is **not served, not silently
patched** — the window falls to the Archive whole.

Timestamps are **UTC with no DST** (verified: the radiation-weighted solar noon
sits at the same hour in winter and summer, matching Leipzig's 11:10 UTC).
Aggregation therefore converts to **Europe/Berlin before grouping**, or the day
boundary is off by one to two hours and rain migrates between days — which
would also desynchronise the series from the `outflow` and `swc` comparisons
the model is validated against (T19).

### Measured data-quality limits — served uncorrected

Three properties of the station record are scope limits, stated in the thesis
rather than corrected (`decisions.md` § Weather sources): no calibration factor, no per-day source
switching, no gap filling — the source serves what the instrument recorded.

- **`tn` is an estimate.** Reconstructed from the coldest interval's mean and
  max. The ambiguity against the naive `min(Tmean)` is **0.25 °C mean /
  0.48 °C p95**, well inside FAO-56's tolerance for the vapour-pressure term.
- **`gs` reads ~26 % below ERA5** (ratio 0.738, r = 0.93 — a gain offset, not
  noise, near-constant across all clearness quintiles). The four independent
  per-segment pyranometers in the `radiation` table agree with each other
  *and* with ERA5 (ratio 0.96–0.97), so `wetter.Rad_SW` is the outlier.
  Consequence: radiation drives `ET_PM`, so station forcing **suppresses ET
  and biases modelled retention upward** relative to an ERA5-forced run.
  `radiation` covers only 2025-03-01 → 2025-10-01 and so cannot serve as the
  general source.
- **`precip` undercatches** — 554.6 mm station vs 766.6 mm ERA5 over 479 days
  (423 vs 585 mm/yr against a Leipzig climatology of ~520–560). It decomposes
  into gauge undercatch on days both call wet (ratio 0.85), near-total loss of
  frozen precipitation (ratio 0.51 on ERA5 snowfall days; two days read
  exactly 0.00 against 6.9 / 9.2 mm at −2 °C), and 54 ERA5-only wet days
  (153 mm) that look like ERA5's known spurious drizzle. Neither source is
  ground truth; the station is chosen for **consistency with the lysimeters**,
  which sat under that gauge — not for accuracy (`decisions.md` § Weather sources).
