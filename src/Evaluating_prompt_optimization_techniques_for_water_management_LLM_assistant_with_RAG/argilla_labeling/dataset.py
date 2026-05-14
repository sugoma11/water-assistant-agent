"""Dataset creation, template loading, and record upload for Argilla."""

import importlib.resources
import json
import pathlib

import argilla as rg

from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.argilla_labeling.discovery import (
    QARecord,
)
from Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG.argilla_labeling.settings import (
    ArgillaSettings,
)

UTF8 = "utf-8"
_STATIC_PACKAGE = (
    "Evaluating_prompt_optimization_techniques_for_water_management_LLM_assistant_with_RAG"
    ".argilla_labeling.static"
)


def _read_static_file(
    static: importlib.resources.abc.Traversable,
    name: str,
) -> str:
    return (static / name).read_text(encoding=UTF8)


def _read_dir_file(static_dir: pathlib.Path, name: str) -> str:
    return (static_dir / name).read_text(encoding=UTF8)


def _assemble_template(css: str, html: str, js: str) -> str:
    return (
        f"<style>\n{css}\n</style>\n\n"
        f"{html}\n\n"
        f"<script>\n{js}\n</script>\n"
    )


def _inject_settings(js: str, settings: ArgillaSettings) -> str:
    js = js.replace("__ARGILLA_URL__", json.dumps(str(settings.argilla_api_url)))
    return js.replace("__ARGILLA_KEY__", json.dumps(str(settings.argilla_api_key)))


def load_template(settings: ArgillaSettings) -> str:
    """Assemble template.html, template.css, and template.js into HTML string."""
    static = importlib.resources.files(_STATIC_PACKAGE)
    return _assemble_template(
        css=_read_static_file(static, "template.css"),
        html=_read_static_file(static, "template.html"),
        js=_inject_settings(_read_static_file(static, "template.js"), settings),
    )


def load_template_from_dir(static_dir: pathlib.Path, settings: ArgillaSettings) -> str:
    """Assemble labeling interface template from files in *static_dir*."""
    return _assemble_template(
        css=_read_dir_file(static_dir, "template.css"),
        html=_read_dir_file(static_dir, "template.html"),
        js=_inject_settings(_read_dir_file(static_dir, "template.js"), settings),
    )


def build_dataset_settings(template: str) -> rg.Settings:
    """Build Argilla dataset settings with a custom HTML field and text questions."""
    return rg.Settings(
        fields=[rg.CustomField(name="content", template=template, advanced_mode=True)],
        questions=[
            rg.TextQuestion(name="question", required=True),
            rg.TextQuestion(name="sql_query", required=True),
            rg.TextQuestion(name="notes", required=False),
        ],
    )


def create_dataset(
    client: rg.Argilla,
    settings: ArgillaSettings,
    dataset_settings: rg.Settings,
) -> rg.Dataset:
    """Create a dataset, failing if a dataset with the same name already exists."""
    existing = client.datasets(
        name=settings.argilla_dataset_name,
        workspace=settings.argilla_workspace,
    )
    if existing is not None:
        raise RuntimeError(
            f"Argilla dataset '{settings.argilla_dataset_name}' already exists in "
            f"workspace '{settings.argilla_workspace}'. Refusing to delete it "
            "automatically. Please delete the existing dataset explicitly or use "
            "a dedicated reset mechanism before calling create_dataset."
        )
    dataset = rg.Dataset(
        name=settings.argilla_dataset_name,
        workspace=settings.argilla_workspace,
        settings=dataset_settings,
        client=client,
    )
    dataset.create()
    return dataset


def update_dataset_template(
    client: rg.Argilla,
    settings: ArgillaSettings,
    template: str,
) -> rg.Dataset:
    """Update the custom field template of an existing dataset (preserves all data)."""
    dataset = client.datasets(
        name=settings.argilla_dataset_name,
        workspace=settings.argilla_workspace,
    )
    if dataset is None:
        raise RuntimeError(
            f"Dataset '{settings.argilla_dataset_name}' not found in "
            f"workspace '{settings.argilla_workspace}'."
        )
    dataset.settings.fields["content"].template = template
    dataset.update()
    return dataset


def upload_records(dataset: rg.Dataset, records: list[QARecord]) -> None:
    """Convert QARecord list to Argilla records and upload."""
    argilla_records = [
        rg.Record(
            fields={
                "content": {
                    "question": rec.question,
                    "sql_query": rec.sql,
                },
            },
        )
        for rec in records
    ]
    dataset.records.log(argilla_records)
