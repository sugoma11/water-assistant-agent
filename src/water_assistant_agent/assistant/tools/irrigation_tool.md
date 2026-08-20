# Smart Irrigation — Decision Tool

Companion to [`gr2l_tool.md`](./gr2l_tool.md) and
[`weather_tool.md`](./weather_tool.md). The agent-facing surface is specified in
architecture §3.5; this document holds the physics, the parameter derivation and
the deviations from the deployed controller.

Source of truth for the algorithm: the UFZ green-roof controller
(`~/Downloads/smart_irrigation.py`, an extraction of the site's deployed
`temp/smart_irrigation.py`). Actuation — magnetic valves, GPIO, irrigation
timing — is out of scope here; this tool answers *whether* to irrigate.

---

## This is not GR2L, and the two must not be merged

Both models are single-site green-roof water balances driven by daily or hourly
weather. They are nonetheless different models, and D29 keeps them separate.

| | GR2L (`gr2l_tool.md`) | irrigation bucket (here) |
|---|---|---|
| stores | two: substrate `Ssub` + retention `Sret`, with `Qdown`/`Qup` exchange | one |
| ET throttle | `Ssub / Ssubmax` | `clip((S − S_r) / (S_fc − S_r), 0, 1)` |
| throttle evaluated on | the current step | the **previous** step |
| ET applied | after the overflow step | before the cap |
| lower bound | floored at `Ssubmin` | none — the store may fall below zero |
| ET0 | computed internally from the weather row | supplied as an input |
| field capacity | per-roof `Ssubmax` (22.9 / 32.6 / 30.4 %θ) | flat 22 %θ for all three substrate roofs |

The last row is the one that bites. The controller applies one field capacity to
three roofs with materially different storage; for `semi_intensive` that is
33.0 mm against GR2L's measured `Ssubmax` of 45.6 mm, so the two models disagree
about when that roof overflows at all — which is exactly the quantity the
"will it refill?" branch of the rule turns on.

**Neither model is a bug in the other.** GR2L is the calibrated research model;
this one is what the site's controller actually runs, and T07/T11 measure the
deployed rule. Answers derived from the two will differ, and an answer that
mixes them is wrong.

---

## The balance

One store, one step at a time, in millimetres:

```
ks[i]      = 1.0                                    if not water_limited
           = clip((S[i-1] − S_r) / (S_fc − S_r), 0, 1)   otherwise

i = 0:  level = initial                    (no ET — the seed step only initialises)
i > 0:  et[i] = et0[i] * ks[i]
        level = S[i-1] + precip[i] − et[i]

outflow[i] = max(level − capacity, 0)
S[i]       = min(level, capacity)
```

Four properties look like defects and are preserved deliberately, because the
deployed controller has them and this tool exists to reproduce its decisions:

1. **`ks` reads the previous step**, so the stress coefficient lags the state it
   throttles by one step.
2. **There is no lower floor.** The store may fall below the residual and below
   zero. GR2L floors at `Ssubmin`; this model does not.
3. **The seed step computes no ET** (`et[0]` undefined) but *does* compute an
   outflow from the raw initial value before capping. That outflow is a seeding
   artifact, which is why the refill window skips it.
4. **ET is subtracted before the cap**, not after the overflow step.

`S_fc` and `capacity` are the same number — see *Units* below.

## Units: millimetres internally, the site's own units at the surface

The extraction adds millimetres of rain and ET to a store held in **%VWC** for
the substrate roofs and in **kg** for the wetland lysimeter:

```python
store[i] = store[i - 1] + precipitation[i] - et_actual[i]   # %VWC + mm - mm
```

Correcting this is not cosmetic. Converting the store to millimetres rescales
each roof's response to a millimetre of rain by `100 / SH_mm` — about 0.7× for
the 7 cm extensive roofs and 1.5× for the 15 cm semi-intensive — so the fix
changes what the model predicts, and with it some decisions. D31 keeps the
deployed thresholds anyway and measures the difference (plan T048); it does not
re-derive them.

Constants are **authored in the unit the site states them** and converted once,
through `swc.theta_pct_to_mm` for %θ and `kg / area_m²` for the lysimeter
(collection area 1 m², so kg ↔ mm is numerically identity):

