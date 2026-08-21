"""Semantic layer for water.duckdb.

Column descriptions copied from data/timeseries/ReadMe_Sensordata .txt, with the
facts the ReadMe leaves implicit written out: the lysimeter collection area's
*value*, the roof each column belongs to and the names that roof answers to.
Columns the ReadMe flags as 'raw data --> you can ignore that' or 'artefact of
merging' are omitted. CNR4.* columns are already dropped at DB build time
(see scripts/build_db.py).

**This text is the sub-agent's schema block** — rendered into the *builder's*
system prompt by ``agents/text_to_sql/builder.py``'s ``formatted_schema``, and
frozen at ``agent_architecture.md`` §3.1. Only what ``text2sql/core.py``'s
``format_schema_for_prompt`` renders reaches the model: each table's
``table_name`` and ``description``, and each column's ``name``, ``type`` and
``description``. A sixth key added here would be dropped silently — no error,
no rendered text — which is why the area, the aliases and the day boundary are
written into the description strings rather than carried beside them.

Roof names and the collection area are read from ``tools/roofs.py``, the one
table that holds them (``agent_architecture.md`` §1 principle 4). Writing
``1 m²`` here as a literal would make this the second place the site's physical
constants live.
"""
from typing import Any

from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    RADIATION_COLUMN_SUFFIXES,
    ROOFS,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE, site_day_expr

_AREA = f"{LYSIMETER_AREA_M2:g} m²"
"""The collection area as it is written into the descriptions, e.g. ``1 m²``.

``:g`` because the model is being told a physical quantity and ``1.0 m²`` reads
as a measurement with a precision the number does not carry.
"""


def _roof_directory(table: str) -> str:
    """The alias map for *table*: one line per roof, plus what is missing.

    The map is a projection of ``roofs.py`` rather than a list of its own — the
    same discipline ``ROOF_SWC_COLUMNS`` and ``ROOF_PRESETS`` follow
    (``agent_architecture.md`` §1 principle 4). Questions name roofs in the
    agent's vocabulary or in alias-covered natural language and **never** by raw
    column name (``questions.md`` §1.6), so this is the only bridge from "das
    Kiesdach" or "KD" to ``Kies_Efflux``, and it has to carry both languages.

    **Aliases are sorted.** ``RoofSegment.aliases`` is a ``frozenset``, and
    iterating one yields strings in an order derived from their hashes — which
    ``PYTHONHASHSEED`` randomizes per process. Rendered unsorted, the frozen
    prompt would differ run to run without a single line of this repository
    changing.

    The roofs a table does *not* instrument are named too. The semi-intensive
    roof has no lysimeter and no radiation mast (``findings.md`` § Not every
    roof is instrumented), and a schema that simply omitted it invites a query
    against a column that does not exist; saying so turns a silent absence into
    a stated one.
    """
    lines = [
        "Roof segments in this table — name a roof by its canonical name, never by "
        "the raw column:"
    ]
    for name in roofs_with_column(table):
        roof = ROOFS[name]
        column = roof.columns[table]
        rendered = f"{column}_*" if table == "radiation" else column
        aliases = ", ".join(sorted(roof.aliases))
        lines.append(
            f"  {rendered} = {roof.name} — EN \"{roof.label_en}\", DE \"{roof.label_de}\"; "
            f"also written: {aliases}"
        )
    absent = [ROOFS[n] for n in ROOFS if table not in ROOFS[n].columns]
    for roof in absent:
        lacks = "no lysimeter" if table == "outflow" else "no radiation mast"
        lines.append(
            f"  The {roof.label_en} (DE \"{roof.label_de}\", site id {roof.site_id}) has "
            f"{lacks} and therefore NO column in this table. A question about it cannot be "
            "answered from here — say so rather than substituting another roof."
        )
    return "\n".join(lines)


_TIMESTAMP = (
    "Date-time of the measurement: stored NAIVE and in UTC, at 30-minute resolution. "
    f"A day is a {SITE_TIMEZONE} calendar day everywhere in this system, so group and "
    f"filter days by {site_day_expr()} — never by CAST(timestamp AS DATE), which is the "
    "UTC day and moves an hour or two of every night into the wrong one."
)
"""The ``timestamp`` description, identical in four of the five tables.

The day boundary is stated per table because every table gets grouped by day and
the model reads whichever block its question sends it to. The SQL is
``site_day_expr()`` itself, not a retyped cast: ``decisions.md`` § The day
boundary requires the oracle and the candidate to group alike, and two copies of
one expression is exactly how they stop.
"""

