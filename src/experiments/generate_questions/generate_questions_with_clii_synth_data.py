import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import click

CLI_FALLBACK_PATHS = {
    "claude": [Path.home() / ".claude" / "local" / "claude"],
    "copilot": [Path.home() / ".local" / "bin" / "copilot"],
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
You are a domain expert in water management, green roof systems, and urban hydrology.
Your task is to generate questions that a researcher or practitioner might ask an LLM assistant which has access to a database with timeseries data from a green roof monitoring platform. \
The researcher/practitioner is interested in understanding the performance of the green roof, diagnosing issues, and optimizing its operation NOT in bare fact knowledge. \
The questions should be ONLY data-driven and do NOT check bare facts knowledge. \

The questions should:
- Not be simply factual lookups like: "What was the total precipitation recorded for each calendar month?"
- Be answerable in ONE SQL query over ONE table (no multi-step reasoning that would require multiple queries or tool calls)
- Be in natural language, as a real user would ask them
- NOT asking for event detection
- Be diverse in complexity (some simple, some requiring deeper analysis)
- Not reference specific column names or database schemas
- Not involving ambiguity, e.g., "during hot days", "Under high incoming sunlight", "rainy days" - not clear what is "hot" or "high" or "rainy". Just specify the conditions: with > 10mm precipitation, with average 30C air temperature, etc.
BAD questions: \
"What was the highest daily precipitation total recorded in the dataset?" - too simple SQL query, just a MAX aggregation.
"What is the relationship between the retention layer capacity and the frequency of overflow events?" - it checks ONLY factual knowledge, no tools are necessary.
"How often does the wetland roof receive irrigation compared with the extensive green roof?" - it requires event detection (irrigation) with unclear threshold.
"What weather conditions coincided with the lowest recorded air pressure?" - vague "weather conditions" are not retrievable with SQL.
"... in the last weekend?" - temporal reference like "last weekend" is not appropriate as we are not building a consumer app, but rather a research assistant.
"What was the average shortwave radiation on days with no recorded precipitation?" - almost good but "average shortwave" is ambiguous: there are upward and downward facing shortwave radiation.
"How did average wind speed vary by calendar month?" - this is not focused on the green roof performance or water management, but rather just a general weather question. Something about precipitation would be more relevant.

GOOD questions: \
How many days were when runoff accounted for 80% of the precipitation? - requires data quering and terms understanding.
What was the maximum corrected surface temperature for each roof type? - requires comparing across roof types not just a simple MAX.
What is the correlation between daily total precipitation and daily maximum surface temperature on the irrigated extensive green roof? - requires understanding correlation and comparing two variables.


Do NOT include similar questions with only minor wording changes. Each question should be distinct in its intent and focus.
"""

USER_PROMPT_TEMPLATE = """\
Generate exactly {num_questions} questions that a researcher might ask about this green roof monitoring system.

The database has the following schema:

{schema}

The questions should require querying this data. They can involve single tables or joins across tables. \
Mix simple lookups with analytical questions (aggregations, comparisons between roof segments, time-series trends, correlations).

Return ONLY the questions, one per line. No other text.
"""


def strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. Qwen)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def parse_questions(response_text: str) -> list[str]:
    text = strip_thinking_tags(response_text)
    lines = text.strip().splitlines()
    questions = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        questions.append(line)
    return questions


def build_cli_command(tool: str, model: str, prompt: str) -> list[str]:
    binary = resolve_cli_binary(tool)
    if tool == "claude":
        return [
            binary,
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "text",
            "--effort",
            "xhigh"
        ]
    if tool == "copilot":
        return [
            binary,
            "-p",
            prompt,
            "--model",
            model,
            "--allow-all-tools",
        ]
    raise ValueError(f"Unsupported tool: {tool}")


def run_cli(tool: str, model: str, system_prompt: str, user_prompt: str, timeout: int) -> str:
    prompt = f"{system_prompt}\n\n{user_prompt}"
    cmd = build_cli_command(tool, model, prompt)
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
            f"{tool} CLI exited with code {e.returncode}.\nstderr:\n{e.stderr}"
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


def generate_from_schema(
    tool: str,
    model: str,
    schema: list[dict],
    num_questions: int,
    timeout: int,
) -> list[str]:
    schema_text = format_schema_for_prompt(schema)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        num_questions=num_questions,
        schema=schema_text,
    )
    text = run_cli(tool, model, SYSTEM_PROMPT, user_prompt, timeout)
    return parse_questions(text)


@click.command()
@click.option(
    "--tool",
    required=True,
    type=click.Choice(["claude", "copilot"]),
    help="Which CLI agent to shell out to",
)
@click.option("--model", required=True, help="Model identifier passed to the CLI, e.g. claude-opus-4-6 or gpt-5")
@click.option("--output-dir", required=True, type=click.Path(), help="Directory to write results")
@click.option("--schema-path", required=True, type=click.Path(exists=True), help="Path to Python file containing table_schema_dict")
@click.option("--num-questions", required=True, type=int, help="Total number of questions to generate")
@click.option("--timeout", default=600, type=int, help="Per-call CLI timeout in seconds")
def generate_questions(
    tool: str,
    model: str,
    output_dir: str,
    schema_path: str,
    num_questions: int,
    timeout: int,
) -> None:
    """Generate domain-specific questions via a local CLI agent (claude or copilot), driven by a database schema."""
    schema = load_schema(schema_path)
    click.echo(f"Loaded schema with {len(schema)} tables from {schema_path}")

    click.echo(f"Generating {num_questions} questions via {tool}...")
    all_questions = generate_from_schema(
        tool=tool,
        model=model,
        schema=schema,
        num_questions=num_questions,
        timeout=timeout,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / "generated_questions.txt"

    with open(output_file, "w") as f:
        f.write("\n".join(all_questions) + "\n")

    click.echo(f"Wrote {len(all_questions)} questions to {output_file}")


if __name__ == "__main__":
    generate_questions()
