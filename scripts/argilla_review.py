"""CLI tool for reviewing Argilla text-to-SQL labeling records with an LLM.

The ``review`` command iterates over the dataset's question/SQL pairs and, for
each one, makes a single ``claude -p`` call (one context window per record)
feeding the question, the candidate SQL, and the DuckDB schema. The model's
verdict on whether the SQL is correct is written to the record's *notes* field;
the question and SQL themselves are never modified.
"""

import datetime as dt
import json
import pathlib
import subprocess
import sys

import argilla as rg
import click

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from experiments.argilla_labeling.settings import (  # noqa: E402
    ArgillaSettings,
)
from water_assistant_agent.tenants.green_roof.sensordata import (  # noqa: E402
    table_schema_dict,
)

_REVIEW_MARKER = "[llm-review]"
_DEFAULT_MODEL = "sonnet"


def _get_dataset(client: rg.Argilla, settings: ArgillaSettings) -> rg.Dataset:
    dataset = client.datasets(
        name=settings.argilla_dataset_name, workspace=settings.argilla_workspace
    )
    if dataset is None:
        raise click.ClickException(
            f"Dataset '{settings.argilla_dataset_name}' not found in "
            f"workspace '{settings.argilla_workspace}'."
        )
    return dataset


def _render_schema() -> str:
    """Render ``table_schema_dict`` as a compact text schema for the prompt."""
    lines: list[str] = []
    for table in table_schema_dict:
        lines.append(f"Table: {table['table_name']}")
        if table.get("description"):
            lines.append(f"  -- {table['description']}")
        for col in table["columns"]:
            desc = col.get("description", "").strip()
            suffix = f"  -- {desc}" if desc else ""
            lines.append(f"  {col['name']} {col['type']}{suffix}")
        lines.append("")
    return "\n".join(lines).strip()


def _get_response_value(rec: rg.Record, question_name: str) -> str | None:
    for resp in rec.responses:
        if resp.question_name == question_name:
            return resp.value
    return None


def _effective_qa(rec: rg.Record) -> tuple[str, str]:
    """Return the effective (question, sql) for a record.

    Prefers the latest response value, falling back to the original field
    content uploaded with the record.
    """
    content = rec.fields.get("content", {}) or {}
    question = _get_response_value(rec, "question") or content.get("question", "") or ""
    sql = _get_response_value(rec, "sql_query") or content.get("sql_query", "") or ""
    return question, sql


def _is_submitted(rec: rg.Record) -> bool:
    return any(resp.status == "submitted" for resp in rec.responses)


def _build_prompt(question: str, sql: str, schema: str) -> str:
    return f"""You are reviewing a text-to-SQL label for a DuckDB database.

Given the database schema, a natural-language question, and a candidate SQL
query, decide whether the SQL correctly and completely answers the question.
Consider table and column correctness, filters, joins, aggregation, time
handling, and units. Do NOT rewrite the SQL.

Respond with your verdict on the FIRST line in exactly this format:
VERDICT: CORRECT      (the SQL correctly answers the question)
VERDICT: INCORRECT    (the SQL is wrong, incomplete, or misuses the schema)
VERDICT: UNSURE       (the question or SQL is too ambiguous to judge)

After the verdict line, give a brief explanation (1-4 sentences) of your
reasoning, naming any specific column/table issues.

=== DATABASE SCHEMA ===
{schema}

=== QUESTION ===
{question}

=== CANDIDATE SQL ===
{sql}
"""


def _parse_verdict(result_text: str) -> str:
    for line in result_text.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("VERDICT:"):
            label = stripped.removeprefix("VERDICT:").strip()
            for candidate in ("CORRECT", "INCORRECT", "UNSURE"):
                if label.startswith(candidate):
                    return candidate
    return "UNKNOWN"


def _review_sql(question: str, sql: str, schema: str, model: str) -> dict:
    """Run one ``claude -p`` call to review a question/SQL pair.

    Returns ``{"ok": bool, "verdict": str, "text": str, "error": str | None}``.
    """
    prompt = _build_prompt(question, sql, schema)
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return {"ok": False, "verdict": "ERROR", "text": "", "error": "claude CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "verdict": "ERROR", "text": "", "error": "claude call timed out"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "verdict": "ERROR",
            "text": "",
            "error": f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}",
        }

    try:
        payload = json.loads(proc.stdout)
        result_text = payload["result"] if isinstance(payload, dict) else str(payload)
    except (json.JSONDecodeError, KeyError) as e:
        return {
            "ok": False,
            "verdict": "ERROR",
            "text": "",
            "error": f"could not parse claude output: {e}",
        }

    result_text = str(result_text).strip()
    return {
        "ok": True,
        "verdict": _parse_verdict(result_text),
        "text": result_text,
        "error": None,
    }


def _strip_verdict_line(text: str) -> str:
    """Drop a leading ``VERDICT: ...`` line so it isn't duplicated in the note."""
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].strip().upper().startswith("VERDICT:")):
        lines.pop(0)
    return "\n".join(lines).strip()


