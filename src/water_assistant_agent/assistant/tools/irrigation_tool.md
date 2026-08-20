# Smart Irrigation — Decision Tool

Companion to [`gr2l_tool.md`](./gr2l_tool.md) and
[`weather_tool.md`](./weather_tool.md). The agent-facing surface is specified in
architecture §3.5; this document holds the physics, the parameter derivation and
the deviations from the deployed controller.

Source of truth for the algorithm: the UFZ green-roof controller
(`~/Downloads/smart_irrigation.py`, an extraction of the site's deployed
`temp/smart_irrigation.py`). Actuation — magnetic valves, GPIO, irrigation
timing — is out of scope here; this tool answers *whether* to irrigate.

That file lives outside any repository, so the port's fidelity to it is pinned
rather than asserted: `scripts/capture_irrigation_golden.py` ran the controller
once over three windows of the site's own record and committed its four output
series and its decisions as `tests/assistant/fixtures/irrigation_golden.json`.
The port reproduces them to 3.6e-15 — the last bit of the same algebra written
over %θ instead of fractions — and the fixture was captured **before** the unit
fix below, which is what makes every difference the fix produces attributable to
the fix.

---

## This is not GR2L, and the two must not be merged

Both models are single-site green-roof water balances driven by daily or hourly
weather. They are nonetheless different models, and `decisions.md`
§ The irrigation calculator keeps them separate.

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

