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

**`predict_fn` raising is not an error the search reports; it becomes the
record's `outputs`.** `_run_single` wraps the call in
`except Exception as e: program_outputs = f"Failed to invoke the predict_fn
with {inputs}: {e}"` (`optimize/optimize.py:299-303`), so a rollout that threw
is handed to the scorers as a *string* where they expect a mapping, and the
iteration continues. Two consequences for this repo: a scorer must be able to
survive a non-mapping `outputs`, and a harness defect inside `predict_fn` is
invisible unless something else counts it — which is what T123's declared
residuals are for.
*Verified:* read from installed mlflow 3.13.0 source, and observed as a
`rollout` exclusion carrying `BrokenBarrierError` when
`tests/harness/test_predict.py` was run under
`MLFLOW_GENAI_EVAL_MAX_WORKERS=1`. *Date:* 2026-08-25.

**ADK strips a docstring into the function declaration, and appends its own
identity block after the static instruction.** For all six tools, the
declaration's `description` in `request.config.tools` equals `__doc__.strip()`
and not `__doc__` — so a docstring's surrounding whitespace is a byte no model
observes, which is why the candidate surface registers the tool text stripped
and the root instruction verbatim. The instruction is *not* stripped or altered:
it arrives as the system instruction's prefix, followed by two newlines and
`You are an agent. Your internal name is "root_agent". The description about you
is "Water-Management Data Analyst.".`
*Verified:* compared against a real `LlmRequest` captured from a scripted
rollout (`tests/harness/test_baseline_registration.py`). *Date:* 2026-08-25.

**The seven seed prompts are registered at version 1.** Against the dev-stack
registry (`http://localhost:5000`, Postgres-backed): `agent_root_instruction`
5484 chars, `agent_tool_text_to_sql_agent` 120, `agent_tool_predict_green_roof_
water_balance_tool` 6804, `agent_tool_get_weather_forecast_tool` 3092,
`agent_tool_calc_irrigation` 4061, `agent_tool_lookup_reference` 4475,
`agent_tool_plot_timeseries` 5655. `just pins`: **16 pinned, 3 unpinned, 0
moved** — both candidate slots closed, the reflection model, its canary and the
task model's canary still open.
*Verified:* `just candidates` followed by `just candidates-check` (7 matched, 0
drifted) and `just pins`. *Date:* 2026-08-25.

**`optimize_prompts` splats the record's `inputs` into keyword arguments, and
`_build_eval_fn` does not.** `convert_predict_fn` validates a `predict_fn`'s
signature against the `inputs` keys — `**kwargs` satisfies it for any envelope,
one positional mapping does not — and returns
`lambda request: predict_fn(**request)` (`genai/utils/trace_utils.py:576-581`,
`genai/utils/data_validation.py:27,136-149`). The inner `_build_eval_fn` then
calls *that* with the mapping positionally, so both shapes are real and they are
two layers of one call. A `predict_fn(inputs)` therefore passes every test
written against `_build_eval_fn` and fails at `optimize_prompts` with
`MlflowException: The 'inputs' column must be a dictionary with the parameter
names of the 'predict_fn' as keys` — a message about the dataset, for a fault in
the function. Two further consequences: with a non-empty `sample_input` the
validation runs **one real rollout** before the search starts, outside the
budget; and it decides whether `predict_fn` gets wrapped in `mlflow.trace`.
*Verified:* read from installed mlflow 3.13.0 source after `just search-smoke`
failed on it. *Date:* 2026-08-25.

**The LLM cache has two halves and either alone is a cache that does nothing.**
`configure_llm_cache` installs `litellm.cache`; `AssistantSettings.litellm_extra`
sends `caching` on every request. litellm consults the installed cache only when
`caching` is unset or `True` (`caching/caching_handler.py:156-161`), and this
repo always sends it — so installing the cache while `llm_cache_enabled` is
`False` produces one that is configured, logged and bypassed on every call.
`harness/optimize.py`'s `search_llm_cache` flips both and restores both.
Measured: `just search-smoke` re-run on the warm cache finished in **14 s**
against minutes cold, adding **no** entries to `.cache/llm`.
*Verified:* read from installed litellm 1.84.0 source, and by the repeat run.
*Date:* 2026-08-25.

**A short search closes the whole loop.** `just search-smoke` — 3 train records,
`max_metric_calls=8`, against the dev-stack registry at `http://localhost:5000`,
the live task and reflection models and the live GR2L — completes with
**selection score 1.0 → 1.0, 0/3 residual cases, 0 newly recorded cache
entries**, and registers seven prompts at v2. Per-scorer:
`{answer: 1.0, trajectory: 1.0, abstention: 1.0}` — `card_recall` is **absent**,
having skipped on all three records (T01 wants no card), which is §7's skip
running through this repo's `aggregate_scores` inside a real search. All three
cases answered correctly, so GEPA logged `Iteration 2: All subsample scores
perfect. Skipping.` and proposed no candidate; the search is degenerate and the
loop is complete. Afterwards `just candidates-check` still reports 7 matched, 0
drifted at v1, and `just pins` 18 pinned, 1 unpinned, 0 moved.
*Verified:* `just search-smoke` twice. *Date:* 2026-08-25.

**A search leaves the registry one version ahead, and `just candidates` would
re-pin onto it.** `optimize_prompts` registers `best_candidate` unconditionally,
so a search that proposed nothing still mints v2 holding the seed text.
`register_candidates` compares against `@latest`, so a `just candidates` run
after a search re-pins the seed to a *new* version — with the same handwritten
bytes, so `just candidates-check` passes and the reference arm does not drift,
but the pinned version number moves. It is therefore not run between the arms of
one measurement.
*Verified:* observed after `just search-smoke`; the seven prompts went to v2 with
byte counts identical to the seed's. *Date:* 2026-08-25.

**GEPA's reflection call carries nothing but the model and the messages, and a
callable cannot be substituted.** `GepaPromptOptimizer` takes
`reflection_model` as a string, and GEPA's string path builds
`litellm.completion(model=reflection_lm_name, messages=…)` with no `api_base`,
`api_key`, `temperature` or `seed` (`gepa/api.py:256-270`). Against an endpoint
that is not the provider default that request does not arrive at all. The
obvious escape — passing a pre-configured callable as
`gepa_kwargs={"reflection_lm": …}` — does not work either: the optimizer builds
`self.gepa_kwargs | {…, "reflection_lm": f"{provider}/{model}", …}`
(`gepa_optimizer.py:349-359`) and the right-hand literal *does* carry that key,
so a caller's value is silently overridden. The same merge that lets
`frontier_type` through (§ Candidate selection) closes on this one. Hence
`harness/reflection.py` binds the pin at `litellm.completion` instead, narrowed
to the pinned model id.
*Verified:* read from installed gepa 0.1.1 / mlflow 3.13.0 source, and by
`tests/harness/test_reflection.py`. *Date:* 2026-08-25.

