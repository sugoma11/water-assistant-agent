"""Every decision the millimetre fix moves, replayed over the record (T067).

The deployed controller adds millimetres of rain and ET to a store held in
%VWC. Correcting that rescales each roof's response to rain by ``100 / SH_mm``,
which changes what the model predicts and with it some decisions — and the
trigger levels are **not** re-derived to absorb the change, because a rescaled
set would be reproducible only from this repository where the deployed ones are
reproducible from the site's own documentation
(``decisions.md`` § No fitted correction between the instrument and the oracle).

So the correction is measured instead. This script replays one historical day
after another through **both** regimes and emits every date and roof where the
irrigate decision flips. That list is two things: the evidence the site would
re-tune against, and the bound on what this testbed's irrigation answers say
about the deployed system (``agent_architecture.md`` §8). The list is the
deliverable, not the run — it is committed beside this script, the way T012's
rain-event count is.

**Unit handling is the only variable.** Both arms take the same station rows, the
same ``et_fao56`` ET0, the same measured seed and the same trigger levels; the
one difference is whether the store is held in %VWC or in millimetres
(:class:`~...irrigation.Regime`). Both arms are read back in %θ, where the two
regimes' thresholds are the same numbers — ``rules_constants.py`` authors them in
%θ and converts once — so a flip is never a threshold moving, only a trajectory.

**The forcing is what actually happened.** Each decision day is replayed against
the station's own record for the following week rather than against the forecast
the site would have had, so forecast error is not part of the comparison. That
overstates neither arm: they see the identical rows.

Run::

    uv run python scripts/irrigation_decision_diff.py
    uv run python scripts/irrigation_decision_diff.py --start 2025-06-01 --end 2026-04-24
"""

import argparse
import dataclasses
import datetime as dt
import os
from collections import Counter
from pathlib import Path

import duckdb

from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
)
from water_assistant_agent.assistant.et_fao56 import et0_for_row
from water_assistant_agent.assistant.irrigation import Regime, RoofRun, run_roof
from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    REFILL_HORIZON_HOURS,
    ROOF_RULES,
    horizon_rows,
    rules_for,
)
from water_assistant_agent.assistant.tools.site import SITE_ELEVATION_M, SITE_LATITUDE
from water_assistant_agent.assistant.tools.swc import (
    SwcUnavailableError,
    latest_measured_swc,
)
from water_assistant_agent.assistant.tools.weather_station import StationWeatherSource

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("WATER_ASSISTANT_DUCKDB_PATH", ROOT / "data/water.duckdb"))
REPORT_PATH = ROOT / "specs/agent_architecture/irrigation_decision_diff.md"

# The catalog's `as_of` band (questions.md §1.7) — the days a case can be asked
# about, which is what makes this the bound on the testbed's own answers.
BAND_START = dt.date(2025, 6, 1)
BAND_END = dt.date(2026, 4, 24)

DAY_HOURS = 24.0
WINDOW_DAYS = horizon_rows(REFILL_HORIZON_HOURS, step_hours=DAY_HOURS)
"""The tool's own window: the refill horizon, in daily rows."""


@dataclasses.dataclass(frozen=True, slots=True)
class Comparison:
    """One roof on one day, decided twice."""

    day: dt.date
    roof: str
    seed_pct: float
    max_tx: float
    deployed: RoofRun
    millimetres: RoofRun

    @property
    def flipped(self) -> bool:
        return self.deployed.decision.irrigate != self.millimetres.decision.irrigate

    def min_swc_pct(self, run: RoofRun) -> float:
        """That arm's driest step, read back in %θ so the two are comparable."""
        return run.regime.theta_pct_from_store(run.features.min_store, rules_for(self.roof))


