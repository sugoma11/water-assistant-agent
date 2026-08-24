# Question template catalog — green-roof water management assistant

Human-readable spec for the evaluation dataset. Machine-readable twins live in `eval/templates/*.yaml`;
instantiated cases in `eval/cases/{train,test_seen,test_unseen}.json`, emitted per architecture §6.1.
Oracles in `eval/oracles/`. **27 template families, 32 entries** counting a/b variants, 7 of them
holdout; **281 instances** (100 / 125 / 56), derived in §1.7.

---

## 1 Conventions

### 1.1 Answer contract

Every case's `A:` field instantiates the four-field JSON contract in architecture §2. Family H
answers `null` on every variant, and its scored surface is the agent-supplied half of the plot spec
(architecture §7) — plus `status`, which is `answered` where a plot is deliverable and
`not_available` on T24a's non-modellable variant, where the abstention metric scores it on the same
terms as any other unanswerable case.

### 1.2 Scoring

Scored per architecture §7. Its per-template inputs are the `A:`, `Traj:`, `Must-not:`, `Cards:` and
tolerance fields below, copied into each case at generation.

### 1.3 Time semantics

Time semantics per architecture §5; every case carries `as_of`, and every relative expression in a
question resolves against it.

### 1.4 Tool vocabulary

`Traj:` and `Must-not:` name the **registered** tools — the six strings a root-agent trajectory can
contain:

| Name | What it is |
|---|---|
| `text_to_sql_agent` | frozen text2SQL sub-agent, reached as an `AgentTool` (architecture §3.1) |
| `get_weather_forecast_tool` | daily weather, station or Archive, resolved in code (§3.3) |
| `predict_green_roof_water_balance_tool` | GR2L water balance, self-contained — fetches its own weather and seed (§3.4) |
| `lookup_reference` | exact card lookup, `topic` enum in the signature (§3.2) |
| `calc_irrigation` | irrigation decision over its own bucket model (§3.5) |
| `plot_timeseries` | declarative multi-source plot spec; returns spec + stats, no series (§3.6) |

`query_database_tool` is the sub-agent's *inner* tool, never appears in a root-agent trajectory, and
no template may name it.

### 1.5 Answer units

Soil-moisture states and every `initial_soil_moisture_pct` are in **%θ**; no template asks for
millimetres of substrate storage. Retention and runoff stay in **mm** (fluxes) or **L** (lysimeter
volumes); a difference between two %θ states answers in **pp**. **`L` and `mm` coincide
numerically** on the lysimeter columns — the collection area is 1 m², so 1 L = 1 mm
(`findings.md`) — so the answer metric normalizes one to the other rather than scoring a litre
answer against a millimetre oracle as wrong (architecture §7).

### 1.6 Generation filters

- **Validity — input data**: three predicates over the `(table, column, window)` set each template
  declares, evaluated **through the as-of view** so a check never passes on data the tool cannot see,
  and anchored at `seed_at = min(window_start, as_of)` for seed-bearing families rather than at
  `as_of` — a retrospective window seeds at its own start, not at the case's present. Failing any
  predicate resamples the params; it never drops the template, so §1.7's `templates × m` holds.
  - **Coverage** — the resolved window's rows are present: a point query needs its day at ≥44 of 48
    rows, a period aggregate ≥95 % of expected rows with no single gap over 24 h. Two whole-system
    lysimeter outages remove 39 band days from `outflow`, `swc` and `tsoil` together, and only 52.5 %
    of band days carry a fully covered 30-day look-back (`findings.md`). Without this an aggregate
    silently returns a November total drawn from two days of data, which the oracle then encodes as
    truth.
  - **Plausibility** — every column read stays inside its per-column physical bounds, which live in
    `roofs.py` beside the rest of each roof's identity (architecture §1 principle 4). This is what
    excludes the dead `QWetland` record from 2026-03-12. Bounds are **per column, never global**: the
    gravel roof has no substrate and legitimately sits at a band median of 0.06 %θ.
  - **Frozenness** — no run of ≥24 identical consecutive samples (12 h) in a **state** column. Zero
    false positives on every healthy state sensor in the record, and it catches the one `QWetland`
    episode frozen at a plausible 77.160 %θ that the bounds miss. Applied to **state columns only**,
    and not to `QGravel`: a flux resting at zero is reporting the truth, and `Sumpf2_Efflux`
    legitimately holds 0.000 for 3474 consecutive hours.
- **Validity — oracle**: discard where the oracle admits more than one defensible reading, or where
  the answer sits within tolerance of its own threshold.
- **Balance**: rejection-sample dates until each bool template is ~50/50 within each split. T04 is
  the binding case — daily outflow is exactly zero on 75–94 % of band days depending on roof, and the
  wetland has 17 non-zero days in the whole band — so wet days need roughly 6:1 oversampling and the
  wet-day pool is what §1.7's train ∩ test_seen = ∅ has to split. Reachable at m = 4/5 with roof drawn
  across P1, but it is the tightest date pool in the catalog. **Constant-value rejection is not a
  validity test**: it would delete every dry day, which is precisely the "no" class this rule needs
  (`findings.md`).
- **Abstention share**: **13.0 % of train, 12.8 % of test_seen, 28.6 % of test_unseen, 16.0 %
  overall** — what the five-template abstention set (T17a, T17b, T18a, T18b, T27) produces at §1.7's
  m, plus the two instances T24a's non-modellable variant contributes, stated rather than steered. An
  earlier draft targeted 7–10 %; hitting a band would take a per-template m, which makes n
  unverifiable against `templates × m` and is what §1.7's derivation rule exists to prevent. The
  holdout's share is fixed by its list — two of its seven templates are abstentions — and is not a
  sampling choice at all.
- **Paraphrases**: EN + DE, 50/50 within each split, style pools disjoint between train and test,
  colloquial German included.
- **Route cue — the documentary reference**: a question that names the documentation ("per the
  manual", "as the operations manual defines it", "the manual's target"), or that asks what a
  documented value *is*, routes to `lookup_reference`; a question asking only for a decision or a
  measured number about the site routes to the tool that computes it. This is the convention that
  separates T16a from T16b — identical inputs, symmetric must-nots, so **phrasing is the only
  discriminator** — and it is why T07 and T11 name no manual against a `calc_irrigation` gold set.
  It binds paraphrase generation as hard as it binds authoring: **a paraphrase may neither add nor
  remove a documentary reference**, in either language, and one that does is a defect in the
  paraphrase rather than a hard case.
- **Roof vocabulary**: questions name roofs in the agent's own vocabulary (`non_irrigated_extensive`,
  …) or in natural language the semantic layer's alias map covers ("das Kiesdach", "KD", "the roof
  without irrigation") — **never raw column names** (`Extensiv1`, `Sumpf2`, `QGravel`).

