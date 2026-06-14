"""Load question/SQL records from a .txt or .json input file."""

import dataclasses
import json
import pathlib
import re

import click

_LEADING_NUMBER = re.compile(r"^\s*\d+[.)]\s*")


@dataclasses.dataclass(frozen=True, slots=True)
class QARecord:
    """One question/SQL pair to be labeled in Argilla."""

    question: str
    sql: str = ""
    prod_question: str = ""


def _load_txt(path: pathlib.Path) -> list[QARecord]:
    records: list[QARecord] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        question = _LEADING_NUMBER.sub("", line).strip()
        if question:
            records.append(QARecord(question=question))
    return records

def _load_jsonl(path: pathlib.Path) -> list[QARecord]:
    records: list[QARecord] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise click.ClickException(
                f"{path}: line {i} is not valid JSON: {e}"
            ) from e
        if not isinstance(obj, dict):
            raise click.ClickException(
                f"{path}: line {i} is not a JSON object."
            )
        question = obj.get("question")
        if not isinstance(question, str) or not question.strip():
            raise click.ClickException(
                f"{path}: line {i} is missing a non-empty 'question' field."
            )
        sql = obj.get("sql", "")
        if not isinstance(sql, str):
            raise click.ClickException(
                f"{path}: line {i} has non-string 'sql'."
            )
        prod_question = obj.get("prod_question", "")
        if not isinstance(prod_question, str):
            raise click.ClickException(
                f"{path}: line {i} has non-string 'prod_question'."
            )
        records.append(
            QARecord(question=question.strip(), sql=sql, prod_question=prod_question)
        )
    return records

def _load_json(path: pathlib.Path) -> list[QARecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise click.ClickException(
            f"{path}: expected a JSON list of objects, got {type(raw).__name__}."
        )
    records: list[QARecord] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise click.ClickException(
                f"{path}: entry {i} is not an object."
            )
        question = entry.get("question")
        if not isinstance(question, str) or not question.strip():
            raise click.ClickException(
                f"{path}: entry {i} is missing a non-empty 'question' field."
            )
        sql = entry.get("sql", "")
        if not isinstance(sql, str):
            raise click.ClickException(
                f"{path}: entry {i} has non-string 'sql'."
            )
        prod_question = entry.get("prod_question", "")
        if not isinstance(prod_question, str):
            raise click.ClickException(
                f"{path}: entry {i} has non-string 'prod_question'."
            )
        records.append(
            QARecord(question=question.strip(), sql=sql, prod_question=prod_question)
        )
    return records


def load_records(path: pathlib.Path) -> list[QARecord]:
    """Load QARecord list from a .txt (one question per line) or .json file."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return _load_txt(path)
    if suffix == ".json":
        return _load_json(path)
    if suffix == ".jsonl":
        return _load_jsonl(path)
    raise click.ClickException(
        f"Unsupported input file extension '{suffix}'. Expected .txt, .json, or .jsonl."
    )
