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
| `error`; the agent contract carries only the first two outcomes. A tool-level
error is detected harness-side from the event log and excludes the case from
scoring (architecture D16).

### 1.2 Scoring
- **Answer**: exact for bool/date/count/not_available; numeric within
  per-template tolerance. Oracles share code with the tool chain, so tolerances
  absorb rounding only — keep them tight.
- **Trajectory**: unordered set precision/recall/F1 vs `Trajectory`; mild
  penalty per extra call. `(opt)` = optional, never penalized. `Must-not` =
  scored as error if called (only where it is the point of the template).
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
A: bool · Traj: {query_database} · Oracle: daily SUM > 0. Balanced sampling.

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

**T18a — missing variable (abstention)**
Q: "What is the forecast **soil temperature** for the next 3 days?"
A: not_available · Traj: {get_weather}
Note: variable absent from fixture schema. Split: train-eligible.

**T18b — beyond horizon (abstention, holdout)**
Q: "What will the temperature be in four weeks?"
A: not_available · Traj: {get_weather}
Note: > 16-day horizon. Distinct from T15 misrouting. Split: **test_unseen**.

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

**T11 — irrigation volume tomorrow**
Q: "How much irrigation does the {roof} roof need tomorrow, per the standard
rule?"
A: numeric (L, ±2% rel)
Traj: {query_database, get_weather, predict_soil_moisture, calc_irrigation}
Oracle: initial SWC → fixture meteo → predict → calc_irrigation.

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
A: bool · Traj: {search_docs} · Must-not: query_database, get_weather
Docs: ops_manual#irrigation_rule · Oracle: rule on stated values.
Note: catches reflexive DB/weather calls for values already given.

**T16b — calculator isolation**
Q: "Given SWC {x}% and {y} mm of rain in the next 48 h for the {roof} roof,
what irrigation volume does the standard rule give?"
A: numeric (L, ±2% rel) · Traj: {calc_irrigation}
Oracle: `calc_irrigation` on stated values.

### G. Counterfactuals

**T21 — forcing override (train-eligible)**
Q: "If 50 mm of rain falls tomorrow, what is the minimum soil moisture of the
{roof} roof over the next 48 h?"
A: numeric (%, ±0.1 abs)
Traj: {query_database, predict_soil_moisture}, (opt) get_weather
Oracle: fixture with precip replaced → predict.
Note: partial override → lenient trajectory (fetch-then-substitute is valid).

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
Note: tool runs its internal fixed query; PNG is an unscored side effect.

**T24b — twin without plot verb**
Q: "What was the mean soil moisture of both extensive roofs in {month}?"
A: numeric ×2 or pp-gap (define one) · Traj: {query_database}
· Must-not: plot_timeseries
Note: identical information need to T24a; only presentation verb differs.

---

## 3 Tool coverage matrix

| Tool | Sole necessary | In chain | Critical distractor (Must-not) |
|---|---|---|---|
| search_docs | T06, T16a, T17a, T17b | T07, T08, T12, T20, T26(i) | all of A, C, D, G-non-doc, H |
| query_database | T01–T05, T15a | T07–T12, T19, T21–T23, T25, T26 | T15b, T16a, T20, T24a |
| get_weather | T13, T14, T15b, T18a, T18b | T07, T09–T11, T20, T22, T25, (opt) T21 | T15a, T16a, T19, T23 |
| predict_soil_moisture | — (documented: never sole) | T09–T11, T19, T21–T23, T26 | — |
| calc_irrigation | T16b | T11 | — |
| plot_timeseries | T24a | — | T24b |

Every tool appears at least once as necessary-and-sufficient (except the
model, by design) and at least once as a distractor.

---

## 4 Prerequisites / blockers

1. `rules_constants.py` + ops-manual sections rendered from it:
   `#irrigation_rule`, `#heatwave_definition`, `#retention_target`.
   Deliberately absent: wind-shutoff threshold, wetland-roof threshold.
2. Lysimeter collection areas added to the semantic layer → unblocks T12
   (and L↔mm conversions generally).
3. Fixture schema frozen with **all** soil-model inputs (§1.4) before any
   fixture or oracle is written.
4. `predict_soil_moisture` refactored to the pure-function signature (§1.5).
5. Semantic-layer alias map + typo fixes (paraphrase robustness for A/E).