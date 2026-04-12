import json
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
Your task is to generate questions that a researcher or practitioner might ask an LLM assistant which has access to timeseries data / system models for a green roof. \
The researcher/practitioner is interested in understanding the performance of the green roof, diagnosing issues, and optimizing its operation NOT in bare fact knowledge. \
The questions should be ONLY data-driven and do NOT check bare facts knowledge. \

The questions should:
- Be in natural language, as a real user would ask them
- Be both answerable and unanswerable (to catch hallucionations). Answerable questions should be based on data analysis, models outputs and domain knowledge.
- Cover practical, analytical, and conceptual aspects
- Be diverse in complexity (some simple, some requiring deeper analysis)
- Not reference specific column names or database schemas

An example of a BAD question: \
"What is the relationship between the retention layer capacity and the frequency of overflow events?" - it checks ONLY factual knowledge, no tools are necessary.

An example of a GOOD question: \
Can you identify periods where downward seepage exceeded the incoming precipitation? - requires both tool usage with data quering and terms understanding.

Do NOT include similar questions with only minor wording changes. Each question should be distinct in its intent and focus.
"""

USER_PROMPT_TEMPLATE = """\
Generate exactly {num_questions} questions related to the category "{category}". You can mix terms from the different categories if relevant.

Use the following domain-specific terms (you don't have to use every term, \
but questions should be relevant to these concepts):

{terms}

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


def generate_for_category(
    tool: str,
    model: str,
    category: str,
    terms: list[str],
    num_questions: int,
    timeout: int,
) -> list[str]:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        num_questions=num_questions,
        category=category.replace("_", " "),
        terms="\n".join(f"- {t}" for t in terms),
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
@click.option("--terms-path", required=True, type=click.Path(exists=True), help="Path to terms JSON")
@click.option("--num-questions", required=True, type=int, help="Total number of questions to generate")
@click.option("--timeout", default=600, type=int, help="Per-call CLI timeout in seconds")
def generate_questions(
    tool: str,
    model: str,
    output_dir: str,
    terms_path: str,
    num_questions: int,
    timeout: int,
) -> None:
    """Generate domain-specific questions via a local CLI agent (claude or copilot)."""
    with open(terms_path) as f:
        terms_by_category: dict[str, list[str]] = json.load(f)

    categories = list(terms_by_category.keys())
    num_categories = len(categories)
    base_per_category = num_questions // num_categories
    remainder = num_questions % num_categories

    all_questions: list[str] = []

    for i, category in enumerate(categories):
        n = base_per_category + (1 if i < remainder else 0)
        if n == 0:
            continue

        click.echo(f"Generating {n} questions for '{category}' via {tool}...")
        questions = generate_for_category(
            tool=tool,
            model=model,
            category=category,
            terms=terms_by_category[category],
            num_questions=n,
            timeout=timeout,
        )
        all_questions.extend(questions)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    output_file = output_path / "generated_questions.txt"

    with open(output_file, "w") as f:
        f.write("\n".join(all_questions) + "\n")

    click.echo(f"Wrote {len(all_questions)} questions to {output_file}")


if __name__ == "__main__":
    generate_questions()
