from typing import Any

table_schema_dict: list[dict[str, Any]] = [
    # ── Reference / dimension tables ──────────────────────────────────
    {
        "table_name": "roof_segments",
        "description": (
            "Lookup table for the three roof segment types monitored on the "
            "experimental green roof platform."
        ),
        "columns": [
            {"name": "segment_id", "type": "INTEGER", "description": "Primary key for the roof segment.", "primary_key": True},
            {"name": "segment_name", "type": "TEXT", "description": "Human-readable name: 'gravel_roof', 'extensive_green_roof', or 'wetland_roof'."},
            {"name": "area_m2", "type": "REAL", "description": "Total surface area of the segment in square metres."},
            {"name": "substrate_depth_cm", "type": "REAL", "description": "Depth of the substrate layer in centimetres (NULL for the gravel roof)."},
            {"name": "num_drains", "type": "INTEGER", "description": "Number of drains per segment (typically 6)."},
        ],
    },
    {
        "table_name": "lysimeters",
        "description": (
            "Registry of the five weighing lysimeters installed across the roof "
            "segments (1 gravel, 2 extensive, 2 wetland)."
        ),
        "columns": [
            {"name": "lysimeter_id", "type": "INTEGER", "description": "Primary key for the lysimeter.", "primary_key": True},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
            {"name": "manufacturer", "type": "TEXT", "description": "Manufacturer name (Umwelt-Geräte-Technik GmbH)."},
            {"name": "weighing_accuracy_g", "type": "REAL", "description": "Accuracy of the weighing system in grams (10 g)."},
            {"name": "weighing_resolution_g", "type": "REAL", "description": "Resolution of the weighing system in grams (1 g)."},
            {"name": "surface_area_m2", "type": "REAL", "description": "Lysimeter surface area in square metres."},
        ],
    },
    {
        "table_name": "sensors",
        "description": (
            "Inventory of all sensors deployed on the roof platform, including "
            "soil probes, heat-flux plates, radiometers, tipping counters, and "
            "the weather station instruments."
        ),
        "columns": [
            {"name": "sensor_id", "type": "INTEGER", "description": "Primary key for the sensor.", "primary_key": True},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id (NULL for weather station sensors).", "foreign_key": "roof_segments.segment_id"},
            {"name": "lysimeter_id", "type": "INTEGER", "description": "Foreign key → lysimeters.lysimeter_id (NULL when sensor is not inside a lysimeter).", "foreign_key": "lysimeters.lysimeter_id"},
            {"name": "sensor_type", "type": "TEXT", "description": "Category: 'tipping_counter', 'soil_moisture_temperature', 'soil_heat_flux', 'net_radiometer', 'weather_station', 'water_meter'."},
            {"name": "model", "type": "TEXT", "description": "Sensor model designation, e.g. 'SMT 100', 'HFP01SC', 'CNR4', 'ClimaVIJE 50', 'Sensus HRI-A4'."},
            {"name": "manufacturer", "type": "TEXT", "description": "Manufacturer of the sensor."},
            {"name": "installation_depth_cm", "type": "REAL", "description": "Depth below substrate surface in cm (e.g. 5 cm for SMT 100, 4 cm for HFP01SC). NULL if not applicable."},
            {"name": "logger", "type": "TEXT", "description": "Logger the sensor is connected to: 'DT80' (2-min resolution) or 'CR1000X' (10-s resolution)."},
            {"name": "logging_interval_s", "type": "INTEGER", "description": "Logging interval in seconds (120 for DT80, 10 for CR1000X)."},
        ],
    },

    # ── Fact / time-series tables ─────────────────────────────────────
    {
        "table_name": "runoff_tipping",
        "description": (
            "Tipping-counter records for whole-segment runoff. Each row is one "
            "tip (100 mL) from a cistern connected to the six drains of a roof "
            "segment. Logged every 10 s via CR1000X."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of the recorded tip (UTC)."},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
            {"name": "sensor_id", "type": "INTEGER", "description": "Foreign key → sensors.sensor_id.", "foreign_key": "sensors.sensor_id"},
            {"name": "tip_volume_mL", "type": "REAL", "description": "Volume per tip in millilitres (nominally 100 mL)."},
            {"name": "cumulative_tips", "type": "INTEGER", "description": "Running tip count since last reset."},
        ],
    },
    {
        "table_name": "lysimeter_measurements",
        "description": (
            "High-resolution weighing-lysimeter data recorded every 2 minutes "
            "via the DataTaker DT80 logger. Includes mass (→ water storage) and "
            "lysimeter-level tipping-counter runoff."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of measurement (UTC)."},
            {"name": "lysimeter_id", "type": "INTEGER", "description": "Foreign key → lysimeters.lysimeter_id.", "foreign_key": "lysimeters.lysimeter_id"},
            {"name": "weight_g", "type": "REAL", "description": "Total lysimeter mass in grams (resolution 1 g)."},
            {"name": "water_storage_mm", "type": "REAL", "description": "Derived water storage depth in mm."},
            {"name": "runoff_tips", "type": "INTEGER", "description": "Number of tips recorded by the lysimeter tipping counter in this interval."},
            {"name": "runoff_volume_mL", "type": "REAL", "description": "Runoff volume in mL derived from tips × 100 mL."},
        ],
    },
    {
        "table_name": "soil_moisture_temperature",
        "description": (
            "Soil volumetric water content and temperature from SMT 100 probes "
            "installed at 5 cm depth. Logged every 2 min via the DT80."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of measurement (UTC)."},
            {"name": "sensor_id", "type": "INTEGER", "description": "Foreign key → sensors.sensor_id.", "foreign_key": "sensors.sensor_id"},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
            {"name": "soil_temperature_C", "type": "REAL", "description": "Substrate temperature at 5 cm depth in degrees Celsius."},
            {"name": "volumetric_water_content", "type": "REAL", "description": "Volumetric water content as a fraction (0–1)."},
        ],
    },
    {
        "table_name": "soil_heat_flux",
        "description": (
            "Ground heat flux measured by HFP01SC plates at 4 cm depth in the "
            "extensive green roof and gravel roof substrates. Logged every 2 min "
            "via the DT80."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of measurement (UTC)."},
            {"name": "sensor_id", "type": "INTEGER", "description": "Foreign key → sensors.sensor_id.", "foreign_key": "sensors.sensor_id"},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
            {"name": "heat_flux_W_per_m2", "type": "REAL", "description": "Ground heat flux in W/m². Positive = downward into substrate."},
        ],
    },
    {
        "table_name": "radiation",
        "description": (
            "Four-component radiation measurements from CNR4 net radiometers. "
            "Logged every 2 min via the DT80."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of measurement (UTC)."},
            {"name": "sensor_id", "type": "INTEGER", "description": "Foreign key → sensors.sensor_id.", "foreign_key": "sensors.sensor_id"},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
            {"name": "shortwave_down_W_per_m2", "type": "REAL", "description": "Incoming shortwave radiation in W/m²."},
            {"name": "shortwave_up_W_per_m2", "type": "REAL", "description": "Reflected shortwave radiation in W/m²."},
            {"name": "longwave_down_W_per_m2", "type": "REAL", "description": "Incoming longwave (thermal) radiation in W/m²."},
            {"name": "longwave_up_W_per_m2", "type": "REAL", "description": "Emitted longwave radiation from the surface in W/m²."},
            {"name": "net_radiation_W_per_m2", "type": "REAL", "description": "Net all-wave radiation (shortwave_down − shortwave_up + longwave_down − longwave_up)."},
            {"name": "albedo", "type": "REAL", "description": "Surface albedo (shortwave_up / shortwave_down). NULL when shortwave_down ≈ 0."},
        ],
    },
    {
        "table_name": "weather_station",
        "description": (
            "Meteorological variables from the ClimaVIJE 50 weather station "
            "(Campbell Scientific). Logged every 10 s via the CR1000X."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of measurement (UTC)."},
            {"name": "air_temperature_C", "type": "REAL", "description": "Air temperature in degrees Celsius."},
            {"name": "relative_humidity_pct", "type": "REAL", "description": "Relative humidity in percent (0–100)."},
            {"name": "wind_speed_m_per_s", "type": "REAL", "description": "Wind speed in m/s."},
            {"name": "wind_direction_deg", "type": "REAL", "description": "Wind direction in degrees from north (0–360)."},
            {"name": "precipitation_mm", "type": "REAL", "description": "Precipitation accumulated in the logging interval in mm."},
            {"name": "air_pressure_hPa", "type": "REAL", "description": "Atmospheric pressure in hPa."},
            {"name": "global_radiation_W_per_m2", "type": "REAL", "description": "Global horizontal irradiance in W/m²."},
        ],
    },
    {
        "table_name": "irrigation",
        "description": (
            "Irrigation volumes recorded by the Q3 water meter with Sensus "
            "HRI-A4 pulse generator. Logged every 10 s via the CR1000X."
        ),
        "columns": [
            {"name": "record_id", "type": "INTEGER", "description": "Auto-incrementing primary key.", "primary_key": True},
            {"name": "timestamp", "type": "TIMESTAMP", "description": "Date-time of measurement (UTC)."},
            {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
            {"name": "pulse_count", "type": "INTEGER", "description": "Cumulative pulse count from the Sensus HRI-A4 generator."},
            {"name": "volume_L", "type": "REAL", "description": "Irrigation volume delivered in litres during the logging interval."},
            {"name": "cumulative_volume_L", "type": "REAL", "description": "Cumulative irrigation volume in litres since last reset."},
        ],
    },
    # {
    #     "table_name": "drone_thermal_flights",
    #     "description": (
    #         "Metadata for thermal survey flights conducted with the DJI Mavic 2 "
    #         "Enterprise drone (thermal sensor 160 × 120 px)."
    #     ),
    #     "columns": [
    #         {"name": "flight_id", "type": "INTEGER", "description": "Primary key for the flight.", "primary_key": True},
    #         {"name": "flight_start", "type": "TIMESTAMP", "description": "Take-off time (UTC)."},
    #         {"name": "flight_end", "type": "TIMESTAMP", "description": "Landing time (UTC)."},
    #         {"name": "altitude_m", "type": "REAL", "description": "Flight altitude above roof surface in metres."},
    #         {"name": "ambient_temperature_C", "type": "REAL", "description": "Reference air temperature during the flight in °C."},
    #         {"name": "num_images", "type": "INTEGER", "description": "Number of thermal images captured."},
    #     ],
    # },
    # {
    #     "table_name": "drone_thermal_images",
    #     "description": (
    #         "Per-segment surface temperature statistics derived from each "
    #         "thermal image captured during a drone flight."
    #     ),
    #     "columns": [
    #         {"name": "image_id", "type": "INTEGER", "description": "Primary key for the image record.", "primary_key": True},
    #         {"name": "flight_id", "type": "INTEGER", "description": "Foreign key → drone_thermal_flights.flight_id.", "foreign_key": "drone_thermal_flights.flight_id"},
    #         {"name": "segment_id", "type": "INTEGER", "description": "Foreign key → roof_segments.segment_id.", "foreign_key": "roof_segments.segment_id"},
    #         {"name": "capture_time", "type": "TIMESTAMP", "description": "Timestamp of image capture (UTC)."},
    #         {"name": "mean_surface_temp_C", "type": "REAL", "description": "Mean surface temperature over the segment in °C."},
    #         {"name": "min_surface_temp_C", "type": "REAL", "description": "Minimum pixel temperature in °C."},
    #         {"name": "max_surface_temp_C", "type": "REAL", "description": "Maximum pixel temperature in °C."},
    #         {"name": "std_surface_temp_C", "type": "REAL", "description": "Standard deviation of pixel temperatures in °C."},
    #     ],
    # },
]