| Roof | site id | `SH` | wilting | dry | capacity |
|---|---|---|---|---|---|
| `irrigated_extensive` | EGR1 | 7 cm | 5.0 %θ → **3.5 mm** | 10.0 %θ → **7.0 mm** | 22.0 %θ → **15.4 mm** |
| `non_irrigated_extensive` | EGR2 | 7 cm | 4.0 %θ → **2.8 mm** | 10.0 %θ → **7.0 mm** | 22.0 %θ → **15.4 mm** |
| `semi_intensive` | IGR | 15 cm | 10.0 %θ → **15.0 mm** | 16.0 %θ → **24.0 mm** | 22.0 %θ → **33.0 mm** |
| `wetland` | WGR | — | 64 kg → **64 mm** (level) | — | 80 kg → **80 mm** |

Residual: 2.5 %θ → 1.75 mm (7 cm) / 3.75 mm (15 cm).

The conversion **removes** a constant rather than adding one. The extraction
carries `SWC_CAPACITY = 22.0` (%VWC) and `THETA_FIELD_CAPACITY = 0.22`
(fraction) as separate module constants; they are one quantity written twice in
two units, serving the overflow threshold and the stress-coefficient
denominator. In millimetres they collapse into a single `capacity_mm`.

The wetland's 80 mm capacity sits just below GR2L's structural `Ssubmax` of
90 mm (the outlet standpipe height), which is a useful cross-check on the 1 m²
area. It assumes the L6 reading is water mass rather than gross weight including
substrate tare — **open question**, and binding only if the wetland is ever
simulated (it is not, see below).

## Which extensive roof is which

The deployed script reads `ICON_forecast['QEx2']` for EGR1 and
`ICON_forecast['QEx2_EGR2']` for EGR2 — both `QEx2`-flavoured, the second merely
suffixed. Those are treated as mislabels on the modelled series, not as column
references. EGR1 is the **irrigated** roof:

- it is the one with a valve (`Ventil_Extensivdach1`, pin 36; EGR2 has none and
  is advisory only);
- `sensordata.py` calls `Extensiv1` the "irrigated (smart algorithm) extensive
  green roof", and `swc.ROOF_SWC_COLUMNS` maps `QEx1 → irrigated_extensive`;
- the thresholds settle it. Converting GR2L's measured wilting points
  (`gr2l_client.ROOF_PRESETS`) to %θ gives `irrigated_extensive`
  3.3 mm / 70 mm = **4.7 %θ** against EGR1's 5.0 trigger, and
  `non_irrigated_extensive` 0.9 mm / 70 mm = **1.3 %θ** against EGR2's 4.0. The
  reverse assignment would put the irrigated roof's trigger *below* its own
  wilting point, where it could never fire in time.

## The rule

A fixed priority ladder — never below wilting point, then minimize expected
runoff, then pre-heat-day cooling. No LLM, no weights. Constants come from
`rules_constants.py` and are mirrored verbatim into the ops manual.

For a substrate roof, over the forecast:

| # | Condition | Decision | Reason code |
|---|---|---|---|
| 1 | min SWC over 48 h ≤ wilting | **irrigate** | `below_wilting_point` |
| 2 | max temperature over 48 h < 24 °C | no | `no_heat_no_stress` |
| 3 | min SWC over 48 h > dry threshold | no | `sufficient_moisture` |
| 4 | outflow expected within 168 h | no | `refill_forecast` |
| 5 | otherwise | **irrigate** | `cooling_requested` |

Degenerate inputs return `no_forecast` or `missing_values` and do not irrigate.

**The wetland is not simulated.** Its rule reads the current lysimeter level
against a fixed minimum and ignores temperature and forecast entirely
(`low_water_level` / `sufficient_water_level`). The extraction computes a
wetland bucket run and then never uses it — dead code, dropped here. This also
means the tare question above never reaches a decision.

**Window offsets are load-bearing.** SWC and temperature are read over
`[0 : decision_horizon]`, *including* the seed step; outflow over
`[1 : outflow_horizon]`, *excluding* it, because the seed step's outflow is an
initialisation artifact (see *The balance*, property 3).

**Reason codes, not prose.** The deployed script carries the German
recommendation strings inline, duplicated once per roof. Here the ladder returns
a code and the DE/EN text is a rendering table shared with the ops manual, so
the wording and the logic cannot drift.

**One ladder, two entry points.** The "will it refill?" conjunct reaches the
rule as a single `will_reach_capacity` boolean, computed either from the
modelled outflow series (`max > 0.01 mm`) or, on the stated-value path, from a
rain total against the deficit to capacity. The comparison chain itself exists
exactly once.