_RADIATION_TIMESTAMP = (
    f"{_TIMESTAMP} WARNING: this table's clock is one hour BEHIND the other four "
    "tables'. A radiation row is stamped 60 min earlier than the outflow / swc / tsoil / "
    "wetter row recorded at the same instant (two half-hourly rows). The offset is real "
    "and the data is served as recorded; do not correct it, and do not join or compare "
    "radiation against another table on equal timestamps without accounting for it."
)
"""``radiation``'s own ``timestamp`` description.

``decisions.md`` § The ``radiation`` timestamp offset: the pinned DB is not
rebuilt, so the offset is disclosed wherever ``radiation`` is reachable — here
and in the plot vocabulary. Its validity condition is this disclosure.
"""

table_schema_dict: list[dict[str, Any]] = [
    {
        "table_name": "outflow",
        "description": (
            f"Lysimeter outflow (drainage leaving the roof build-up), in liters per {_AREA} "
            f"collection area. Because the area is exactly {_AREA}, 1 L of outflow IS 1 mm of "
            "depth on these columns: answer in mm without applying any area factor, and never "
            "multiply or divide by an area. The two *lysi* columns are small test lysimeters, "
            "not roof segments, and this equivalence does not hold for them.\n"
            f"{_roof_directory('outflow')}"
        ),
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": _TIMESTAMP},
            {"name": "Kies_Efflux", "type": "DOUBLE", "description": f"Outflow of the Lysimeter (with {_AREA} collection area) in the gravel roof (in liter, numerically mm)"},
            {"name": "Extensiv1_Efflux", "type": "DOUBLE", "description": f"Outflow of the Lysimeter (with {_AREA} collection area) in the irrigated (smart algorithm) extensive green roof (in liter, numerically mm)"},
            {"name": "Extensiv2_Efflux", "type": "DOUBLE", "description": f"Outflow of the Lysimeter (with {_AREA} collection area) in the non-irrigated extensive green roof (in liter, numerically mm)"},
            {"name": "Sumpf2_Efflux", "type": "DOUBLE", "description": f"Outflow of the Lysimeter (with {_AREA} collection area) in the wetland green roof (in liter, numerically mm)"},
            {"name": "Zeitlysi_Efflux_x", "type": "DOUBLE", "description": "Outflow of the small Lysimeter with extensive green roof substrate with a timer based irrigation (in liter). A test lysimeter, not a roof segment; its collection area is not the roof lysimeters', so liters are not millimeters here"},
            {"name": "Sensorlysi_Efflux_x", "type": "DOUBLE", "description": "Outflow of the small Lysimeter with extensive green roof substrate with a threshold based irrigation (in liter). A test lysimeter, not a roof segment; its collection area is not the roof lysimeters', so liters are not millimeters here"},
        ],
    },
    {
        "table_name": "radiation",
        "description": (
            "Radiation-mast readings, four masts, one per instrumented roof. Each roof's "
            f"entry below is a PREFIX, not a column: the mast reports {len(RADIATION_COLUMN_SUFFIXES)} "
            f"quantities, so the columns are prefix + '_' + one of "
            f"{', '.join(RADIATION_COLUMN_SUFFIXES)} (e.g. ED1_SWdown). Shortwave and longwave are "
            "in W/m²; TSFC and TSFCkorr are surface temperatures in KELVIN, not °C. "
            "This table's timestamps are one hour behind the other four tables' — see the "
            "timestamp column. It also covers only 2025-03-01 to 2025-10-01, a fraction of the "
            "other tables' span, so a window outside that range returns no rows at all.\n"
            f"{_roof_directory('radiation')}"
        ),
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": _RADIATION_TIMESTAMP},
            {"name": "ED1_SWdown", "type": "DOUBLE", "description": "downward facing shortwave radiation on the irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED1_SWup", "type": "DOUBLE", "description": "upward facing shortwave radiation on the irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED1_LWdown", "type": "DOUBLE", "description": "downward facing longwave radiation on the irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED1_LWup", "type": "DOUBLE", "description": "upward facing longwave radiation on the irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED1_TSFC", "type": "DOUBLE", "description": "surface temperature on the irrigated extensive green roof (in K)"},
            {"name": "ED1_TSFCkorr", "type": "DOUBLE", "description": "corrected surface temperature on the irrigated extensive green roof (in K)"},
            {"name": "ED2_SWdown", "type": "DOUBLE", "description": "downward facing shortwave radiation on the non-irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED2_SWup", "type": "DOUBLE", "description": "upward facing shortwave radiation on the non-irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED2_LWdown", "type": "DOUBLE", "description": "downward facing longwave radiation on the non-irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED2_LWup", "type": "DOUBLE", "description": "upward facing longwave radiation on the non-irrigated esxtensive green roof (in W/m²)"},
            {"name": "ED2_TSFC", "type": "DOUBLE", "description": "surface temperature on the non-irrigated extensive green roof (in K)"},
            {"name": "ED2_TSFCkorr", "type": "DOUBLE", "description": "corrected surface temperature on the non-irrigated extensive green roof (in K)"},
            {"name": "KD_SWdown", "type": "DOUBLE", "description": "downward facing shortwave radiation on the gravel roof (in W/m²)"},
            {"name": "KD_SWup", "type": "DOUBLE", "description": "upward facing shortwave radiation on the gravel roof (in W/m²)"},
            {"name": "KD_LWdown", "type": "DOUBLE", "description": "downward facing longwave radiation on the gravel roof (in W/m²)"},
            {"name": "KD_LWup", "type": "DOUBLE", "description": "upward facing longwave radiation on the gravel roof (in W/m²)"},
            {"name": "KD_TSFC", "type": "DOUBLE", "description": "surface temperature on the gravel roof (in K)"},
            {"name": "KD_TSFCkorr", "type": "DOUBLE", "description": "corrected surface temperature on the gravel roof (in K)"},
            {"name": "SD_SWdown", "type": "DOUBLE", "description": "downward facing shortwave radiation on the wetland green roof (in W/m²)"},
            {"name": "SD_SWup", "type": "DOUBLE", "description": "upward facing shortwave radiation on the wetland green roof (in W/m²)"},
            {"name": "SD_LWdown", "type": "DOUBLE", "description": "downward facing longwave radiation on the wetland green rcorrectedoof (in W/m²)"},
            {"name": "SD_LWup", "type": "DOUBLE", "description": "upward facing longwave radiation on the wetland green roof (in W/m²)"},
            {"name": "SD_TSFC", "type": "DOUBLE", "description": "surface temperature on the wetland green roof (in K)"},
            {"name": "SD_TSFCkorr", "type": "DOUBLE", "description": "corrected surface temperature on the wetland green roof (in K)"},
        ],
    },
    {
        "table_name": "swc",
        "description": (
            "Volumetric soil-water content, one column per roof segment, as a percentage "
            "(%θ) and never as millimetres of storage. All five roof segments are "
            "instrumented here. The gravel roof has no substrate and honestly reads near "
            "0 %θ; that is the sensor working, not failing.\n"
            f"{_roof_directory('swc')}"
        ),
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": _TIMESTAMP},
            {"name": "QGravel", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the gravel roof (in %)"},
            {"name": "QEx1", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the irrigated extensive green roof (in %)"},
            {"name": "QEx2", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the non-irrigated extensive green roof (in %)"},
            {"name": "QIn", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the intensive green roof (in %)"},
            {"name": "QWetland", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the wetland green roof (in %)"},
        ],
    },
    {
        "table_name": "tsoil",
        "description": (
            "Soil temperature, one column per roof segment, in °C. All five roof segments "
            "are instrumented here. Same roof vocabulary as swc, different prefix: T… "
            "instead of Q….\n"
            f"{_roof_directory('tsoil')}"
        ),
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": _TIMESTAMP},
            {"name": "TGravel", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the gravel roof (in °C)"},
            {"name": "TEx1", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the irrigated extensive green roof (in °C)"},
            {"name": "TEx2", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the non-irrigated extensive green roof (in °C)"},
            {"name": "TIn", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the intensive green roof (in °C)"},
            {"name": "TWetland", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the wetland green roof (in °C)"},
        ],
    },
    {
        "table_name": "wetter",
        "description": (
            "The site's own weather station: ONE set of columns shared by every roof. No "
            "column here belongs to a single roof, so a per-roof question never filters "
            "this table by roof — it joins the roof's own table on timestamp. Rain is the "
            "site's gauge, in mm.\n"
            "Roof segments in this table — none: the station measures the air above all "
            f"five ({', '.join(ROOFS[n].label_en for n in ROOFS)}) at once."
        ),
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": _TIMESTAMP},
            {"name": "Tmean", "type": "DOUBLE", "description": "Air temperature "},
            {"name": "Tmax", "type": "DOUBLE", "description": "Maximum air temperature of the day"},
            {"name": "Rain", "type": "DOUBLE", "description": "Precipitation (in mm)"},
            {"name": "Rad_SW", "type": "DOUBLE", "description": "Shortwave radiation (w/m²)"},
            {"name": "RH", "type": "DOUBLE", "description": "Relative humidity in %"},
            {"name": "windspeed", "type": "DOUBLE", "description": "in (m/s)"},
        ],
    },
]