### 1.7 Splits, sizing and generation constraints

Three splits: **train**, **test_seen**, **test_unseen**. Train's number is a selection score, not a
training accuracy; the reporting rules over these splits are architecture §7's.

**Template ledger.** 32 entries = 7 holdout (T16b, T17b, T18b, T20, T22, T23, T26) + 25 carried by
both train and test_seen. So train holds **25** templates, test_seen **25**, test_unseen **7**. T12
sits in both since the pinned record carries 11 qualifying rain events, enough for disjoint train and
test_seen event sets (§2's T12 entry, [`t12_rain_events.md`](./t12_rain_events.md)).

**Sizing.** Counts are derived from `templates × m`, never chosen: errors cluster by template, so
m ≈ 5 captures most of the achievable trajectory power and further instances asymptote.

| Split | Templates | m | n |
|---|---|---|---|
| train | 25 | 4 | **100** |
| test_seen | 25 | 5 | **125** |
| test_unseen | 7 | 8 | **56** |

`25×4 = 100`, `25×5 = 125`, `7×8 = 56`, total **281**. Train's m is a flat 4 rather than a 3–4 band
because the balance rule needs an even m ≥ 4 on every bool template and 8 of train's 25 are bool; a
per-template m makes n unverifiable against the product.

**Category shares are outputs of that product, not targets**, and this table **is** the suite's
stated composition. Each template contributes 9 instances where it is carried by train and
test_seen, 8 where it is holdout.

| Category | Families | Instances | Share |
|---|---|---|---|
| Pure SQL | A | 54 | 19.2 % |
| Pure lookup (incl. abstention) | B | 26 | 9.3 % |
| Pure weather (incl. abstention) | C | 44 | 15.7 % |
| Model chains (incl. availability abstention) | D + I | 36 | 12.8 % |
| Hybrid / full chain | E | 53 | 18.9 % |
| Given-values controls | F | 17 | 6.0 % |
| Counterfactual | G | 33 | 11.7 % |
| Presentation (plot) | H | 18 | 6.4 % |

Sum 281, 100.0 %. An earlier draft named eight target shares (28 / 12 / 10 / 12 / 20 / 4 / 8 / 6),
set before the template set settled; they are **retired, not approximated** — the derived shares miss
them by up to 8.8 pp, and steering to them would take a per-template m, which contradicts the
derivation rule above. Composition is therefore verified against the product, and a share moves only
when a template is authored or retired.

**Abstention lands at 45/281 = 16.0 %** (T17a 9, T17b 8, T18a 9, T18b 8, T27 9, T24a's non-modellable
variant 2): train 13/100 = 13.0 %, test_seen 16/125 = 12.8 %, test_unseen 16/56 = 28.6 %, the last
fixed by the holdout list rather than sampled. §1.6 states those as the suite's shares. T24a's two
are the only abstentions the ledger does not read off a whole template — they are a stratified slice
of one template's `variant` axis (§2's T24a entry), which is why the count is stated per instance
here and the template ledger above is untouched.

**Generation constraints**, enforced in generation and not achieved by independent resampling:

- **Per-param disjointness, per discrete value.** train ∩ test_seen = ∅ on every sampled param —
  T02/T09/T13's `thr`, T01/T05's `month`, T12's `event`, T21/T22's override values, T27's window and
  alias. Without it the memorized-constant detector fails open.
- **The `variant` axis is shared, on the same footing as roof.** T24a's and T27's variants are
  distinct probes rather than values of one quantity, so both splits carry all of them and the rule
  above does not reach them: a disjoint variant axis would move a whole probe into one split, which
  is what the holdout list is for. Each template's variant mix is stratified within a split, stated
  in its §2 entry, and never resampled.
- **`as_of` is a striped partition, never a cut point.** Bands are disjoint but interleaved over
  ~2025-06-01 → 2026-04-24; a contiguous split strands summer on one side and makes T02/T08/T20
  unbalanceable.
- **Roof is a shared axis, deliberately.** The §1.8 pools are 3–5 wide and confounded with
  modellability; disjoint pools would confound roof generalization with tool availability.
- **Language balanced 50/50 within each split** and reported as a stratum: DE routing accuracy is a
  measured quantity, not sampling noise.
- **Paraphrase style pools disjoint** between train and test.

Each holdout template is the b-side of a train-side pair moving one named axis: T16b←T16a,
T17b←T17a, T18b←T18a, T20←T08, T22←T21, T23←T21, T26←T21+T06+T09/T10.

### 1.8 Roof sampling pools

`{roof}` is drawn per family, because the water-balance tools cover fewer roofs than the database.
Both exclusions are architecture §3.4's: the gravel roof and the wetland sit in
`NON_MODELLABLE_ROOFS`, outside GR2L's scope **and** outside `calc_irrigation`'s — the gravel roof
has no substrate store and the wetland is a ponded fleece mat whose θ sensor saturates near 86 %, so
neither is the classical substrate roof both water balances are built for.

Instrumentation narrows the roster a second time, independently of modellability: the
**semi-intensive roof has no lysimeter and no radiation mast** (`findings.md`), so `outflow` and
`radiation` carry four roofs where `swc` and `tsoil` carry five. A template reading an outflow
column cannot sample it at all.

| Pool | Members | Sampled by |
|---|---|---|
| **P1** — full roster | gravel · irrigated ext. · non-irrigated ext. · semi-intensive · wetland | H(i)'s `measured` pair on its `swc` draw; any A or B template naming a roof on `swc` or `tsoil` |
| **P1f** — flux-instrumented subset of P1 | gravel · irrigated ext. · non-irrigated ext. · wetland | A (T01, T04, T05), E (T12); H(i)'s `measured` pair on its `outflow` draw |
| **P2** — substrate roofs: modellable and irrigation-decidable | irrigated ext. · non-irrigated ext. · semi-intensive | D (T09, T10, T19), E (T07, T11), F (T16b), G (T21–T23, T26), H(ii)'s `model` overlay |

**P1f is a data fact, not a scope limit, and no template probes it.** The semi-intensive roof is
absent from the pool because the column does not exist, not because the system declines to answer;
nothing in the suite rewards noticing the difference, and the abstention families stay the ones
§1.6 lists. A case asking for that roof's outflow would have no oracle, which is why the pool
excludes it at generation rather than the filter rejecting it afterwards.

