## Water Assistant Agent

Evaluation of Prompt Optimization Techniques for a Water Management LLM Assistant with Retrieval-Augmented Generation

### Run

#### Development stacks

Experiments/ops infra, split in two so the light half can run on its own:

- **MLflow** (experiment tracking) + its Postgres + MinIO (artifact storage) — `docker-compose.yml`
- **Argilla** (labeling) + Elasticsearch + Redis + its Postgres — `docker-compose.argilla.yml`

Argilla is opt-in: Elasticsearch alone reserves 512 MB of heap, so leave it down
unless you are actually labeling.

```bash
just dev-up           # MLflow only (the usual case)
just dev-down         # Stop it

just dev-full-up      # MLflow + Argilla
just dev-full-down    # Stop both
just dev-full-ngrok   # + ngrok HTTPS front for the Argilla UI (needs NGROK_AUTHTOKEN)
```

Or run directly:
```bash
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml -f docker-compose.argilla.yml up -d
```

Both files share one compose project, so `just dev-down` only stops the MLflow
half (a running Argilla half is reported as an orphan and left alone) — use
`just dev-full-down` to take everything down. MLflow is on http://localhost:5000,
MinIO console on http://localhost:9001, Argilla on http://localhost:6900.

#### Production stack

The production stack runs Postgres, FastAPI backend, and Next.js frontend together.

```bash
just prod-up          # Start the stack (Ctrl-C stops it)
just prod-down        # Stop the stack
just prod-ngrok       # Start with ngrok HTTPS front (no certs needed)
```

Or run directly:
```bash
docker compose -f docker-compose.prod.yml up --build
docker compose -f docker-compose.prod.yml -f docker-compose.prod.ngrok.yml up --build
```

### Chat frontend

A multi-user chat UI (Next.js + CopilotKit over AG-UI) lives in `web/`, backed by
the FastAPI `water-assistant` service. The backend owns all auth, conversation
metadata, and per-user isolation; the browser only ever talks to the Next.js
route handlers (auth cookie, REST proxy, CopilotKit runtime) — never to FastAPI
directly.

#### Configure

Copy `.example.env` to `.env` and set the service vars (all `WATER_ASSISTANT_*`
plus `AGENT_JWT_SECRET`):

- `AGENT_JWT_SECRET` — **required**; JWT signing secret (the service refuses to
  start without it).
- `WATER_ASSISTANT_SESSION_DB_URL` — **required**; one async SQLAlchemy URL backs
  ADK sessions + users + conversations. Dev default:
  `sqlite+aiosqlite:///./water.db`. Prod: `postgresql+asyncpg://…`.
- `WATER_ASSISTANT_ADMIN_API_KEY` — admin API key; when unset the `/admin/*` API
  fails closed (503). Set it to provision users.
- `WATER_ASSISTANT_AUTH_TOKEN_TTL_DAYS` — access-token lifetime (default 7).

The frontend reads `web/.env` (`BACKEND_URL`, default `http://localhost:8080`).
Schema is created on startup (`metadata.create_all`); there are no migrations yet.

#### Run

```
just chat        # backend + frontend together (Ctrl-C stops both)
```

Or run each half on its own: `just assistant` (FastAPI, port 8080) and `just web`
(Next.js dev server, port 3000). Open http://localhost:3000 and sign in.

#### Provision the first user (admin API)

There is no signup page — accounts are created only through the admin API, guarded
by `X-Admin-API-Key`. Create the first user with curl:

```
curl -X POST http://localhost:8080/admin/users \
  -H "X-Admin-API-Key: $WATER_ASSISTANT_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "s3cret", "display_name": "Alice"}'
```

