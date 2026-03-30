import json
import os
from pathlib import Path

import click
from dotenv import load_dotenv
from litellm import completion

load_dotenv()

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


def build_completion_kwargs(model: str) -> dict:
    api_base = os.environ["LLM_API_BASE"]
    api_key = os.environ["LLM_API_KEY"]
    temperature = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
    top_p = float(os.environ.get("LLM_TOP_P", "0.9"))
    seed = int(os.environ.get("LLM_SEED", "42"))

    kwargs = {
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
        "temperature": temperature,
        "top_p": top_p,
        "seed": seed,
    }

    top_k = os.environ.get("LLM_TOP_K")
    if top_k is not None:
        kwargs["top_k"] = int(top_k)

    return kwargs


def parse_questions(response_text: str) -> list[str]:
    lines = response_text.strip().splitlines()
    questions = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line:
            questions.append(line)
    return questions


def generate_for_category(
    category: str,
    terms: list[str],
    num_questions: int,
    completion_kwargs: dict,
) -> list[str]:
    user_prompt = USER_PROMPT_TEMPLATE.format(
        num_questions=num_questions,
        category=category.replace("_", " "),
        terms="\n".join(f"- {t}" for t in terms),
    )

    resp = completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        **completion_kwargs,
    )

    text = resp.choices[0].message.content
    return parse_questions(text)


@click.command()
@click.option("--model", required=True, help="LiteLLM model identifier, e.g. openai/glm-4.7")
@click.option("--output-dir", required=True, type=click.Path(), help="Directory to write results")
@click.option("--terms-path", required=True, type=click.Path(exists=True), help="Path to terms JSON")
@click.option("--num-questions", required=True, type=int, help="Total number of questions to generate")
def generate_questions(
    model: str,
    output_dir: str,
    terms_path: str,
    num_questions: int,
) -> None:
    """Generate domain-specific questions for a water management LLM assistant."""
    with open(terms_path) as f:
        terms_by_category: dict[str, list[str]] = json.load(f)

    categories = list(terms_by_category.keys())
    num_categories = len(categories)
    base_per_category = num_questions // num_categories
    remainder = num_questions % num_categories

    completion_kwargs = build_completion_kwargs(model)

    all_questions: list[str] = []

    for i, category in enumerate(categories):
        n = base_per_category + (1 if i < remainder else 0)
        if n == 0:
            continue

        click.echo(f"Generating {n} questions for '{category}'...")
        questions = generate_for_category(
            category=category,
            terms=terms_by_category[category],
            num_questions=n,
            completion_kwargs=completion_kwargs,
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