**The reflection model's reply is stable; its response envelope is not.** Four
probes of `openai/qwen3.5-397b-a17b` at `temperature=0, seed=42` with the prompt
`Reply with exactly the three words: green roof canary` returned
`content == "\n\ngreen roof canary"` **byte-identical every time**, while
`completion_tokens` moved between 235 and 236 — it is a thinking model and the
reasoning trace behind `reasoning_content` is not stable. So the canary hashes
the assistant message's content (together with the probe), and hashing the
response object would have failed on every run. Pinned:
`reflection_model_canary_sha256 = 335fb27d41c9ba573d7af52d762e8ccb867f02c24a313
c7f753b8338ac29d103`. The endpoint serves 16 models; the task model
`qwen3.6-35b-a3b` and this one are the two the testbed names.
*Verified:* four live probes through `harness/reflection.py`'s bound seam,
then `just pins-reflection` twice with the same result. *Date:* 2026-08-25.

**All 100 train cases are fully captured, re-checked where the search reads
them.** Replaying every committed `train.json` record through its own oracle
against `eval/cache/`, with `httpx.AsyncClient.send` replaced for the duration,
drops **none**: 100 of 100 kept, in ~2 s over one event loop with one
`ScenarioContext` per distinct `as_of`. The same pass drops a case whose window
was never recorded — a T09 record moved from `d = 6` to `d = 30`, far outside
the ±1 neighbourhood T116 warmed — with `CacheMissError` naming the Archive
request and the nearest captured one. So the search's `train_data` is the whole
split today, and a capture gap opened later shows up as a shrinking split rather
than as residual failures nobody can attribute.
*Verified:* `tests/harness/test_train_data.py::
test_the_committed_train_split_is_fully_captured` and
`::test_a_case_whose_window_was_never_captured_is_dropped_by_name`.
*Date:* 2026-08-25.

**The aggregation callable is load-bearing, measured on one record rather than
read.** The same plot-deliverable record — null oracle answer, empty
`gold_cards`, so both of §7's skips at once — through
`create_metric_from_scorers(SCORERS, aggregate_scores)` returns **1.0** with
`individual_scores` holding only `trajectory` and `abstention`; through
`create_metric_from_scorers(SCORERS, None)` the identical record raises
`MlflowException: Scorers [answer (type: Feedback), card_recall (type:
Feedback)] return non-numerical values that cannot be automatically
aggregated.` So omitting the argument does not reweight the objective quietly
on this suite — it stops the run at the first skipping case. A second
consequence, useful rather than accidental: `_convert_to_numeric` drops a
non-numeric value, so a skipped metric is absent from `individual_scores` and
therefore from the per-scorer averages GEPA logs, which is §7's coverage
semantics arriving without a second implementation. All four `rationales` still
reach the reflective dataset.
*Verified:* `tests/harness/test_scorers.py::
test_a_skip_is_a_skip_only_through_this_repos_aggregation_callable` on mlflow
3.13.0. *Date:* 2026-08-25.

**Two records really are evaluated in parallel, and a per-record context is
what keeps them apart.** Two records differing only in `as_of` (2025-06-05 and
2025-06-15), evaluated through `predict_fn` by MLflow's own
`_build_eval_fn` and plotting the same `swc.soil_moisture` window
(2025-06-01..10) off the same pinned database, come back with **480 points and
`truncated: false`** against **209 points, `truncated: true` and a last reading
of 2025-06-05**. Enforced concurrency: each record's model waits on a shared
`threading.Barrier(2)`, which breaks under
`MLFLOW_GENAI_EVAL_MAX_WORKERS=1` and passes at the default 10.
*Verified:* `tests/harness/test_predict.py::
test_two_records_with_different_as_of_run_concurrently`. *Date:* 2026-08-25.

**GEPA starts an MLflow run when none is active, and ends the one it started.**
`gepa.logging.experiment_tracker` calls `mlflow.start_run()` if `active_run()` is
`None` (`:87-88`), records that it created it, and terminates it on the way out
(`:253`); given an already-active run it uses that one and leaves it open. So a
run ledger written *after* `optimize_prompts` returns lands in a second,
auto-created run — beside the per-iteration tables it is supposed to sit with,
and looking for all the world like a complete record of a search that logged
nothing else. `harness/optimize.py` therefore opens the run itself, in a named
experiment, and GEPA joins it.
*Verified:* read from installed gepa 0.1.1 source, and by
`tests/harness/test_ledger.py::test_the_ledger_and_the_pins_land_in_the_active_run`.
*Date:* 2026-08-25.

**A candidate cannot be identified by its registered version, so the ledger
hashes its text.** The patch that injects candidate text replaces
`PromptVersion.template` on the *class* and matches on the prompt **name**
(§ Candidate text is injected by a process-global patch), so every candidate GEPA
evaluates is read back through the same pinned `candidate_prompt_versions`. A
per-rollout ledger keyed on those versions files a whole search — every
candidate, every iteration — under one id, and the table looks complete while
answering no question anyone would ask of it. `harness.ledger.candidate_id`
hashes the seven components' text instead, in `CANDIDATE_COMPONENTS` order and
length-delimited per component.
*Verified:* `tests/harness/test_ledger.py::
test_the_candidate_id_is_the_text_and_never_the_version`. *Date:* 2026-08-25.

**ADK's `LlmResponse.model_version` is the served model id, and it is the only
place a rollout carries one.** `_model_response_to_generate_content_response`
sets `model_version=response.model` (`google/adk/models/lite_llm.py:1769`), and
`Event` extends `LlmResponse`, so the field survives into the event stream —
but `harness/run_case.py`'s `CaseResult.diagnostics` does not carry it and that
module is frozen. The ledger reaches it through `make_predict_fn`'s existing
`model_factory` seam instead: one `WitnessedLiteLlm` per rollout, so attribution
is by construction rather than by thread. The requested id is litellm's
`<provider>/<model>` and the endpoint echoes the model half alone, so the two
columns are compared on that half and neither is normalized to the other.
*Verified:* read from installed google-adk 2.3.0 source, and by
`tests/harness/test_ledger.py::
test_the_witness_records_what_the_endpoint_said_it_served`. *Date:* 2026-08-25.

**GEPA spends `len(trainset)` metric calls before it proposes anything, so a
budget equal to the split's size is a no-op that reports itself as a search.**
`GepaPromptOptimizer` passes `trainset` and **no `valset`**
(`gepa_optimizer.py:349-359`), so GEPA evaluates the seed candidate over the
whole split first; each accepted candidate then costs another full evaluation,
with reflection minibatches of 3 in between (`gepa/api.py:328`,
`core/engine.py:480,563`). At 100 train cases a `max_metric_calls=100` therefore
buys **zero** proposals and returns the seed unchanged — and the run looks
complete, because it is: it evaluated the thing it was given. Caught before any
test rollout and amended in the pre-registration rather than discovered from a
flat result.
*Verified:* read from installed gepa 0.1.1 / mlflow 3.13.0 source, and by
`just search-smoke` at 3 records / 6 calls producing 7 rollouts — the initial
full evaluation of 3, one minibatch of 3, and one over.
*Date:* 2026-08-25.