def compare(
    day: dt.date,
    roof: str,
    seed_pct: float,
    precip: list[float],
    et0: list[float],
    tx: list[float],
) -> Comparison:
    """Decide *roof* on *day* twice, changing nothing but the store's unit."""
    runs = {
        regime: run_roof(
            roof,
            precipitation_mm=precip,
            et0_mm=et0,
            temperature_c=tx,
            seed_theta_pct=seed_pct,
            step_hours=DAY_HOURS,
            regime=regime,
        )
        for regime in Regime
    }
    return Comparison(
        day=day,
        roof=roof,
        seed_pct=seed_pct,
        max_tx=max(tx[: horizon_rows(DECISION_HORIZON_HOURS, step_hours=DAY_HOURS)]),
        deployed=runs[Regime.PERCENT_THETA],
        millimetres=runs[Regime.MILLIMETRES],
    )


def replay(
    executor: DuckDbQueryExecutor, start: dt.date, end: dt.date
) -> tuple[list[Comparison], Counter[str]]:
    """Every decision day in ``[start, end]`` the record can serve, decided twice.

    A day is skipped, and counted, when the station record cannot cover its whole
    window or when a roof has no trustworthy seed to start from — the same two
    reasons the tool itself returns ``not_available``, so the comparison covers
    exactly the days the tool would have answered.
    """
    station = StationWeatherSource(executor)
    rows = {row.Date: row for row in station.daily_rows(start, end + dt.timedelta(days=WINDOW_DAYS))}
    et0 = {
        date: et0_for_row(row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M)
        for date, row in rows.items()
    }

    comparisons: list[Comparison] = []
    skipped: Counter[str] = Counter()
    day = start
    while day <= end:
        window = [
            rows.get((day + dt.timedelta(days=offset)).isoformat())
            for offset in range(WINDOW_DAYS)
        ]
        if any(row is None for row in window):
            skipped["window not covered by the station record"] += 1
            day += dt.timedelta(days=1)
            continue

        precip = [row.precip for row in window]
        forcing = [et0[row.Date] for row in window]
        tx = [row.tx for row in window]
        for roof in ROOF_RULES:
            try:
                seed = latest_measured_swc(
                    executor, roof, day, as_of=dt.datetime.combine(day, dt.time.min)
                )
            except SwcUnavailableError:
                skipped[f"no soil-moisture seed ({roof})"] += 1
                continue
            comparisons.append(compare(day, roof, seed.theta_pct, precip, forcing, tx))
        day += dt.timedelta(days=1)
    return comparisons, skipped


def _decision(run: RoofRun) -> str:
    verdict = "**irrigate**" if run.decision.irrigate else "no"
    return f"{verdict} ({run.decision.reason.value})"