**One pool serves both tools because their scopes coincide** — `NON_MODELLABLE_ROOFS` bounds the
GR2L chain and the irrigation decision alike, so no roof abstains under one tool and answers under
the other. `plot_timeseries` adds no third scope: its `model` series routes through `run_gr2l`, so
architecture §3.6's `not_available` trigger is the same membership test, which is why a `model`
series draws P2 while a `measured` series for the same roof stays valid.

Family H splits across all three pools, per sampled variant rather than per template — the pair
variant draws P1 on a state column and P1f on a flux column, the overlay variant draws P2, and each
row above names the variant that reaches it. A plot is only as modellable as its most demanding
series, and only as widely instrumented as its narrowest one, and the H rows now record which
variant that binds on. Before T24a's series spec was sampled, all three rows were notional: the
template fixed both roofs and drew one `measured` `swc` series, so no case reached P1f or P2 through
family H at all (plan.md § 8, Q4).

**Family I samples the two excluded roofs, from outside this table**, and so does **H's
non-modellable variant**: the pools govern answerable cases, and the point of both is the roofs they
exclude. The two probe different tools — I puts the excluded roofs in front of the model and the
calculator, H(iii) in front of the plot tool's own trigger — which is why neither pool row absorbs
them.

---

## 2 Catalog

Legend — **Q** question sketch · **DE** example German paraphrase · **A** answer type (unit,
tolerance) · **Traj** gold trajectory · **Must-not** listed distractors · **Cards** gold reference
cards (architecture §3.2) · **Oracle** logic · **Note** purpose · **Split** `train+seen` |
`unseen` — the two the ledger admits, since T007 retired the train-only entry.

### A. Pure SQL — negative controls for `lookup_reference` / `get_weather_forecast_tool`

**T01 — total outflow**
Q: "What was the total outflow of the {roof} roof in {month}?" · DE: "Wie viel Wasser ist im Juli vom
Kiesdach abgeflossen?" · roof ∈ P1f
A: numeric (L, ±2 % rel) · Traj: {text_to_sql_agent} · Must-not: `lookup_reference` · Split: train+seen
Oracle: trusted SUM over the roof's efflux column.
Note: train's only distractor slot for `lookup_reference` — the `topic` enum carries nothing that
could answer a monthly outflow total.

**T02 — hot-day count**
Q: "On how many days in {period} did the max air temperature exceed {thr} °C?"
A: numeric (count, exact) · Traj: {text_to_sql_agent} · Split: train+seen
Oracle: COUNT on the station's daily max air temperature.

**T03 — irrigation SWC gap**
Q: "What was the mean soil-moisture difference between the irrigated and non-irrigated extensive roof
over {period}?"
A: numeric (pp, ±0.2 abs) · Traj: {text_to_sql_agent} · Split: train+seen
Oracle: mean difference of the two roofs' θ columns.

**T04 — outflow occurred?**
Q: "Did the {roof} roof produce any outflow on {date}?" · roof ∈ P1f
A: bool (balanced) · Traj: {text_to_sql_agent} · Must-not:
`predict_green_roof_water_balance_tool` · Split: train+seen
Oracle: daily SUM > 0.
Note: the model tool's only train-side distractor slot — a recorded past fact for which a simulation
is the epistemically wrong source.

**T05 — peak-outflow day**
Q: "On which day in {month} did the {roof} roof have its highest outflow?" · roof ∈ P1f
A: date (exact) · Traj: {text_to_sql_agent} · Split: train+seen · Oracle: argmax of daily sums.

**T15a — past rain**
Q: "How much rain fell in {past_period}?" · DE: "Wie viel hat es letzte Woche geregnet?"
· `{past_period}` written `YYYY-MM-DD..YYYY-MM-DD`, ending ≤ `as_of`
A: numeric (mm, ±2 % rel) · Traj: {text_to_sql_agent} · Split: train+seen
Oracle: SUM over the station rain column.
Note: tense minimal pair with T15b, scored as a pure-SQL positive — the station weather path derives
its `precip` from this same column, so a weather-tool route returns the identical number.

### B. Pure reference lookup

**T06 — stated constant**
Q: "What is the soil-moisture threshold for irrigating the extensive roofs?"
A: numeric (%θ, exact) · Traj: {lookup_reference} · Cards: `irrigation_threshold` · Split: train+seen
Oracle: the constant in `rules_constants.py`, to which the card's `values:` block is test-bound
(architecture §3.2).

**T17a — absent constant (abstention)**
Q: "What is the maximum wind speed at which irrigation must be shut off?"
A: not_available · Traj: {lookup_reference} · Cards: `irrigation_rule` · Split: train+seen
Note: the absence is grounded inside a card, not in the topic vocabulary — a wind-shutoff condition
would sit in the `irrigation_rule` ladder beside the moisture, heat and refill conjuncts, so the
agent must fetch that card and find no wind clause. `Cards` is the card that should have carried it,
so card recall stays scored.

**T17b — scope near-miss (abstention)**
Q: "What is the soil-moisture irrigation threshold for the **wetland** roof?"
A: not_available · Traj: {lookup_reference} · Cards: `irrigation_threshold` · Split: unseen
Note: the card returns whole — three substrate roofs under `values:`, the wetland under
`not_applicable:` — so the agent must read the exclusion with a confidently worded, wrong-scope rule
beside it. Strongest hallucination probe.

### C. Pure weather

**T13 — rain expected?**
Q: "Is more than {thr} mm of rain expected in the next {d} days?"
A: bool (balanced) · Traj: {get_weather_forecast_tool} · Split: train+seen
Oracle: sum of `precip` over the resolved forward window, fetched through `ctx.weather` so the window
resolves to the source the tool sees — never `fetch_daily_weather` directly, which is Archive-only and
bypasses source resolution (architecture §3.3).
Note: **asks in days for T09's reason, and it is the same defect.** This entry said "the next {h}
hours" until T110. Weather rows are daily, so an hourly horizon has to become a day count somewhere,
and wherever that happens it is a rule the candidate has never been told — which is exactly what
T107's pilot measured on T09, where the oracle read "72 hours" as three days and a candidate writing
explicit dates read it as four. The horizon is now the number the question names, passed straight
into `forecast_days`.

**T14 — forecast max temperature**
Q: "What is the highest temperature forecast for the next {d} days?"
A: numeric (°C, ±0.1 abs) · Traj: {get_weather_forecast_tool} · Split: train+seen
Oracle: max of `tx` over the resolved window, same fetch path.