**A canary detects a swap only where the swap changes the reply, and both
reflection models answered identically.** Re-capturing
`reflection_model_canary_sha256` after moving from `openai/qwen3.5-397b-a17b` to
`openai/qwen3.5-122b-a10b` produced **the same hash**,
`335fb27d41c9ba573d7af52d762e8ccb867f02c24a313c7f753b8338ac29d103` — the probe
asks for exact compliance and both models comply exactly, byte for byte. So the
canary is not an identity check and was never one: what caught this move was the
`reflection_model` pin's served id and endpoint, and the canary's job is the
narrower one of catching a model that started answering *differently*. Worth
stating plainly, because a matching canary reads as "nothing moved" and here
everything did.
*Verified:* `just pins-reflection --accept-moved` against both models.
*Date:* 2026-08-25.

**T115's loop-bound GR2L client is a *search*-path hazard too, and it can wedge
the run rather than fail it.** `gr2l_client` keeps its `httpx.AsyncClient` in a
module-level `_ClientHolder` (`:157-165`) created on the first live call and
bound to that call's event loop, while `run_case` runs `asyncio.run` per
rollout — so every rollout has a new loop and the client does not. `run_case`'s
own docstring scopes this to the capture pass, on the ground that "replay never
reaches the client at all". **The search is the third pass and it does reach the
client**: record mode is `allow_live=True`, so a window the *candidate* chose
and the capture pass never recorded goes out live (§5).
Under MLflow's `ThreadPoolExecutor` the failure is worse than the documented
one. A registered search over the 100-case train split logged
`Event loop is closed` ×4 and `connection pool` ×4, completed 324 rollouts, and
then **stopped dead** — 0 % CPU, no open sockets, no log output, no progress for
twenty minutes. Not a slow retry: a wedged pool.
`gr2l_client.py` is frozen (T107), so this is reported and not repaired. The
**measurement path is not exposed** — it replays, and `ReplayCache` refuses to
call out at all — so what it costs is search reliability, not any measured
number. Two mitigations need no frozen code: fewer eval workers, and the fact
that a re-run replays what the wedged run *recorded* (16 new `eval/cache/`
entries) plus its LLM responses, so the same ground is re-covered without a live
call.
*Verified:* the killed search's log, its socket table and CPU while stalled.
*Date:* 2026-08-25.

**GEPA's proposer calls every component "instructions for an assistant", and the
metaprompt is a supported per-component parameter.** The default template is two
slots and one framing: *"I provided an assistant with the following
**instructions**… Your task is to write a new **instruction** for the
assistant"* (`gepa/strategies/instruction_proposal.py:13-29`), with
`<curr_param>` and `<side_info>` the only substitutions. Six of this repo's seven
optimizable components are **tool descriptions**, so the proposer is told a
function declaration is a system prompt — which is exactly what the P8c search
produced when it rewrote the GR2L docstring into a Role section with a routing
table.

`gepa.optimize` takes `reflection_prompt_template: str | dict[str, str] | None`
(`api.py:58,140`), where a dict maps **component name → template**, and the path
to it is clear in both places it could have been blocked. MLflow's merge literal
does not carry the key (`gepa_optimizer.py:349-357`), so `gepa_kwargs` passes it
through; and GEPA's guard —
`assert not (adapter is not None and getattr(adapter, "propose_new_texts", None) is not None)`
(`api.py:343-347`) — passes because `MlflowGEPAAdapter` implements only
`evaluate` and `make_reflective_dataset` while `GEPAAdapter.propose_new_texts` is
a class attribute set to `None`.

**Demonstrated end to end, not read.** A search wired with a dict of two sentinel
templates — one for `agent_root_instruction`, one for every `agent_tool_*` —
captured 5 intercepted reflection requests: the root instruction's proposal
carried the root sentinel, and all four tool-component proposals carried the tool
sentinel. Per-component dispatch works, so §7's two kinds of component can be
told apart at the one place it matters.

**The same capture confirms the prefix leak.** `component_name` appears in the
rendered prompt, and it is the *registered* name — `agent_tool_…`, T120's
namespacing against the shared text-to-SQL registry — which is where the
optimized docstring got the tool name `agent_tool_predict_green_roof_water_
balance_tool`, a string no toolset resolves. And the rendered `<side_info>`
carries the reflective dataset's `trace` key as a `## trace` heading with
**nothing under it**, which is the trace finding below arriving from the
proposer's side. The JSON spelling `"trace"` appears nowhere because the dataset
is rendered as markdown rather than serialized — what is missing is the spans,
not the key, and T131 below measures the rendering field by field.
*Verified:* `gepa_kwargs={"reflection_prompt_template": {...}}` through
`optimize_prompts` with `litellm.completion` intercepted, on gepa 0.1.1 /
mlflow 3.13.0. *Date:* 2026-08-26.

**The search logged no traces at all, because MLflow decided `predict_fn` was
already traced.** `convert_predict_fn` probes the function once under a patched
`NoOpTracer`, and wraps it in `mlflow.trace` **only if it saw no spans**
(`trace_utils.py:568-580`). Measured on this repo's own `predict_fn`: the probe
sees **4 spans** — ADK emits them through MLflow's tracer provider, and MLflow's
ADK translation is live enough to log `Skipping missing token usage metadata for
agent root_agent` — so `counter.count == 4`, the wrap is skipped, and nothing
then exports those spans as a trace bound to the eval request id.
`_run_single` calls `mlflow.get_trace(eval_request_id, silent=True)`
(`optimize.py:309`), gets `None`, and `make_reflective_dataset` writes
`spans = []` (`gepa_optimizer.py:317-325`). A plain stub `predict_fn` in the same
harness sees 0 spans, *is* wrapped, and traces normally — so the failure is
caused by having instrumentation, not by lacking it.

**What it cost, and what it did not.** GEPA still reflected on `current_text`,
`inputs`, `outputs`, `expectations`, `score` and — crucially — `rationales`,
which is the channel §7 designed as the search signal and which
`harness/scorers.py` fills. What it lost is the span half of the reflective
dataset: the reflection model never saw the tool-call structure of a rollout, only
the scorers' prose about it. **The measurement path is entirely unaffected** —
`harness/stats.py` reads `CaseOutcome` rows and never touches a trace, so no
number in the P8c report depends on this.

**The remedy is one decorator, and it is not `autolog`.** `mlflow.litellm.autolog()`
does not help (the root agent's own turns are not litellm-visible as the trace
root), and setting `MLFLOW_GENAI_EVAL_SKIP_TRACE_VALIDATION` makes it *worse* —
that flag skips the wrap as well as the probe. Decorating this repo's
`predict_fn` with `@mlflow.trace` restores a logged trace
(`tr-…`, span `predict_fn`) because MLflow then has a span of its own to export.
Left for T130 rather than applied now: turning it on changes what a search
reflects on, and the P8c search has already run.
*Verified:* span counts from `NoOpTracerPatcher` for both function shapes, and
`mlflow.get_trace` returning `None` versus a trace across three scripted probes.
*Date:* 2026-08-26.

