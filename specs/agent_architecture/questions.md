# Question template catalog — green-roof water management assistant

Human-readable spec for the evaluation dataset. Machine-readable twins live in
`eval/templates/*.yaml`; instantiated cases in `eval/cases/*.jsonl`. Oracles in
`eval/oracles/`. 26 template families (31 entries incl. a/b variants),
target ~250–300 instances.

---

## 1 Conventions

### 1.1 Answer contract
Every agent run must end with:

```json
{"status": "answered" | "not_available",
 "answer": <bool | number | "YYYY-MM-DD" | null>,
 "unit": "<L | mm | °C | pp | % | count | null>",
 "explanation": "<free text, unscored in phase 1>"}
```

Phase 1 scores `status` + `answer` (after unit normalization). Phase 2 adds an
LLM judge over `explanation`, anchored to oracle facts — additive, no refactor.

**Artifact deliverables (family H) answer `null`.** The plot is scored from the
resolved spec echoed by `plot_timeseries` in the tool result, never from the
final message: requiring the model to retype source, variable, range and
aggregation into `answer` measures transcription rather than tool selection —
the same objection D14 raises against passing series data *into* the tool.
The answer component is therefore **skipped** for these templates and the
scorer reports answer-metric *coverage* alongside the score; `null` must never
be compared against an oracle answer and scored 0.

**Clarifying questions are out of contract.** Both `status` values end the turn,
and the §1.6 validity filter discards cases whose oracle is ambiguous, so no
instance should warrant one. The candidate instruction forbids them explicitly
(architecture D15). A final message parsing to neither status value is a
`parse_failure` diagnostic, not a wrong answer.

**Tool `error` has no contract status.** Tools emit `success` | `not_available`
| `error`; the agent contract carries only the first two outcomes. Harness-side,
errors split by cause (architecture D16): an **`upstream`** error marks the case
`harness_error` — excluded from every aggregate, counted separately per arm. An
**`invalid_argument`** error never excludes: the case stays in and scores
through the normal metrics (an unrecovered fumble surfaces as a wrong answer,
false abstention, or `parse_failure`).

### 1.2 Scoring
- **Answer**: exact for bool/date/count/not_available; numeric within
  per-template tolerance. Oracles share code with the tool chain, so tolerances
  absorb rounding only — keep them tight.
- **Trajectory**: **binary per case** (architecture D21) — 1 iff every tool in
  `Traj` was called, no `Must-not` tool was called, and the argument checks
  pass where specified; else 0. No partial credit, no extra-call penalty:
  calls beyond the gold set are free and reported only as a diagnostic (mean
  extra calls per arm). `Must-not` = trajectory 0 if called (only where it is
  the point of the template). There is no `(opt)` marker — under a zero
  penalty, optional and unlisted are the same thing.
- **Retrieval**: recall@k vs `Docs`, reported twice — with gold queries
  (retriever quality) and agent-generated queries (system quality).
- **Abstention**: two separate numbers — abstention accuracy on unanswerable
  cases; false-abstention rate on answerable cases. Never aggregate.

### 1.3 Time semantics
Each case carries `as_of` (Europe/Berlin). DB is served through as-of views
(`timestamp <= as_of`). Relative expressions ("yesterday", "übermorgen")
resolve against `as_of`. Calendar day = Europe/Berlin. Forecast horizon 16 d.

### 1.4 Weather fixtures
One file per `as_of`: `eval/fixtures/weather/{as_of}.json`. Frozen hourly
schema — must cover **all** soil-model inputs: `time, precip_mm, temp_c,
rh_pct, wind_ms, swrad_wm2`. Fixtures are forecasts *as issued at* `as_of`
(archived model runs), not measured outcomes.

### 1.5 Tools & the pure-function refactor
`search_docs`, `query_database` (text2SQL sub-agent), `get_weather` (replayed),
`predict_soil_moisture(meteo_series, initial_swc, roof, params=None)`,
`calc_irrigation`, `plot_timeseries` (declarative spec, internal fixed query,
echoes resolved spec).
**Note**: since `predict_soil_moisture` is a pure function, all predictive
chains include a DB read for the initial SWC state — trajectories below
reflect this (supersedes earlier `[weather, model]` sketches).

### 1.6 Generation filters
- **Validity**: discard sampled params where sensor gaps make the oracle
  ambiguous (e.g. no non-null SWC within 24 h of `as_of`).
