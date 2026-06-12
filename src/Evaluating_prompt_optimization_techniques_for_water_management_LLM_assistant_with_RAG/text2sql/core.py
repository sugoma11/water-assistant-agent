import importlib.util
import re

SYSTEM_PROMPT_TEMPLATE = """\
You are an expert DuckDB SQL author. \
Given a natural-language question and a database schema, produce ONE DuckDB-dialect SQL query that answers the question.

Strict output rules:
- Return ONLY the SQL query. No prose. No explanation. No markdown code fences.
- Use DuckDB SQL syntax (e.g. DATE_TRUNC, INTERVAL, list/struct functions, EPOCH, QUALIFY).
- Use only the tables and columns provided in the schema. Do not invent identifiers.
- Reference tables by the exact name from the schema.
- If the question cannot be answered with the given schema, return a single line: -- unanswerable

Schema:

{schema}
"""

USER_PROMPT_TEMPLATE = """\
Question:
{question}

Return the DuckDB SQL query only.
"""


def strip_thinking_tags(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def strip_code_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:sql|duckdb)?\s*\n(.*?)\n```\s*$", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def clean_sql(response_text: str) -> str:
    text = strip_thinking_tags(response_text)
    text = strip_code_fences(text)
    return text.strip()


def load_schema(schema_path: str) -> list[dict]:
    spec = importlib.util.spec_from_file_location("schema_module", schema_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.table_schema_dict


def format_schema_for_prompt(schema: list[dict]) -> str:
    parts = []
    for table in schema:
        cols = "\n".join(
            f"  - {c['name']} ({c['type']}): {c['description']}"
            for c in table["columns"]
        )
        parts.append(f"Table: {table['table_name']}\n{table['description']}\nColumns:\n{cols}")
    return "\n\n".join(parts)
