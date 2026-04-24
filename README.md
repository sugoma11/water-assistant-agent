## Evaluating prompt optimization techniques for water management analysis assistant with RAG

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


p1: Better Prompt Optimization with Fewer Prompts  
https://arxiv.org/html/2604.08801v1  
 - Researches how system / user prompt affect GEPA optimization. More examples - worse results. It's coherent with https://decagon.ai/blog/optimizing-gepa-for-production





### Ideas for prompt tuning enhancements:
- Force GEPA to maintain some prompts from MIPRPOv2 in a buffer
- Apply any GD heuristics: add noise, curriculum learning, any analogy of LR scheduling, warm-up, dropouts (for pormpts?)
- Unsupervised / semisupervised GEPA

### Extra
TabPFN
Zeroshot timeseries prediction
https://github.com/priorlabs/tabpfn  

Text-2-SQL approaches (internal):
https://linear.app/finalyst/issue/FIN-7/create-text2sql-evaluation-pipeline


### Stack
- Argilla for labelling. Two projects with different layout: text-2-SQL and for complex analyzis with fixtures. Open question: how to inject fixtures?
- MLflow for prompt tuning / experiments