**What the search actually optimizes on, captured field by field rather than
read.** `run_search` over 4 committed train records against a throwaway SQLite
registry, with three seams and no edit to `harness/`: `harness.predict.run_case`
stubbed, so no endpoint is called and no rollout is paid for;
`litellm.completion` intercepted, so the reflection request is recorded and a
canned proposal returned; and GEPA's own callbacks attached through
`gepa_kwargs={"callbacks": […]}`, which survives MLflow's merge for the same
reason `frontier_type` would. Three proposals, three intercepted requests,
nothing spent. The sizes below are from the run in the P8c trace shape — no
trace reaches the adapter — and they are per component and per record: a larger
split changes which three records the prompt is built from, not how many.

**Nine keys per record, and the dicts are rendered as markdown rather than
serialized.** `make_reflective_dataset` returns one row per minibatch record —
`component_name`, `current_text`, `trace`, `score`, `inputs`, `outputs`,
`expectations`, `rationales`, `index` (`gepa_optimizer.py:329-338`) — and GEPA's
`format_samples` turns each row into `# Example N` with `## <key>` per field,
nesting a dict as deeper headings (`## inputs` → `### params` → `#### roof`) and
a list as `### Item 1`. A scalar is `str(value).strip()`. Heading depth caps at
six (`min(level + 1, 6)`), so anything nested deeper renders flattened — out of
reach here until `trace` is populated, which is T132's.

**The prompt is mostly the candidate, copied four times.** `current_text`
repeats the whole component text once per record, beside `<curr_param>`'s own
copy. Measured: the root instruction (5484 chars registered) produces a
**27183-char** reflection prompt, **81 %** of it four copies of that
instruction; the GR2L docstring (6804) produces 32732, 83 %; the sub-agent
description (120) produces 5948, 8 %. The per-record *evidence* — score, inputs,
outputs, expectations, rationales — is ~1.3–1.4 kB whichever component is being
rewritten.

**All four rationales arrive, whole, including the skip's.** ~400 characters per
record and ~1.2 kB per prompt: **4.3 %** of the root instruction's prompt and
20 % of the sub-agent description's. Verbatim apart from `.strip()` — nothing
between `Feedback.rationale` and the rendered prompt truncates, a 7 kB rationale
renders whole, and a `None` rationale renders as the literal `None`. The only
limiter is this repo's own `_DETAIL_LIMIT = 300` inside `harness/scorers.py`. So
§7's design holds: the channel it built the search's signal on is intact and
uncut. What does **not** arrive is the four numbers: `individual_scores` reaches
GEPA as `objective_scores` and reaches the logs, but the reflective row carries
only the blended `## score`, so the per-metric signal is present as the scorers'
prose and absent as arithmetic.

**The proposer is shown the oracle.** `## expectations` renders the record's
whole expectation block: gold answer and unit, tolerance, `answer_metric`,
`expected_tool_calls`, `must_not_tools`, `gold_cards`, `argument_checks` and the
`duckdb_sha256` pin. The reflection model therefore reads the answers to the
three cases it is proposing from, and nothing stops a candidate carrying them
into the text it writes. This is a train-side channel and not a leak into the
measured numbers — the search never touches either test split (§7) — but it is
the reason an optimized instruction naming a specific value should be read as
memorization until checked against the case it came from.

**Three of a hundred, and the same three decide acceptance.** The reflective
dataset is exactly the minibatch — 3 rows against a trainset of 4 here, 3 of 100
in the registered search, GEPA's default `reflection_minibatch_size`
(`api.py:328`). The acceptance gate is strict improvement on those same 3
(`engine.py:539-541`); only after passing it does the candidate get a full
evaluation, and that one runs over the whole trainset, MLflow passing no
`valset`. Observed end to end: minibatch `[2, 1, 0]` summing 0.667, the proposal
summing 2.0, accepted, then `valset_evaluated` over 4 / 4. So a proposal is
formed on 3 % of the population, accepted on that same 3 %, and reported on
100 % — and the three records it was formed on are never the population the
score came from.
**The three need not be three cases.** The epoch-shuffled sampler pads each
epoch to a multiple of the minibatch size by repeating its least frequent ids
(`batch_sampler.py:50-56`); the next minibatch came back `[3, 3, 0]` and the
rendered prompt carried the same case twice, as `# Example 1` and `# Example 2`
differing only in `index`. The padding is deterministic, not a coincidence of a
four-record split: the ids appended are the last and the second-to-last of the
epoch's shuffled order, so **the final minibatch of every epoch is `[last, last,
second-to-last]`**. At 100 records that is one minibatch in 34, built on two
cases while presenting three.

**The trace half, measured both ways.** With the wrap skipped — the state the
P8c search ran in — `## trace` renders with nothing under it, one character.
With `predict_fn` wrapped, each record carries one span (`rollout`) holding its
inputs and outputs — 620 to 690 chars per record across the three captures — and
the record's inputs and outputs then appear **twice** in the prompt. That is a floor and not an estimate of what
T132 buys: the rollout is stubbed here, so the span tree is the root alone,
while a real traced run would carry ADK's tool spans beneath it — the half the
reflection model has never seen.

**The request itself is one user message.** The intercepted
`litellm.completion` carries `model`, `api_base`, `api_key`, `temperature=0`,
`seed=42` and `num_retries=5` — T124's pin, observed at the wire rather than at
the seam that binds it — and a single `role: user` message holding the whole
rendered prompt. No system message, no tools, no response format.
*Verified:* `run_search` over 4 committed train records with the rollout
stubbed, `litellm.completion` intercepted and GEPA's callbacks attached, in both
trace
shapes on mlflow 3.13.0 / gepa 0.1.1; plus a direct
`InstructionProposalSignature.prompt_renderer` probe for the truncation and
`None` cases. *Date:* 2026-08-26.

## The measurement run (P8c)

Two arms, three splits, **one repeat** (pre-registration amendment 1), on
`google/gemma-4-31b-it` via OpenRouter, LLM response cache off, response cache
replaying. Written to `eval/measurements/20260825T174410Z.json`; the report
re-renders from that file with no model in reach.

**The run cost \$4.963 of the \$5 available** — the registered search \$2.76 and
the six measured conditions \$2.20, with \$0.037 left. Amendment 3 ordered the
splits so that train, whose number is a selection score rather than an
inference, was the one at risk; in the event nothing was lost.

**§7's repeat condition fires, and it governs how all of this may be read.**
Exclusion counts diverge between the arms on the split that carries the primary
estimand: **14 of 125 baseline against 25 of 125 optimized on test_seen**, driven
by `upstream` (20 vs 34) and `text_to_sql_agent` (1 vs 9). §7 is explicit —
"diverging failure counts between arms mean the run is repeated" — so the
comparison below is **not established**, and one repeat is exactly what the
budget did not buy. The direction is worth recording; the interval is not worth
believing until a repeat says the arms were measured under the same conditions.

