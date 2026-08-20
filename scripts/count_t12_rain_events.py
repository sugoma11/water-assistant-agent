"""Count T12's qualifying rain events over the pinned record (offline, read-only).

T12 ("Was the retention of the {roof} roof during {event} above the manual's
target?") draws ``{event}`` from the rain events the pinned database actually
carries. The catalog kept it train-only because that count was unknown while the
lysimeter collection area was, and the area blocker is gone — the lysimeters
collect 1 m², so outflow in litres is already millimetres and retention carries no
area factor (``specs/agent_architecture/findings.md``). This script computes the
count, so ``questions.md`` §4's T12 item can be closed either way: with enough
events for disjoint train and test_seen sets, T12 returns to test_seen; without
them, the count is the standing justification for train-only.

**Qualification** follows ``questions.md`` §1.6's filters and nothing else:

- **event** — a maximal run of consecutive Europe/Berlin calendar days whose
  station rain reaches ``WET_DAY_MM``, inside the catalog's ``as_of`` band. The day
  boundary is Europe/Berlin everywhere (``decisions.md`` § The day boundary).
- **window** — the event days plus ``DRAINAGE_TAIL_DAYS``: a lysimeter keeps
  draining after the rain stops, so a window cut at the last wet day books that
  outflow to no event and overstates retention.
- **depth** — window rain of at least ``MIN_EVENT_MM``. Retention is a ratio, and
  over a shallow event the gauge's own 0.1 mm resolution and its ~15 % undercatch
  (``findings.md``) move it further than the answer's tolerance.
- **coverage** — at least ``MIN_COVERAGE`` of the expected 48 rows per day in
  ``outflow`` over the window, with no gap over 24 h. Two whole-system lysimeter
  outages remove 39 band days (``findings.md``); without this a "retention" is
  computed from an event whose runoff was never recorded, and reads as 100 %.
- **oracle validity, per roof** — retention within [0, 1]. Outflow above rain is
  physically impossible over a closed window and means the gauge undercaught
  (frozen precipitation) or a previous event was still draining in; an oracle
  would encode the impossible value as truth.

The manual's retention target is authored eval policy that does not exist yet
(``rules_constants.py``, task T062), so this script never assumes one: it reports
the retention of every qualifying (event, roof) pair and the yes/no balance each
candidate target would produce.

Run::

    uv run python scripts/count_t12_rain_events.py
    uv run python scripts/count_t12_rain_events.py --tail 2 --min-event-mm 15
"""

import argparse
import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("WATER_ASSISTANT_DUCKDB_PATH", ROOT / "data/water.duckdb"))
REPORT_PATH = ROOT / "specs/agent_architecture/t12_rain_events.md"

# The catalog's as_of band (questions.md §1.7): the record's own limits, not choices.
BAND_START = dt.date(2025, 6, 1)
BAND_END = dt.date(2026, 4, 24)

WET_DAY_MM = 0.2  # a day belongs to an event at or above this station rain total
MIN_EVENT_MM = 10.0  # window depth below which the retention ratio is gauge noise
DRAINAGE_TAIL_DAYS = 1  # days of drainage booked to the event after its last wet day
MIN_COVERAGE = 0.95  # share of the expected 48 rows/day, per §1.6's period rule
MAX_GAP_HOURS = 24.0  # no single hole in the window may exceed this

# P1f — the flux-instrumented roofs (questions.md §1.8). The semi-intensive roof has
# no lysimeter, so it carries no outflow column and cannot be sampled at all.
ROOF_COLUMNS = {
    "gravel": "Kies_Efflux",
    "irrigated_extensive": "Extensiv1_Efflux",
    "non_irrigated_extensive": "Extensiv2_Efflux",
    "wetland": "Sumpf2_Efflux",
}

# Instances per split at the catalog's m (questions.md §1.7). Per-param disjointness
# makes these the number of *distinct* events each split needs.
TRAIN_M = 4
TEST_SEEN_M = 5

CANDIDATE_TARGETS = (0.4, 0.5, 0.6, 0.7)

BERLIN_DAY = "(timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Berlin')::date"


