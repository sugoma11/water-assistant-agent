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

from water_assistant_agent.assistant.tools.roofs import LYSIMETER_AREA_M2

_AREA = f"{LYSIMETER_AREA_M2:g} m²"
"""The collection area as it is written into the descriptions, e.g. ``1 m²``.

``:g`` because the model is being told a physical quantity and ``1.0 m²`` reads
as a measurement with a precision the number does not carry.
"""

table_schema_dict: list[dict[str, Any]] = [
    {
        "table_name": "outflow",
        "description": (
            f"Lysimeter outflow (drainage leaving the roof build-up), in liters per {_AREA} "
            f"collection area. Because the area is exactly {_AREA}, 1 L of outflow IS 1 mm of "
            "depth on these columns: answer in mm without applying any area factor, and never "
            "multiply or divide by an area. The two *lysi* columns are small test lysimeters, "
            "not roof segments, and this equivalence does not hold for them."
        ),
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the measurement"},
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
        "description": "",
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the measurement"},
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
        "description": "",
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the measurement"},
            {"name": "QGravel", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the gravel roof (in %)"},
            {"name": "QEx1", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the irrigated extensive green roof (in %)"},
            {"name": "QEx2", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the non-irrigated extensive green roof (in %)"},
            {"name": "QIn", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the intensive green roof (in %)"},
            {"name": "QWetland", "type": "DOUBLE", "description": "Sensor Average of Soil moisture in the wetland green roof (in %)"},
        ],
    },
    {
        "table_name": "tsoil",
        "description": "",
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the measurement"},
            {"name": "TGravel", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the gravel roof (in °C)"},
            {"name": "TEx1", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the irrigated extensive green roof (in °C)"},
            {"name": "TEx2", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the non-irrigated extensive green roof (in °C)"},
            {"name": "TIn", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the intensive green roof (in °C)"},
            {"name": "TWetland", "type": "DOUBLE", "description": "Sensor Average of Soil temperature in the wetland green roof (in °C)"},
        ],
    },
    {
        "table_name": "wetter",
        "description": "",
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the measurement"},
            {"name": "Tmean", "type": "DOUBLE", "description": "Air temperature "},
            {"name": "Tmax", "type": "DOUBLE", "description": "Maximum air temperature of the day"},
            {"name": "Rain", "type": "DOUBLE", "description": "Precipitation (in mm)"},
            {"name": "Rad_SW", "type": "DOUBLE", "description": "Shortwave radiation (w/m²)"},
            {"name": "RH", "type": "DOUBLE", "description": "Relative humidity in %"},
            {"name": "windspeed", "type": "DOUBLE", "description": "in (m/s)"},
        ],
    },
]
