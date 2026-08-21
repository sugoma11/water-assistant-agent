"""What the semantic layer promises the SQL model, checked where the model reads it.

The subject is not ``table_schema_dict`` but **the string the builder renders**.
``format_schema_for_prompt`` (``text2sql/core.py``) projects each table down to
``table_name``, ``description`` and per-column ``name``/``type``/
``description``; a key added anywhere else in the dict is dropped without an
error, and ``build_sqlglot_schema`` tolerates it too, so nothing in the pipeline
would notice. Every assertion here therefore runs against
``formatted_schema()`` — the actual prompt block — rather than against the dict
that feeds it.

Four facts have to survive that projection (``agent_architecture.md`` §3.1):
the lysimeter collection area's **value** (T105), and the roof alias map, the
``radiation`` hour offset and the Europe/Berlin day boundary (T106).
"""

import subprocess
import sys

from water_assistant_agent.assistant.agents.text_to_sql.builder import formatted_schema
from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    ROOFS,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE, site_day_expr
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


# --- T106: the alias map ------------------------------------------------------


def _rendered_column(table: str, roof_name: str) -> str:
    """How this roof's ``table`` entry is written in the prompt.

    ``radiation``'s entry is a mast prefix rather than a column, so it renders
    as ``KD_*``; the other three tables render the column itself.
    """
    column = ROOFS[roof_name].columns[table]
    return f"{column}_*" if table == "radiation" else column


def test_every_roof_column_is_mapped_to_its_roof_in_every_table():
    """Five roofs × the tables that instrument them, each mapped in the prompt.

    ``questions.md`` §1.6 lets a question name a roof in the agent's vocabulary
    or in alias-covered natural language and **never** by raw column name, so
    this map is the model's only route from the words it is given to the column
    it must select.
    """
    prompt = formatted_schema()

    for table in ("swc", "tsoil", "outflow", "radiation"):
        block = _table_block(prompt, table)
        for roof_name in roofs_with_column(table):
            rendered = _rendered_column(table, roof_name)
            assert f"{rendered} = {roof_name}" in block, f"{table}.{rendered} maps to no roof"


def test_both_languages_reach_the_prompt_for_every_roof():
    """DE and EN labels, because half the paraphrases are German (§1.6).

    German is the site's own vocabulary, and a model that only ever saw
    "gravel roof" has to guess at "das Kiesdach".
    """
    prompt = formatted_schema()

    for roof in ROOFS.values():
        assert roof.label_de in prompt, f"{roof.name} has no German label in the prompt"
        assert roof.label_en in prompt, f"{roof.name} has no English label in the prompt"


def test_the_bridging_spellings_t106_names_all_resolve():
    """``Kies`` / ``KD`` / ``Kiesdach`` / ``QGravel`` → the gravel roof, and the rest.

    The task's own example, checked as an example rather than as a special case:
    every alias of every roof is rendered in the table blocks that carry it.
    """
    prompt = formatted_schema()

    for alias in ("kies", "kd", "kiesdach", "qgravel"):
        assert alias in ROOFS["gravel"].aliases
    for table in ("swc", "tsoil", "outflow", "radiation"):
        block = _table_block(prompt, table)
        for roof_name in roofs_with_column(table):
            for alias in ROOFS[roof_name].aliases:
                assert alias in block, f"{alias!r} is missing from the {table} block"


def test_aliases_render_in_a_stable_order():
    """``RoofSegment.aliases`` is a ``frozenset``, whose iteration order is hash-seeded.

    Rendered unsorted, the frozen prompt would differ between processes started
    with different ``PYTHONHASHSEED`` values — byte-instability that no
    single-process comparison could ever catch, in the one text
    ``agent_architecture.md`` §3.1 requires to be byte-stable.
    """
    prompt = formatted_schema()

    for roof in ROOFS.values():
        assert ", ".join(sorted(roof.aliases)) in prompt, f"{roof.name}'s aliases are unsorted"


def test_the_rendered_schema_is_identical_under_a_different_hash_seed():
    """The same claim, made against the thing itself rather than against sorting.

    Two subprocesses, two ``PYTHONHASHSEED`` values, one string. This is what
    "byte-stable" means for a prompt built from set-valued data.
    """
    program = (
        "from water_assistant_agent.assistant.agents.text_to_sql.builder import "
        "formatted_schema; print(formatted_schema(), end='')"
    )
    renders = [
        subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "12345")
    ]

    # Against this process's render too, so a subprocess that printed nothing
    # cannot pass by matching another subprocess that printed nothing.
    assert renders[0] == renders[1] == formatted_schema()


def test_the_semi_intensive_roof_is_declared_absent_where_it_is_uninstrumented():
    """No lysimeter and no radiation mast (``findings.md``), so no column to name.

    Omitting it silently invites a query against a column that does not exist;
    the blocks name the roof and say it has none, which is what an abstention
    has to be grounded in.
    """
    prompt = formatted_schema()
    semi = ROOFS["semi_intensive"]

    for table, lacks in (("outflow", "no lysimeter"), ("radiation", "no radiation mast")):
        block = _table_block(prompt, table)
        assert f"= {semi.name}" not in block, f"{table} maps a column to an uninstrumented roof"
        assert semi.label_en in block and lacks in block


def test_the_two_five_roof_tables_carry_all_five():
    """``swc`` and ``tsoil`` instrument every segment; the map has to show that."""
    prompt = formatted_schema()

    for table in ("swc", "tsoil"):
        block = _table_block(prompt, table)
        for roof_name in ROOFS:
            assert f"= {roof_name}" in block


# --- T106: the radiation offset and the day boundary --------------------------


def test_the_radiation_hour_offset_is_disclosed_where_radiation_is_reachable():
    """``decisions.md`` § The ``radiation`` timestamp offset, validity condition.

    The pinned DB is served as recorded rather than rebuilt — rebuilding moves
    the sha256 every captured response is stamped against — so the offset has to
    be stated wherever ``radiation`` can be read.
    """
    block = _table_block(formatted_schema(), "radiation")

    assert "one hour BEHIND" in block
    assert "60 min earlier" in block


def test_only_radiation_claims_the_offset():
    """The other four share the station clock; a blanket warning would be false."""
    prompt = formatted_schema()

    for table in ("outflow", "swc", "tsoil", "wetter"):
        assert "one hour BEHIND" not in _table_block(prompt, table)


def test_every_table_states_the_berlin_day_boundary():
    """A day is a Europe/Berlin calendar day everywhere (``decisions.md``).

    All five tables hold naive UTC and all five get grouped by day, so the rule
    belongs in each block rather than in whichever one the model happens to
    read first.
    """
    prompt = formatted_schema()

    for table in TABLES:
        assert SITE_TIMEZONE in _table_block(prompt, table), f"{table} omits the day boundary"


def test_the_prompt_hands_over_the_same_sql_the_oracles_group_by():
    """The rendered expression is ``site_day_expr()``, character for character.

    ``decisions.md`` § The day boundary's validity condition: the oracle and the
    candidate must group alike, or the oracle encodes as truth a total no route
    could produce. Retyping the cast here would let the two drift while both
    kept looking right.
    """
    prompt = formatted_schema()

    assert site_day_expr() in prompt
    assert prompt.count(site_day_expr()) == len(TABLES)


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
