"""What the semantic layer promises the SQL model, checked where the model reads it.

The subject is not ``table_schema_dict`` but **the string the builder renders**.
``format_schema_for_prompt`` (``text2sql/core.py``) projects each table down to
``table_name``, ``description`` and per-column ``name``/``type``/
``description``; a key added anywhere else in the dict is dropped without an
error, and ``build_sqlglot_schema`` tolerates it too, so nothing in the pipeline
would notice. Every assertion here therefore runs against
``formatted_schema()`` — the actual prompt block — rather than against the dict
that feeds it.

The facts that have to survive that projection (``agent_architecture.md`` §3.1)
start with the lysimeter collection area's **value** (T105).
"""

from water_assistant_agent.assistant.agents.text_to_sql.builder import formatted_schema
from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    ROOFS,
    roofs_with_column,
)
from water_assistant_agent.tenants.green_roof.sensordata import table_schema_dict

TABLES = ("outflow", "radiation", "swc", "tsoil", "wetter")


def _table_block(prompt: str, table: str) -> str:
    """The rendered block for one table: its header through its last column."""
    matching = [b for b in prompt.split("Table: ") if b.startswith(f"{table}\n")]
    assert len(matching) == 1, f"{table} appears {len(matching)}× in the rendered schema"
    return matching[0]


def _column_line(block: str, column: str) -> str:
    """The one rendered line describing *column*."""
    lines = [ln for ln in block.splitlines() if ln.strip().startswith(f"- {column} ")]
    assert len(lines) == 1, f"{column} renders on {len(lines)} lines"
    return lines[0]


# --- T105: the collection area's value ---------------------------------------


def test_the_collection_area_reaches_the_prompt_as_a_number():
    """The area is stated as ``1 m²``, not merely named as in the source ReadMe.

    ``findings.md`` § The semantic layer states neither the collection area's
    value nor an alias map: the descriptions used to read "(with m² collection
    area)", so a model asked for millimetres had no factor to convert with and
    no way to learn there is none to apply.
    """
    outflow = _table_block(formatted_schema(), "outflow")

    assert f"{LYSIMETER_AREA_M2:g} m²" in outflow
    assert "(with m² collection area)" not in outflow


def test_every_roof_lysimeter_column_carries_the_area():
    """All four roof-segment efflux columns, not just the table's own line.

    A column description is what the model reads when it picks a column, and it
    is read on its own.
    """
    outflow = _table_block(formatted_schema(), "outflow")
    area = f"{LYSIMETER_AREA_M2:g} m²"

    for roof_name in roofs_with_column("outflow"):
        column = ROOFS[roof_name].columns["outflow"]
        assert area in _column_line(outflow, column), f"{column} omits the collection area"


def test_the_litre_millimetre_equivalence_is_stated_and_scoped():
    """1 L = 1 mm, and *not* on the two small test lysimeters.

    ``findings.md`` establishes the area from the four roof lysimeters' outflow
    ratios against station rainfall. It says nothing about the two small
    extensive-substrate test lysimeters, which the ReadMe itself calls small —
    so claiming the equivalence there would state an unmeasured number as
    schema fact, on the one route (``L`` → ``mm``) this task exists to open.
    """
    outflow = _table_block(formatted_schema(), "outflow")

    assert "1 L of outflow IS 1 mm" in outflow
    for column in ("Zeitlysi_Efflux_x", "Sensorlysi_Efflux_x"):
        line = _column_line(outflow, column)
        assert "not the roof lysimeters'" in line
        assert "numerically mm" not in line


# --- The projection itself ----------------------------------------------------


def test_no_fact_is_carried_in_a_key_the_renderer_drops():
    """The trap this module exists to close.

    ``format_schema_for_prompt`` reads five keys and ignores the rest. If a
    later edit parks a fact under ``table['aliases']``, every assertion above
    still has to fail — so pin the key sets rather than trusting them.
    """
    assert tuple(t["table_name"] for t in table_schema_dict) == TABLES
    for table in table_schema_dict:
        assert set(table) == {"table_name", "description", "columns"}
        for column in table["columns"]:
            assert set(column) == {"name", "type", "description"}