**T15b — future rain**
Q: "How much rain will fall in the next {d} days?" · DE: "Wie viel soll es in den nächsten sieben
Tagen regnen?"
A: numeric (mm, ±2 % rel) · Traj: {get_weather_forecast_tool} · Must-not: `text_to_sql_agent`
· Split: train+seen
Oracle: sum of `precip` over the resolved future window.
Note: the surviving half of the tense probe — the database holds no future, so the must-not binds in
this direction only. **The horizon is a forward day count, not a `{future_period}` range**, and both
halves of that are forced. A future window written `start..end` names days past the case's own
`as_of`, so `period_param_within_as_of` rejects every instance of it (T104) — the same reason T18a
carries `ahead_days` as a count. Days rather than hours is T13's reason, which is T09's. The
paraphrase constraint that follows is T112's to hold: a paraphrase may restate the window but not
**redenote** it, so "in den nächsten sieben Tagen" is `d = 7` and "nächste Woche" is not — a calendar
week starting Monday is a different window from the seven days beginning today, and the oracle
resolves the parameter rather than the prose.

**T18a — unservable window (abstention)**
Q: "What will the temperature be in four weeks?"
A: not_available · Traj: {get_weather_forecast_tool} · Split: train+seen
Note: a well-formed window beyond the 16-day horizon, the tool's single scope limit; the tool types
the `not_available` and the agent relays it as the contract status. Distinct from T15 misrouting.

**T18b — missing variable (abstention)**
Q: "What is the forecast {variable} for the next {d} days?" · `{variable}` a quantity the daily row
does not carry (soil temperature, soil moisture, air pressure, …); `{d}` inside the 16-day horizon
A: not_available · Traj: {} (empty) · Split: unseen
Note: the tool takes no variable argument and always returns the seven documented fields, so the
abstention is the agent's alone, grounded in the docstring's variable list; it tests transfer from
T18a's tool-signalled abstention to one with no tool signal.
**The oracle grounds it in `DailyWeatherRow` and not in that docstring**, which is candidate-owned
and therefore cannot be ground truth for anything: a candidate that deleted the variable list would
otherwise move the answer. The row's seven fields and their frozen descriptions are what the draw is
checked against, and a `{variable}` matching any of them is refused as a template fault rather than
recorded as an abstention — a false abstention encoded as truth is the one error here that no
metric could catch. `{d}` stays inside the horizon on purpose: past it the *tool* would abstain, and
the case would be T18a wearing T18b's words.

### D. Model chains

