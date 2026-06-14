import "evals.just"

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

# Evaluate text-2-SQL generation with an LLM-as-Judge and log results to MLflow
# Experiment name + tracking URI are read from .env (MLFLOW_EXPERIMENT_NAME, MLFLOW_TRACKING_URI)
text2sql-eval questions-path schema-path db-path model endpoint judge-model judge-endpoint:
    uv run text2sql-eval --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}}

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


# GEPA-train the text2sql system prompt; before/after val+test evals logged to MLflow
# Experiment name + tracking URI are read from .env (MLFLOW_EXPERIMENT_NAME, MLFLOW_TRACKING_URI)
text2sql-train questions-path schema-path db-path model endpoint judge-model judge-endpoint teacher-model teacher-endpoint sampler-seed="42":
    uv run text2sql-train --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}}

# Group-aware train/val/test split (near-duplicate groups never straddle splits)
text2sql-sample questions-path seed="42":
    uv run text2sql-sample --questions-path {{questions-path}} --seed {{seed}}

# Paraphrase questions into Denglish / hurried German and write a new JSON with prod_question added
text2sql-paraphrase model output input="data/text2sql/deflated_75_sqls.json":
    uv run text2sql-paraphrase --model {{model}} --input {{input}} --output {{output}}