- **Balance**: rejection-sample dates until each bool template is ~50/50.
- **Abstention share**: 7–10% of all instances.
- **Paraphrases**: EN + DE; style pools disjoint between train and test;
  include alias-heavy phrasings ("das Kiesdach", "the roof without
  irrigation") and colloquial German.

### 1.7 Splits & mix
train ~80 / val ~50 / test_seen ~70 (same templates, unseen params + unseen
paraphrase styles) / test_unseen ~50 (holdout templates only:
**T17b, T18b, T22, T23, T26**). Note the pairing: T17a/T18a train on one
abstention variant, T17b/T18b test generalization to the other.

| Category | share |
|---|---|
| Pure SQL | 28% |
| Pure doc (incl. abstention) | 12% |
| Pure weather (incl. abstention) | 10% |
| Model chains | 12% |
| Hybrid / full chain | 20% |
| Given-values controls | 4% |
| Counterfactual | 8% |
| Presentation (plot) | 6% |

---

## 2 Catalog

Field legend — **Q**: question sketch · **DE**: example German paraphrase ·
**A**: answer type (unit, tolerance) · **Traj**: gold trajectory ·
**Docs**: gold sections · **Oracle**: logic · **Note**: purpose · **Split**.

### A. Pure SQL — negative controls for `search_docs` / `get_weather`

**T01 — total outflow**
Q: "What was the total outflow of the {roof} roof in {month}?"
DE: "Wie viel Wasser ist im Juli vom Kiesdach abgeflossen?"
Params: roof ∈ {Kies, Extensiv1, Extensiv2, Sumpf2}; month.
A: numeric (L, ±2% rel) · Traj: {query_database}
Oracle: trusted SUM over the efflux column.

**T02 — hot-day count**
Q: "On how many days in {period} did the max air temperature exceed {thr} °C?"
A: numeric (count, exact) · Traj: {query_database}
Oracle: COUNT on daily `wetter.Tmax`.

**T03 — irrigation SWC gap**
Q: "What was the mean soil-moisture difference between the irrigated and
non-irrigated extensive roof over {period}?"
A: numeric (pp, ±0.2 abs) · Traj: {query_database}
Oracle: AVG(QEx1 − QEx2).

**T04 — outflow occurred?**
Q: "Did the {roof} roof produce any outflow on {date}?"
A: bool · Traj: {query_database}
· Must-not: predict_green_roof_water_balance_tool
Oracle: daily SUM > 0. Balanced sampling.
Note: the model tool's one distractor slot (D24) — a recorded past fact for
which a simulation is the epistemically wrong source.

**T05 — peak-outflow day**
Q: "On which day in {month} did the {roof} roof have its highest outflow?"
A: date (exact) · Traj: {query_database} · Oracle: argmax of daily sums.

**T15a — past rain (minimal-pair partner of T15b)**
Q: "How much rain fell in {past_period}?"
DE: "Wie viel hat es letzte Woche geregnet?"
A: numeric (mm, ±2% rel) · Traj: {query_database} · Must-not: get_weather
Oracle: SUM over `wetter.Rain`.
Note: only tense differs from T15b — temporal routing probe.

### B. Pure document (RAG)

**T06 — stated constant**
Q: "What is the soil-moisture threshold for irrigating the extensive roofs?"
A: numeric (%, exact) · Traj: {search_docs} · Docs: ops_manual#irrigation_rule
Oracle: constant from `rules_constants.py`.

**T17a — absent constant (abstention)**
Q: "What is the maximum wind speed at which irrigation must be shut off?"
A: not_available · Traj: {search_docs} · Docs: []
Note: plausible but deliberately not in the manual. Split: train-eligible.

**T17b — scope near-miss (abstention, holdout)**
Q: "What is the soil-moisture irrigation threshold for the **wetland** roof?"
A: not_available · Traj: {search_docs} · Docs: []
Note: retrieval will surface the extensive-roof rule — confidently worded,
wrong scope. Strongest hallucination probe. Split: **test_unseen**.

### C. Pure weather

**T13 — rain expected?**
Q: "Is more than {thr} mm of rain expected within the next {h} hours?"
A: bool · Traj: {get_weather} · Oracle: sum fixture precip over window.

**T14 — forecast max temperature**
Q: "What is the highest temperature forecast for the next {d} days?"
A: numeric (°C, ±0.1 abs) · Traj: {get_weather} · Oracle: max over fixture.

**T15b — future rain (pair of T15a)**
Q: "How much rain will fall in {future_period}?"
DE: "Wie viel soll es nächste Woche regnen?"
A: numeric (mm, ±2% rel) · Traj: {get_weather} · Must-not: query_database
Oracle: sum fixture precip.

**T18a — unservable window (abstention)** *(relabeled per architecture D23)*
Q: "What will the temperature be in four weeks?"
A: not_available · Traj: {get_weather}
Note: window no backend can serve — beyond the 16-day horizon, before Archive
coverage, or spanning the Archive/Forecast cutoff (D17). The tool returns its
typed `not_available` (D11); the agent must relay it as the contract status.
Distinct from T15 misrouting. Split: train-eligible.

**T18b — missing variable (abstention, holdout)** *(relabeled per D23)*
Q: "What is the forecast **soil temperature** for the next 3 days?"
A: not_available · Traj: {} (empty — the tool has no variable argument and
always returns the seven documented fields, so the abstention is the agent's
alone, grounded in the docstring's variable list, D11; a verification call is a
free extra call under D21, neither required nor penalized).
Note: only the abstention metric carries this template's signal. Holdout tests
generalization from T18a's tool-signaled abstention to abstention with no tool
signal. Split: **test_unseen**.

### D. Model chains

**T09 — threshold crossing ahead**
Q: "Will the soil moisture of the {roof} roof fall below {thr}% within the
next {h} hours?"
A: bool · Traj: {query_database, get_weather, predict_soil_moisture}
Oracle: latest measured SWC → fixture meteo → predict → min < thr.

**T10 — predicted minimum**
Q: "What is the minimum soil moisture predicted for the {roof} roof over the
next 72 h?"
A: numeric (%, ±0.1 abs) · Traj: as T09 · Oracle: same chain, return min.

**T19 — retrospective model check**
Q: "How far off was the soil-moisture model for the {roof} roof last week,
on average?"
A: numeric (pp, ±0.1 abs) · Traj: {query_database, predict_soil_moisture}
· Must-not: get_weather
Oracle: DB meteo + measured initial SWC → predict → mean |pred − measured|.
Note: past-facing model use; weather tool is the critical distractor.
Window must end ≤ `as_of` (as-of view check).

### E. Hybrid / full chain

**T07 — irrigation check per manual**
Q: "Does the {roof} roof need irrigation right now, according to the
operations manual?"
DE: "Muss das unbewässerte Extensivdach heute bewässert werden?"
A: bool · Traj: {search_docs, query_database, get_weather}
Docs: ops_manual#irrigation_rule
Oracle: latest SWC + 48 h rain lookahead vs rule constants.

**T08 — heatwave days (manual definition)**
Q: "How many heatwave days, as defined in the operations manual, occurred in
{month}?"
A: numeric (count, exact) · Traj: {search_docs, query_database}
Docs: ops_manual#heatwave_definition
Oracle: definition constants → SQL count on `wetter`.

**T11 — irrigation decision tomorrow** *(reframed per architecture D22)*
Q: "Does the {roof} roof need irrigation tomorrow, per the standard rule?"
A: bool (balanced sampling)
Traj: {predict_green_roof_water_balance_tool, calc_irrigation} expected; final
chain pending the D22 input confirmation (whether get_weather joins it).
Oracle: predicted SWC (+ confirmed rule inputs) → calc_irrigation decision.
Note: predictive twin of T07 — T07 runs on measured SWC "right now", T11 on the
model's prediction for tomorrow. The dose, when yes, is the fixed per-roof
p90-of-historical-ET constant from the manual, not a computed volume.

**T12 — retention vs target** *(blocked: needs lysimeter areas)*
Q: "Was the retention of the {roof} roof during {event} above the manual's
target?"
A: bool · Traj: {search_docs, query_database} · Docs: ops_manual#retention_target
Oracle: (rain − outflow/area) / rain vs target.

**T20 — forecast heatwave per manual**
Q: "Does the coming week's forecast qualify as a heatwave under the manual's
definition?"
A: bool · Traj: {search_docs, get_weather} · Must-not: query_database
Docs: ops_manual#heatwave_definition
Oracle: definition applied to fixture.
Note: the only hybrid excluding the DB — `query_database` negative control.

**T25 — past/future bridge**
Q: "Will tomorrow be warmer than yesterday?"
A: bool · Traj: {query_database, get_weather}
Oracle: `wetter.Tmax` yesterday vs fixture max tomorrow.

### F. Given-values controls

**T16a — rule applied to stated values**
Q: "Soil moisture is at 12% and only 2 mm of rain is forecast — should we
irrigate, per the manual?"
A: bool · Traj: {search_docs} · Must-not: query_database, get_weather,
calc_irrigation (D24)
Docs: ops_manual#irrigation_rule · Oracle: rule on stated values.
Note: catches reflexive DB/weather calls for values already given; the
calc_irrigation must-not makes the a-side of the routing probe binding.

**T16b — calculator isolation** *(reframed per architecture D22)*
Q: "Given SWC {x}% and {y} mm of rain in the next 48 h for the {roof} roof,
does the standard rule say to irrigate?"
A: bool (balanced sampling) · Traj: {calc_irrigation} · Must-not: search_docs
(D24)
Oracle: `calc_irrigation` on stated values.
Note: identical inputs to T16a; symmetric must-nots (D24) make the
docs-vs-calculator probe binding in both directions, so the phrasing must cue
the route unambiguously (settle wording in T002 — a docs lookup before
calculating is defensible behaviour and fails only because the cue says
calculator). Stated values must stay expressible once the D22 input set is
confirmed.

### G. Counterfactuals

**T21 — forcing override (train-eligible)**
Q: "If 50 mm of rain falls tomorrow, what is the minimum soil moisture of the
{roof} roof over the next 48 h?"
A: numeric (%, ±0.1 abs)
Traj: {query_database, predict_soil_moisture}
Oracle: fixture with precip replaced → predict.
Note: partial override; a prior get_weather fetch is a free extra call under
D21 — fetch-then-substitute stays valid without an `(opt)` marker.

**T22 — parameter override (holdout)**
Q: "Under the current forecast but with albedo {a}, what soil moisture is
predicted for the {roof} roof tomorrow?"
A: numeric (%, ±0.1 abs)
Traj: {query_database, get_weather, predict_soil_moisture(params)}
Split: **test_unseen**.

**T23 — state override (holdout)**
Q: "If soil moisture had been 20% last Monday, where would it be now?"
A: numeric (%, ±0.1 abs)
Traj: {query_database, predict_soil_moisture} · Must-not: get_weather
Oracle: `initial_swc=0.20` + DB meteo since Monday. Split: **test_unseen**.

**T26 — compositional (holdout)**
Q variants: (i) "If albedo were {a} **and** 30 mm fell tomorrow, would the
{roof} roof stay above the irrigation threshold?" (adds Docs:
ops_manual#irrigation_rule); (ii) override + cross-roof comparison.
A: bool / numeric · Traj: union of the composed chains.
Split: **test_unseen** — the compositional-generalization headline.

### H. Presentation intent

**T24a — plot request**
Q: "Show me how the soil moisture of both extensive roofs developed in
{month}."
A: null (artifact deliverable, §1.1) — scored on the resolved spec echoed by
`plot_timeseries`: columns {QEx1, QEx2} (set match), time range, aggregation
· Traj: {plot_timeseries} · Must-not: query_database
Note: tool runs its internal fixed query; rendering is an unscored side effect
(nothing renders server-side — the frontend draws from the session-state
payload named by `artifact_ref`, architecture D20).

**T24b — twin without plot verb**
Q: "What was the mean soil-moisture difference between the two extensive roofs
in {month}?"
A: numeric (pp, ±0.2 abs — T03's convention; D25) · Traj: {query_database}
· Must-not: plot_timeseries
Note: near-identical information need to T24a; only the presentation verb
differs. The difference wording keeps the answer a single scalar within the
§1.1 contract (D25 — "numeric ×2" was never expressible).

---

## 3 Tool coverage matrix

| Tool | Sole necessary | In chain | Critical distractor (Must-not) |
|---|---|---|---|
| search_docs | T06, T16a, T17a, T17b | T07, T08, T12, T20, T26(i) | T16b (D24) + all of A, C, D, G-non-doc, H |
| query_database | T01–T05, T15a | T07–T12, T19, T21–T23, T25, T26 | T15b, T16a, T20, T24a |
| get_weather | T13, T14, T15b, T18a | T07, T20, T25 (+T11 pending D22) | T15a, T16a, T19, T23 |
| predict_soil_moisture | — (documented: never sole) | T09–T11, T19, T21–T23, T26 | T04 (D24) |
| calc_irrigation | T16b | T11 | T16a (D24) |
| plot_timeseries | T24a | — | T24b |

Every tool appears at least once as necessary-and-sufficient (except the
model, by design, and T18b whose gold set is deliberately empty) and at least
once as a distractor. The model-chain rows still predate architecture §8/D21:
under the self-contained GR2L tool, `query_database`/`get_weather` leave the
T09/T10/T19/T21–T23 chains — re-derive the matrix wholesale in T002.

---

## 4 Prerequisites / blockers

1. `rules_constants.py` + ops-manual sections rendered from it:
   `#irrigation_rule`, `#heatwave_definition`, `#retention_target`.
   Deliberately absent: wind-shutoff threshold, wetland-roof threshold.
2. Lysimeter collection areas added to the semantic layer → unblocks T12
   (and L↔mm conversions generally). Plan T077.
3. Semantic-layer alias map + typo fixes (paraphrase robustness for A/E).
   Plan T078.

Former items here — a frozen hourly fixture schema and the
`predict_soil_moisture` pure-function refactor — are **superseded**, not
pending: the request-keyed response cache replaced fixtures (architecture D4)
and the built self-contained `predict_green_roof_water_balance_tool` replaced
the pure function (D13/D14). §1.4/§1.5 above are stale for the same reason and
await the T002 re-derivation.