@dataclass
class Event:
    """A rain event and everything qualification needs to judge it."""

    start: dt.date
    end: dt.date
    window: list[dt.date]
    rain_mm: float
    rows: int
    expected_rows: int
    max_gap_h: float
    outflow_mm: dict[str, float]
    min_rain_mm: float

    @property
    def coverage(self) -> float:
        return self.rows / self.expected_rows

    @property
    def covered(self) -> bool:
        return self.coverage >= MIN_COVERAGE and self.max_gap_h <= MAX_GAP_HOURS

    def retention(self, roof: str) -> float:
        return (self.rain_mm - self.outflow_mm[roof]) / self.rain_mm

    def qualifies(self, roof: str) -> bool:
        return (
            self.rain_mm >= self.min_rain_mm
            and self.covered
            and 0.0 <= self.retention(roof) <= 1.0
        )

    @property
    def qualifying_roofs(self) -> list[str]:
        return [roof for roof in ROOF_COLUMNS if self.qualifies(roof)]


def daily_rain(con: duckdb.DuckDBPyConnection) -> dict[dt.date, float]:
    rows = con.execute(
        f"SELECT {BERLIN_DAY} AS day, sum(Rain) FROM wetter GROUP BY 1"
    ).fetchall()
    return {day: rain for day, rain in rows}


def find_events(rain: dict[dt.date, float], tail: int) -> list[tuple[dt.date, dt.date]]:
    """Maximal runs of consecutive wet band days, each extended by the drainage tail."""
    band = [d for d in sorted(rain) if BAND_START <= d <= BAND_END]
    runs: list[list[dt.date]] = []
    for day in band:
        if rain[day] < WET_DAY_MM:
            continue
        if runs and (day - runs[-1][-1]).days == 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    return [(run[0], run[-1] + dt.timedelta(days=tail)) for run in runs]


def measure(
    con: duckdb.DuckDBPyConnection,
    rain: dict[dt.date, float],
    start: dt.date,
    window_end: dt.date,
    tail: int,
    min_rain_mm: float,
) -> Event:
    """Rain, outflow and outflow coverage over one event window."""
    window = [
        start + dt.timedelta(days=i) for i in range((window_end - start).days + 1)
    ]
    sums = ", ".join(f'sum("{col}")' for col in ROOF_COLUMNS.values())
    # The gap is measured over the window's own edges too, so a hole at either end
    # counts the same as one in the middle.
    row = con.execute(
        f"""
        WITH w AS (
            SELECT timestamp, {", ".join(f'"{c}"' for c in ROOF_COLUMNS.values())}
            FROM outflow
            WHERE {BERLIN_DAY} BETWEEN ? AND ?
        ), gaps AS (
            SELECT epoch(timestamp - lag(timestamp) OVER (ORDER BY timestamp)) / 3600
                   AS gap_h
            FROM w
        )
        SELECT count(*), (SELECT max(gap_h) FROM gaps), {sums} FROM w
        """,
        [start, window_end],
    ).fetchone()
    rows, max_gap_h = row[0], row[1]
    outflow = dict(zip(ROOF_COLUMNS, (value or 0.0 for value in row[2:]), strict=True))
    expected = 48 * len(window)
    return Event(
        start=start,
        end=window_end - dt.timedelta(days=tail),
        window=window,
        rain_mm=sum(rain.get(day, 0.0) for day in window),
        rows=rows,
        expected_rows=expected,
        # A window with too few rows to lag over still has a hole; sizing it from the
        # row deficit reports the whole window as the gap when nothing was recorded.
        max_gap_h=max(max_gap_h or 0.0, 24.0 * (expected - rows) / 48),
        outflow_mm=outflow,
        min_rain_mm=min_rain_mm,
    )


def balance(events: list[Event], target: float) -> tuple[int, int, int]:
    """(pairs above target, pairs below, events offering both classes)."""
    above = below = both = 0
    for event in events:
        hits = [event.retention(roof) > target for roof in event.qualifying_roofs]
        above += sum(hits)
        below += len(hits) - sum(hits)
        both += any(hits) and not all(hits)
    return above, below, both


