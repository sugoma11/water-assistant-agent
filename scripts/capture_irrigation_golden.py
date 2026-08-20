"""Replay the deployed irrigation controller and commit its series as a fixture (T064).

The port in ``assistant/irrigation.py`` has to reproduce the controller
*element for element*, and the controller lives outside any repository —
``/home/shpilevo/Downloads/smart_irrigation.py``, an extraction of the site's own
``temp/smart_irrigation.py`` (``findings.md`` § External sources on this machine).
A test that imported it at run time would stop testing the port the day that file
moves, so this script runs it **once** and commits what it produced;
``tests/assistant/test_irrigation_golden.py`` replays the committed numbers and
never touches the path above.

**In the controller's own regime, and before the unit fix.** The store is held in
%VWC with millimetres of rain and ET added to it, the flat 22 %VWC capacity
applies to all three roofs, and the forcing is **hourly** — the step the site
runs, at which the horizons in hours and the controller's hard-coded row counts
(``[:48]``, ``[1:168]``) are the same slice. Every window below is 168 hours for
that reason. Landing this before T065 is what makes every later difference
attributable to the unit fix rather than to the port.

**The forcing is the pinned record's, not an invention.** Hourly rain and air
temperature come from ``wetter`` (Berlin hours, the site's own day boundary), the
day-1 store from each roof's own ``swc`` sensor under the one seed rule
(:func:`~...swc.latest_measured_swc`), and ET0 from ``et_fao56`` — the port of
GR2L's R routine — spread evenly across the day's 24 hours, because the site's
ICON forcing carries an hourly ``et0_fao_evapotranspiration`` this record has no
equivalent of. The spreading is a property of the *fixture*, not of the tool:
both implementations receive the identical numbers, which is all the comparison
needs.

The windows are chosen to walk the whole ladder — a hot dry spell, a hot wet one,
a cold one — plus two degenerate cases the controller has branches for. Their
reason codes are printed at the end; a window that stops adding a rung is a
window worth replacing.

Run::

    uv run python scripts/capture_irrigation_golden.py
    uv run python scripts/capture_irrigation_golden.py --controller /path/to/smart_irrigation.py
"""

import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
)
from water_assistant_agent.assistant.et_fao56 import et0_for_row
from water_assistant_agent.assistant.tools.roofs import ROOFS
from water_assistant_agent.assistant.tools.site import (
    SITE_ELEVATION_M,
    SITE_LATITUDE,
    site_day_expr,
)
from water_assistant_agent.assistant.tools.swc import latest_measured_swc
from water_assistant_agent.assistant.tools.weather_station import StationWeatherSource

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("WATER_ASSISTANT_DUCKDB_PATH", ROOT / "data/water.duckdb"))
CONTROLLER_PATH = Path(
    os.environ.get("SMART_IRRIGATION_PATH", "/home/shpilevo/Downloads/smart_irrigation.py")
)
FIXTURE_PATH = ROOT / "tests/assistant/fixtures/irrigation_golden.json"

HOURS_PER_DAY = 24
WINDOW_HOURS = 168
"""One refill horizon exactly: ``[1:168]`` is the whole series bar the seed step."""

# The site's ids are the controller's roof keys; the canonical names are this
# repository's. Read off `roofs.py` rather than transcribed, so the pairing that
# `irrigation_tool.md` § Which extensive roof is which argues for cannot drift.
CONTROLLER_ROOFS: dict[str, str] = {
    roof.site_id: name for name, roof in ROOFS.items() if roof.site_id is not None
}

WINDOWS: list[tuple[str, dt.date]] = [
    # 0.3 mm of rain and 38.1 °C: heat, dry roofs, and no refill to wait for.
    # Rungs 1 and 5.
    ("hot_dry", dt.date(2025, 6, 28)),
    # A week that reaches 29.7 °C but not inside the first 48 hours, over roofs
    # sitting at their driest. Rungs 1 and 2.
    ("hot_wet", dt.date(2025, 7, 12)),
    # 25 °C over roofs the April rain left wet, with more rain behind it: the one
    # window that reaches the middle of the ladder. Rungs 3 and 4.
    ("warm_wet", dt.date(2025, 4, 16)),
]


