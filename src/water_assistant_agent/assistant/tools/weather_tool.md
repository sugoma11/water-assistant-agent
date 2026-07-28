# Weather Tool — Open-Meteo Daily Forecast / Archive

> Spec for the weather source, in **two layers** — read the one you need:
>
> 1. **[Agent-facing tool](#agent-facing-tool)** — `get_weather_forecast_tool`.
>    Answers questions about **the weather itself**. Takes a date window and
>    nothing else.
> 2. **[Open-Meteo client](#open-meteo-client)** — `fetch_daily_weather` in
>    `weather_client.py`. Builds the HTTP query, transposes the response and
>    applies the two unit conversions. It is the layer that knows about
>    coordinates and Open-Meteo's parameter names.
>
> **The agent never chains this tool into [GR2L](./gr2l_tool.md).** GR2L's tool
> calls `fetch_daily_weather` itself, at layer 2, for exactly the window it is
> simulating — that is what makes it self-contained. Nothing below is a step
> towards a roof simulation, and no part of layer 2 belongs in an agent-facing
> docstring.
>
> Open-Meteo is a free HTTP API, **no API key required**.

## What it does

Returns **daily** weather for a single lat/lon point — past (observed/reanalysis)
and/or future (forecast) — with exactly the variables GR2L needs: mean/max/min
temperature, mean relative humidity, precipitation sum, mean wind speed, and
shortwave-radiation sum.

Two backends, same shape of response:

| Backend      | Base URL                                     | Use for                          |
| ------------ | -------------------------------------------- | -------------------------------- |
| **Forecast** | `https://api.open-meteo.com/v1/forecast`     | future days + recent past (≤92 d back via `past_days`, up to 16 d ahead) |
| **Archive**  | `https://archive-api.open-meteo.com/v1/archive` | historical days (`start_date`/`end_date`, older than the forecast window) |

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
| `start_date` / `end_date` | one of the two pairs | Explicit window, `YYYY-MM-DD` |
| `past_days` / `forecast_days` | one of the two pairs | Relative window: 0–92 back, 0–16 ahead |

Not arguments, deliberately: **latitude/longitude/elevation** (pinned in
`site.py`) and the backend (chosen from the window, see below).

Returns `status='success'` with the site's `latitude`/`longitude`/`elevation`,
the `timezone`, the `backend` actually used, and `data` — one **already
transposed and unit-converted** daily row per day, in GR2L's row shape. On
failure: `status='error'` with `error_details`.

### When the agent should call it

Call it when the user asks about **the weather itself** — "will it rain this
week?", "how warm was last May?". Windows resolve automatically:

- **Forward-looking** ("next week") → Forecast backend, up to 16 days ahead.
- **Recent past** ("last 30 days") → Forecast backend.
- **Historical** ("summer 2023") → Archive backend.

Do **not** call it as a step towards a green-roof simulation. The roof tool
fetches the weather for its own window; calling this one first is redundant,
floods the context with daily arrays, and invites re-doing conversions that have
already been applied. See [GR2L § Who fetches the weather](./gr2l_tool.md#who-fetches-the-weather).

## Open-Meteo client

> Layer 2 — `weather_client.fetch_daily_weather`. Its callers are the two tool
> wrappers, never the agent. Everything below describes the upstream HTTP API.

### Request (GET, query params)

| Param              | Required | Value sent                                                                     |
| ------------------ | -------- | ------------------------------------------------------------------------------ |
| `latitude`         | yes      | `SITE_LATITUDE` — passed by the caller, not chosen here                         |
| `longitude`        | yes      | `SITE_LONGITUDE` — likewise                                                     |
| `daily`            | yes      | `temperature_2m_mean,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,precipitation_sum,wind_speed_10m_mean,shortwave_radiation_sum` |
| `timezone`         | yes\*    | `auto` (resolves to `Europe/Berlin` at this site). Required whenever `daily` is used |
| `wind_speed_unit`  | yes      | **`kmh`** — see wind note below (this is also the Open-Meteo default)           |
| `forecast_days`    | no       | 0–16 (Forecast backend; default 7)                                             |
| `past_days`        | no       | 0–92 (Forecast backend)                                                         |
| `start_date`/`end_date` | no  | `YYYY-MM-DD` window (both backends; the only way to select ranges on Archive)  |

`temperature_unit` defaults to `celsius` (what GR2L wants) — leave unset.

### Response (JSON)

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
differ. Both were traced through GR2L's actual math in
`gr2l_model/R/GR2L_function.R` (line numbers below); that math **disagrees with
the units labelled in GR2L's Pydantic schema — trust the math, not the label**:

1. **Wind (`w`) → request in `kmh` (no arithmetic).**
   R line 40: `df$u2 <- df[[w]] / 3.6`. The factor 3.6 is exactly the km/h→m/s
   conversion, so GR2L expects `w` in **km/h** — despite the schema field being
   labelled "m/s". → Request Open-Meteo wind as `wind_speed_unit=kmh` and pass it
   straight through.
   *(Do **not** request `ms`; GR2L would divide an m/s value by 3.6 and under-read
   wind by 3.6×.)*
   Physical caveat (not a unit error): Open-Meteo wind is at **10 m**; FAO
   Penman-Monteith assumes **2 m** and GR2L applies no height correction — a small
   positive bias in ET.

2. **Radiation (`gs`) → multiply by 100.**
   R lines 53–54: `df$Rs <- df[[gs]] / 8.64` then `df$Rs <- df$Rs / 1e6 * 86400`.
   The first step is J/cm²/day → W/m² (`1 J/cm²/day = 1/8.64 W/m²`), the second is
   W/m² → MJ/m²/day; net `Rs = gs × 0.01` MJ/m²/day. So GR2L expects `gs` in
   **J/cm²/day**, not "W/m²" as the schema says. Open-Meteo's
   `shortwave_radiation_sum` is **MJ/m²/day**, and `1 MJ/m² = 100 J/cm²`. →
   **`gs = shortwave_radiation_sum × 100`.**

Both conversions are **verified against the R source arithmetic** (deterministic,
not a guess). Still worth a one-off ET sanity-check on first integration to catch
any surprise in the model service wrapping the R code.

## Verified: I tried it out ✅

Live call (2026-07-06), Berlin, `wind_speed_unit=ms` to inspect units:

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
- **Forecast horizon:** up to 16 days ahead; `past_days` up to 92. For older
  history use the **Archive** backend with `start_date`/`end_date`.
- **One point per call**, and one point is all this deployment needs: every roof
  segment is on the same building, so one fetch serves them all. (Open-Meteo does
  accept comma-separated coordinates for multi-site use.)
- **Free tier:** no key, but fair-use rate limits apply; cache responses per
  (lat, lon, date-range).
- Source docs: <https://open-meteo.com/en/docs>
