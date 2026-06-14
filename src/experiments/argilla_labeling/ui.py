"""Registry of labeling UI variants (templates + dataset schema + mappings).

A *variant* bundles everything that differs between labeling UIs: which static
template subfolder to load, which required text questions the dataset exposes,
how a :class:`QARecord` seeds the baseline ``content`` field, and which fields
are written back out by the exporter.
"""

import dataclasses

import click

from experiments.argilla_labeling.discovery import (
    QARecord,
)


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """One editable text question shared across the dataset schema, the record
    content baseline, and the export output."""

    name: str  # argilla question name == content key (template.js reads content.<name>)
    source: str  # QARecord attribute used to seed the baseline content
    title: str  # human-readable label shown in Argilla
    export_key: str  # key under which the value appears in export_qa output


@dataclasses.dataclass(frozen=True, slots=True)
class UIVariant:
    """A labeling UI variant: template subfolder plus its editable fields."""

    name: str  # CLI selector ("v1" / "v2")
    subdir: str  # static/<subdir>/ holding template.{html,css,js}
    fields: tuple[FieldSpec, ...]  # required text questions, in display order

    def content_for(self, rec: QARecord) -> dict[str, str]:
        """Build the ``content`` custom-field payload for a record."""
        return {f.name: getattr(rec, f.source) for f in self.fields}


_V1 = UIVariant(
    name="v1",
    subdir="v1",
    fields=(
        FieldSpec(name="question", source="question", title="Question", export_key="question"),
        FieldSpec(name="sql_query", source="sql", title="SQL Query", export_key="sql"),
    ),
)

_V2 = UIVariant(
    name="v2",
    subdir="v2",
    fields=(
        FieldSpec(
            name="question",
            source="question",
            title="Question (English)",
            export_key="question",
        ),
        FieldSpec(
            name="prod_question",
            source="prod_question",
            title="Question (DE / Denglish)",
            export_key="prod_question",
        ),
        FieldSpec(name="sql_query", source="sql", title="SQL Query", export_key="sql"),
    ),
)

UI_VARIANTS: dict[str, UIVariant] = {_V1.name: _V1, _V2.name: _V2}


def get_variant(name: str) -> UIVariant:
    """Resolve a UI variant by name, raising a CLI error on unknown values."""
    try:
        return UI_VARIANTS[name]
    except KeyError as e:
        choices = ", ".join(sorted(UI_VARIANTS))
        raise click.ClickException(
            f"Unknown UI variant '{name}'. Expected one of: {choices}."
        ) from e