| condition | included | excluded | selection | answer | trajectory |
|---|---|---|---|---|---|
| baseline / train | 93/100 | 7 | 0.812 | 0.713 | 0.817 |
| optimized / train | 90/100 | 10 | 0.904 | 0.909 | 0.889 |
| baseline / test_seen | 111/125 | 14 | 0.788 | 0.677 | 0.838 |
| optimized / test_seen | 100/125 | 25 | 0.869 | 0.889 | 0.880 |
| baseline / test_unseen | 26/56 | 30 | 0.872 | 0.750 | 0.923 |
| optimized / test_unseen | 28/56 | 28 | 0.845 | 0.625 | 0.964 |

**The primary estimand.** Paired bootstrap over `template_id`, 10 000 resamples,
`default_rng(42)`, percentile 95 %: on test_seen the **answer** metric moves
**+0.179 [+0.012, +0.369]** over 21 templates — an interval that excludes zero —
while trajectory moves +0.040 [−0.080, +0.160] and the blended selection score
+0.060 [−0.025, +0.144], both spanning it. The one metric that moved is the
deliverable; the routing judgement did not measurably move.

**The optimizer traded abstention for answers, which is what blending permits.**
On test_seen, abstention accuracy over the unanswerable cases falls **0.800 →
0.562** while the false-abstention rate stays at ~0.01, and the paired difference
is −0.052 [−0.136, +0.000]. `aggregate_scores` weights answer 0.40 against
abstention 0.20 and protects nothing, so a candidate buying 0.18 of answer with
0.05 of abstention is selected — precisely the trade
`decisions.md § Candidate selection and the scorers' aggregation` says the
weights license. It is visible only because §7 refuses to average the two
abstention numbers into one.

**Both generalization gaps are small and neither excludes zero.** train →
test_seen is +0.018 [−0.017, +0.060] for the baseline and +0.026 [−0.056,
+0.112] for the optimized arm; test_seen → test_unseen is −0.080 and −0.020, as
point estimates over disjoint template sets. No evidence of the memorized
constants or the per-template routing table the two gaps exist to catch — on a
suite this size that is a weak statement, not a clean bill.

**The holdout as a table, and two rows of it are empty.** T16b, T17b, T18b and
T20 tie at 1.000 trajectory in both arms; T26 is the one win (0.500 → 0.857);
**T22 has no data in either arm and T23 none in the optimized arm**, every case
excluded on a replay miss. 1 win, 0 loss, 4 ties, 2 no data — which is why §7
asks for a table and forbids an accuracy: an accuracy would have quietly averaged
over the two templates nobody measured.

**test_unseen lost more than half its cases to replay misses** — 30 of 56
baseline, 28 of 56 optimized, almost all `upstream`. The capture pass recorded
the windows each case's *oracle* asked for; a rollout picks its own, and on the
measurement path a miss is an exclusion by design (§7). It is the same asymmetry
the search answers with record mode, and here it costs the holdout half its
population.

**Diagnostics, reported and never scored.** The optimized arm is consistently
more expensive: mean steps 1.78 → 1.89 on test_seen and 2.54 → 3.57 on
test_unseen, mean extra calls 0.87 → 0.92 and 1.58 → 2.68, mean tokens 23.2k →
24.3k and 24.6k → 27.4k, mean latency 12.6 s → 18.0 s. `parse_failure` tracks
`step_cap_exceeded` exactly in all six conditions — every unparseable final
message came from a rollout that spent the 7-call cap, not from a candidate that
mangled the format. `fixer_iterations` is unmeasurable from the root side and is
reported as absent in all six.

**No noise floor.** One repeat cannot disagree with itself, so residual
nondeterminism is reported as unmeasured rather than as zero, and every
difference above is read without knowing what "small" means on this endpoint.
That is amendment 1's stated cost, arriving exactly where it was said it would.

## The repeat §7 asked for (T134)

Two arms, three splits, **three repeats**, under **registration 2**
(`09f7985293487ae6ede6369fabe0d6a048c314fca4e5849e23331bf6a2fbd7e9`, with its two
amendments), on `openrouter/deepseek/deepseek-v4-flash-0731` pinned to the
provider `deepinfra`, LLM response cache off, response cache replaying. 1686
rollouts, written to `eval/measurements/20260826T164850Z.json`; the report
re-renders from that file with no model in reach.

**§7's repeat condition does not fire, which is the whole point of the run.**
Exclusion counts on the split carrying the primary estimand, summed over three
repeats:

| split | baseline | optimized |
|---|---|---|
| test_seen | 43/375 (11.5 %) | 39/375 (10.4 %) |
| test_unseen | 66/168 (39.3 %) | 65/168 (38.7 %) |
| train | 1/300 | 0/300 |

P8c's were **14/125 against 25/125** on test_seen — a nine-point divergence, and
the architecture's stated condition for repeating a run. Here the arms differ by
one point, and the sign flips repeat to repeat (14 vs 11, 14 vs 15, 15 vs 13). The
arms were measured under the same conditions, so **this run's comparison is
established rather than recorded**, which is the thing P8c could not claim about
its own.

**The primary estimand.** Paired bootstrap over `template_id`, 10 000 resamples,
`default_rng(42)`, percentile 95 %: on test_seen the **answer** metric moves
**+0.011 [+0.000, +0.032]** over 19 templates, `card_recall` +0.047 [+0.000,
+0.140] over 5, `abstention` +0.003 [+0.000, +0.009], trajectory +0.037 [−0.032,
+0.128] and the blended selection score +0.017 [−0.008, +0.046]. Three intervals
have a lower bound **at** zero rather than above it, and two span it. The
direction is consistently positive on every test_seen metric; the magnitude is
small enough that none of it is separable from zero with confidence.

**The noise floor exists this time, and it is not small.** Three repeats are what
registration 1 gave up under its amendment 1 and reported as unmeasured. Measured:
on test_seen **13.5 % of cases disagree with themselves across their own three
repeats** — 0.135 of 111 in *both* arms, identically — and 9–11 % on train.
Temperature is 0 and the seed is 42 and sent on every call; that buys nothing here.
So a +0.011 shift in a template-averaged mean sits against a population where one
case in seven is not reproducible against itself, and the honest reading of every
interval above is that this suite, at this size, cannot resolve effects of this
size. That is a fact about the apparatus, and it is the fact P8c's report had to
leave blank.

**test_unseen is a ceiling and tells us nothing.** Every metric is 1.000 in both
arms; the win/loss table is **0 win, 0 loss, 6 tie, 1 no data**, T22 having no data
in the optimized arm. A split where both arms are perfect on everything measured
cannot discriminate between them, and the reason is visible one line up: **39 % of
its cases are excluded**, almost all `upstream`, so what survives to be scored is
the easy remainder. The d±3 capture widening (registration 2) did work — P8c lost
30 and 28 of 56, this run loses ~22 of 56 per repeat — but recovering a third of
the population was not enough to make the holdout informative.

