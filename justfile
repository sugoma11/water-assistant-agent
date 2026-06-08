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
argilla-create input dataset-name:
    uv run argilla-labeling create --input {{input}} --dataset-name {{dataset-name}}

# Upsert questions from a .txt/.json/.jsonl file into an existing Argilla dataset
argilla-upsert input dataset-name:
    uv run argilla-labeling upsert --input {{input}} --dataset-name {{dataset-name}}

# Dump an Argilla dataset (records + responses + metadata) to a JSON file
argilla-dump output dataset-name:
    uv run argilla-labeling dump --output {{output}} --dataset-name {{dataset-name}}

# Recreate an Argilla dataset from a dump file produced by argilla-dump
argilla-load-dump input dataset-name:
    uv run argilla-labeling load-dump --input {{input}} --dataset-name {{dataset-name}}

# Copy an Argilla dataset (settings + records + responses) to a new name
argilla-copy source target:
    uv run argilla-labeling copy --source-dataset {{source}} --target-dataset {{target}}

# Export submitted records as JSON [{question, sql, argilla_link}, ...]
argilla-export output dataset-name:
    uv run argilla-labeling export --output {{output}} --dataset-name {{dataset-name}}

# Update the labeling UI template of an existing dataset (preserves data)
argilla-update-ui dataset-name:
    uv run argilla-labeling update-ui --dataset-name {{dataset-name}}

# Review text-to-SQL labels with an LLM (claude -p) and write verdicts to notes.
# Extra args, e.g.: just argilla-review my-ds "--check-submitted --force --model opus --dry-run"
argilla-review dataset-name args="":
    uv run python scripts/argilla_review.py review --dataset-name {{dataset-name}} {{args}}

# Builds duckdb from raw data files (Outflow.txt, Radiation.txt, etc.)
build-db:
    uv run python scripts/build_db.py