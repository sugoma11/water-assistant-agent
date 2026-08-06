"""Semantic layer for water.duckdb.

Column descriptions copied verbatim from data/timeseries/ReadMe_Sensordata .txt.
Columns the ReadMe flags as 'raw data --> you can ignore that' or 'artefact of
merging' are omitted. CNR4.* columns are already dropped at DB build time
(see scripts/build_db.py).
"""
from typing import Any

table_schema_dict: list[dict[str, Any]] = [
    {
        "table_name": "outflow",
        "description": "",
        "columns": [
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the measurement"},
            {"name": "Gravel_outflow", "type": "DOUBLE", "description": "Outflow of the Lysimeter (with m² collection area) in the gravel roof (in liter)"},
            {"name": "Extensiv1_Efflux", "type": "DOUBLE", "description": "Outflow of the Lysimeter (with m² collection area) in the irrigated (smart algorithm) extensive green roof (in liter)"},
            {"name": "Extensiv2_Efflux", "type": "DOUBLE", "description": "Outflow of the Lysimeter (with m² collection area) in the non-irrigated extensive green roof (in liter)"},
            {"name": "Sumpf2_Efflux", "type": "DOUBLE", "description": "Outflow of the Lysimeter (with m² collection area) in the wetland green roof (in liter)"},
            {"name": "Zeitlysi_Efflux_x", "type": "DOUBLE", "description": "Outflow of the small Lysimeter (with m² collection area) with extensive green roof substrate with a timer based irrigation (in liter)"},
            {"name": "Sensorlysi_Efflux_x", "type": "DOUBLE", "description": "Outflow of the small Lysimeter (with m² collection area) with extensive green roof substrate with a threshold based irrigation (in liter)"},
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