**T09 — threshold crossing ahead**
Q: "Will the soil moisture of the {roof} roof fall below {thr} %θ over the next {d} days?"
· roof ∈ P2
A: bool (balanced) · Traj: {predict_green_roof_water_balance_tool} · Split: train+seen
Oracle: `run_gr2l` over the resolved window, seeded per architecture §3.4's `min(window_start,
as_of)` rule; min `swc_pct` < thr.
Note: **days, not hours, and that is a T107 finding rather than a preference.** Phrased in hours,
the horizon had to be converted to GR2L's daily rows somewhere, and the conversion was a rule the
candidate was never told: the oracle read "the next 72 hours" as three days while the pilot's
candidate, writing the window as explicit dates, read it as four. Neither reading is wrong, which
is precisely the problem — the disagreement is invisible except on a draw whose minimum falls
between the two windows. In days the candidate passes the number the question names straight into
`forecast_days`, and there is nothing left to infer. **T10 and T19 carried the same ambiguity and are
repaired here** (T110): T10's "the next 72 h" is T09's defect verbatim, and T19's "last week" is its
backward twin — a calendar week beginning Monday is a different set of days from the seven ending
yesterday, which is the redenotation `decisions.md` § Forward horizons are counted in days already
forbids a paraphrase.

**T10 — predicted minimum**
Q: "What is the minimum soil moisture predicted for the {roof} roof over the next {d} days?"
· roof ∈ P2
A: numeric (%θ, ±0.1 abs) · Traj: {predict_green_roof_water_balance_tool} · Split: train+seen
Oracle: the same chain, returning the summary's `min_swc_pct`.
Note: T09's number without T09's comparison, off one run and one `min_swc_pct`. The pair is why both
share an oracle helper: a candidate that reports the minimum correctly and compares it wrongly fails
T09 and passes T10, and two oracles with two notions of "the minimum" could not tell those apart.
Answered in **%θ, never in millimetres of storage** (§1.5) — GR2L holds the store in mm and the tool
converts once.

**T19 — retrospective model check**
Q: "How far off was the soil-moisture model for the {roof} roof over the last {d} days, on average?"
· roof ∈ P2; the window is *d* complete past days ending yesterday, so it ends ≤ `as_of` by
construction
A: numeric (pp, ±0.1 abs) · Traj:
{predict_green_roof_water_balance_tool(evaluate_against_measured=True)} · Must-not:
`get_weather_forecast_tool` · Split: train+seen
Oracle: the same call, reading mean |predicted − measured| from the summary.
Note: past-facing model use; the must-not binds on the disclosure the tool contract mandates — the
green-roof tool fetches its own weather. The **only retrospective GR2L window in the catalog**, and
therefore the only model template whose forcing resolves to the station and whose pins carry
`station_derivation` — every forward window falls to the Archive whole, measured under family C.

### E. Hybrid / full chain

**T07 — irrigation check, current state**
Q: "Does the {roof} roof need irrigation right now?" · DE: "Muss das unbewässerte Extensivdach heute
bewässert werden?" · roof ∈ P2
A: bool (balanced) · Traj: {calc_irrigation} · Cards: [] · Split: train+seen
Oracle: `irrigation_decision` on the measured seed plus a 48 h lookahead.
Note: measured-now twin of T11's predictive decision. The phrase "according to the operations manual"
is **deleted, not reworded**: it named the documentation against a `calc_irrigation` gold set, which
is §1.6's route cue pointing the other way, and it collided with T16a's docs probe. What is left is
an operational question with no values supplied — the shape the self-contained calculator answers,
and the shape the deployed assistant is actually asked.

**T08 — heatwave days (manual definition)**
Q: "How many heatwave days, as defined in the operations manual, occurred in {period}?"
A: numeric (count, exact) · Traj: {lookup_reference, text_to_sql_agent} · Cards:
`heatwave_definition` · Split: train+seen
Oracle: definition constants → count on the station daily record. The consecutive-day rule is
authored eval policy in `rules_constants.py`; the deployed controller carries only a heat threshold.
Note: **`{period}` and not `{month}`, forced by the oracle's own boundary guard** (T110). A heatwave
run crossing the edge of the window makes the count two-valued — the days the window contains against
the days the record marks — which is §1.6's "more than one defensible reading" and is discarded. Over
calendar months the record offers 10 and refuses 3 for exactly that, short of §1.7's 9 disjoint
draws, with six survivors answering zero; sampled 14-day periods accept 287 of 315 and 82 of those
answer non-zero (`findings.md`). It also makes T08 the card-grounded twin of T02, which counts its own
threshold over its own `{period}`. Also note the two conventions differ on purpose: the card says
"reaches or exceeds", so 24.0 °C counts here, where T02's "exceed" is strict.

**T11 — irrigation decision tomorrow**
Q: "Does the {roof} roof need irrigation tomorrow?" · roof ∈ P2
A: bool (balanced) · Traj: {calc_irrigation} · Split: train+seen
Oracle: `irrigation_decision` over the calculator's own modelled features.
Note: predictive twin of T07; the dose, where the answer is yes, is the fixed per-roof constant
stated for disclosure, not a computed volume. "Per the standard rule" is dropped for T07's reason —
same gold set, same route cue.

**T12 — retention vs target**
Q: "Was the retention of the {roof} roof during {event} above the manual's target?" · roof ∈ P1f
A: bool (balanced) · Traj: {lookup_reference, text_to_sql_agent} · Cards: `retention_target`
· Split: train+seen
Oracle: (rain − outflow) / rain against the target, itself authored eval policy in
`rules_constants.py`. **No area factor** — the lysimeter collects 1 m², so outflow in litres is
already millimetres (`findings.md`).
Note: `{event}` is written as the event's **window** — `YYYY-MM-DD..YYYY-MM-DD`, drainage day
included — so the case file carries the days the answer was computed over and
`period_param_within_as_of` sees both of its ends. It draws from the **11 qualifying rain events** the
pinned record carries: a maximal run of wet Europe/Berlin days plus one drainage day, ≥ 10 mm deep,
with `outflow` coverage per §1.6 and a retention inside [0, 1] — the full table is
[`t12_rain_events.md`](./t12_rain_events.md), regenerated by `scripts/count_t12_rain_events.py`, whose
`Window` column is the parameter. The drainage tail is applied when the events are enumerated and not
again by the oracle, which answers over the days it is given. Eleven covers 4 + 5 with two events to spare, so train and
test_seen hold **disjoint** event sets and T12 is no longer train-only; 8 of the 11 carry both classes
across their roofs at any target from 50 % to 70 %, which is the headroom the balance rule needs on
either side.

**T20 — forecast heatwave per manual**
Q: "Does the forecast for the next {d} days qualify as a heatwave under the manual's definition?"
A: bool (balanced) · Traj: {lookup_reference, get_weather_forecast_tool} · Must-not:
`text_to_sql_agent` · Cards: `heatwave_definition` · Split: unseen
Oracle: the card's definition applied to the resolved forward window's daily rows, as in T13.
Note: the only hybrid excluding the database and the holdout's only hybrid chain; parent T08 teaches
the same card-grounded definition against the database. **"The coming week" is repaired to `{d}` days**
(T110): three consecutive days is the whole rule, so a calendar week beginning Monday and the seven
days beginning today can differ in the answer, and that is the redenotation `decisions.md § Forward
horizons are counted in days` forbids. T08's boundary guard applies at the far edge — the oracle
fetches `d + 2` days to see whether a run at the end continues, and refuses the draw where the two
readings differ — but not at the near edge: yesterday is not forecast, so a spell already under way
cannot make the coming days qualify.

**T25 — past/future bridge**
Q: "Will tomorrow be warmer than yesterday?"
A: bool (balanced) · Split: train+seen · Traj: **either** {text_to_sql_agent,
get_weather_forecast_tool} **or** {get_weather_forecast_tool} alone
Oracle: yesterday's max air temperature against tomorrow's `tx`, each from the source its window
resolves to, **and refused where the other gold route would answer differently**.
Note: the docstring recommends the single combined call for exactly this shape, so both routes are
gold. Yesterday arrives from the station and tomorrow from Archive with a measured station warm bias
between them, so balanced sampling must correct for it or route both sides to one source. The bias is
not only a sampling nuisance: source resolution is whole-window, so the combined call falls to the
Archive *for both days* and reads a different yesterday from the one the two-call route reads —
measured at 13.20 °C against the station's 12.57 °C at `as_of` 2026-04-20 (`findings.md`). Where that
gap flips the comparison, the case would score a candidate wrong for taking a route this entry calls
correct, so the oracle computes both readings and discards the draw when they disagree (§1.6).

### F. Given-values controls

**T16a — rule applied to stated values**
Q: "The {roof} roof is at {x} %θ, {tmax} °C is expected over the next 48 h and {y} mm of rain over
the coming week — **what does the operations manual say**, should we irrigate?" · roof ∈ P2
A: bool (balanced) · Traj: {lookup_reference} · Must-not: `text_to_sql_agent`,
`get_weather_forecast_tool`, `calc_irrigation` · Cards: `irrigation_rule`, `irrigation_threshold`
· Split: train+seen · Oracle: the rule on the stated values.
Note: catches reflexive fetches for values already given, and carries three of train's six distractor
slots. The two cards must between them state the ladder *and* the per-roof numbers, or the docs half
of the probe is unanswerable by construction (architecture §3.2). The documentary reference is the
whole cue (§1.6) and is now explicit rather than a trailing "per the manual", since it is what makes
the `calc_irrigation` must-not fair against T16b's identical inputs.
**The roof is named here, and it had to be** (T110). The earlier sketch fixed 12 %θ and 2 mm and named
no roof, but the trigger levels are per segment — the `irrigation_threshold` card says so in as many
words — and at exactly those values the two extensive roofs answer *no* while the semi-intensive
answers *yes*, because its dry threshold is 16 %θ against their 10 %θ. Across a plausible grid the
three roofs disagree on **27 %** of draws, over the whole 4.5–16.0 %θ span (`findings.md`). A question
with no roof in it therefore had three answers, and the oracle would have had to pick one. Naming the
roof also makes the pair's claim exact rather than approximate: T16a and T16b now supply the *same*
parameters and differ in one thing, which is the phrasing §1.6 says is the discriminator.

**T16b — calculator isolation**
Q: "The {roof} roof is at {x} %θ, {tmax} °C is expected over the next 48 h and {y} mm of rain over
the coming week — should we irrigate?" · roof ∈ P2; stated values are soil moisture in %θ, the
maximum air temperature over the **decision** horizon and the rain total over the **refill** horizon,
per architecture §3.5
Note on the horizons: the earlier sketch called `{y}` "48 h forecast rain", which is the wrong
window for it — the stated rain fills the refill conjunct, which the ladder reads over 168 h, and
48 h is the decision horizon the stated *temperature* is read over (`irrigation_tool.md` § The rule).
Both sides always passed the same number, so nothing was scored wrongly; the question described it as
something the rule does not treat it as. `{tmax}` is also written out rather than left implicit,
because the three stated arguments are all-or-none: two of them is an `invalid_argument`, not a
partial answer.
A: bool (balanced) · Traj: {calc_irrigation} · Must-not: `lookup_reference` · Split: unseen
Oracle: `calc_irrigation` on the stated values.
Note: identical inputs to T16a with symmetric must-nots, so the probe binds in both directions —
T16a trains the docs direction, T16b tests transfer to the calculator direction, which T07 and T11
still teach in train. "Does the standard rule say to irrigate" is dropped: it read as a documentary
reference and therefore cued T16a's route against T16b's gold set. What separates the two is now one
thing only, and it is the thing §1.6 states — T16a asks what the manual says, T16b asks for a
decision.

### G. Counterfactuals

**T21 — forcing override**
Q: "If {mm} mm of rain falls on day {offset} of the next {d} days, what is the minimum soil moisture
of the {roof} roof over that window?" · roof ∈ P2
A: numeric (%θ, ±0.1 abs) · Traj: {predict_green_roof_water_balance_tool(forcings=…)}
· Split: train+seen
Oracle: `run_gr2l` with `precip` overridden on the named day, sparse per architecture §3.4.
Note: a prior weather fetch is a free extra call — fetch-then-substitute is a valid route. The forced
day is a **`{offset}` counting today as 0** rather than a date, so it cannot fall outside a window
sampled independently of it. **The override reaches the model, measured on the answer**: 50 mm forced
onto one day moves the three-day minimum from 12.33 to 13.99 %θ and books 48.1 mm of runoff to the
forced day (`findings.md`), so GR2L computed the counterfactual rather than the wrapper adjusting a
baseline — which is the property the whole family probes.

**T22 — parameter override**
Q: "Under the current forecast but with albedo {a}, what soil moisture is predicted for the {roof}
roof tomorrow?" · roof ∈ P2
A: numeric (%θ, ±0.1 abs) · Traj: {predict_green_roof_water_balance_tool(albedo=…)} · Split: unseen
Oracle: the same call with the flat `albedo` scalar set.
Note: **not materializable against the GR2L build serving this deployment, and the oracle refuses
rather than emitting a baseline** (T110). The service accepts `albedo` and ignores it — six values
from 0.0 to 1.0 return one byte-identical response, where our own port of the same R convention spans
10.03 mm to 2.83 mm of ET0 over that range (`findings.md`). Every answer would therefore equal the
un-overridden prediction, and a candidate that never passed the argument would score full marks on
the answer metric. The refusal is computed per draw against the roof's own default, so T22 begins
materializing unchanged the day the service wires the parameter up; **until then this template
contributes no instances, and §1.7's holdout ledger is short by one template.** Its trajectory claim
— that the agent issues a plausible `albedo` — is unaffected and needs no answer, but nothing scores
it while the template emits nothing.

**T23 — state override**
Q: "If the {roof} roof's soil moisture had been {x} %θ {d} days ago, where would it be now?"
· roof ∈ P2
A: numeric (%θ, ±0.1 abs) · Traj:
{predict_green_roof_water_balance_tool(initial_soil_moisture_pct=…)} · Must-not:
`get_weather_forecast_tool` · Split: unseen
Oracle: the same call over the window since that day, **refusing a window whose store has saturated**.
Note: "last Monday" is repaired to `{d}` days for the reason `decisions.md § Forward horizons are
counted in days` gives — a named weekday denotes a different date depending on when it is read. The
saturation guard is the second validity condition that entry now carries: GR2L's memory of an initial
condition is finite and its length depends on the weather, so a 5 %θ and a 20 %θ seed land on the same
last day from `d = 2` at a wet April `as_of`, from `d = 7` over a dry August window, and still differ
by 6.29 pp at `d = 10` in October (`findings.md`). There is no safe `{d}` to write here, so the oracle
probes each draw.

**T26 — compositional**
Q variants: (i) "If albedo were {a} **and** 30 mm fell tomorrow, would the {roof} roof stay above the
irrigation threshold?" — adds `lookup_reference` to Traj and `irrigation_threshold` to Cards;
(ii) override plus cross-roof comparison; (iii) a variant composing only train-taught axes
(`forcings` plus cross-roof comparison, no `albedo`). roof ∈ P2.
A: bool / numeric · Traj: union of the composed calls, argument-checked · Split: unseen
Note: the compositional-generalization headline, with train-side parents T21, T06 and T09/T10.
Variant (i) composes `albedo`, whose axis is itself holdout, so it is interpretable only where T22
passes and is reported conditionally; variant (iii) keeps the headline off double transfer. **Since
T22 does not pass against the current service build** — the model ignores `albedo` — (i) is
answerable and *half inert*: the rain moves its answer and the albedo cannot, so it composes one live
axis with one dead one. It is not refused, because the case still probes composing a forcing with a
card lookup, but **(iii) is the variant the headline should rest on** meanwhile. The comparison
variants run both roofs over one window with one overlay, so the roof is the only thing that differs,
and a tie is refused rather than broken.

### H. Presentation intent

**T24a — plot request**
Q variants, sampled; each fixes the *shape* of the series spec, not the wording alone:
(i) **measured pair** — "Show me how the soil moisture of the {roof_a} and the {roof_b} developed in
{month}." On the flux draw: "Show me how much water ran off the {roof_a} and the {roof_b} in
{month}." Two `measured` series, one variable, the roofs a set match.
(ii) **model overlay** — "Plot the measured soil moisture of the {roof} roof against the model's
prediction for {month}." One `measured` `swc` series and one `model` `swc_pct` series, same roof,
same window.
(iii) **non-modellable overlay** — (ii)'s request for a roof outside the model's scope: "Plot the
{alias}'s measured soil moisture against the model's prediction for {month}."
DE: "Zeig mir den Bodenfeuchteverlauf der beiden Extensivdächer im Juli."
Params: variant; `{roof_a},{roof_b}` an unordered pair of distinct roofs; `{roof}`; `{alias}` ∈ the
semantic layer's gravel and wetland aliases (§1.6, T27's map); (i)'s table ∈ {`swc`, `outflow`}, the
column following from the roof through §3.6's closed vocabulary, while (ii) and (iii) are fixed to
measured `swc` against the model's `swc_pct`; `{month}`, a completed calendar month ending ≤ `as_of`
on every variant, since the `measured` half must exist and a month sits inside architecture §3.3's
31-day cap so the truncation flag stays clear. Pools: (i) **P1** on `swc`, **P1f** on `outflow`;
(ii) **P2**; (iii) gravel and wetland, deliberately outside §1.8's pools.
**Variant mix, stratified within each split and not resampled**: train 2/1/1, test_seen 2/2/1 over
(i)/(ii)/(iii). m stays §1.7's 4 and 5, so the ledger and the 18-instance family total are unchanged.
(i)'s two instances per split take one `swc` pair and one `outflow` pair, and the `swc` pair includes
the gravel roof or the wetland.
A: (i) and (ii) null with status `answered` (artifact deliverable); (iii) not_available (answer null,
unit null). Scored through `argument_checks` on the agent-supplied half of the spec — series source,
variable and roof as a set match, plus resolved `start`/`end` — and on (ii) also the `model` series'
modelling arguments, `present` rather than `eq` per architecture §7. Aggregation, unit, axis and
`kind` are **not** scored: §3.6 derives the first three in code from the variable and §7 fixes the
scored surface to the rest, so `model_overlay` against `line` discriminates no candidate.
Traj: {plot_timeseries} · Must-not: `text_to_sql_agent` · Cards: [] · Split: train+seen
Oracle: **one oracle, and what it materializes is `status` rather than an answer.** The answer is
null on all three variants and the answer metric is skipped on all three, so there is no number for
an oracle to compute — but (i) and (ii) are `answered` where (iii) is `not_available`, and §6.1 gives
`status` no channel but the oracle's, so something has to decide which per instance. It decides by
building the series the request denotes and running them through §3.6's own trigger
(`prepare_series`): a `model` series for the gravel roof or the wetland raises the tool's typed
`not_available`, which the agent relays as the contract status. The declared variant is then checked
**against** that outcome rather than trusted, because a mislabelled draw is silent in both directions
— a (ii) on a non-modellable roof records an abstention as answerable, a (iii) on a modellable one
records an answerable case as an abstention, and the second is invisible to the very metric that
measures false abstention. Nothing is fetched on any variant, so (iii) costs no cache entry.
Note: the tool fetches its own data; rendering is an unscored side effect. The earlier form fixed
both roofs and drew a single `measured` `swc` series, which left §1.8's H pools notional and the
model path, the mixed-resolution rule and the plot tool's `not_available` unreached by any case
(plan.md § 8, Q4). What the three variants add, in order:

- **(ii) is the suite's only `model` series**, and the only place §3.6's mixed-resolution rule binds:
  `swc` is half-hourly and GR2L daily, so the measured half aggregates to calendar days
  (Europe/Berlin) under the state variable's derived `mean`, and the resolved spec reports the
  resolution. Unscored, diffed as a diagnostic. It is T19's presentation twin — same retrospective
  measured-against-modelled comparison, reached by a presentation verb instead of a scalar question.
- **(iii) is not a second T27.** T27 probes modellability as *family-dependent* across the model tool
  and the calculator, which a sampling variant inside H cannot test and this one does not attempt.
  (iii) probes §3.6's own `not_available` trigger, which no other template reaches, and its gold set
  is `plot_timeseries`. Naming the model side is what keeps the oracle single-valued under §1.6:
  dropping the `model` series and plotting the measured half alone does not answer the request, so
  there is no second defensible reading in which the case is answerable.
- **(i) is (iii)'s counter-probe.** A `measured` series for the gravel roof or the wetland stays
  valid (§3.6), so pinning one of them into each split's `swc` pair charges a candidate that
  generalized "gravel ⇒ `not_available`" through the false-abstention rate. Inside one family and one
  tool, the two roofs are then separated by the series' **source**, which is the distinction §3.6
  actually draws and the one no other template puts in front of a candidate.
- The **`outflow` draw** is what makes §1.8's P1f row real for H, and it exercises the other derived
  operator: a flux sums where a state averages, on the same closed vocabulary.
Must-not stays `text_to_sql_agent` on every variant, on the tool's own docstring ground. The model
tool is deliberately **not** listed on (ii): §3's margin paragraph places its unauthored second slot
on T05, and listing it here would settle that as a side effect of a sampling change rather than as
the decision that paragraph asks for.

**T24b — twin without plot verb**
Q: "What was the mean soil-moisture difference between the two extensive roofs in {month}?"
A: numeric (pp, ±0.2 abs — T03's convention) · Traj: {text_to_sql_agent} · Must-not:
`plot_timeseries` · Split: train+seen
Note: near-identical information need to T24a's **measured-pair variant**, differing only in the
presentation verb; the difference wording keeps the answer a single scalar inside the answer
contract. T24b's pair stays fixed where T24a's is sampled, because a scalar gap needs a comparable
pair — a mean difference between the gravel roof's 0.06 %θ and the wetland's near-saturated fleece
is arithmetic without an information need, while plotting the same two is merely a dull plot. The
twin therefore pairs the two *shapes*, a two-roof measured comparison against its scalar, not two
identical parameter draws, and `decisions.md § The answer contract`'s "twin plots both roofs" holds
on every variant-(i) instance.

### I. Modelling availability — non-modellable roofs (abstention)

**T27 — modelling request for a non-modellable roof (abstention)**
Q variants, all naming a roof outside both water-balance tools' scope:
(i) "What is the minimum soil moisture predicted for the {alias} over the next {d} days?" —
Traj: {predict_green_roof_water_balance_tool} · Must-not: `text_to_sql_agent`
(ii) "Does the {alias} need irrigation tomorrow?" — Traj: {calc_irrigation}
(iii) "If {mm} mm fell tomorrow, what would the {alias}'s minimum soil moisture be over the next
{d} days?" — Traj: {predict_green_roof_water_balance_tool(forcings=…)} · Must-not: `text_to_sql_agent`
DE: "Wie feucht wird das Kiesdach morgen sein?"
Params: alias ∈ the spellings that **actually reach the scope limit** — `gravel`, `gravel_roof`,
`kies`, `kiesdach`, `KD`, `QGravel` and `wetland`, `wetland_roof`, `sumpf`, `sumpfdach`, `Sumpf2`,
`QWetland`, matched case-insensitively; window; variant.
**The pool is the intersection of two vocabularies that ought to be one, and T110 measured the gap.**
`roofs.py`'s alias map and `NON_MODELLABLE_ROOFS` — the table both tools match an argument against —
disagree in two ways. `SD` resolves to the wetland in the alias map and is **absent** from the scope
table, so a candidate passing that spelling gets `invalid_argument` where the gold expectation is
`not_available`; and multi-word natural language resolves in neither, because `normalize_roof_type`
only strips and lowercases — so `das Kiesdach`, **named verbatim in this entry's own earlier params
line**, and `the gravel roof`, §1.6's own example, both fail. The catalog now states the pool as
spellings rather than as roofs, and the oracle reads it off the two tables rather than carrying a
list (`findings.md`). Note the question *text* may still say "das Kiesdach" — that is a paraphrase
concern, and what the pool constrains is the `{alias}` parameter.
**The horizon on (i) and (iii) is `{d}` days**, repaired here from "the next `{h}` hours" and "the
next 48 h" — the last two entries carrying the phrasing T09, T13 and T15b were repaired out of, and
the ones P7a1 handed to this packet. It moves no answer, since an abstention is an abstention
whatever the window is, which is exactly why it would have survived: this is the one family where
the defect is invisible to its own oracle and would have reached a candidate as an unspecified
hours-to-days conversion. Roof pool: **gravel and wetland**, deliberately outside
§1.8's pools. **Every variant samples both roofs** — `calc_irrigation` excludes the wetland on the
same terms as GR2L (architecture §3.5), so variant (ii) is an abstention case for either roof.
A: not_available (unit null) · Cards: [] · Split: train+seen
Oracle: `NON_MODELLABLE_ROOFS` membership — the tool returns its typed `not_available` (architecture
§3.4 for i/iii, §3.5 for ii) and the agent relays it as the contract status. There is no numeric
oracle; the oracle asserts the outcome.
Note: the gravel roof is the most-sampled roof in family A, so modellability has to be learned as
**family-dependent**, which a sampling variant inside family H cannot test. The `text_to_sql_agent`
must-not binds on T15b's ground — the database holds no future, so a last measured value is not a
prediction — and variant (ii) reaches the same limit through a second tool, making the limit a
property of the roof rather than of one tool. Both roofs behave identically under every
water-balance route, so family dependence carries the whole probe: A, B and H's `measured` series
answer them from measured data, and every route that reaches a model abstains — I directly, through
the model tool and the calculator, and T24a(iii) through the plot tool's `model` series. That third
route does not dilute this template's probe and is not a second copy of it: it varies the *source*
inside one family and one tool, where T27 varies the *family* while holding the roof and the
modelling intent fixed, which is the thing a sampling variant inside H cannot test.
**No template splits one roof across two tools** — the gravel roof and the wetland are denied by
every modelling route and admitted by every measured one, T24a's two variants included — so a
per-tool scope limit is outside what the suite measures (architecture §8).

---

## 3 Tool coverage matrix

Derived from §2. A distractor claim appears only where a template names the tool in its `Must-not:`
line; train-side slots are **bold**.

| Tool | Sole necessary | In chain | Critical distractor (listed must-nots) |
|---|---|---|---|
| `lookup_reference` | T06, T16a, T17a, T17b | T08, T12, T20, T26(i) | **T01**, T16b |
| `text_to_sql_agent` | T01–T05, T15a, T24b | T08, T12, T25 (one accepted route) | **T15b**, **T16a**, **T24a**, **T27(i,iii)**, T20 |
| `get_weather_forecast_tool` | T13, T14, T15b, T18a | T20, T25 | **T16a**, **T19**, T23 |
| `predict_green_roof_water_balance_tool` | T09, T10, T19, T21, T22, T23, T27(i,iii) | T26 | **T04** |
| `calc_irrigation` | T07, T11, T16b, T27(ii) | — | **T16a** |
| `plot_timeseries` | T24a | — | **T24b** |

**Invariant 1 — only listed must-nots score.** Trajectory is binary over the gold set and the listed
must-not set alone; unlisted calls are free. The former blanket claim that every A/C/D/G-non-doc/H
template implicitly forbade the docs route is deleted, not restated: it scored nothing.

**Invariant 2 — every tool holds a distractor slot inside train.** Checked against the holdout list
(T16b, T17b, T18b, T20, T22, T23, T26): no column is empty of train templates. This is the binary
metric's only defence against a shotgun candidate, since extra calls cost nothing.

**Margin — audited, and accepted rather than closed.** Invariant 2 holds, but thinly: counting the
train-side must-not slots per column,

| Tool | Train-side slots | Templates |
|---|---:|---|
| `text_to_sql_agent` | 4 | T15b, T16a, T24a, T27(i,iii) |
| `get_weather_forecast_tool` | 2 | T16a, T19 |
| `lookup_reference` | 1 | T01 |
| `predict_green_roof_water_balance_tool` | 1 | T04 |
| `calc_irrigation` | 1 | T16a |
| `plot_timeseries` | 1 | T24b |

Four columns rest on a single template each, and **T16a alone supplies three of them** — so those
three move together with whatever happens to T16a. Moving any of T01, T04, T16a or T24b to holdout
empties a column and voids Invariant 2 outright.

What the margin still buys is the claim it exists for. Eight of train's 25 templates carry a
must-not (T01, T04, T15b, T16a, T19, T24a, T24b, T27), and the same eight carry one in test_seen,
against 3 of 7 in test_unseen (T16b, T20, T23) — so a call-everything candidate hard-fails **32 % of
train trajectory** and cannot recover it anywhere, which is what
`decisions.md § Trajectory scoring and routing probes` needs. What a one-deep column does **not**
support is a per-tool claim: a candidate that reflexively looks up a card before answering loses
exactly one train template, which is a weak signal about that one habit rather than about routing.

Accepted at that reading, with the second slots named so authoring them stays a decision rather than
a discovery — none of them changes §1.7's sizing, since a must-not is a field on an existing
template: `lookup_reference` on T03 (no card carries a measured aggregate), the model tool on T05
(T04's own ground — a recorded past fact for which a simulation is the wrong source),
`plot_timeseries` on T03 or T05 (T24b's presentation-verb ground). `calc_irrigation` has **no clean
second slot**: every other train template either carries the calculator in its gold set or would
punish an extra call that costs the answer nothing, which is the design's own rule against listing
it. That asymmetry is the reason the margin is accepted here rather than legislated away.

`get_weather_forecast_tool`'s standalone identifiability rests on T13, T14, T15b and T18a; T18b's
gold set is deliberately empty. The model tool is sole-necessary across families D and G because it
is self-contained — `text_to_sql_agent` and `get_weather_forecast_tool` are not in those gold sets,
so a candidate that skips them loses nothing and one that calls them pays nothing.

---

## 4 Open

Nothing. The six items this section carried are settled and each is written where it binds, not
here: the suite's composition and abstention share in §1.6 and §1.7, T12's event basis in §2 and
[`t12_rain_events.md`](./t12_rain_events.md), the distractor margin in §3, and the T07 / T16a / T16b
phrasing in §1.6's route cue. One residual is deliberately *not* closed and lives in
`decisions.md § Trajectory scoring and routing probes` as an accepted risk: a candidate that looks
the rule up before calculating still fails T16b, and the cue makes that defensible behaviour
distinguishable rather than impossible.

New items belong here only while their resolution is scheduled; anything settled moves into the
section it governs.