def _format_note(verdict: str, text: str, model: str) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    return f"{_REVIEW_MARKER} {model} {stamp} | VERDICT: {verdict}\n\n{_strip_verdict_line(text)}"


def _write_note(
    dataset: rg.Dataset, client: rg.Argilla, rec: rg.Record, note: str
) -> None:
    existing = None
    for resp in rec.responses:
        if resp.question_name == "notes":
            existing = resp
            break
    if existing is not None:
        existing.value = note
    else:
        rec.responses.add(
            rg.Response(
                question_name="notes",
                value=note,
                status="draft",
                user_id=client.me.id,
            )
        )
    dataset.records.log(records=[rec])


@click.group()
def cli():
    """Argilla review tool for text-to-SQL labeling QA."""


_dataset_name_option = click.option(
    "--dataset-name",
    default=None,
    show_default=False,
    help="Argilla dataset name (overrides ARGILLA_DATASET_NAME env var).",
)


@cli.command()
@_dataset_name_option
@click.option("--model", default=_DEFAULT_MODEL, show_default=True, help="Claude model alias for `claude -p`.")
@click.option(
    "--check-submitted",
    is_flag=True,
    default=False,
    help="Also review records that already have a submitted response "
    "(by default only non-submitted records are reviewed).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-review records that already carry an LLM review note.",
)
@click.option("--limit", type=int, default=None, help="Process at most this many records.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print verdicts without writing notes back to Argilla.",
)
def review(
    dataset_name: str | None,
    model: str,
    check_submitted: bool,
    force: bool,
    limit: int | None,
    dry_run: bool,
):
    """Review each question/SQL pair with an LLM and write verdicts to notes."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    client = settings.make_client()
    dataset = _get_dataset(client, settings)
    schema = _render_schema()

    reviewed = skipped = errors = 0
    for rec in dataset.records(with_responses=True):
        if limit is not None and reviewed >= limit:
            break

        if _is_submitted(rec) and not check_submitted:
            skipped += 1
            continue

        existing_notes = _get_response_value(rec, "notes") or ""
        if _REVIEW_MARKER in existing_notes and not force:
            click.echo(f"[skip] {rec.id}: already reviewed")
            skipped += 1
            continue

        question, sql = _effective_qa(rec)
        if not question.strip() or not sql.strip():
            click.echo(f"[skip] {rec.id}: missing question or SQL")
            skipped += 1
            continue

        outcome = _review_sql(question, sql, schema, model)
        if not outcome["ok"]:
            click.echo(f"[error] {rec.id}: {outcome['error']}", err=True)
            errors += 1
            continue

        click.echo(f"[{outcome['verdict']}] {rec.id}: {question[:70]}")
        if dry_run:
            click.echo(outcome["text"])
            click.echo("-" * 60)
        else:
            note = _format_note(outcome["verdict"], outcome["text"], model)
            _write_note(dataset, client, rec, note)
        reviewed += 1

    click.echo(
        f"\nDone. reviewed={reviewed} skipped={skipped} errors={errors}"
        + (" (dry-run, nothing written)" if dry_run else "")
    )


@cli.command()
@_dataset_name_option
@click.option("--output", "-o", type=click.Path(), default=None, help="Output file path (default: stdout)")
@click.option("--record-id", type=str, default=None, help="Fetch a single record by ID")
def fetch(dataset_name: str | None, output: str | None, record_id: str | None):
    """Fetch records (question/SQL/notes) from Argilla for review."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    client = settings.make_client()
    dataset = _get_dataset(client, settings)

    records = []
    for rec in dataset.records(with_responses=True):
        question, sql = _effective_qa(rec)
        rec_dict = {
            "id": str(rec.id),
            "question": question,
            "sql": sql,
            "notes": _get_response_value(rec, "notes") or "",
            "submitted": _is_submitted(rec),
        }
        if record_id and rec_dict["id"] != record_id:
            continue
        records.append(rec_dict)
        if record_id:
            break

    result = json.dumps(records, indent=2, ensure_ascii=False)
    if output:
        pathlib.Path(output).write_text(result, encoding="utf-8")
        click.echo(f"Wrote {len(records)} record(s) to {output}")
    else:
        click.echo(result)


@cli.command("update-notes")
@_dataset_name_option
@click.option("--record-id", required=True, type=str, help="Record UUID")
@click.option("--notes", required=True, type=str, help="Notes text to set")
def update_notes(dataset_name: str | None, record_id: str, notes: str):
    """Write review notes to a single record's response."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    client = settings.make_client()
    dataset = _get_dataset(client, settings)

    target = None
    for rec in dataset.records(with_responses=True):
        if str(rec.id) == record_id:
            target = rec
            break
    if target is None:
        raise click.ClickException(f"Record {record_id} not found")

    _write_note(dataset, client, target, notes)
    click.echo(f"Updated notes for record {record_id}")


if __name__ == "__main__":
    cli()
