# Green Roof Water Balance Tool — `predict_green_roof_water_balance_tool`

Model the green roof's water balance (soil moisture, runoff, evapotranspiration) with the GR2L model, over a window of past and/or forecast days.

## When to Call This Tool
**Do not call this tool for simple weather or database queries.**

| User Query Intent | Call this tool? | Reason |
| :--- | :--- | :--- |
| **Green Roof Modeling** (e.g., "What is the soil moisture?", "How much runoff?", "Drought risk?", "Stormwater retention?", "Wie ist die Bodenfeuchte auf dem extensive Dach?") | Yes | This requires the GR2L water-balance model. |
| **What-If Scenarios** (e.g., "What if we got 50mm rain?", "What if the roof started dry?") | Yes | Use the `forcings` or `initial_soil_moisture_pct` parameters. |
| **Historical Weather/Sensors** (e.g., "How much rain fell last week?", "How many heat days?", "What did the gravel roof do?") | No — `text_to_sql_agent` | This is a database query. It asks what a sensor recorded; this tool models a balance rather than reading the record. |
| **Future Weather Forecast** (e.g., "Will it rain tomorrow?", "Is rain expected in 2 days?") | No — `get_weather_forecast_tool` | This is a simple weather query. Call this tool only if the user asks about the *roof's reaction* to the weather. |

## Green Roof Tool Specifications
When calling `predict_green_roof_water_balance_tool`, adhere to these domain-specific rules:

### 1. Roof Types & Exclusions
*   **Modelable Types:** `non_irrigated_extensive`, `irrigated_extensive`, `semi_intensive`.
*   **Excluded Types:** `gravel roof`, `wetland`.
    *   If the user asks about these, the tool will return `status='not_available'`.
    *   **Action:** Inform the user that these cannot be modeled (no substrate for gravel, sensor limitation for wetland) but their sensor data is available via the database (`text_to_sql_agent`).

### 2. Data & Units
*   **Soil Moisture:** Expressed in **% volumetric water content (%θ)**. Millimeters of stored water appear alongside for water balance.
*   **Weather Source:** The tool fetches its own weather. If `weather_source='station'`, state that data comes from the site's own instruments.
*   **Initial Moisture:** The tool reads the roof's own sensor for the window start date.
    *   **Do not pass `initial_soil_moisture_pct`** unless the user explicitly asks for a "what-if" starting value (e.g., "if the roof started at 5%").
*   **Albedo:** Default is 0.2 for vegetated roofs.
    *   **Do not override** unless the user explicitly describes a different surface (e.g., "cool roof coating", "snow").
    *   If overridden, state the value used in the answer.

### 3. Date Windows
*   **Range:** Up to 16 days ahead.
*   **Past:** Supported via `past_days` (complete past days ending yesterday).
*   **Future:** Supported via `forecast_days` (0-16 days from today).
*   **Combined:** Can combine past and forecast to simulate across "today".
*   **Long Windows:** If >31 days, the tool returns `truncated: true` with weekly aggregates. Answer from the summary, do not re-run.

### 4. What-If Scenarios (`forcings`)
*   Use `forcings` only when the user asks "what *would* happen under different weather".
*   Format: `{"precip": {"YYYY-MM-DD": 50.0}}`.
*   **Constraint:** Every day named must fall inside the window.
*   **Transparency:** State in the answer which values were assumed rather than measured.

### 5. Model Evaluation (`evaluate_against_measured`)
*   Set to `True` only when the user asks "how close was the simulation to the sensor?" or "did the model get last month right?".
*   **Constraint:** Only works for past windows. On forecasts, it returns `days: 0`.
*   **Cost:** It requires an extra database read. Do not use for standard queries.