## Horizons and step

The decision horizon (48 h) and the refill horizon (168 h) are **authored in
hours** and converted to row counts by the series' step, so one implementation
serves the site's hourly ICON forcing and this system's daily rows.

At daily resolution the heat test reads `max(tx)` over two days rather than the
hourly maximum of `temperature_2m` over 48 hours. That is the more faithful
quantity — `tx` is a measured daily maximum, where the hourly series is a mean
per hour — but it is a deviation, not an identity.

## ET0 is computed, not fetched

The deployed script takes `et0_fao_evapotranspiration` from the ICON forecast.
This system cannot: `DailyWeatherRow` carries no `et0` field and its schema is
frozen (architecture §3.3), and the station source (D26) has no ET0 to give
either. `et_fao56.py` therefore computes FAO-56 Penman-Monteith ET0 locally.

Two choices worth stating:

- **Albedo 0.23**, the FAO-56 reference-crop value, not the roof's own. Roof
  albedo belongs to the stress coefficient's job, not the ET0 term's, and 0.23
  is what makes the result comparable to the ICON figure the controller used.
- The implementation is a **verbatim port of GR2L's R block**, including its
  fixed `Pressure = 100 kPa` where textbook FAO-56 derives pressure
  barometrically. At this site's 142 m that is under 1 % of ET0. Carrying the
  simplification keeps the Python and R implementations numerically identical,
  which is what makes the canary check in D30 meaningful; the deviation from
  textbook FAO-56 is a stated scope limit.

## Deviations from the deployed controller

Every one of these is deliberate, and together they mean this tool's answer can
differ from what the roof's own controller did on a given day.

1. **Millimetres** (the unit fix), rescaling response to rain by `100 / SH_mm`
   per roof (D31).
2. **Daily step** here; the site runs hourly. `max(tx)` over 2 days replaces the
   hourly maximum of `temperature_2m` over 48 h.
3. **ET0 computed** via FAO-56 rather than taken from ICON/Open-Meteo.
4. **Seed** = the latest trustworthy reading at or before
   `min(window_start, as_of)` (D5), not the mean over every sensor and every row
   of the measurement CSV as the extraction does.
5. **Flat 22 %θ capacity retained** for all three substrate roofs (D31), though
   GR2L measures `semi_intensive`'s `Ssubmax` at 45.6 mm.
6. **Wetland not simulated** for the decision.

## What is dropped as out of scope

`ValveController`, `irrigate`, `VALVE_PINS`, the GPIO import and the `time.sleep`
schedule; `main()` and argparse; and the CSV layer (`read_roof_state`,
`read_forecast`, `_column_mean`, `_weight_mean`, `data_age_hours`,
`SENSOR_COLUMNS`, `MISSING_VALUE`, `MAX_AGE_*`).

The data-freshness gate the CSV layer implemented is already covered:
`SwcSeed.is_stale` flags a seed older than `STALE_AFTER_DAYS = 7` and the answer
discloses it.

A site adapter that still needs the CSV path can keep it as a thin reader over
`irrigation.simulate_store` / `irrigation_decision` — the core takes series and
parameters, not files.

## The R endpoint

The same model ships to the weinbau API as `POST /predict_greenroof_swb` on the
existing `gr2l_model` container, for consumers outside this system. **The agent
never calls it** (D30): the bucket and the rule run locally in Python, which is
what makes irrigation cases fully offline.

All thresholds arrive in the request; no site policy is baked into R, so
`rules_constants.py` stays the single source of truth. The two implementations
are kept honest by a committed canary request/response hash alongside GR2L's,
marked non-evaluation because no case's answer depends on it. See
`docs/greenroof_swb_tool.md` in the API repository for the endpoint contract.

## Open questions for the site

- The p90-of-historical-ET dose figures (D22). `rules_constants.py` carries the
  deployed valve minutes (30 / 30 / 31) meanwhile.
- The wetland's irrigation duration: the deployed script prints 60 minutes but
  sleeps 30 + 1.
- Whether the L6 lysimeter reading is water mass or gross weight including tare.

## Reference implementation

- `assistant/irrigation.py` — bucket, feature extraction, ladder
- `assistant/rules_constants.py` — thresholds, horizons, doses
- `assistant/tools/roofs.py` — roof identity, geometry, aliases
- `assistant/et_fao56.py` — ET0
- `assistant/tools/irrigation.py` — the agent-facing wrapper