**Both generalization gaps are small and neither excludes zero.** train →
test_seen is −0.007 [−0.030, +0.012] for the baseline and −0.011 [−0.031, +0.005]
for the optimized arm. test_seen → test_unseen is −0.139 and −0.118 as point
estimates over disjoint template sets, and both are artefacts of the ceiling above
rather than evidence of anything. No sign of the memorized constants or the
per-template routing table the two gaps exist to catch — on a suite this size,
still a weak statement rather than a clean bill.

**The abstention trade P8c found did not recur.** On test_seen, abstention
accuracy over the unanswerable cases moves **0.872 → 0.891** while false
abstention stays at 0.000 over ~290 answerable cases in both arms. P8c's optimized
arm bought 0.18 of answer with 0.24 of abstention accuracy; this one buys neither,
and gives nothing up. On train it does fall, 0.923 → 0.846 over 39 unanswerable
cases, which is the split the search selected on and therefore the one place the
weighting could express itself.

**Parse failures are gone.** `parse_failures` and `step_cap_exceeded` are **0.000
in all six conditions**. In P8c the two tracked each other exactly and neither was
zero. Mean steps fall to 1.23–1.28 from 1.78–1.89, mean extra calls to 0.15–0.35
from 0.87–2.68. Some of that is a stronger model; some of it is
`_final_text` no longer folding a reasoning model's thought parts into the message
the contract is parsed from (T134's first fix), without which every one of these
rollouts would have been scored on text no candidate wrote.

**Four things differ from P8c, so the two runs are never pooled and never
differenced.** The task model (`gemma-4-31b-it` → `deepseek-v4-flash-0731`); the
six tool texts (T136 added 605 characters that reach the task model on every
rollout of *both* arms, so the reference arm moved too); the capture neighbourhood
(±1 → ±3, which changes the exclusion rate, which is what the arms are read
against); and the provider regime — P8c was unpinned across OpenRouter's 29
providers for that id and **which mixture it drew is not recoverable**. Only the
qualitative questions carry across, and both are answered above: the exclusion
divergence did not recur, and the direction of the answer metric's movement did.

**Cost: €4.09 of the registered $6** — the search €1.01 and the eighteen measured
conditions €3.09, against a registered estimate of $4.4. 1686 rollouts at
workers = 1 took ~14 hours, and 2 rate-limit raises survived amendment 2's retry
budget out of 1686.

## The headroom runs (T142, T143)

**Measured on the superseded suite.** Both runs predate T138's generator fix and
T139's regeneration: their cases are `train.json`
`d66cc411…`/`test_seen.json` `a0d0a016…`, the files that carried the `{d}` and
`{thr}` stripe overlap between train and test_seen, and the committed files are
now `c8be84c0…`/`eadf8225…`. **No number below is comparable with one measured
after the regeneration**, and none of them is quoted beside one. `test_unseen`
is byte-identical across the repair, so only that split's figures survive the
change unaltered.

What does *not* depend on the repair is the T06 finding below: its answer is
`10.0` in train and test_seen on **both** sides of the regeneration, because the
template asks for one documented constant and its invariance is a property of the
question rather than of the sampler. The same holds for the exclusion-convergence
result and for the variance decomposition, neither of which reads a drawn value.

Three registrations now ask one question — *how much does prompt optimization
add?* — under three configurations, and the answer depends on what room the seed
and the model leave. They are reported side by side and **never pooled**: more
than one thing moved between any two of them.

| reg. | student | seed | test_seen answer Δ |
|---|---|---|---|
| 2 (T134) | deepseek-v4-flash | hand-written, 30,296 ch | +0.011 [0, +0.032] |
| 3 (T142) | deepseek-v4-flash | weakened, 4,964 ch | *search only* |
| 4 (T143) | ministral-8b | weakened, 4,964 ch | +0.087 [−0.046, +0.246] |

**Weakening the seed did far less than expected.** T142 cut the candidate
surface 84% — every routing rule, tool-result taxonomy and argument description
deleted, contract preserved byte-for-byte — and the train selection score fell
only 0.844 → 0.829. The per-scorer split says why: abstention lost 0.070 and
answer about 0.03, while **card recall rose 0.075**, because that metric scores
the union of every `lookup_reference` call in a run and an unguided agent calls
it more often. Half the degradation cancelled inside the 0.4/0.3/0.2/0.1 blend.
A metric can move the wrong way for a right reason, and the blend hides it.

**The model is the variable the text could not move.** deepseek answers this
suite at ~0.82 whatever the prompt says. Swapping the root agent for an 8B model
on the *same* weakened seed dropped the train selection score to 0.692 and
answer accuracy to 0.571 — 13.6 and 21.5 points of headroom where the text
alone had bought 1.5. §8's stated 20-point sensitivity is a claim about the
suite; this is the first configuration that clears it.

**T143's measurement is complete and its primary estimand does not exclude
zero**: 18 conditions, three repeats, \$2.85 of the \$6.00 registered limit,
written to `eval/measurements/20260827T104417Z.json`. Exclusion counts converged
between the arms on every split — test_seen 57 vs 59 of 375, test_unseen 82 vs
78 of 168, train 9 vs 8 of 300 — so §7's repeat condition does **not** fire and
the comparison is established rather than merely recorded. That convergence is
what P8c lacked (14 vs 25) and is the one procedural thing this run can claim
outright.

| test_seen | baseline | optimized | Δ [95% CI] |
|---|---|---|---|
| answer | 0.601 | 0.688 | +0.087 [−0.046, +0.246] |
| trajectory | 0.754 | 0.740 | −0.013 [−0.116, +0.072] |
| abstention | 0.829 | 0.864 | +0.036 [−0.033, +0.105] |
| card recall | 0.740 | 0.773 | +0.033 [−0.147, +0.273] |
| selection | 0.716 | 0.754 | +0.037 [−0.032, +0.110] |

### What the optimizer wrote, and why the gain is not what it looks like

Two of seven components changed; the five function-tool docstrings came through
byte-identical in **both** T142 and T143. The gain is concentrated in two
templates of nineteen, and dropping them dissolves it:

| test_seen answer, paired per case | n | Δ | 95% CI |
|---|---|---|---|
| all templates | 19 | +0.135 | [+0.012, +0.287] |
| minus T06 | 18 | +0.087 | [−0.010, +0.204] |
| minus T06 and T03 | 17 | +0.045 | [−0.026, +0.123] |

**T06 (+1.000) is answer memorization.** The winning root instruction states
*"The irrigation threshold for the extensive roofs is **10.0 %θ**"*, and `10.0`
is the gold answer of every T06 instance in train **and** test_seen — the
template asks for one documented constant, so only its phrasing varies. T06's
trajectory difference is `+0.000`: the agent still calls `lookup_reference` and
merely reports the number from its own prompt instead of from the card.

**T03 (+0.800) is a legitimate but template-shaped fix.** Train answers 9.514 pp
and test_seen 8.542 pp, so no constant transfers; what transfers is the recipe —
sensors `QEx1`/`QEx2`, per-timestamp alignment, and the `pp` unit rule.

**One change is genuinely general**, and it explains the abstention trade: *"Do
not confuse a zero aggregate with missing data. If a query like `SUM(...)`
returns `0.0`, that is a valid answer."* It lifts answer accuracy and suppresses
correct refusals at once — abstention accuracy on unanswerable cases falls
0.978 → 0.860 on test_seen and 0.778 → 0.556 on test_unseen, while false
abstention improves 0.125 → 0.092 and 0.153 → 0.032. Both losing templates
(T01 −0.250, T04 −0.133) are wetland scope-limit questions.

**And one change is fabricated.** The winning `text_to_sql_agent` text names a
`measurements` table with columns `outflow_L` / `roof_segment` / `date` and
instructs the caller to *"pass a complete SQL query string"*. None of that
exists — the tables are `outflow`, `radiation`, `swc`, `tsoil`, `wetter`, and
the sub-agent takes natural language. The candidate won carrying it, and
nothing in the harness validates a candidate's claims about the schema.

### The methodological finding: test_seen cannot catch a memorized constant

§7 states that the train → test_seen gap *"catches memorized constants and
phrasing overfit"*. **It cannot, where the constant is invariant across a
template's instances.** test_seen shares its templates with train by
construction, so a memorized T06 answer is *correct* on test_seen and scores
as a gain rather than as overfit. Only test_unseen sees through it — and there
the answer gain is +0.058 with trajectory falling 0.033 and a win/loss table of
1 win, 1 loss, 5 ties, which §7 restricts to description anyway.

This is a blind spot in the design, not in the run, and it is why **T144's
leakage check is mechanical**: a gold answer present in a candidate and absent
from the seed is refused on the measurement path and reported on the search
path.
Run against T143's own winner it flags `10.0` (T06) and `0.0` (T01, the
zero-aggregate illustration — a debatable flag kept deliberately, since `0.0` is
genuinely T01's answer and T01 got *worse*).

### More rollouts would not have helped; more templates would

A variance decomposition over test_seen settles what the wide interval is made
of. Between-template variance is 0.0923; within-case variance across repeats is
0.0358, which at 4.4 cases per template and three repeats contributes **3.0%**
of the total. Infinite repeats would narrow the SD from 0.3038 to 0.2993 — a
1.5% narrowing, nowhere near enough. §8's claim that "instances of the same
template asymptote and only more templates move the ceiling" is now measured
rather than asserted.

| templates | power to exclude zero |
|---|---|
| 19 (current) | 0.63 |
| 25 | 0.75 |
| 40 | 0.92 |
| 60 | 0.99 |

**The search, by contrast, was budget-starved rather than converged.** A full
valset evaluation costs 100 rollouts, so 500 metric calls bought six iterations;
improvements landed at 1, 2 and 3, and iterations 4–6 found nothing before the
budget ended. Three barren iterations is not a plateau. A larger budget is the
lever that targets effect size, where repeats target noise that is already
negligible — with the caveat that more iterations are also more opportunities to
bake in a constant, which is what T144's check now guards.

## The exclusion cascade, and why the cache is what causes it

**Measured on T143's 18 conditions**
(`eval/measurements/20260827T104417Z.json`),
which lost 160 of 336 test_unseen rollouts and 116 of 750 test_seen rollouts to
`harness_error`. Every one of them is `upstream`. The loss is not spread over
the suite: it is concentrated in a *kind* of question, and one miss costs the
whole case rather than one call.

**It falls on forward-looking templates, and on nothing else.**

| split | template | excluded | what it asks |
|---|---|---|---|
| test_unseen | T22 | 45/48 (94 %) | soil moisture predicted **tomorrow** |
| test_unseen | T18b | 42/48 (88 %) | forecast dew point, **next 5 days** |
| test_unseen | T26 | 32/48 (67 %) | albedo 0.05, 20 mm on **day 1 of 2** |
| test_unseen | T23 | 30/48 (62 %) | where moisture **would lie today** if… |
| test_seen | T21 | 73 % | minimum moisture over the **next 7 days** |
| test_seen | T09 | 57 % | moisture below a threshold, **next 7 days** |
| test_unseen | T17b | 0/48 | — |

T17b loses nothing. The split is not between templates but between *window
kinds*: a question naming an absolute past window resolves to one request and
hits; a question naming a relative forward window resolves to whichever window
the candidate chose, and the cache is keyed on the request.

**The cascade, and its size.** An excluded case averages **5.45 extra tool
calls** against **0.73** for an included one — 7.5×. 38 % of excluded cases hit
the step cap and 40 % end in a `parse_failure`. The sequence is: the candidate
resolves a forward window a day away from the oracle's → the request key misses
→
`upstream` → the agent retries with different arguments → misses again → burns
its step budget → emits no parseable contract. **One key miss costs the case,
not
the call**, which is why per-condition `upstream` counts (75–124) exceed the
number of cases that carry them.

The agent's half of that is a prompt matter: §3 says an `upstream` error means
*do not repeat the call*, and the weakened seed is exactly the text that deleted
the tool-result taxonomy saying so. That explains the amplitude. It does not
explain the miss.

**Why ±3 did not fix it.** Registration 2 widened the capture neighbourhood from
`[-1, 1]` to `[-3…3]`, 509 → 947 entries, and test_unseen still lost ~39 %.
Two reasons, both structural rather than a matter of degree:

- **A window has two degrees of freedom**, not one. The neighbourhood varies one
  offset; a candidate may move the start, the end, or reach the same days
  through
  `past_days`/`forecast_days` instead of absolute dates. The covered set is a
  line
  through a plane.
- **GR2L is keyed on `data[]` plus parameters** (§5) — the *entire* fetched row
  array. A window differing by one day is not a nearby key, it is an unrelated
  one. There is no locality in the key for a wider neighbourhood to exploit.

**The fix the architecture already licenses.** §5 states what the cache is for:

> What the cache is load-bearing *for* is GR2L; for weather it is cost and
> speed, the station needing no entry at all and Archive being re-fetchable
> indefinitely.

So a weather miss on the measurement path is being treated as fatal when the
architecture says weather determinism does not rest on the cache at all. Three
changes follow, in order of how much they buy:

1. **Cache weather per day, not per request.** One entry per `(source, date)`,
   assembled into whatever window is asked for. Any window then hits as long as
   its *days* are captured, and the days are bounded — `as_of` plus the 16-day
   horizon §3.3 enforces. This turns a plane of possible windows into a bounded
   set of days and should remove the weather half of the misses outright.
2. **Let Archive fill a measurement miss, and record it.** Reanalysis of a past
   window is stable, which is the property §5 already relies on. The station
   path
   needs nothing: it is a pure function of the pinned DB. GR2L keeps the strict
   rule, since that is the component whose determinism the cache carries.
3. **Capture over the horizon, not over a neighbourhood.** For a forward
   template the reachable windows are enumerable from `as_of` and the 16-day
   cap, so the capture pass can cover them exhaustively rather than guess an
   offset.

**What this would change in the numbers.** The exclusion rate is not noise: §7
reads per-arm exclusion counts to decide whether a comparison happened under
equal conditions at all, and test_unseen currently answers on 28–31 of 56 cases.
Its seven templates already support description rather than inference (§8); at a
50 % loss they support less than that. The four worst-hit templates are all
model-bearing or forecast-facing, so the loss is **not** uniform over what the
suite measures — it falls hardest on exactly the families the water-balance
tools
exist for.

## External endpoints and what they can carry

Measured while sizing P8c's measurement run, and the reason the run is routed
the
way it is. None of it is a result; all of it is infrastructure that decides
whether a result can exist.

**`saia.gwdg.de` stopped serving chat completions.** `GET /v1/models` answers
200 in 0.11 s and lists 16 models; `POST /v1/chat/completions` for
`qwen3.6-35b-a3b` either hangs past 110 s or returns an **empty HTTP 500** with
`content-length: 0` and `x-kong-upstream-latency: 10` — the gateway is fine and
the model backend is not. Rate-limit headers read healthy at the time
(`x-ratelimit-remaining-day: 365` of 400), so it is a backend fault rather than
metering. This is the endpoint T107 re-pinned to and the one every P8 packet up
to T126 ran against.
*Verified:* repeated `curl` against both endpoints, and `just pins-task` failing
through litellm's retries. *Date:* 2026-08-25.

**`chat-ai.academiccloud.de` serves the models but returns empty 500s in
bursts.** `gemma-4-31b-it` came back 6/8 and `qwen3.5-122b-a10b` 3/8 across
successive probes, with the same empty-500 signature. Quota is **30/minute,
200/hour, 1000/day, 3000/month — per key**, and `.env` holds two keys against
this host. Because a rollout takes up to `MAX_LLM_CALLS` turns, an unretried
rollout completes with probability (1−p)^7; at the observed p that is a minority
of rollouts, and the exclusions it produces are a fact about the endpoint rather
than about the candidate. Hence `AssistantSettings.llm_num_retries`, which is a
litellm-side count: it never reaches the provider, so it moves no pin.
*Verified:* 8 probes per model per key, and the rate-limit headers on each.
*Date:* 2026-08-25.

**A rollout costs about 13 requests, which is what the daily caps meter.**
Measured off the quota counter rather than from the diagnostics: three rollouts
plus two probes moved `x-ratelimit-remaining-day` from 486 to 445. The gap
between that and `model_turns` (2, 0, 3 for those three) is the text-to-SQL
chain's own calls — the sub-agent runs on its own `Runner` and its turns never
enter the root event stream — plus the retries the 500s force. So the
measurement run's 562 rollouts are ~7300 requests against a 1000/day cap: four
to nine days across two keys, which is why the task model is routed to
OpenRouter instead.
*Verified:* quota counter before and after three live rollouts.
*Date:* 2026-08-25.

**OpenRouter prices and reliability, measured over 11 rollouts.**
`qwen/qwen3.6-35b-a3b` at \$0.14/\$1.00 per Mtok cost **\$0.0063 per rollout**
across a seven-template spread (T01, T06, T09, T13, T17a, T21, T24a, T27), read
from the provider's own usage counter and not estimated — the ledger's
token-derived estimate was 2.3× higher, which prompt caching accounts for.
`google/gemma-4-31b-it` is cheaper at \$0.10/\$0.34 and measured **€0.0025 per
rollout** on the smoke search. No request failed. So the registered protocol —
2 arms × 281 cases × 3 repeats = 1686 rollouts — is about \$10.60 on
qwen3.6-35b, against a \$5 budget; that arithmetic is what amendment 1 responds
to.
*Verified:* OpenRouter `/api/v1/key` usage deltas around each batch.
*Date:* 2026-08-25.

**Two baseline failures visible in the very first live rollouts, and both are
what the search exists to move.** On `qwen3.6-35b-a3b`, **T13 and T21 spent the
whole 7-call step cap without producing a final message** — `parse_failure` with
`step_cap_exceeded`, which §7 reports apart from answer accuracy precisely so
this stays distinguishable from wrong reasoning. And **T09 came back
`harness_error`** under replay: the rollout chose a window the capture pass never
recorded, so the miss became an `upstream` error and the case excluded. That is
the measurement path's exclusion channel working as specified, and the per-arm
count of it is what §7 asks to be published beside the results.
*Verified:* eight live rollouts on the handwritten baseline. *Date:* 2026-08-25.

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

**The value stripe was not a property of the value on `{d}`, and the committed
suite carries it.** `{d}` is fed by five horizon lists — `FORWARD_HORIZONS`
`(2,3,4,5,6,7)`, T15b's `(3,4,5,6,7,8,10)`, `PAST_HORIZONS` `(3,5,7,10,14)`,
T20's `(3,4,5,6,7)` and T23's `(2,3,4,5,7,10)` — each striped over itself, each
internally disjoint. Read over the parameter rather than over
`(template_id, {d})`, `train ∩ test_seen = {3, 4, 5, 6}`: **21 of 100 train cases
and 27 of 125 test_seen cases** in the committed files carry a horizon the other
side also carries, `d = 3` being train's through T15b and test_seen's through
T09, T13, T14 and T21. `{thr}` was one draw away from the same thing — 25 is
train's through T09's `MOISTURE_THRESHOLDS` and test_seen's through T02's
`HOT_DAY_THRESHOLDS`, and no train draw took it. `{x}` is placed twice as well
and is inert only because T23 is holdout. Every check the repo had passed,
because all four key on `(template_id, parameter)` while §1.7 states the rule on
the parameter alone. The generator is repaired (T138: one ladder per parameter)
and the suite regenerated onto it (T139).
*Verified:* `splits.value_overlaps` over the committed `train.json` and
`test_seen.json`, and by striping each pool directly through
`splits.pools()["train"|"test_seen"]`. *Date:* 2026-08-27.

**What the regeneration moved, and what it did not.** Re-emitting the suite on
the repaired stripe changes **36 of 100 train cases and 64 of 125 test_seen**,
not all 225: `_pick` calls `rng.choice` once whatever the pool holds, so a
changed pool changes the value drawn and not the stream position, and a template
whose ladder left its pool alone re-draws identically. The case ids are the same
set on both sides. **`test_unseen.json` is byte-identical**, the holdout taking
every pool whole and its rng being seeded per split. The ledger is unmoved —
100 / 125 / 56, zero shortfalls, abstentions 13 / 16 / 16, languages 50/50,
63/62, 28/28 — and both overlap reports are `{}`. The capture pass over the new
windows answered **281 of 281, 0 refused and 0 failed**, added 336 entries
(954 → 1290) and found **every case's recomputed answer equal to its committed
one**, so the ground truth reproduces against the live GR2L and Archive rather
than being assumed to. It adds and never prunes, so entries keyed to the old
draws' windows remain committed and unused. `eval/measurements/`'s two runs
(P8c, T134) describe the pre-T139 suite and may not be differenced against
anything measured after it.
*Verified:* `just cases`, `just cases-check` ("byte-identical to a fresh
generation pass"), `just capture`, `just capture-verify` (0 live calls, 0
entries recorded). *Date:* 2026-08-28.