def report(events: list[Event], tail: int, min_rain_mm: float) -> str:
    qualifying = [e for e in events if e.qualifying_roofs]
    needed = TRAIN_M + TEST_SEEN_M
    out = [
        "# T12 — qualifying rain events in the pinned record",
        "",
        f"Generated by `scripts/count_t12_rain_events.py` against `{DB_PATH.name}`.",
        "Regenerate rather than edit. Thresholds: wet day ≥ "
        f"{WET_DAY_MM} mm, window = event + {tail} drainage day(s), depth ≥ "
        f"{min_rain_mm} mm, outflow coverage ≥ {MIN_COVERAGE:.0%} with no gap over "
        f"{MAX_GAP_HOURS:.0f} h, retention within [0, 1].",
        "",
        f"**{len(qualifying)} qualifying events** over the `as_of` band "
        f"{BAND_START} → {BAND_END}, carrying "
        f"{sum(len(e.qualifying_roofs) for e in qualifying)} (event, roof) pairs. "
        f"T12 needs {TRAIN_M} + {TEST_SEEN_M} = {needed} disjoint events to sit in "
        "train and test_seen alike (per-param disjointness, questions.md §1.7).",
        "",
        "## Events at or above the depth threshold",
        "",
        "Retention is `(rain − outflow) / rain` in mm over the window; 1 L = 1 mm on "
        "these columns. `—` marks a pair failing qualification, with the reason in "
        "the last column.",
        "",
        "| Event | Window | Rain mm | Coverage | "
        + " | ".join(roof.replace("_", " ") for roof in ROOF_COLUMNS)
        + " | Failed |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for event in events:
        if event.rain_mm < min_rain_mm:
            continue
        cells = []
        for roof in ROOF_COLUMNS:
            value = f"{event.retention(roof) * 100:.1f} %"
            cells.append(value if event.qualifies(roof) else f"— ({value})")
        failed = []
        if not event.covered:
            failed.append(f"coverage {event.coverage:.0%}, gap {event.max_gap_h:.0f} h")
        implausible = [r for r in ROOF_COLUMNS if not 0.0 <= event.retention(r) <= 1.0]
        if implausible:
            failed.append("outflow > rain: " + ", ".join(implausible))
        out.append(
            f"| {event.start} … {event.end} | {event.window[0]} … {event.window[-1]} "
            f"| {event.rain_mm:.2f} | {event.coverage:.0%} | "
            + " | ".join(cells)
            + f" | {'; '.join(failed) or '—'} |"
        )

    out += [
        "",
        "## Class balance the target would produce",
        "",
        "The manual's retention target is authored eval policy and does not exist "
        "yet (`rules_constants.py`, T062), so every candidate is shown. "
        "*Both* counts the qualifying events that carry a yes **and** a no across "
        "their roofs — the events a split can draw either class from.",
        "",
        "| Target | Pairs above | Pairs below | Events offering both |",
        "|---:|---:|---:|---:|",
    ]
    for target in CANDIDATE_TARGETS:
        above, below, both = balance(qualifying, target)
        out.append(f"| {target:.0%} | {above} | {below} | {both} |")

    out += [
        "",
        "## Per-roof qualifying pairs",
        "",
        "| Roof | Qualifying events |",
        "|---|---:|",
    ]
    for roof in ROOF_COLUMNS:
        out.append(f"| {roof} | {sum(roof in e.qualifying_roofs for e in qualifying)} |")

    verdict = (
        f"{len(qualifying)} ≥ {needed}: T12 can hold disjoint train and test_seen "
        "event sets and returns to test_seen."
        if len(qualifying) >= needed
        else f"{len(qualifying)} < {needed}: T12 stays train-only — the record "
        "cannot fill disjoint train and test_seen event sets."
    )
    out += ["", "## Verdict", "", verdict, ""]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tail", type=int, default=DRAINAGE_TAIL_DAYS)
    parser.add_argument("--min-event-mm", type=float, default=MIN_EVENT_MM)
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    rain = daily_rain(con)
    events = [
        measure(con, rain, start, window_end, args.tail, args.min_event_mm)
        for start, window_end in find_events(rain, args.tail)
    ]
    con.close()

    text = report(events, args.tail, args.min_event_mm)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
