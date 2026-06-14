import json
import subprocess
from pathlib import Path

import click

from experiments.text2sql.text2sql_with_cli import build_cli_command

TO_GERMAN_PROMPT = """\
Translate the following question into German.

Write it the way a real person would type it into a chat window in a slight hurry:
- Colloquial vocabulary, short sentences
- Slightly sloppy: feel Free to start with lowercase letters; a missing comma or a casual “u.” instead of “und” is okay
- NO polished, LLM-style language, no perfect written language
- Leave technical terms and proper nouns (e.g., Extensiv, Kies, Sumpf, Lysimeter, mm) unchanged
- The meaning of the question must be preserved exactly

Return ONLY the German question, without quotation marks, without explanation.

Question:
{question}
"""

TO_DENGLISH_PROMPT = """\
Rewrite the following question in English, exactly as a native German speaker would type it by hand.

Style requirements:
- natural Denglish artifacts: German-influenced word order, occasional article or preposition quirks, \
"since" vs "for" confusion, German comma habits
- written a bit in a hurry, plain and human — NOT polished LLM English
- keep domain terms and proper names unchanged (e.g. Extensiv, Kies, Sumpf, Lysimeter, mm)
- the meaning of the question must stay exactly the same

Return ONLY the rewritten English question, no quotes, no explanation.

Question:
{question}
"""


def run_prompt(model: str, prompt: str, timeout: int) -> str:
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
    return result.stdout.strip()


def paraphrase(model: str, question: str, index: int, timeout: int) -> tuple[str, str]:
    if index % 2 == 0:
        style = "denglish"
        prompt = TO_DENGLISH_PROMPT.format(question=question)
    else:
        style = "german"
        prompt = TO_GERMAN_PROMPT.format(question=question)
    paraphrased = run_prompt(model, prompt, timeout)
    if not paraphrased:
        raise click.ClickException(
            f"Empty paraphrase for question {index}: {question!r}"
        )
    return paraphrased, style


@click.command()
@click.option("--model", required=True, help="Model identifier passed to claude -p, e.g. claude-opus-4-7")
@click.option("--input", "input_path", default="data/text2sql/deflated_75_sqls.json", type=click.Path(exists=True), help="Input JSON with question/sql records")
@click.option("--output", required=True, type=click.Path(), help="Output JSON path (input copy with added prod_question)")
@click.option("--timeout", default=600, type=int, help="Per-call CLI timeout in seconds")
def paraphrase_questions(model: str, input_path: str, output: str, timeout: int) -> None:
    """Add a prod_question paraphrase to every record, alternating Denglish and hurried German."""
    in_path = Path(input_path).resolve()
    out_path = Path(output).resolve()
    if in_path == out_path:
        raise click.UsageError("--output must differ from --input; the original file is not modified.")

    with open(in_path) as f:
        records = json.load(f)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for i, record in enumerate(records):
        prod_question, style = paraphrase(model, record["question"], i, timeout)
        results.append({**record, "prod_question": prod_question})
        with open(out_path, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        click.echo(f"[{i + 1}/{len(records)}] ({style}) {prod_question}")
    click.echo(f"Wrote {len(results)} records to {out_path}")


if __name__ == "__main__":
    paraphrase_questions()