def load_controller(path: Path) -> Any:
    """Import the deployed controller from *path* as a module.

    Registered in ``sys.modules`` before execution because its ``@dataclass``
    decorators resolve their own module to read annotations.
    """
    spec = importlib.util.spec_from_file_location("deployed_smart_irrigation", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot import the deployed controller from {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def hourly_forcing(
    con: duckdb.DuckDBPyConnection, start: dt.date
) -> tuple[list[str], list[float], list[float]]:
    """Berlin-hour rain totals and mean air temperature over the window from *start*.

    Only whole hours — both half-hourly samples present — are served, and the
    window must come back complete: a hole would make the two implementations
    agree about a series neither the site nor this system would ever run.
    """
    end = start + dt.timedelta(days=WINDOW_HOURS // HOURS_PER_DAY - 1)
    berlin = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Berlin')"
    rows = con.execute(
        f"""
        SELECT date_trunc('hour', {berlin}) AS hour, sum(Rain), avg(Tmean)
        FROM wetter
        WHERE {site_day_expr()} BETWEEN ? AND ?
        GROUP BY 1 HAVING count(*) = 2 ORDER BY 1
        """,  # noqa: S608 - both expressions are this module's own literals
        [start, end],
    ).fetchall()
    if len(rows) != WINDOW_HOURS:
        raise SystemExit(
            f"The window from {start} has {len(rows)} complete hours, not {WINDOW_HOURS}."
        )
    hours = [hour.isoformat(sep=" ") for hour, _, _ in rows]
    return hours, [float(rain) for _, rain, _ in rows], [float(tm) for _, _, tm in rows]


def hourly_et0(executor: DuckDbQueryExecutor, start: dt.date) -> list[float]:
    """The window's daily FAO-56 ET0, each day's value spread over its 24 hours.

    ET0 is a daily quantity here — ``DailyWeatherRow`` carries no hourly forcing
    and the station derivation is per day — while the controller consumes an
    hourly series. Spreading evenly is the fixture's own convention, stated here
    and used nowhere else.
    """
    days = WINDOW_HOURS // HOURS_PER_DAY
    end = start + dt.timedelta(days=days - 1)
    rows = StationWeatherSource(executor).daily_rows(start, end)
    if len(rows) != days:
        raise SystemExit(f"The station record has {len(rows)} of the {days} days from {start}.")
    return [
        et0_for_row(row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M) / HOURS_PER_DAY
        for row in rows
        for _ in range(HOURS_PER_DAY)
    ]


def seeds(executor: DuckDbQueryExecutor, start: dt.date) -> dict[str, float]:
    """Each roof's day-1 %θ, read under the seed rule the tool itself uses."""
    as_of = dt.datetime.combine(start, dt.time.min)
    return {
        site_id: latest_measured_swc(executor, roof, start, as_of=as_of).theta_pct
        for site_id, roof in CONTROLLER_ROOFS.items()
    }


def replay(controller: Any, forcing: dict[str, Any], initial: dict[str, float]) -> dict[str, Any]:
    """Run the controller over one forcing window and record every roof's series.

    Goes through ``run_water_balance`` and ``decide_substrate_roof`` — the
    composition the site actually runs — rather than the private bucket, so the
    fixture pins the controller's own wiring and not a re-assembly of its parts.
    """
    frame = pd.DataFrame(
        {
            "Date": pd.to_datetime(forcing["hours"]),
            "precipitation": forcing["precip_mm"],
            "et0_fao_evapotranspiration": forcing["et0_mm"],
            "temperature_2m": forcing["temperature_c"],
        }
    )
    state = controller.RoofState(
        theta_egr1=initial["EGR1"],
        theta_egr2=initial["EGR2"],
        theta_egr=0.0,
        theta_igr=initial["IGR"],
        # The wetland's lysimeter is out of scope with the roof itself; the
        # controller still runs its bucket, and nothing here reads it.
        weight_wgr=70.0,
        weight_l5=0.0,
    )
    model = controller.run_water_balance(frame, state)

    triggers = {
        "EGR1": (controller.EGR1_WILTING, controller.EGR1_DRY),
        "EGR2": (controller.EGR2_WILTING, controller.EGR2_DRY),
        "IGR": (controller.IGR_WILTING, controller.IGR_DRY),
    }
    roofs: dict[str, Any] = {}
    for site_id, (wilting, dry) in triggers.items():
        decision = controller.decide_substrate_roof(model, site_id, wilting, dry)
        roofs[site_id] = {
            "roof_type": CONTROLLER_ROOFS[site_id],
            "seed_theta_pct": initial[site_id],
            "wilting_pct": wilting,
            "dry_pct": dry,
            "store": model[f"{site_id}_store"].tolist(),
            "outflow": model[f"{site_id}_outflow"].tolist(),
            "et_actual": [
                None if math.isnan(value) else value
                for value in model[f"{site_id}_et"].tolist()
            ],
            "ks": model[f"{site_id}_ks"].tolist(),
            "irrigate": bool(decision.irrigate),
            "reason_de": decision.reason,
        }
    return roofs


def degenerate_cases(controller: Any) -> list[dict[str, Any]]:
    """The two windows the controller refuses to decide from.

    Neither carries a series: an empty window has none, and a gap makes every
    step after it ``NaN`` — what is pinned here is the rung, not the bucket. The
    gapped *forcing* is committed, ``NaN`` and all, so the replay meets the same
    hole the controller met; Python's ``json`` writes and reads that literal,
    which is why the fixture is read with ``json`` and not by a strict parser.
    """
    empty = pd.DataFrame(
        {
            "Date": pd.to_datetime([]),
            "precipitation": [],
            "et0_fao_evapotranspiration": [],
            "temperature_2m": [],
        }
    )
    state = controller.RoofState(0.0, 0.0, 0.0, 0.0, 70.0, 0.0)
    no_rows = controller.decide_substrate_roof(
        controller.run_water_balance(empty, state), "EGR1", 5.0, 10.0
    )

    hours = WINDOW_HOURS
    gapped = pd.DataFrame(
        {
            "Date": pd.date_range("2025-06-28", periods=hours, freq="h"),
            # One hour of rain the forecast did not carry: the store is NaN from
            # there on, and a gap is not a dry roof.
            "precipitation": [float("nan") if i == 5 else 0.0 for i in range(hours)],
            "et0_fao_evapotranspiration": [0.2] * hours,
            "temperature_2m": [26.0] * hours,
        }
    )
    state = controller.RoofState(14.0, 14.0, 14.0, 14.0, 70.0, 0.0)
    with_gap = controller.decide_substrate_roof(
        controller.run_water_balance(gapped, state), "EGR1", 5.0, 10.0
    )

    return [
        {
            "name": "empty_window",
            "roof_type": CONTROLLER_ROOFS["EGR1"],
            "seed_theta_pct": 0.0,
            "precip_mm": [],
            "et0_mm": [],
            "temperature_c": [],
            "irrigate": bool(no_rows.irrigate),
            "reason_de": no_rows.reason,
        },
        {
            "name": "gap_in_the_forcing",
            "roof_type": CONTROLLER_ROOFS["EGR1"],
            "seed_theta_pct": 14.0,
            "precip_mm": gapped["precipitation"].tolist(),
            "et0_mm": gapped["et0_fao_evapotranspiration"].tolist(),
            "temperature_c": gapped["temperature_2m"].tolist(),
            "irrigate": bool(with_gap.irrigate),
            "reason_de": with_gap.reason,
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, default=CONTROLLER_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out", type=Path, default=FIXTURE_PATH)
    args = parser.parse_args()

    controller = load_controller(args.controller)
    con = duckdb.connect(str(args.db), read_only=True)
    executor = DuckDbQueryExecutor(connection_factory=lambda: con)

    cases = []
    for name, start in WINDOWS:
        hours, precip, temperature = hourly_forcing(con, start)
        forcing = {
            "hours": hours,
            "precip_mm": precip,
            "et0_mm": hourly_et0(executor, start),
            "temperature_c": temperature,
        }
        cases.append(
            {
                "name": name,
                "start": start.isoformat(),
                "step_hours": 1.0,
                **forcing,
                "roofs": replay(controller, forcing, seeds(executor, start)),
            }
        )

    fixture = {
        "_comment": (
            "The deployed controller's own output, captured by "
            "scripts/capture_irrigation_golden.py. Regenerate rather than edit — "
            "these numbers are evidence about a file outside this repository "
            "(findings.md, External sources on this machine), not test data to "
            "adjust. Hourly forcing, %VWC store, before the millimetre fix."
        ),
        "controller": str(args.controller),
        "captured": dt.date.today().isoformat(),
        "capacity_pct": controller.SWC_CAPACITY,
        "residual_pct": controller.THETA_RESIDUAL * 100,
        "heat_threshold_c": controller.HEAT_THRESHOLD_C,
        "decision_horizon_h": controller.DECISION_HORIZON_H,
        "outflow_horizon_h": controller.OUTFLOW_HORIZON_H,
        "outflow_eps": controller.OUTFLOW_EPS,
        "cases": cases,
        "degenerate": degenerate_cases(controller),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fixture, indent=1) + "\n", encoding="utf-8")

    for case in cases:
        for site_id, roof in case["roofs"].items():
            print(
                f"{case['name']:>8} {site_id:>5} seed {roof['seed_theta_pct']:5.1f} %θ  "
                f"min {min(roof['store']):6.2f}  irrigate={roof['irrigate']!s:<5} "
                f"{roof['reason_de'][:60]}"
            )
    for case in fixture["degenerate"]:
        print(f"{case['name']:>18}  irrigate={case['irrigate']!s:<5} {case['reason_de'][:60]}")
    print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