```text
ks[i]      = clip((S[i-1] − S_r) / (S_fc − S_r), 0, 1)

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

The extraction's `ks = 1.0` arm (`water_limited=False`) is **not** carried: it is
the open-water throttle, and it belongs to the wetland's bucket run, which leaves
scope with the roof. A parameter no call site can set is a branch nothing tests.

## Units: millimetres internally, the site's own units at the surface

The extraction adds millimetres of rain and ET to a store held in **%VWC**:

```python
store[i] = store[i - 1] + precipitation[i] - et_actual[i]   # %VWC + mm - mm
```

Correcting this is not cosmetic. Converting the store to millimetres rescales
each roof's response to a millimetre of rain by `100 / SH_mm` — 1.43 %θ per mm on
the 7 cm extensive roofs and 0.67 on the 15 cm semi-intensive, against a flat
1.0 for every roof in the deployed balance — so the fix changes what the model
predicts, and with it some decisions; `decisions.md` § The irrigation calculator
keeps the deployed thresholds anyway and measures the difference (*The decisions
this moves*, below); it does not re-derive them.

**Millimetres are what this tool runs.** The deployed regime stays reachable —
`irrigation.Regime` is an argument, not a repaired constant — because the two
arms are what the decision-diff replay compares.

Constants are **authored in the unit the site states them** and converted once,
through `swc.theta_pct_to_mm`:

| Roof | site id | `SH` | wilting | dry | capacity |
|---|---|---|---|---|---|
| `irrigated_extensive` | EGR1 | 7 cm | 5.0 %θ → **3.5 mm** | 10.0 %θ → **7.0 mm** | 22.0 %θ → **15.4 mm** |
| `non_irrigated_extensive` | EGR2 | 7 cm | 4.0 %θ → **2.8 mm** | 10.0 %θ → **7.0 mm** | 22.0 %θ → **15.4 mm** |
| `semi_intensive` | IGR | 15 cm | 10.0 %θ → **15.0 mm** | 16.0 %θ → **24.0 mm** | 22.0 %θ → **33.0 mm** |

Residual: 2.5 %θ → 1.75 mm (7 cm) / 3.75 mm (15 cm). The wetland has no row: it
is out of scope, see *The rule*.

The conversion **removes** a constant rather than adding one. The extraction
carries `SWC_CAPACITY = 22.0` (%VWC) and `THETA_FIELD_CAPACITY = 0.22`
(fraction) as separate module constants; they are one quantity written twice in
two units, serving the overflow threshold and the stress-coefficient
denominator. In millimetres they collapse into a single `capacity_mm`.

## The decisions this moves — and it is a list, not an estimate

Because the thresholds are not re-tuned, the unit fix has to be *disclosed*
instead. `scripts/irrigation_decision_diff.py` replays every day of the
catalog's `as_of` band through both unit regimes — same station rows, same
`et_fao56` ET0, same measured seeds, same trigger levels, the store's unit the
only variable — and commits every flip to
`specs/agent_architecture/irrigation_decision_diff.md`. The list is the
deliverable, not the run.

**19 of 930 decisions flip, 2.0 %**, over 2025-06-01 → 2026-04-24 on the three
substrate roofs: the millimetre balance waters on 8 the deployed one does not
and declines on 11 it does. The dominant pair is `cooling_requested` giving way
to `refill_forecast` — 8 of the 19, all on the extensive roofs, where rain the
deployed balance did not think would refill a shallow roof now does. The
semi-intensive roof moves the other way at 0.67×, three flips of
`refill_forecast → cooling_requested`. Deep roofs become harder to move and
shallow ones easier: the physics the fix restores, seen in decisions.

Both arms are reported in %θ, where the two regimes' thresholds are the same
numbers, so a flip is always a trajectory and never a threshold that moved. That
table is the bound on what this tool's answers say about the deployed system
(architecture §8), and re-tuning against it is the site's call.

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
A gap is not a dry roof.

The order is the rule, not an implementation detail: rung 1 fires *whatever*
rungs 2–4 would say, which is why the tests feed each rung a window that
satisfies every rung below it as well. Both boundaries are reachable — a store
sits exactly on a threshold whenever the seed does — and both are the
controller's way round: `≤` at the wilting point, `>` at the dry threshold.

**The wetland is out of scope, and the tool returns `not_available` for it.**
This rule is built for a classical substrate roof — a soil store read in %θ,
against wilting and dry thresholds — not for a ponded fleece mat. The deployed
controller decides the wetland from an L6 lysimeter level in kg
(`low_water_level` / `sufficient_water_level`); that branch, its constants and
the extraction's unused wetland bucket run are all dropped here. The wetland
therefore joins the gravel roof in `NON_MODELLABLE_ROOFS` (architecture §3.5),
which now bounds this tool and GR2L alike.

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

The two paths do differ in one thing worth stating in an answer: the modelled
one spends part of the forecast rain on evaporation before the roof fills, while
the stated one takes the rain at face value, so a stated refill is an upper
bound.

## The tool surface

`calc_irrigation(roof_type, soil_moisture_pct=None, max_temperature_c=None,
forecast_precip_mm=None)` — architecture §3.5's signature, with no date
arguments, because the question is about now: the window is the **refill
horizon from `ctx.as_of`**, seven daily rows, sized by the rule's own constant
rather than by a literal week.

It returns `irrigate`, the `reason` code, `inputs` (`modelled` | `stated`), the
`features` the rung turned on, the `dose` — `valve_minutes`, and `dose_mm` null
until the site supplies it — plus the `seed` and `weather_source` a modelled run
disclosed. **Never a computed volume.**

**The three stated arguments are all-or-none.** Supplying them makes the call
pure: no database, no weather, no simulation. Supplying some of them is an
`invalid_argument` naming the rest, because a half-measured, half-assumed
decision is one no disclosure could describe.

`not_available` triggers: the gravel roof, the wetland, and a window with no
trustworthy soil-moisture reading to seed from.

**"Fully offline" is a claim about the calculator, not about the forcing.** The
bucket, the ladder and the ET0 are local Python, so nothing on the answer path
is cached or canaried and no `upstream` class exists for a model service. A
*modelled* call still fetches its forcing, and since its window opens at the
case's own `as_of` and runs forward, the station source — read through the
as-of view — cannot cover it: that fetch falls to Archive whole and replays from
one committed weather entry. A stated-value call is the shape that replays with
**zero** cache entries.

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
frozen (architecture §3.3), and the station source has no ET0 to give
either. `et_fao56.py` therefore computes FAO-56 Penman-Monteith ET0 locally.

Two choices worth stating:

- **Albedo 0.23**, the FAO-56 reference-crop value, not the roof's own. Roof
  albedo belongs to the stress coefficient's job, not the ET0 term's, and 0.23
  is what makes the result comparable to the ICON figure the controller used.
- The implementation is a **verbatim port of GR2L's R block**, including the four
  places that routine departs from FAO-56: the fixed `Pressure = 100 kPa`
  (under 1 % of ET0 at this site's 142 m), an `R_a` that omits the solar constant
  and runs ~12× large, a 238 where eq. 11 has 237.3, and fourth powers taken in
  °C where eq. 39 uses kelvin. Together they make this ET0 **1.17× to 1.38× a
  textbook FAO-56** (`findings.md` § External sources on this machine), and none
  is repaired. Carrying them keeps this ET0 numerically identical to the one GR2L
  computes, so the two water-balance tools cannot disagree about evaporative
  demand for a reason no case is asking about — and the site's trigger levels
  were tuned against this convention's ET, so a "correct" ET0 here would silently
  retune the controller. The deviation from textbook FAO-56 is a stated scope
  limit (architecture §8).

## Deviations from the deployed controller

Every one of these is deliberate, and together they mean this tool's answer can
differ from what the roof's own controller did on a given day.

1. **Millimetres** (the unit fix), rescaling response to rain by `100 / SH_mm`
   per roof (`decisions.md` § The irrigation calculator). Measured: 19 of 930
   decisions over the `as_of` band, listed date by date in
   *The decisions this moves*.
2. **Daily step** here; the site runs hourly. `max(tx)` over 2 days replaces the
   hourly maximum of `temperature_2m` over 48 h.
3. **ET0 computed** via FAO-56 rather than taken from ICON/Open-Meteo.
4. **Seed** = the latest trustworthy reading at or before
   `min(window_start, as_of)` (architecture §3.4), not the mean over every
   sensor and every row of the measurement CSV as the extraction does.
5. **Flat 22 %θ capacity retained** for all three substrate roofs
   (`decisions.md` § The irrigation calculator), though GR2L measures
   `semi_intensive`'s `Ssubmax` at 45.6 mm.
6. **Wetland out of scope** — the deployed controller decides it from a lysimeter
   level; this tool returns `not_available` instead.

## What is dropped as out of scope

`ValveController`, `irrigate`, `VALVE_PINS`, the GPIO import and the `time.sleep`
schedule; `main()` and argparse; and the CSV layer (`read_roof_state`,
`read_forecast`, `_column_mean`, `_weight_mean`, `data_age_hours`,
`SENSOR_COLUMNS`, `MISSING_VALUE`, `MAX_AGE_*`).

With the wetland: `WGR_CAPACITY`, `WGR_MIN_WEIGHT`, `decide_wetland_roof`, and
`_simulate_store`'s `water_limited=False` arm.

`first_outflow_date` is dropped too, and for a reason of its own: it scans from
index 0 against `> 0`, so it can name the seed step's initialisation artifact as
the day the roof fills. The refill day is read off the refill window instead, and
it is disclosure — never part of the decision.

The data-freshness gate the CSV layer implemented is already covered:
`SwcSeed.is_stale` flags a seed older than `STALE_AFTER_DAYS = 7` and the answer
discloses it.

A site adapter that still needs the CSV path can keep it as a thin reader over
`irrigation.simulate_store` / `irrigation_decision` — the core takes series and
parameters, not files.

## The R endpoint — a deliverable of the API repository, not pending work here

The same model ships to the weinbau API as `POST /predict_greenroof_swb` on the
existing `gr2l_model` container, for consumers outside this system. **The agent
never calls it** (`decisions.md` § The irrigation calculator): the bucket and the
rule run locally in Python, which is what makes the calculator offline.

All thresholds arrive in the request; no site policy is baked into R, so
`rules_constants.py` stays the single source of truth. The endpoint is a
deliverable of the API repository and is built and kept in step with
`irrigation.py` there, not here: this repo carries no conformance check against
it, because no case's answer depends on it (`decisions.md` § The irrigation
calculator). See `docs/greenroof_swb_tool.md` in the API repository for the
endpoint contract.

## Open questions for the site

- The p90-of-historical-ET dose figures (`decisions.md` § The irrigation
  calculator). `rules_constants.py` carries the deployed valve minutes
  (30 / 30 / 31) meanwhile.
- The two wetland questions — the irrigation duration (the deployed script prints
  60 minutes but sleeps 30 + 1) and whether the L6 reading is water mass or gross
  weight including tare — are **parked**, not answered: they bind only if the
  wetland is ever brought back into scope.

## Reference implementation

- `assistant/irrigation.py` — bucket, unit regime, feature extraction, ladder
- `assistant/rules_constants.py` — thresholds, horizons, doses
- `assistant/tools/roofs.py` — roof identity, geometry, aliases
- `assistant/et_fao56.py` — ET0
- `assistant/tools/irrigation.py` — the agent-facing wrapper

And the two artifacts that are evidence rather than code, each committed beside
the script that emitted it:

- `capture_irrigation_golden.py` → `tests/assistant/fixtures/irrigation_golden.json`
  — the deployed controller's own series, what the port is checked against
- `irrigation_decision_diff.py` → `specs/agent_architecture/irrigation_decision_diff.md`
  — every decision the unit fix moves
