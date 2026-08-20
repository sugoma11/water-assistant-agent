# GR2L — Green Roof Water-Balance Tool

> Specification for the **GR2L** model as wired into this assistant. It describes
> **two layers**, and they answer "who fetches the weather?" differently — read
> the one you actually need:
>
> 1. **[Agent-facing tool](#agent-facing-tool)** —
>    `predict_green_roof_water_balance`, the layer the LLM sees. It is
>    **self-contained: it fetches its own weather and its own starting soil
>    moisture** from a roof type + date window. It speaks **%θ**.
> 2. **[Endpoint contract](#endpoint-contract)** — `POST {API_PREFIX}/predict_gr2l`,
>    the raw model service. It fetches nothing; every daily weather row and the
>    day-1 state in **mm** must be supplied by *its* caller — which is the tool in
>    (1), never the agent.
>
> Do not paste layer-2 language ("the caller must supply the weather", "theta_01
> is in mm") into a layer-1 docstring: it would tell the agent to fetch weather
> separately and to do the unit conversion itself, which is exactly what the tool
> exists to prevent.

## What the model does

GR2L (**G**reen **R**oof, **2 L**ayer) simulates the daily water balance of a
green roof. The **agent-facing tool serves three substrate roof types**:
**non-irrigated extensive**, **irrigated extensive**, and **semi-intensive**
(see "Roof types and their parameters" below). It answers questions like
*"how much water is the roof storing, evaporating, and draining over this
weather period?"*

Two of the facility's five segments are **out of scope for the tool** — the
**gravel roof** and the **wetland** — see
"[Roofs that cannot be modelled](#roofs-that-cannot-be-modelled)". The wetland's
GR2L parameterization is **kept, not deleted**: it stays in the roof-type table
below and in `ROOF_PRESETS`, layer 2 still accepts it, and the exclusion is a
layer-1 scope decision that can be lifted.

The model runs in two stages per day:

1. **Reference evapotranspiration (ET_PM)** — computed with the
   **FAO-56 Penman-Monteith** equation from daily weather (temperature,
   humidity, wind, radiation) plus site geometry (elevation, latitude,
   day-of-year for extraterrestrial radiation).

2. **Two-layer water balance** — the roof is modelled as two coupled stores:
   - **Substrate store `Ssub`** — the growing medium where roots sit and
     evapotranspiration is drawn from. Bounded by `Ssubmin`…`Ssubmax`.
   - **Retention store `Sret`** — the drainage/retention layer beneath the
     substrate. Bounded by `Sretmax`.

   Each day, precipitation fills the substrate. Water above `Ssubmax` percolates
   down (`Qdown`) into the retention layer. Water can rise back up from
   retention into the substrate by capillary uptake (`Qup`). Retention above
   `Sretmax` leaves the roof as **outflow / runoff `OUT`**. Actual
   evapotranspiration `ET` is the potential `ET_PM` scaled by a crop factor `kg`
   and the current substrate saturation (`Ssub / Ssubmax`).

The result is a per-day time series of stored water, evapotranspiration, and
roof runoff — the quantities a green-roof analysis needs to reason about
retention performance, drought stress, and stormwater discharge.

## When an agent should call it

Call GR2L when the task requires **quantitative green-roof hydrology** over a
sequence of days: estimating stormwater retention, roof runoff, substrate
moisture / drought risk, or evapotranspiration cooling.

It is **not** a classifier or a single-value predictor — it simulates a run of
consecutive days and returns one output row per day. Give it a **date window**;
it has nothing useful to say about a single instant. The agent supplies the
window and the roof type — **not** the weather (see
"[Who fetches the weather](#who-fetches-the-weather)"), **not** the starting soil
moisture (see
"[Where day 1's soil moisture comes from](#where-day-1s-soil-moisture-comes-from)")
and **not** the location (see "[The site is pinned](#the-site-is-pinned)").

## The site is pinned

All the facility's roof types are segments of **one building**, so the
agent-facing tool takes no coordinates. `site.py` holds them and both the weather fetch and the
GR2L request use them:

| GR2L field  | Value      | Source |
| ----------- | ---------- | ------ |
| `lat`       | `51.353484` | `SITE_LATITUDE` |
| `long`      | `12.432152` | `SITE_LONGITUDE` |
| `hoehe_nn`  | `142`       | `SITE_ELEVATION_M` — surveyed roof height, **not** Open-Meteo's ~1 km grid-cell `elevation` |

To model a roof elsewhere, change `site.py` (and re-derive `Ssubmin`/`Ssubmax`
from that site's own soil-moisture record — see below); the underlying clients
`fetch_daily_weather` / `run_gr2l` remain fully parameterized.

## Resolution and scale

- **Temporal resolution: daily.** GR2L runs on **daily time steps** — one input
  row and one output row per day. This is the model's native step (in the source
  study, 5-minute lysimeter data was aggregated to daily values to drive it).
  Do not pass sub-daily (hourly) rows; aggregate to daily first.
- **Spatial scale: a single green-roof structure ("point" scale).** GR2L is a
  lumped two-bucket model of *one* homogeneous roof described by its parameters
  (`SH`, `Ssubmax`, `Sretmax`, …); it was calibrated on ~0.5 m² pilot plots. It
  is **not gridded** — to analyse several roofs or zones, call it once per roof
  with that roof's parameters.

Source: Green Roof 2 Layer (GR2L), *Frontiers in Climate* (2023) —
<https://www.frontiersin.org/journals/climate/articles/10.3389/fclim.2023.1115595/full>

## Who fetches the weather

GR2L is a **simulation**, not a weather forecaster. The model computes one output
day for every input day it is given and **never invents or predicts weather
itself** — so *something* has to supply a meteorological value for every day in
the window. The question is only **which layer does it**, and the answer differs
between the two layers of this document.

| Layer | Who supplies the daily weather rows |
| ----- | ----------------------------------- |
| **Agent-facing tool** (`predict_green_roof_water_balance`) | **The tool itself.** It resolves the window through `ctx.weather` — the composite that picks the site's own station or Open-Meteo's Archive from the window alone — and POSTs the rows it gets back, already unit-converted. The agent supplies only a date window and a roof type, and the response echoes which source forced the run in `weather_source`. |
| **HTTP endpoint** (`POST …/predict_gr2l`) | **Its caller** — i.e. the tool above. The endpoint has no weather source of its own; `data[]` is required. |

**Implications for the agent** (this is the layer-1 contract — it is what the
tool docstring says, and it is the *opposite* of the endpoint's rule):

- **Do not call the weather tool first.** Pass `start_date`/`end_date` (or
  `past_days`/`forecast_days`) and let the roof tool resolve the weather. Fetching
  weather separately and trying to reason over it is redundant, floods the context
  with daily arrays, and re-introduces the two unit conversions the tool already
  handles correctly.
- Call the [weather tool](./weather_tool.md) **only** when the user asks about the
  weather *itself* (e.g. "will it rain this week?"), not as a step towards a roof
  simulation.
- **Which source forces the run is resolved from the window, not decided by the
  agent.** There are two, and no argument selects them: the site's own **station**
  record whenever it covers the window entirely, and Open-Meteo's ERA5 **Archive**
  for everything else, including every window reaching past the case's present.
  Every window has exactly one provenance — a partly covered window falls to
  Archive whole rather than being stitched together — and the response says which
  in `weather_source`. A retrospective run and a forecast run are therefore forced
  by different instruments, and an answer **discloses the station** when it served
  (architecture §3.3, [weather tool](./weather_tool.md)).
- **The window may not reach more than 16 days past today.** GR2L needs a
  meteorological row for every day it simulates and no source has one beyond the
  forecast horizon, so a window ending later is `status='not_available'` — the
  same limit, checked the same way, as the weather tool's. Nothing bounds how far
  **back** a window may reach.

**Day-to-day state and cold starts.** The only thing GR2L carries between days is
the roof's **internal water state** (`Ssub`, `Sret`), seeded on day 1 from
`theta_01` / `theta_02` and propagated forward. That is *soil-moisture
continuity*, not weather. A day outside the window is simply not simulated. Day 1
is a **seed day**: it only initialises the stores and computes `ET`, so `Qdown`,
`Qup` and `OUT` are `null` for it. Two consequences worth knowing:

- Day 1's state is not the agent's problem either — the tool reads it from the
  roof's own sensor, see
  "[Where day 1's soil moisture comes from](#where-day-1s-soil-moisture-comes-from)".
- To let the stores settle instead of trusting a single reading, **start the
  window a few days early** and treat those days as spin-up. This also avoids
  the retention-summary edge below.

> ⚠️ **Retention over a window includes the seed day's rain but not its runoff.**
> `summary.retention_mm` is `Σprecip − ΣOUT`, and day 1's `OUT` is `null`
> (counted as 0), so a window whose first day is wet over-reports retention.
> Prepend a lead-in day when the number matters. The tool sets
> `summary.retention_excludes_seed_day_runoff` when the first day was wet, so the
> agent can disclose it rather than quoting an inflated number silently.

## Units: %θ in, %θ out

GR2L's state is substrate water **storage in mm**. Everything else in this
project — the SMT100 sensors, the ops manual, the numbers a researcher says out
loud — is **volumetric water content in %θ**. The conversion is the tool's job at
layer 1, never the agent's and never the LLM's:

| Layer | Soil-moisture unit |
| ----- | ------------------ |
| **Agent-facing tool** | **%θ**. `initial_soil_moisture_pct` is a percentage; `data[].swc_pct` and `summary.min_swc_pct` come back as percentages. `Ssub` in mm rides along for the water balance. |
| **HTTP endpoint** | **mm**. `theta_01`, `Ssub`, `Ssubmin`, `Ssubmax` are all millimetres of stored water. |

The relation is GR2L's own depth scaling, applied in `swc.py` with each roof's
`SH`:

```
S_mm = (θ% / 100) × SH_mm          θ% = S_mm / SH_mm × 100
```

Retention and runoff (`OUT`, `retention_mm`) stay in **mm** in both directions —
they are fluxes, not states, and have no θ equivalent.

> ⚠️ **This conversion is why the wetland is out of layer-1 scope.** Its store is
> a 17 mm fleece mat plus water ponded above it to the 90 mm standpipe height,
> and the sensor saturates near 86 % θ (≈14.7 mm), so above the mat θ is not
> recoverable from mm — the tool's %θ-in/%θ-out contract cannot describe it. Its
> preset is retained for layer 2, where a run is read in mm throughout; see
> "[Roofs that cannot be modelled](#roofs-that-cannot-be-modelled)".

## Where day 1's soil moisture comes from

The tool reads it from the roof's own SMT100 sensor: the **latest reading at or
before `min(window_start, as_of)`** (`swc.latest_measured_swc` /
`swc.seed_bound`, `data/water.duckdb`, `swc` table), converted %θ → mm before the
request. The agent supplies nothing.

**Both halves of that bound are load-bearing, and each fails a case the other
handles.** Seeding at or before the *window start* alone lets a forecast window —
one that opens in the future — read sensor data recorded after the moment the
question is being asked from, which for a frozen evaluation case is leakage.
Seeding at or before *now* alone starts a retrospective run from a reading taken
months after that window closed. Taking the earlier of the two is the only rule
that holds for both, and it is applied inside `latest_measured_swc` rather than
left to the caller's database view, because an oracle may call the same function
with an unbounded connection.

- **Do not query the database for soil moisture to feed this tool.** It is
  already doing that, against the same table, for exactly the right day.
- `initial_soil_moisture_pct` overrides the sensor, in **%θ**. Use it only for a
  stated or hypothetical starting value ("if the roof started out dry, at 5 %"),
  not to nudge a result.
- The response echoes what was used in `seed`: `source` (`measured` / `caller`),
  `swc_pct`, the `substrate_storage_mm` actually sent, `measured_at`, `age_days`
  and `is_stale`.

**Freshness.** Substrate moisture has a memory of days, not months, so a reading
much older than the window start describes a roof that no longer exists. `age_days`
is measured against **the window's start**, not against the bound the reading was
picked at, precisely so a forecast seeded at today's cut reports how stale it will
be by the time the window opens. Beyond `swc.STALE_AFTER_DAYS` (7) the seed is
flagged `is_stale`; the run still proceeds, and **the answer must say what it was
seeded from and when**. This is not hypothetical: the sensor record currently ends
2026-04-24, so present-day forecasts are seeded from stale readings until the
record is refreshed.

**When there is nothing to seed from** — the window opens before the record
starts, or every candidate reading falls in a period the sensor is known to have
failed — the tool returns `status='not_available'` with the reason, and does
**not** substitute a default. GR2L's generic `theta_01 = 20 mm` is not a usable
fallback here: it is above `Ssubmax` for the non-irrigated extensive roof and
within 3 mm of field capacity for the irrigated extensive, so it would silently
start the simulation from a saturated roof.

## Roofs that cannot be modelled

Two of the facility's five segments are outside the agent-facing tool's scope.
Both live in `NON_MODELLABLE_ROOFS` and both return `status='not_available'`.

**The gravel roof** (`Kies` / `KD` / `QGravel`) has **no substrate**, so `SH` and
the measured `Ssubmin`/`Ssubmax` bounds the two-layer balance is built on do not
exist for it; there is no store to fill, drain or evaporate from. There is
nothing to parameterize and no preset for it anywhere.

**The wetland** (`QWetland`) is a different case: it *has* a preset, and that
preset is **retained deliberately** — in the roof-type table below, in
`ROOF_PRESETS`, and in the layer-2 request body. What is withdrawn is layer-1
service, for two reasons: the tool's %θ contract cannot describe a store whose
sensor saturates near 86 % θ while water ponds above the mat (see "Units"), and
the `QWetland` record fails from 2026-03-12 — a flat ~0 %θ to the end of the
record on 2026-04-24, against a healthy range of 4–96 %θ — leaving nothing
trustworthy to seed a present-day run from. Lifting the exclusion is a one-line change to
`NON_MODELLABLE_ROOFS` if the sensor situation improves and the answer is allowed
to come back in mm.

- Asking to model either returns **`status='not_available'`** with a `reason`,
  not an error. Nothing has malfunctioned — the request is outside the tool's
  scope, and the agent should say so plainly and offer what is available.
- Both remain **first-class for measured data**: their soil-moisture, outflow and
  temperature columns are queried like any other roof's. "How much water ran off
  the gravel roof in July?" is a normal database question; "how much will it
  retain next week?" is not answerable.
- **Neither roof has an irrigation decision either.** `calc_irrigation` excludes
  both on its own terms — its rule is built for a classical substrate roof (see
  [irrigation tool](./irrigation_tool.md)) — so `NON_MODELLABLE_ROOFS` bounds the
  two water-balance tools alike and no roof answers under one while abstaining
  under the other.

## Roof types and their parameters

This deployment carries presets for **four roof types**. The agent-facing tool
serves the **three substrate roofs**; the **wetland preset is retained but not
reachable through layer 1** (see
"[Roofs that cannot be modelled](#roofs-that-cannot-be-modelled)"). Each has a
fixed substrate height `SH`; none of them has a retention layer, so every
retention-related parameter is pinned to `0` (not the model's generic defaults):

| Roof type                  | `SH` (cm) | `Ssubmin` (mm) | `Ssubmax` (mm) | `Sretmax` (mm) | `Sret` (mm) | `theta_02` (mm) | `kg` | `albedo` | `open_water` |
| --------------------------- | --------- | -------------- | -------------- | -------------- | ----------- | ---------------- | ---- | -------- | ------------ |
| Wetland ⚠️ *(layer 2 only)*  | 1.7       | 1.3            | 90             | 0              | 0           | 0                 | 1    | 0.06     | true         |
| Non-irrigated extensive     | 7         | 0.9            | 16.0           | 0              | 0           | 0                 | 1    | 0.2      | false        |
| Irrigated extensive         | 7         | 3.3            | 22.8           | 0              | 0           | 0                 | 1    | 0.2      | false        |
| Semi-intensive              | 15        | 6.3            | 45.6           | 0              | 0           | 0                 | 1    | 0.2      | false        |

> **How `Ssubmin`/`Ssubmax` were derived.** These are **not** the model's generic
> defaults — they come from this facility's own soil-moisture record
> (`data/water.duckdb`, `swc` table; SMT100 volumetric sensors at 5 cm depth,
> ~28.5k hourly rows, 2024-07 → 2026-04). For each roof the robust min/max
> (1st/99th percentile, to reject sensor dropouts and noise) of volumetric water
> content θ was converted to substrate water storage with the GR2L depth-scaling
> relation `S_mm = (θ% / 100) × SH_mm`:
>
> | Roof (`swc` column)          | θ min–max (p1–p99) | SH    | `Ssubmin` → `Ssubmax` |
> | ---------------------------- | ------------------ | ----- | --------------------- |
> | Irrigated extensive (`QEx1`)     | 4.70–32.63 % | 7 cm  | 3.3 → 22.8 mm |
> | Non-irrigated extensive (`QEx2`) | 1.22–22.85 % | 7 cm  | 0.9 → 16.0 mm |
> | Semi-intensive (`QIn`)           | 4.20–30.38 % | 15 cm | 6.3 → 45.6 mm |
> | Wetland (`QWetland`)             | 7.55–86.19 % ⚠️ | 1.7 cm | 1.3 → 90 mm |
>
> ⚠️ **Wetland specifics.** The wetland roof has no soil substrate — its store
> is a **17 mm water-storage mat of recycled polypropylene fleece** that ponds
> water above it up to the **9 cm vertical standpipes on the outlets**
> (Moeller et al. 2025, Ecological Engineering 220:107729). So `Ssubmax = 90 mm`
> is **structural** (the standpipe overflow height), not percentile-derived,
> while `Ssubmin = 1.3 mm` is the sensor p1 (7.55 % θ × 17 mm mat depth,
> computed **excluding 2026-02-01 → end of record**). That cut-off was chosen
> before the failure onset was measured: the sensor actually fails from
> 2026-03-12, so the derivation discarded 40 days of healthy record, and the p1
> over the corrected window is 6.31 % θ (`Ssubmin = 1.07 mm`). **The value is
> left as it stands** — the roof presets are pinned (architecture §5) and the
> wetland sits in `NON_MODELLABLE_ROOFS`, so no layer-1 call reaches this
> preset; moving it would invalidate the GR2L canary to no effect. Because the ponded surface behaves like
> open water, the preset also sets `open_water = true`: GR2L evaporates at the
> potential rate (`ET = ET_PM · kg`, no `Ssub/Ssubmax` throttling) while still
> tracking the water balance (`Ssub`, `OUT`, …), and `albedo = 0.06` (open
> water) instead of the vegetated 0.2. Note the mat's sensor saturates at
> ~86 % θ (≈14.7 mm over the mat depth): any `Ssub` above that is ponded water
> the SWC sensor cannot distinguish.
>
> ⚠️ **Irrigation is a real but unmodelled inflow.** All roof segments —
> the wetland included — are watered by **drip irrigation** (measured on-site
> by a Q3 water meter, but not present in `data/water.duckdb`). GR2L only
> sees the `precip` column, so simulations driven by rain alone
> **underestimate** the wetland's storage whenever irrigation runs (the
> 2025 record shows ~24 rain-free days with θ rising 2–13 %/day). If an
> irrigation series is available, add it to `precip` per day before calling
> the model.

**Why these are fixed, not defaults to override** — with exactly two exceptions:
`theta_01` (the day-1 moisture seed, which describes the *state* of the roof, not
the roof — resolved from the sensor, see
"[Where day 1's soil moisture comes from](#where-day-1s-soil-moisture-comes-from)")
and `albedo` (see "[Overriding `albedo`](#overriding-albedo)" below).
Everything else in the table is a physical property of the installed roof and
must not be varied:
- `Ssubmin` / `Ssubmax` — **measured from this site's soil-moisture record**,
  not the generic model defaults (see the derivation footnote under the table).
  `Ssubmin` is the minimum substrate water content (wilting point) and
  `Ssubmax` the maximum (field capacity). The **wetland**'s `Ssubmax = 90 mm`
  is the exception: it is structural — the 9 cm outlet standpipes set the
  ponding/overflow height above its 17 mm fleece storage mat (see the footnote).
- `Sretmax = 0` and `Sret = 0` — none of the four roof types has a physical
  retention/drainage layer, so the retention store has no capacity and starts
  empty.
- `theta_02 = 0` — the initial retention-layer moisture seed is meaningless
  without a retention layer, so it must be `0` too.
- `kg = 1` — use a crop factor of `1` (no scaling) for all four roof types,
  regardless of vegetation. This overrides the model's generic `kg = 0.35`
  default.
- `SH` varies by roof type: `1.7` cm for the wetland's fleece storage mat,
  `7` cm for both extensive roofs (irrigated and non-irrigated), `15` cm for
  semi-intensive.
- `open_water = true` (wetland only) — the saturated mat evaporates at the
  potential rate: `ET = ET_PM · kg` with **no** `Ssub/Ssubmax` scaling. The
  water balance (`Ssub`, `Sret`, `Qdown`, `Qup`, `OUT`) is still computed and
  returned.
- `albedo` — a per-roof **default**, not a constant: `0.06` (open water) for the
  wetland, `0.2` (vegetated) for the other three. This is the one physical
  parameter a caller may deliberately override; see below.

Because `Sretmax = 0`, expect every day's `Qdown` to pass straight through as
`OUT` (no percolation storage to fill) and `Qup` to stay `0` (nothing to draw
back up) — the two-layer balance degenerates to a single substrate bucket for
all four roof types.

`Ssubmin`/`Ssubmax` depend on the substrate mix/depth actually installed. For
**this** deployment they have been measured (Leipzig facility, SMT100 sensors)
and are given per roof type in the table above — use those rather than the
generic defaults. At a different site, re-derive them from that site's own
soil-moisture data (or omit `Ssubmax` to fall back to the model default).

### Overriding `albedo`

`albedo` is the fraction of incoming shortwave radiation the surface reflects. It
enters the FAO-56 net-radiation term, so it moves `ET_PM` and therefore `ET`,
`Ssub` and `OUT` — a **higher** albedo reflects more energy away, lowering
evapotranspiration and leaving the roof wetter.

Unlike `SH`, `Ssubmin`/`Ssubmax`, `Sret*`, `theta_02` and `kg`, albedo is not a
structural fact of the installation — it depends on what the surface currently
*looks* like, which can legitimately differ from the preset:

| Situation | Typical albedo |
| --------- | -------------- |
| Wetland, ponded open water (**default**)      | `0.06` |
| Dense green vegetation (**default**, other 3) | `0.2`  |
| Dry / senescent vegetation, exposed substrate | `0.25–0.3` |
| Light gravel or a reflective "cool roof" coat | `0.4–0.6` |
| Fresh snow cover                              | `0.8`  |

**Rules for the agent**

- **Leave it alone by default.** Omit the `albedo` argument and the roof-type
  preset applies. The preset is the calibrated value for this facility.
- **Override only on an explicit request** — the user names a surface change, a
  coating, a snow-covered roof, or asks a "what if the roof were more reflective"
  counterfactual. Never override to "improve" a result, to reconcile a simulation
  with a measurement, or on your own initiative.
- Valid range is `0.0–1.0`; the tool rejects anything outside it.
- An override applies to the **whole window** — GR2L takes one albedo per run,
  not a per-day series. To model a surface that changes mid-window, run the
  segments as separate calls, chaining the first run's final `swc_pct` into the
  second's `initial_soil_moisture_pct`.
- The response always echoes the effective value in `parameters.albedo`. **Say in
  the answer that a non-default albedo was used**, so the number is not mistaken
  for a baseline simulation.

## Agent-facing tool

> Layer 1 — what the LLM actually sees. Implemented in `gr2l.py`; it resolves the
> weather, the starting soil moisture, the site and the roof preset, converts
> %θ → mm, then calls layer 2 once and converts mm → %θ back.

```python
async def predict_green_roof_water_balance_tool(
    roof_type: str,
    start_date: str | None = None,
    end_date: str | None = None,
    past_days: int | None = None,
    forecast_days: int | None = None,
    initial_soil_moisture_pct: float | None = None,
    albedo: float | None = None,
    forcings: dict[str, dict[str, float]] | None = None,
    evaluate_against_measured: bool = False,
) -> dict
```

> ⚠️ **`roof_type` is a plain `str` and must stay one.** ADK renders a
> `Literal[...]` into the function declaration as a schema `enum`, which would
> leave the agent unable to *name* the gravel roof or the wetland — and naming
> one is how a scope question gets asked at all. Scope is decided in code against
> `NON_MODELLABLE_ROOFS`, after normalizing the argument once at entry, and a
> test pins the annotation (architecture §3.4).

| Argument | Required | Meaning |
| -------- | -------- | ------- |
| `roof_type` | yes | `non_irrigated_extensive`, `irrigated_extensive`, `semi_intensive` — selects the preset from the roof-type table. Gravel and wetland → `not_available` |
| `start_date` / `end_date` | one of the two pairs | Explicit window, `YYYY-MM-DD`. Both dates, or neither |
| `past_days` / `forecast_days` | one of the two pairs | Relative window: 0–92 back, 0–16 ahead. `past_days` is complete past days ending **yesterday** and carries no forecast tail; the two forms are exclusive. Resolved to absolute dates before the weather fetch — see [weather § Relative windows are resolved first](./weather_tool.md#relative-windows-are-resolved-first) |
| `initial_soil_moisture_pct` | no | Day-1 soil moisture in **%θ**. Omitted → read from the roof's sensor for the window's first day — see "[Where day 1's soil moisture comes from](#where-day-1s-soil-moisture-comes-from)" |
| `albedo` | no | Override the roof's default albedo, `0.0–1.0`. Omit unless explicitly asked — see "[Overriding `albedo`](#overriding-albedo)" |
| `forcings` | no | Counterfactual weather: `{field: {day: value}}` in the daily row's **own** field names, e.g. `{"precip": {"2026-07-22": 50.0}}`. Sparse — unnamed days and fields keep what was fetched. Every day named must fall inside the window. See "[Counterfactual weather](#counterfactual-weather)" |
| `evaluate_against_measured` | no | Compare the predicted `swc_pct` series against the roof's own sensor record and return the deviation. See "[Comparing a run with the measurement](#comparing-a-run-with-the-measurement)" |

Not arguments, deliberately: **weather** (fetched internally — `forcings`
overrides values, it does not supply the series), **starting soil moisture**
(read from the sensor), **latitude/longitude/elevation** (pinned in `site.py`),
**which weather source forces the run** (resolved in code from the window), and
every other roof parameter (fixed per roof type).

Three outcomes:

- `status='success'` — the resolved `roof_type`, the `weather_source` that forced
  the run (`station` / `archive`), the `forcings` actually applied (`null` when
  none were), the full effective `parameters` (echoing `albedo` and the
  `theta_01` in mm actually sent), the `seed` day 1 started from, `data` (one row
  per day: the GR2L fields plus `swc_pct`), and a `summary` (`retention_mm`/
  `retention_pct`, `min_substrate_storage_mm`, `min_swc_pct`, `drought_stress`,
  `retention_excludes_seed_day_runoff`). Past the series cap, `data` is empty,
  `truncated` is `true` and `weekly` carries the run instead — see
  "[The series is bounded](#the-series-is-bounded)". With
  `evaluate_against_measured` it also carries `evaluation`.
- `status='not_available'` with a `reason` — the request is outside what can be
  modelled: the gravel roof or the wetland, a window with no soil-moisture record
  to start from, or a window ending more than 16 days ahead. Report the scope
  limit; this is not a fault.
- `status='error'` with `error_details` **and an `error_type`**:
  - `invalid_argument` — the call itself was wrong, and the message says what
    would have been valid: an unknown `roof_type`, a malformed window, an
    `albedo` or `initial_soil_moisture_pct` out of range, a `forcings` naming a
    field that is not a weather field, a day outside the window, or a
    non-numeric value. Every one of these is decided **before any I/O**, so a bad
    argument costs no upstream hop. Correct the arguments and retry.
  - `upstream` — something the call depended on failed: the weather fetch, the
    soil-moisture read, the GR2L service, its configuration, or a cache miss
    replay cannot fill. Nothing the agent can rephrase fixes it; report the
    system-side problem. The harness excludes these from its aggregates and
    scores the `invalid_argument` ones (architecture §7).

## Counterfactual weather

`forcings` answers "what if the weather had been different?" — a 50 mm downpour
tomorrow, a week 5 °C warmer — by **replacing values in the rows the model runs
on**, before the request. The what-if is therefore computed by GR2L rather than
estimated from a baseline result afterwards.

```python
forcings={"precip": {"2026-07-22": 50.0}, "tm": {"2026-07-22": 24.0}}
```

- **Keyed by the daily row's own field names**: `precip` (mm), `tm` / `tx` / `tn`
  (°C), `rf` (%), `w` (km/h), `gs` (J/cm²/day) — the same names `data[]` uses and
  the weather tool returns, never `precip_mm` or a second vocabulary. One naming
  means a forcing and the row it replaces never need translating.
- **Sparse.** A field not named keeps its fetched value on every day; a day not
  named keeps every field. Only what is written is replaced.
- **Bounded by the window.** Every day named must fall inside the resolved
  window; one outside it is an `invalid_argument`, not a silent no-op.
- **Echoed back** in the response's `forcings`, exactly as applied, so an answer
  can attribute the assumed values — and so the arguments themselves can be
  checked without reading the result.
- The `summary` is computed over the **forced** rows: a counterfactual's
  retention is measured against its own rain, not against the rain it replaced.

## Comparing a run with the measurement

`evaluate_against_measured=True` joins the predicted `swc_pct` series to the
roof's own `swc` record and adds an `evaluation`:

| Field | Meaning |
| ----- | ------- |
| `days` | Days both series cover |
| `overlap_start` / `overlap_end` | The compared window |
| `mean_abs_deviation_pct` | Mean \|predicted − measured\|, **%θ** |
| `max_abs_deviation_pct` | Largest single-day \|predicted − measured\|, **%θ** |
| `reason` | Why there is nothing to compare (only when `days` is 0) |

The measured series is the sensor's **daily mean per Europe/Berlin day**, the same
day boundary the model's rows use, so no part of a deviation comes from the
grouping. Days inside a period the sensor is known to have failed are left out
rather than compared. A window with no overlap — a forecast, typically — comes
back with `days: 0` and a `reason`: that is a success, and quoting a deviation of
zero for it would be wrong.

## The series is bounded

`data` is capped at **31 days**. Past the cap the response carries `truncated:
true`, an empty `data`, and `weekly` — consecutive seven-day aggregates (the last
short, with its own `days`) in the same field names, fluxes accumulated and
states averaged, plus `min_swc_pct` per week. The `summary` still covers the
whole run, and so does `evaluation`: **the cap bounds the response, never the
simulation** — GR2L is still run day by day, because the balance carries state
from one day to the next. Answer from the summary and the weekly rows rather than
re-running the window in pieces to get the days back.

## Endpoint contract

> Layer 2 — the raw model service. Its caller is
> `predict_green_roof_water_balance`, **not** the agent. Everything below,
> including "the caller must supply `data[]`", is addressed to the tool wrapper.

### Endpoint

```
POST {API_PREFIX}/predict_gr2l
```

`API_PREFIX` is deployment-specific (`/test-api` by default; production mounts
it under its own prefix such as `/api-weinbau`). Confirm the base URL for the
target environment before wiring.

### Headers

| Header         | Value                       | Required |
| -------------- | --------------------------- | -------- |
| `API-KEY`      | API key with `predict_gr2l` (or `any`) access | yes |
| `Content-Type` | `application/json`          | yes |

A key whose `allowed_predict_endpoints` does not include `predict_gr2l` (or
`any`) is rejected with `403`.

### Request body

```jsonc
{
  "data": [                     // required: ordered list of daily weather rows
    {
      "Date":   "2024-01-01",   // string, date of the day (YYYY-MM-DD)
      "tm":     2.0,            // mean temperature, °C
      "rf":     80.0,          // relative humidity, %
      "precip": 5.0,           // precipitation, mm
      "w":      2.5,           // wind speed, km/h  ⚠️ schema says "m/s" — the model treats it as km/h
      "gs":     150.0,          // global radiation, J/cm²/day  ⚠️ schema says "W/m²" — the model treats it as J/cm²/day
      "tx":     4.0,           // max temperature, °C
      "tn":     -1.0           // min temperature, °C
    }
    // … one object per day, in chronological order
  ],

  // Site & model parameters (all optional — model defaults shown, but see
  // "Roof types and their parameters" above: this deployment's four roof
  // types pin SH/Sret/Sretmax/theta_02/kg to fixed, non-default values)
  "hoehe_nn":  120,   // elevation above sea level, m — this deployment sends 142 (site.py)
  "lat":       52,    // latitude,  decimal degrees — this deployment sends 51.353484
  "long":      11,    // longitude, decimal degrees — this deployment sends 12.432152
  "SH":        6,     // substrate height, cm — use the roof-type table, not this generic default
  "Ssubmin":   5.4,   // min substrate water content, mm — generic default; use the roof-type table's measured value
  "Ssubmax":   25.4,  // max substrate water content (field capacity), mm — generic default; use the roof-type table's measured value
  "Sret":      0,     // initial retention-layer storage, mm — always 0 (no retention layer)
  "Sretmax":   5.0,   // max retention-layer storage, mm — always 0 (no retention layer)
  "theta_01":  20,    // initial substrate moisture, mm — generic default; DO NOT use it.
                      // The tool sends the roof's measured state, converted %θ → mm
                      // (20 mm is at or above Ssubmax for both extensive roof types)
  "theta_02":  5,     // initial retention storage, mm — always 0 (no retention layer)
  "kg":        0.35,  // crop / vegetation coefficient (0–1) — always 1 for these roof types
  "albedo":    0.2,   // surface albedo — roof-type DEFAULT (0.06 wetland / 0.2 others);
                      // overridable on explicit request, see "Overriding albedo"
  "open_water": false // true only for the wetland: ET = ET_PM·kg (no saturation scaling);
                      // the water balance (Ssub/Sret/Qdown/Qup/OUT) is still tracked
}
```

**Notes for the caller**
- `data` rows must be chronological — the water balance carries state from one
  day to the next (`Ssub`/`Sret` of day *i* depend on day *i-1*).
- Every field in each row is required and numeric (except `Date`, a string).
- The site/model parameters describe the physical roof. For `SH`, `Ssubmin`,
  `Ssubmax`, `Sret`, `Sretmax`, `theta_02`, and `kg`, do **not** fall back to
  the generic model defaults shown above — use the values from the roof-type
  table instead, keyed by which of the four roof types (wetland, non-irrigated
  extensive, irrigated extensive, semi-intensive) is being modelled — layer 2
  accepts all four, the wetland the agent-facing tool declines included.
  `Ssubmin`/`Ssubmax` there are the measured wilting-point / field-capacity
  bounds for this site (wetland: `Ssubmax = 90` is structural — the outlet
  standpipe height; see the roof-type footnote). `lat`, `long` and `hoehe_nn`
  come from `site.py`, never from the request. Two parameters are left to the
  caller: `theta_01` (day-1 moisture **in mm** — the tool converts the sensor's
  %θ before sending it; do not fall back to the generic 20) and `albedo`
  (roof-type default unless the user explicitly asked for a different surface —
  see "[Overriding `albedo`](#overriding-albedo)").
- ⚠️ **`w` is km/h and `gs` is J/cm²/day.** The model core does `u2 = w/3.6`
  (km/h→m/s) and `Rs = gs × 0.01` MJ/m² (i.e. `gs` in J/cm²/day) — see
  `gr2l_model/R/GR2L_function.R:42` and `:56`; the schema field descriptions
  (`api_gateway/prediction_models/gr2l.py:13-14`) name the same conversions.
  (Earlier revisions of this file warned that the schema labels and R comments
  said "m/s" / "W/m²" and contradicted the core; both were fixed upstream.)
  The [weather tool](./weather_tool.md) already emits these correct units.

### Response body

`200 OK` — one output row per input day, in the same order:

```jsonc
{
  "data": [
    {
      "Date":  "2024-01-01",
      "ET_PM": 0.42,    // potential (Penman-Monteith) evapotranspiration, mm/day
      "Ssub":  20.0,    // substrate water storage at end of day, mm
      "Sret":  5.0,     // retention-layer storage at end of day, mm
      "Qdown": null,    // water percolating substrate → retention, mm (null on day 1)
      "Qup":   null,    // capillary uptake retention → substrate, mm (null on day 1)
      "OUT":   null,    // outflow / roof runoff leaving the system, mm (null on day 1)
      "ET":    0.12     // actual evapotranspiration, mm/day
    }
    // … one object per input day
  ]
}
```

| Field   | Meaning                                            | Unit   |
| ------- | -------------------------------------------------- | ------ |
| `Date`  | Day of the record (echoes the input)               | —      |
| `ET_PM` | Potential evapotranspiration (Penman-Monteith)     | mm/day |
| `Ssub`  | Substrate water storage (end of day)               | mm     |
| `Sret`  | Retention-layer water storage (end of day)         | mm     |
| `Qdown` | Excess water percolating substrate → retention     | mm     |
| `Qup`   | Capillary water uptake retention → substrate       | mm     |
| `OUT`   | Outflow / stormwater runoff leaving the roof       | mm     |
| `ET`    | Actual evapotranspiration (`ET_PM · kg · Ssub/Ssubmax`; open water: `ET_PM · kg`) | mm/day |

`Qdown`, `Qup`, and `OUT` are `null` on the first day (day 1 only seeds the
initial stores and computes `ET`; the flux terms are defined from day 2 onward).

### Errors

| Status | Cause                                                        |
| ------ | ----------------------------------------------------------- |
| `401`  | Missing / invalid `API-KEY`                                 |
| `403`  | Key not authorised for `predict_gr2l`                       |
| `422`  | Request body fails schema validation (missing/typed fields) |
| `5xx`  | Upstream GR2L model service unavailable or timed out (90 s) |

## Interpreting the output (guidance for the agent)

- **Soil moisture** is `swc_pct` (%θ) — the unit the sensors and the ops manual
  use, and the one to answer in. `Ssub` is the same state in mm, for the water
  balance.
- **Stormwater retention** over a period ≈ total `precip` in − total `OUT` out.
  Days with `OUT = 0` mean the roof fully absorbed that day's rain. Check
  `summary.retention_excludes_seed_day_runoff` before quoting the number.
- **Drought / plant stress** is indicated by `Ssub` approaching `Ssubmin` and
  `ET` collapsing toward zero — the roof has little plant-available water.
  `summary.drought_stress` fires when the driest day comes within 5 % of the
  roof's `Ssubmin`–`Ssubmax` range of `Ssubmin`.
- **Cooling / evaporative service** is tracked by `ET` (actual, not `ET_PM`).
- The gap between `ET_PM` and `ET` shows how water-limited the roof is: when they
  diverge, evapotranspiration is being throttled by low substrate moisture.
- **Three things in the response the answer is obliged to pass on**, because each
  one changes what the number means: `seed.is_stale` (what the run started from
  and when), a non-default `parameters.albedo` (a non-standard surface was
  assumed), and a non-null `forcings` (those values were assumed, not measured or
  forecast). `weather_source == "station"` is disclosed for the same reason —
  the run was forced by the site's own instruments.

## Reference implementation

- Contract (Pydantic schemas): `/home/al/work/weinbau-api-v1/api_gateway/prediction_models/gr2l.py`
- Gateway route: `api_gateway/routers/predict.py` (`/home/al/work/weinbau-api-v1/api_gateway/routers/predict.pyl`)
- Model core (FAO Penman-Monteith + 2-layer balance): `/home/al/work/weinbau-api-v1/gr2l_model/R/GR2L_function.R`
- Example call: `/home/al/work/weinbau-api-v1/scripts/test_predict_gr2l.py`