def report(comparisons: list[Comparison], skipped: Counter[str], start: dt.date, end: dt.date) -> str:
    """The committed list, and enough around it to read the list correctly."""
    flips = [c for c in comparisons if c.flipped]
    days = len({c.day for c in comparisons})
    added = [c for c in flips if c.millimetres.decision.irrigate]
    removed = [c for c in flips if not c.millimetres.decision.irrigate]

    out = [
        "# Irrigation decisions the millimetre fix moves",
        "",
        f"Generated by `scripts/irrigation_decision_diff.py` against `{DB_PATH.name}`.",
        "Regenerate rather than edit.",
        "",
        "The deployed controller holds its store in %VWC and adds millimetres of rain "
        "and ET to it. Carrying the balance into millimetres rescales each roof's "
        "response to rain by `100 / SH_mm` — about 1.43 %θ per mm on the 7 cm "
        "extensive roofs against the deployed 1.0, and 0.67 on the 15 cm "
        "semi-intensive. **The trigger levels are not re-derived**: they are the "
        "site's, carried verbatim, and whether to re-tune them against this list is "
        "the site's call (`decisions.md` § No fitted correction between the "
        "instrument and the oracle).",
        "",
        "**Method.** Each day of the catalog's `as_of` band is decided twice over the "
        f"same {WINDOW_DAYS}-day window — the refill horizon, the tool's own window — "
        "with the same station rows, the same `et_fao56` ET0, the same measured seed "
        "and the same trigger levels. The only difference is the unit the store is "
        "held in. Both arms are read back in %θ, where the two regimes' thresholds "
        "are the same numbers, so a flip is a trajectory and never a threshold. The "
        "window is forced by what the station actually recorded, not by the forecast "
        "the site would have had, so forecast error is not part of the comparison.",
        "",
        f"**{len(flips)} of {len(comparisons)} decisions flip** "
        f"({len(flips) / len(comparisons):.1%}) across {days} days and "
        f"{len(ROOF_RULES)} roofs over {start} → {end}: the millimetre balance "
        f"irrigates on {len(added)} where the deployed one does not, and declines on "
        f"{len(removed)} where it does.",
        "",
    ]
    if skipped:
        out += [
            "Days the comparison could not cover, for the same reasons the tool "
            "itself abstains — an uncovered window, or no trustworthy seed: "
            + "; ".join(f"{count} — {reason}" for reason, count in sorted(skipped.items()))
            + ".",
            "",
        ]

    out += [
        "## Every decision that flips",
        "",
        "`min %θ` is the driest step of the 48 h decision window in each arm, read "
        "back in %θ. `refill` is whether an outflow was expected inside the "
        f"{REFILL_HORIZON_HOURS} h window.",
        "",
        "| Date | Roof | Deployed (%VWC store) | Corrected (mm store) | seed %θ | "
        "min %θ deployed | min %θ mm | wilting / dry %θ | max tx °C | refill dep / mm |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for flip in flips:
        rules = rules_for(flip.roof)
        out.append(
            f"| {flip.day} | {flip.roof.replace('_', ' ')} "
            f"| {_decision(flip.deployed)} | {_decision(flip.millimetres)} "
            f"| {flip.seed_pct:.1f} | {flip.min_swc_pct(flip.deployed):.1f} "
            f"| {flip.min_swc_pct(flip.millimetres):.1f} "
            f"| {rules.wilting_pct:.0f} / {rules.dry_pct:.0f} | {flip.max_tx:.1f} "
            f"| {flip.deployed.features.will_reach_capacity!s:.1} / "
            f"{flip.millimetres.features.will_reach_capacity!s:.1} |"
        )
    if not flips:
        out.append("| — | — | — | — | — | — | — | — | — | — |")

    out += ["", "## Flips per roof", "", "| Roof | Decisions | Flips | Share |", "|---|---:|---:|---:|"]
    for roof in ROOF_RULES:
        total = sum(c.roof == roof for c in comparisons)
        moved = sum(c.roof == roof for c in flips)
        share = f"{moved / total:.1%}" if total else "—"
        out.append(f"| {roof.replace('_', ' ')} | {total} | {moved} | {share} |")

    out += [
        "",
        "## Which rung gives way to which",
        "",
        "The pair of reason codes on each side of a flip. A pair that dominates is "
        "the sentence to write about this fix.",
        "",
        "| Deployed | Corrected | Flips |",
        "|---|---|---:|",
    ]
    pairs = Counter(
        (c.deployed.decision.reason.value, c.millimetres.decision.reason.value) for c in flips
    )
    for (deployed, corrected), count in pairs.most_common():
        out.append(f"| {deployed} | {corrected} | {count} |")
    if not pairs:
        out.append("| — | — | 0 |")

    out += [
        "",
        "## What this list bounds",
        "",
        "An irrigation answer from this testbed is the *corrected* column. On the "
        "dates and roofs above it differs from what the roof's own controller would "
        "have done, and on every other day of the band it does not. That is the "
        "whole of the difference between the two, ET0 and forecast error held out.",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=BAND_START)
    parser.add_argument("--end", type=dt.date.fromisoformat, default=BAND_END)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    executor = DuckDbQueryExecutor(connection_factory=lambda: con)
    comparisons, skipped = replay(executor, args.start, args.end)
    con.close()

    if not comparisons:
        raise SystemExit(f"No decision day between {args.start} and {args.end} could be replayed.")

    text = report(comparisons, skipped, args.start, args.end)
    args.out.write_text(text, encoding="utf-8")
    flips = sum(c.flipped for c in comparisons)
    print(f"{flips} of {len(comparisons)} decisions flip. Written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
