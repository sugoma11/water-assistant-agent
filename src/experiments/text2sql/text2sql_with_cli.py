import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import click

CLI_FALLBACK_PATHS = {
    "claude": [Path.home() / ".claude" / "local" / "claude"],
}


def resolve_cli_binary(tool: str) -> str:
    found = shutil.which(tool)
    if found:
        return found
    for candidate in CLI_FALLBACK_PATHS.get(tool, []):
        if candidate.exists():
            return str(candidate)
    raise click.ClickException(
        f"Could not find '{tool}' on PATH or in known install locations. "
        f"Install it or add it to PATH."
    )


SYSTEM_PROMPT = """\
You are an expert DuckDB SQL author. \
Given a natural-language question and a database schema, produce ONE DuckDB-dialect SQL query that answers the question.

Strict output rules:
- Return ONLY the SQL query. No prose. No explanation. No markdown code fences.
- Use DuckDB SQL syntax (e.g. DATE_TRUNC, INTERVAL, list/struct functions, EPOCH, QUALIFY).
- Use only the tables and columns provided in the schema. Do not invent identifiers.
- Reference tables by the exact name from the schema.
- If the question cannot be answered with the given schema, return a single line: -- unanswerable
"""

USER_PROMPT_TEMPLATE = """\
Schema:

{schema}

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


def build_cli_command(model: str, prompt: str) -> list[str]:
    binary = resolve_cli_binary("claude")
    return [
        binary,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "text",
    ]


def run_cli(model: str, system_prompt: str, user_prompt: str, timeout: int) -> str:
    prompt = f"{system_prompt}\n\n{user_prompt}"
    cmd = build_cli_command(model, prompt)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"claude CLI exited with code {e.returncode}.\nstderr:\n{e.stderr}"
        ) from e
    return result.stdout


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


def generate_sql(
    model: str,
    schema_text: str,
    question: str,
    timeout: int,
) -> str:
    user_prompt = USER_PROMPT_TEMPLATE.format(schema=schema_text, question=question)
    raw = run_cli(model, SYSTEM_PROMPT, user_prompt, timeout)
    return clean_sql(raw)


def read_questions_file(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


@click.command()
@click.option("--model", required=True, help="Model identifier passed to claude -p, e.g. claude-opus-4-7")
@click.option("--schema-path", required=True, type=click.Path(exists=True), help="Python file containing table_schema_dict")
@click.option("--question", default=None, help="Single natural-language question")
@click.option("--questions-file", default=None, type=click.Path(exists=True), help="File with one question per line (batch mode)")
@click.option("--output", default=None, type=click.Path(), help="JSONL output file ({\"question\": ..., \"sql\": ...} per line). Required in batch mode; optional in single-question mode (defaults to printing SQL to stdout)")
@click.option("--timeout", default=600, type=int, help="Per-call CLI timeout in seconds")
def text2sql(
    model: str,
    schema_path: str,
    question: str | None,
    questions_file: str | None,
    output: str | None,
    timeout: int,
) -> None:
    """Generate DuckDB SQL queries from natural-language questions via `claude -p`."""
    if bool(question) == bool(questions_file):
        raise click.UsageError("Provide exactly one of --question or --questions-file.")

    schema = load_schema(schema_path)
    schema_text = format_schema_for_prompt(schema)

    if question:
        sql = generate_sql(model, schema_text, question, timeout)
        if output:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(json.dumps({"question": question, "sql": sql}, ensure_ascii=False) + "\n")
            click.echo(f"Wrote JSONL record to {output_path}")
        else:
            click.echo(sql)
        return

    if not output:
        raise click.UsageError("--output is required when using --questions-file.")

    questions = read_questions_file(questions_file)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for i, q in enumerate(questions, 1):
            click.echo(f"[{i}/{len(questions)}] {q}")
            sql = generate_sql(model, schema_text, q, timeout)
            f.write(json.dumps({"question": q, "sql": sql}, ensure_ascii=False) + "\n")
            f.flush()
    click.echo(f"Wrote {len(questions)} JSONL records to {output_path}")


if __name__ == "__main__":
    text2sql()