Other admin routes: `GET /admin/users`, `PATCH /admin/users/{id}`,
`DELETE /admin/users/{id}` (cascades the user's conversations and ADK sessions).
Then sign in from the web UI, or check credentials directly:

```
curl -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "s3cret"}'
```

#### Experiments


#### Given
Green roofs:
Four green roofs in Leipzig: near the Opera House, MDR, UFZ, Tarosser strasse.
Data:
  - Soil temperature and moisture timeseries
  - Vegetation indexes from satellites and UAV data (few rasters)
  - Weather data

- Tools:
  - GR2L model: given a roof and meteorogical parameters returns water stored in substrate/retention layers, the actual evapotranspiration, and runoff leaving the system

Wineyards (**to do after green roofs if have time**):
Data:
  - Soil temperature and moisture timeseries
  - Vegetation indexes from satellites and UAV data (few rasters)
  - Weather data

- Tools:
  - Weinbau model: similar with the GR2L model but also provides daily irrigation recommendations

It's better to start with the green roofs as we have the GR2L model.

The **problem**: one should dig into database to find insights and manually call the tools, analyse results. No text-to-data interface.
**Solution**: LLM/Agent workflow accessing data. At that point no novelty.

#### Questions and goals

- Evaluate different LLMs for solving the task
- Evaluate prompt optimization techniques for the domain
- Measure prompt optimization accuracy gain over model size
- Analyze performance of open-source models to explain their performance for water management
- Try to improve any prompt optimization techinque: GEPA / MIPROv2 / TextGrad. For example try to merge the GEPA and MIPROv2 approach.
- Try unsupervised / bootstrapped GEPA: given dataset X manually setup an LLM-as-judge prompt and use it as a metric. Compare it with the supervised approach with Y. Find some text-to-text datasets.
- Adding noise to the algorythms (inverting metrics)
- Mixing / ensembling algorythms: mixing prompt pools?

How we will evaluate: manually create a set of QA pairs with data states of a water-soil system (in simpler, just text-2-SQL case QA is a natural language query and it's DDL counterpart).


#### Solution architecture

Now I see one agent (core agent) with tools: a text-2-sql tool, a model tool, some simple tools for calculations (PM evaporation).

Every site (roof / wineyard) has an own semantic layer with terms definitions and assumptions (initialized with us, probably optimized). Some part of the core agent's prompt should be shared before sites.

Text-2-SQL prompt is shared (or even a ready solution is used), schema description could be different.


#### Prompt-optimization training

The text-2-SQL system prompt is optimized by one of three interchangeable techniques, each a
`just` recipe that logs a before/after val+test eval to MLflow (experiment name and tracking
URI come from `.env`):

- `just text2sql-train-gepa …` — **GEPA**. The proposer is the
  `--teacher-model`/`--teacher-endpoint` role.
- `just text2sql-train-textgrad …` — **TextGrad**. `--batch-size` sets records per gradient
  step and `--val-gate-size` the fixed val subset scored for the per-step keep-best gate; the
  backward/proposal LLM is the `--optimizer-model`/`--optimizer-endpoint` role.
- `just text2sql-train-…-skillopt …` — **SkillOpt** (Microsoft's ReflACT loop: rollout →
  reflect → merge/select edits → keep-best on a hard validation gate). Structural knobs:
  `--edit-budget` (max prompt edits applied per round, a textual learning rate) and
  `--minibatch-size` (reflection batch).
  `--reflect-on-success/--no-reflect-on-success` (default off) toggles
  success reflection; failure reflection is always on. The reflection/edit LLM is the
  `--optimizer-model`/`--optimizer-endpoint` role. Only the prompt's **instruction block** is
  the trainable skill; the DB schema stays fixed context, and `best_skill.md` is recombined
  into the full template before registration. A new prompt version is registered only when the
  best skill strictly beats the baseline on the validation gate (otherwise the seed prompt is
  kept byte-for-byte).

**The only stop is a money budget.** Every trainer requires `--budget` (EUR, no default);
the run's optimization phase stops at the technique's next natural checkpoint (GEPA
iteration / TextGrad gradient step / SkillOpt rollout round) once billable spend reaches it,
keeping the best-so-far prompt exactly as a natural finish would. The former effort caps are
gone as stops: GEPA's metric-call budget, TextGrad's `--epochs`/`--metric-call-budget`/
`--max-steps-per-epoch`, SkillOpt's `--epochs`. Spend is priced from six env vars
`PRICE_{TASK,JUDGE,OPTIMIZER}_{INPUT,OUTPUT}` in **EUR per 1M tokens** (see `.example.env`;
a missing/invalid price refuses to start, naming the offending variable). Every run logs the
budget + prices as params and, per role, `tokens_*_{input,output}` / `cost_*` metrics plus
`cost_total` (billable), `cost_excluded` (the bracketing passes: test-before/after and the
baseline/final full-val evals, which never charge the budget), `unmetered_calls`, and an
`optimization_stop_reason` param (`budget_exhausted` / `completed` / `failed` — logged even
on FAILED runs). Cost-profile note: TextGrad's engine disk cache is disabled for metered
runs, so every call pays real tokens — pre-budget TextGrad runs partially rode that cache
and are only comparable via the post-hoc trace audit (`scripts/count_tokens.py`);
`scripts/verify_budget_stop.py` reconciles a run's live meter against that audit and checks
the returned prompt is the best-on-validation one.

**Learning curves (quality per EUR).** Pass `--probe-interval-eur K` (0 = off, the default;
`probe_interval_eur` in `common.just` for the recipes) and every `K` EUR of billable spend
the run pauses at its next natural checkpoint and scores its **best-so-far** prompt — the
one it would return if the budget stopped it there — on the same held-out 55-record test
split and with the same judge as `test_quality_{before,after}`. That turns a run's two
generalization data points into a curve: `curve_test_quality` is logged against a money
x-axis (the MLflow step is spend in cents, so curves from different runs and techniques
overlay directly), together with `curve_spend_eur` / `curve_probe_cost_eur` /
`curve_prompt_changed` and a `learning_curve.json` artifact; the curve's endpoints reuse the
before/after evaluations, so they cost nothing extra.
`scripts/export_learning_curves.py` collects the curves of several runs into one tidy CSV
for plotting. **Probe spend never charges `--budget`** — it goes to its own `cost_probe`
bucket, so a probed run performs exactly the same optimization work as an unprobed one —
but it is real money and time on top of it: ≈ 0.35 EUR and ≈ 35 min per point at the
default `--probe-workers 1` (sequential, like the before/after phases it must match; raise
it to trade endpoint concurrency for wall clock). So `K` should be set against the run's
budget, and `--max-probes` (default 20) caps the instrumentation bill. Probing an unchanged prompt is
served from cache and costs nothing, and a failed probe is a hole in the curve, never a
failed run. **Reporting only:** the curve does not feed back into optimization, and the
result of a run remains the prompt its own validation gate selected — picking the
best-scoring probe point afterwards would be test-set selection.

All three share the same dataset loader, the same seeded train/val/test split (`--sampler-seed`),
the same FLEX LLM-as-Judge, the same metric names, and register the same prompt — so runs are
directly comparable in the MLflow UI. **Three model roles** are configured per run:

- **task** (`--model`/`--endpoint`) — the student model that generates the SQL.
- **judge** (`--judge-model`/`--judge-endpoint`) — the LLM-as-Judge scoring correctness.
- **proposer** — GEPA's `--teacher-*`, or TextGrad's / SkillOpt's `--optimizer-*`, the model
  that rewrites the prompt.

The proposer sampling (temperature/top-p/top-k/seed) is read from the `OPTIMIZER_*` variables
in `.env` (see `.example.env`; defaults `0.0 / 1.0 / 1 / 42` for deterministic, reproducible
proposals) and is logged on the TextGrad and SkillOpt runs only, so GEPA runs stay unaffected.


## Literature


### Green Roofs

Research green roof in Leipzig, Germany
https://www.sciencedirect.com/science/article/pii/S0925857425002198
- Describes the green roof we operate.

Weather forecast driven irrigation of green roofs improves their thermal and
hydrological performance
https://www.sciencedirect.com/science/article/pii/S0048969726003530
- A smart weather forecast algo for irrigation decreases water usage and minimizes runoff.

Green Roofs Classifications, Plant Species, Substrates
https://doi.org/10.1016/b978-0-12-812150-4.00006-9
- Green roofs fundamenthals

Evapotranspiration Measurements and Assessment of Driving Factors: A Comparison of Different Green Roof Systems during Summer in Germany
https://www.researchgate.net/publication/356803878_Evapotranspiration_Measurements_and_Assessment_of_Driving_Factors_A_Comparison_of_Different_Green_Roof_Systems_during_Summer_in_Germany
- Deep comparison of garden, nature, economy and retention roofs. PAI, moisture, temperature, meteorological data, evapotranspiration data could be requested from them.

GR2L: A robust dual-layer green roof water balance model to assess multifunctionality aspects under climate variability
https://www.frontiersin.org/journals/climate/articles/10.3389/fclim.2023.1115595/full
- Our tool.

### LLM benchmarks for Earth system

EarthSE: A Benchmark for Evaluating Earth Scientific Exploration Capability of LLMs:
https://arxiv.org/pdf/2505.17139
- Earth system QA LLM benchmark. 4000+ questions w/ target are open: https://huggingface.co/datasets/PrismaX/Earth-Gold
- They picked 10.000 articles, feed content to GPT-4 to generate QA pairs (T/F, Multiple choise, Fill-in-blanck, open-ended), then evaluated the models on the generated QA pairs.
- Categories: Term Explanation, Knowledge QA, Fact Verification, Analysis, Relation Extraction, Calculation, Tool Utilization, Literature Citation, Dataset, Experimental Design, Code Generation
- Results:
  - Grok-3 is the best model in factual knowledge, Sonnet 3.5 is worse than 3.7;
  - Models perform similiarly across questions about different earth spheres, excepting Sonnet 3.7 and 4o.

GeoAnalystBench: A GeoAI benchmark for assessing large language models for spatial analysis workflow and code generation.
https://arxiv.org/pdf/2509.05881
- Spatial data tasks code generation benchmark. It has logistics, shape / position detection questions. There are 50 questions with target (not for all questions) are open: https://github.com/GeoDS/GeoAnalystBench/blob/master/dataset/GeoAnalystBench.csv
- Results:
  - Data description boosts models performance
  - Sonnet 3.5 is close with 4o what is controverisal with the EarthSE benchmark


UnivEARTH
https://arxiv.org/html/2504.12110v2
- Code generation with GEE. 140 QA (Y/N format) dataset, global scale
- They also picked articles and sampled questions with Sonnet 3.5. Then model should select a GEE layer (from 400), generate code and give an answer
- Results:
  - 33% accuracy, in 58% cases code generation fails. Multi-turn and reflection does not help.
  - Web search improved results



A Comprehensive Evaluation of Multimodal Large Language Models in Hydrological applications
https://eartharxiv.org/repository/object/7176/download/13733/
- Actually the evaluation is not comprehensive: authors picked some images of hydrosphere and inferenced some 2023 models. No dataset, not metrics comparision.


RS-Agent: Automating Remote Sensing Tasks through Intelligent Agent
https://arxiv.org/pdf/2406.07089
- Processing focused code generation (i.e., denoise, remote clouds, count objects) benchmark over sattelite RGB data, not really in scope of current work.

---

### LLMs for water management / irrigation

SPADE: A Large Language Model Framework for Soil Moisture Pattern Recognition and Anomaly Detection in Precision Agriculture
https://arxiv.org/pdf/2509.18123
- They picked GPT-4.1 and just fed soil moisture timeseries as text, soil moisture patterns description and asked the model to find anomalies / irrigation events.
- No prompt optimization while they have the TS with descriptions dataset. Actually their prompt with irrigation patterns could be intesting for us.

LLM-Orchestrated Digital Twins for Safe, Human-Centered Decision Support in Precision Agriculture
https://openreview.net/pdf/c02cc4dbacbc373bc2d5028378897a7ff5708c0b.pdf
- Evaluated LLM with tools on digital twins of farms. Data: NL queries / actions pair (not available), no model specified.

Digital twins for green roofs and facade inspection using drones and multi-modal foundation models
https://www.e3s-conferences.org/articles/e3sconf/pdf/2025/08/e3sconf_eenviro2024_05001.pdf
- They just picked photos of vegetation on greenroofs and used 4o to desribe is vegetation there / healthy.

IWMS-LLM: an intelligent water resources management system based on large language models
https://iwaponline.com/jh/article/27/11/1685/110111
- Introduced the concept of LLM with RAG for water management. No QA data, no code, no actual novelty.

---

### Prompt optimization (pure algo)

GEPA
https://arxiv.org/abs/2507.19457
- Continious merging / mutating of prompts resulting in highest metrics on XY pairs

MIPRPOv2
https://arxiv.org/pdf/2406.11695
- Injecting few-shot examples from datasets resulting in highest metric to the optimized prompts

TextGrad
https://arxiv.org/pdf/2406.07496
- Introduces TGD (Textual Gradient Descent): a teacher LLM proposes prompt change given X, student LLM output of X and LLM-as-Judge judgements for y and LLM_student(X)


- p1: Better Prompt Optimization with Fewer Prompts  
https://arxiv.org/html/2604.08801v1  

- Researches how system / user prompt affect GEPA optimization. More examples - worse results. It's coherent with https://decagon.ai/blog/optimizing-gepa-for-production

- SkillOpt: could beat GEPA
https://microsoft.github.io/SkillOpt/#idea

### Text to SQL



### Ideas for prompt tuning enhancements:
- Force GEPA to maintain some prompts from MIPRPOv2 in a buffer
- Apply any GD heuristics: add noise, curriculum learning, any analogy of LR scheduling, warm-up, dropouts (for pormpts?)
- Unsupervised / semisupervised GEPA


### Things to check:
- English / German / Broken English question effect
- 

### Extra
TabPFN
Zeroshot timeseries prediction
https://github.com/priorlabs/tabpfn  

Text-2-SQL approaches (internal):
https://linear.app/finalyst/issue/FIN-7/create-text2sql-evaluation-pipeline

Temperature for LLM-as-Judge. Set 0.0.
https://arxiv.org/pdf/2603.28304


### Stack
- Argilla for labelling. Two projects with different layout: text-2-SQL and for complex analyzis with fixtures. Open question: how to inject fixtures?
- MLflow for prompt tuning / experiments
