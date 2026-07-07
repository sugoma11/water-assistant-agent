import "models.just"
import "common.just"
import "experiments.just"

# ── Chat frontend + backend ───────────────────────────────────────────────────

# Run the FastAPI backend (AG-UI ADK endpoint at "/"; auth + conversations API)
assistant:
    uv run water-assistant

# Run the Next.js chat frontend dev server (talks to the backend via route handlers)
web:
    cd web && npm run dev

# Run backend + frontend together for the end-to-end chat setup (Ctrl-C stops both)
chat:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT
    uv run water-assistant &
    (cd web && npm run dev) &
    wait

# Generate domain-specific questions for a water management LLM assistant
# openai/qwen3-235b-a22b looks the best from GAIA
generate-questions model output-dir terms-path num-questions:
    uv run generate-questions --model {{model}} --output-dir {{output-dir}} --terms-path {{terms-path}} --num-questions {{num-questions}}

# Generate questions via a local CLI agent (claude or copilot)
generate-questions-cli tool model output-dir terms-path num-questions:
    uv run generate-questions-cli --tool {{tool}} --model {{model}} --output-dir {{output-dir}} --terms-path {{terms-path}} --num-questions {{num-questions}}

# Generate questions via a local CLI agent using a database schema (synthetic data)
# models: claude-opus-4.6; gpt-5.4
generate-questions-cli-synth tool model output-dir schema-path num-questions:
    uv run generate-questions-cli-synth --tool {{tool}} --model {{model}} --output-dir {{output-dir}} --schema-path {{schema-path}} --num-questions {{num-questions}}

# Generate a DuckDB SQL query for a single question via claude -p (schema embedded in the prompt)
text2sql model schema-path question:
    uv run text2sql-cli --model {{model}} --schema-path {{schema-path}} --question {{quote(question)}}

# Generate DuckDB SQL queries for a file of questions (one per line) via claude -p; writes JSON
text2sql-batch model schema-path questions-file output:
    uv run text2sql-cli --model {{model}} --schema-path {{schema-path}} --questions-file {{questions-file}} --output {{output}}

# Create an Argilla dataset and upload questions from a .txt or .json file
# ui: v1 (question/SQL) or v2 (adds required German/Denglish prod_question)
argilla-create input dataset-name ui="v2":
    uv run argilla-labeling create --input {{input}} --dataset-name {{dataset-name}} --ui {{ui}}

# Upsert questions from a .txt/.json/.jsonl file into an existing Argilla dataset
argilla-upsert input dataset-name ui="v1":
    uv run argilla-labeling upsert --input {{input}} --dataset-name {{dataset-name}} --ui {{ui}}

# Dump an Argilla dataset (records + responses + metadata) to a JSON file
argilla-dump output dataset-name:
    uv run argilla-labeling dump --output {{output}} --dataset-name {{dataset-name}}

# Recreate an Argilla dataset from a dump file produced by argilla-dump
argilla-load-dump input dataset-name ui="v1":
    uv run argilla-labeling load-dump --input {{input}} --dataset-name {{dataset-name}} --ui {{ui}}

# Copy an Argilla dataset (settings + records + responses) to a new name
argilla-copy source target ui="v1":
    uv run argilla-labeling copy --source-dataset {{source}} --target-dataset {{target}} --ui {{ui}}

# Export submitted records as JSON [{question, sql, argilla_link, ...}, ...]
# ui: v1 -> {question, sql, argilla_link}; v2 also adds prod_question
argilla-export output dataset-name ui="v2":
    uv run argilla-labeling export --output {{output}} --dataset-name {{dataset-name}} --ui {{ui}} --include-pending

# Update the labeling UI template of an existing dataset (preserves data)
argilla-update-ui dataset-name ui="v1":
    uv run argilla-labeling update-ui --dataset-name {{dataset-name}} --ui {{ui}}

# Review text-to-SQL labels with an LLM (claude -p) and write verdicts to notes.
# Extra args, e.g.: just argilla-review my-ds "--check-submitted --force --model opus --dry-run"
argilla-review dataset-name args="":
    uv run python scripts/argilla_review.py review --dataset-name {{dataset-name}} {{args}}

# Builds duckdb from raw data files (Outflow.txt, Radiation.txt, etc.)
build-db:
    uv run python scripts/build_db.py


# Evaluate text-2-SQL generation with an LLM-as-Judge and log results to MLflow
# Experiment name + tracking URI are read from .env (MLFLOW_EVAL_EXPERIMENT_NAME, MLFLOW_TRACKING_URI)
text2sql-eval model endpoint judge-model judge-endpoint use-prod-questions="false" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-eval --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# GEPA-train the text2sql system prompt; before/after val+test evals logged to MLflow
# Experiment name + tracking URI are read from .env (MLFLOW_TRAIN_EXPERIMENT_NAME, MLFLOW_TRACKING_URI)
# text2sql-train-gepa model=qwen36-35b-blablador endpoint=blablador judge-model=qwen-397b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-blablador teacher-endpoint=blablador sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
#     uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

text2sql-train-gepa model=eve-instruct endpoint=blablador judge-model=eve-instruct judge-endpoint=blablador teacher-model=eve-instruct teacher-endpoint=blablador sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# TextGrad-train the text2sql system prompt; before/after val+test evals logged to MLflow
# The money budget is the sole stop; --optimizer-* drives the backward/proposal model.
# Experiment name + tracking URI are read from .env (MLFLOW_TRAIN_EXPERIMENT_NAME, MLFLOW_TRACKING_URI)
text2sql-train-textgrad model=eve-instruct endpoint=blablador judge-model=eve-instruct judge-endpoint=blablador optimizer-model=glm47 optimizer-endpoint=kisski budget=train_budget batch-size="1" sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-textgrad --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{optimizer-model}} --optimizer-endpoint {{optimizer-endpoint}} --budget {{budget}} --batch-size {{batch-size}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# Group-aware train/val/test split (near-duplicate groups never straddle splits)
text2sql-sample questions-path=questions_path seed="42":
    uv run text2sql-sample --questions-path {{questions-path}} --seed {{seed}}

# Paraphrase questions into Denglish / hurried German and write a new JSON with prod_question added
text2sql-paraphrase model output input="data/text2sql/deflated_75_sqls.json":
    uv run text2sql-paraphrase --model {{model}} --input {{input}} --output {{output}}