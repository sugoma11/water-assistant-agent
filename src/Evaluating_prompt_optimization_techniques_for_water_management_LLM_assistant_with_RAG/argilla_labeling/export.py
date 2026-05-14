"""Dump, restore, and export Argilla records for the Q/SQL labeling project."""

import datetime as dt
import json
import pathlib
from typing import Any
from urllib.parse import quote

import argilla as rg

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.argilla_labeling.dataset import (
    build_dataset_settings,
    create_dataset,
    load_template,
)
from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.argilla_labeling.settings import (
    ArgillaSettings,
)

_UPLOAD_BATCH_SIZE = 64


def _inject_response_statuses(
    rec: rg.Record,
    record_dict: dict[str, Any],
) -> None:
    """Patch *record_dict* in-place with response status from the Record object."""
    for resp_obj in rec.responses:
        question = resp_obj.question_name
        if question not in record_dict.get("responses", {}):
            continue
        for resp_dict in record_dict["responses"][question]:
            if str(resp_dict.get("user_id")) == str(resp_obj.user_id):
                resp_dict["status"] = resp_obj.status


def _query_records(dataset: rg.Dataset, include_pending: bool) -> list[rg.Record]:
    if include_pending:
        return list(dataset.records())
    query = rg.Query(filter=rg.Filter(("response.status", "==", "submitted")))
    return list(dataset.records(query=query))


def _fetch_records(
    client: rg.Argilla,
    settings: ArgillaSettings,
    include_pending: bool,
) -> tuple[rg.Dataset | None, list[dict[str, Any]]]:
    dataset = client.datasets(
        name=settings.argilla_dataset_name,
        workspace=settings.argilla_workspace,
    )
    if dataset is None:
        return None, []
    records = _query_records(dataset, include_pending)
    exported: list[dict[str, Any]] = []
    for rec in records:
        record_dict = rec.to_dict()
        _inject_response_statuses(rec, record_dict)
        if "inserted_at" not in record_dict and getattr(rec, "inserted_at", None):
            record_dict["inserted_at"] = rec.inserted_at
        exported.append(record_dict)
    return dataset, exported


def _convert_responses(
    resp_map: dict[str, Any],
    user_id: str,
) -> list[rg.Response]:
    responses: list[rg.Response] = []
    for question_name, resp_list in resp_map.items():
        for resp in resp_list:
            responses.append(
                rg.Response(
                    question_name,
                    resp["value"],
                    user_id=resp.get("user_id", user_id),
                    status=resp.get("status", "submitted"),
                )
            )
    return responses


def _build_argilla_records(
    raw: list[dict[str, Any]],
    user_id: str,
) -> list[rg.Record]:
    argilla_records: list[rg.Record] = []
    for entry in raw:
        responses = _convert_responses(entry.get("responses", {}), user_id)
        argilla_records.append(
            rg.Record(fields=entry.get("fields", {}), responses=responses or None)
        )
    return argilla_records


def dump_dataset(
    client: rg.Argilla,
    settings: ArgillaSettings,
    output_path: pathlib.Path,
) -> int:
    """Dump dataset records (with responses) plus name/workspace metadata to JSON.

    Returns the number of records dumped. Raises if the dataset is missing.
    """
    dataset, records = _fetch_records(client, settings, include_pending=True)
    if dataset is None:
        raise RuntimeError(
            f"Dataset '{settings.argilla_dataset_name}' not found in "
            f"workspace '{settings.argilla_workspace}'."
        )
    payload = {
        "dataset_name": settings.argilla_dataset_name,
        "workspace": settings.argilla_workspace,
        "exported_at": dt.datetime.now(dt.UTC).isoformat(),
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    return len(records)


def load_from_dump(
    client: rg.Argilla,
    target_settings: ArgillaSettings,
    dump_path: pathlib.Path,
) -> int:
    """Create a dataset from *target_settings* and re-upload records from a dump file.

    The labeling UI template is freshly assembled from the bundled static/
    directory rather than restored from the dump.
    """
    payload = json.loads(dump_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        raw_records = payload
    else:
        raw_records = payload.get("records", [])

    dataset = create_dataset(
        client,
        target_settings,
        build_dataset_settings(load_template(target_settings)),
    )
    argilla_records = _build_argilla_records(raw_records, user_id=str(client.me.id))
    dataset.records.log(argilla_records, batch_size=_UPLOAD_BATCH_SIZE)
    return len(argilla_records)


def _latest_submitted_value(
    responses: dict[str, Any],
    question_name: str,
) -> str | None:
    """Return the value of the most recent submitted response for *question_name*."""
    items = responses.get(question_name, [])
    submitted = [r for r in items if r.get("status") == "submitted"]
    if not submitted:
        return None
    last = submitted[-1]
    value = last.get("value")
    return value if isinstance(value, str) else None


def _record_inserted_at(record_dict: dict[str, Any]) -> str:
    return str(record_dict.get("inserted_at") or "")


def _build_argilla_link(api_url: str, dataset_id: str, page: int) -> str:
    base = api_url.rstrip("/")
    return (
        f"{base}/dataset/{quote(dataset_id, safe='')}/annotation-mode"
        f"?page={page}&status=submitted&sort=record.inserted_at.asc"
    )


def export_submitted_qa(
    client: rg.Argilla,
    settings: ArgillaSettings,
    output_path: pathlib.Path,
) -> int:
    """Export submitted records as ``[{question, sql, argilla_link}, ...]``.

    Records are sorted by ``inserted_at`` ascending so each ``argilla_link``'s
    ``page`` parameter (1-indexed position) deterministically opens the same
    record under the matching sort in the Argilla UI.
    """
    dataset, records = _fetch_records(client, settings, include_pending=False)
    if dataset is None:
        raise RuntimeError(
            f"Dataset '{settings.argilla_dataset_name}' not found in "
            f"workspace '{settings.argilla_workspace}'."
        )
    records.sort(key=_record_inserted_at)

    dataset_id = str(dataset.id)
    rows: list[dict[str, str]] = []
    for i, rec in enumerate(records, start=1):
        responses = rec.get("responses", {}) or {}
        fields_content = (rec.get("fields", {}) or {}).get("content", {}) or {}
        question = (
            _latest_submitted_value(responses, "question")
            or fields_content.get("question", "")
            or ""
        )
        sql = (
            _latest_submitted_value(responses, "sql_query")
            or fields_content.get("sql_query", "")
            or ""
        )
        rows.append(
            {
                "question": question,
                "sql": sql,
                "argilla_link": _build_argilla_link(
                    settings.argilla_api_url, dataset_id, i
                ),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(rows)
