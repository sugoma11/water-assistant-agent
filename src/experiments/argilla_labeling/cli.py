"""Click CLI: create dataset, dump/copy/export records, update labeling UI."""

import pathlib

import argilla as rg
import click

from experiments.argilla_labeling.dataset import (
    build_dataset_settings,
    create_dataset,
    get_dataset,
    load_template,
    load_template_from_dir,
    update_dataset_template,
    upload_records,
    upsert_records,
)
from experiments.argilla_labeling.discovery import (
    load_records,
)
from experiments.argilla_labeling.export import (
    dump_dataset,
    export_submitted_qa,
    load_from_dump,
)
from experiments.argilla_labeling.settings import (
    ArgillaSettings,
)

_dataset_name_option = click.option(
    "--dataset-name",
    default=None,
    show_default=False,
    help="Argilla dataset name (overrides ARGILLA_DATASET_NAME env var).",
)


def _prepare_dataset(settings: ArgillaSettings) -> rg.Dataset:
    """Create Argilla client, load template, and create the dataset."""
    client = settings.make_client()
    return create_dataset(
        client, settings, build_dataset_settings(load_template(settings))
    )


@click.group()
def main() -> None:
    """Argilla labeling commands for the water-management Q/SQL project."""


@main.command("create")
@_dataset_name_option
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    required=True,
    help="Path to a .txt (one question per line) or .json (list of "
    "{question, sql?}) file.",
)
def create(dataset_name: str | None, input_path: pathlib.Path) -> None:
    """Create the Argilla dataset and upload question records."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    records = load_records(input_path)
    if not records:
        raise click.ClickException(f"No records loaded from {input_path}.")
    dataset = _prepare_dataset(settings)
    upload_records(dataset, records)
    click.echo(
        f"Uploaded {len(records)} records to '{settings.argilla_dataset_name}' "
        f"(workspace '{settings.argilla_workspace}')."
    )


@main.command("upsert")
@_dataset_name_option
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    required=True,
    help="Path to a .txt (one question per line), .json, or .jsonl file with "
    "{question, sql?} entries.",
)
def upsert(dataset_name: str | None, input_path: pathlib.Path) -> None:
    """Upsert question records into an existing Argilla dataset.

    Records use stable IDs derived from the question text, so re-uploading the
    same question updates the existing record instead of creating a duplicate.
    """
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    records = load_records(input_path)
    if not records:
        raise click.ClickException(f"No records loaded from {input_path}.")
    client = settings.make_client()
    dataset = get_dataset(client, settings)
    upsert_records(dataset, records)
    click.echo(
        f"Upserted {len(records)} records into '{settings.argilla_dataset_name}' "
        f"(workspace '{settings.argilla_workspace}')."
    )


@main.command("dump")
@_dataset_name_option
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    required=True,
    help="Path to write the dump JSON file.",
)
def dump(dataset_name: str | None, output: pathlib.Path) -> None:
    """Dump the dataset (records + responses + metadata) to a JSON file."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    client = settings.make_client()
    count = dump_dataset(client, settings, output)
    click.echo(f"Dumped {count} records to {output}.")


@main.command("load-dump")
@_dataset_name_option
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=pathlib.Path),
    required=True,
    help="Path to a dump JSON file produced by `dump`.",
)
def load_dump(dataset_name: str | None, input_path: pathlib.Path) -> None:
    """Create a new dataset from a dump file (records + responses preserved)."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    client = settings.make_client()
    count = load_from_dump(client, settings, input_path)
    click.echo(
        f"Loaded {count} records into '{settings.argilla_dataset_name}' "
        f"from {input_path}."
    )


@main.command("copy")
@click.option(
    "--source-dataset",
    required=True,
    help="Name of the existing Argilla dataset to copy from.",
)
@click.option(
    "--target-dataset",
    required=True,
    help="Name for the new Argilla dataset (must not already exist).",
)
@click.option(
    "--dump-path",
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    default=None,
    show_default=False,
    help="Where to keep the intermediate dump JSON. Defaults to a temp file.",
)
def copy(
    source_dataset: str,
    target_dataset: str,
    dump_path: pathlib.Path | None,
) -> None:
    """Copy a dataset by dumping the source and recreating it under a new name."""
    base = ArgillaSettings()
    client = base.make_client()

    if dump_path is None:
        import tempfile

        tmp = tempfile.NamedTemporaryFile(
            prefix=f"argilla_dump_{source_dataset}_",
            suffix=".json",
            delete=False,
        )
        tmp.close()
        dump_path = pathlib.Path(tmp.name)

    dumped = dump_dataset(client, base.with_dataset_name(source_dataset), dump_path)
    loaded = load_from_dump(
        client, base.with_dataset_name(target_dataset), dump_path
    )
    click.echo(
        f"Copied {dumped} records from '{source_dataset}' to "
        f"'{target_dataset}' (loaded {loaded}). Dump kept at {dump_path}."
    )


@main.command("export")
@_dataset_name_option
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    required=True,
    help="Path to write the JSON export of submitted records.",
)
def export(dataset_name: str | None, output: pathlib.Path) -> None:
    """Export submitted records as JSON ``[{question, sql, argilla_link}, ...]``."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    client = settings.make_client()
    count = export_submitted_qa(client, settings, output)
    click.echo(f"Exported {count} submitted records to {output}.")


@main.command("update-ui")
@_dataset_name_option
@click.option(
    "--static-dir",
    type=click.Path(exists=True, file_okay=False, path_type=pathlib.Path),
    default=None,
    show_default=False,
    help="Directory with template.html/template.css/template.js. "
    "Defaults to the bundled static/ directory.",
)
def update_ui(dataset_name: str | None, static_dir: pathlib.Path | None) -> None:
    """Update the labeling UI of an existing dataset (preserves data)."""
    settings = ArgillaSettings().with_dataset_name(dataset_name)
    template = (
        load_template_from_dir(static_dir, settings)
        if static_dir is not None
        else load_template(settings)
    )
    client = settings.make_client()
    update_dataset_template(client, settings, template)
    click.echo(
        f"Updated UI for '{settings.argilla_dataset_name}' "
        f"(source: {static_dir if static_dir else 'bundled static/'})."
    )
