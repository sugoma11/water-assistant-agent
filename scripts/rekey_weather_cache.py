"""T146's re-key: turn the committed weather windows into committed weather days.

``ArchiveWeatherClient`` caches one calendar day per entry and assembles a window
from days. Everything captured before that change is keyed on a *window*, so
without this pass the switch would throw away 1677 entries' worth of captured
reanalysis and re-fetch it — and re-fetching is the one thing a pinned surface
should not need to do to stay pinned.

**Nothing is recomputed here.** A window entry already holds one
:class:`DailyWeatherRow` per day; this pass files each row under the canonical
one-day request for the day it says it is, carrying the window response's own
provenance — the grid cell Archive answered from, its timezone, the ``archive``
source stamp — onto it. The result is byte-identical to what a one-day fetch
would have recorded, which is checkable rather than asserted: the 187 windows
that were already one day long are re-derived by the same code path and must come
out equal to the entries they replace.

**The day is a sound unit, measured rather than assumed.** The committed windows
overlap heavily — 1677 of them over 439 distinct days — so every shared day is an
independent statement of the same value by a different request. Re-keyed, they
**agree on all 439 with no conflict**, which is the empirical half of the
argument §5 makes structurally (ERA5 is a reanalysis: a day's row does not depend
on the window it was asked for). A conflict would mean the opposite, so this pass
refuses to write one — it reports every disagreeing day and exits non-zero rather
than silently letting the last writer win.

GR2L entries are not touched. Its key is the whole request, ``data[]`` and
parameters, because GR2L is the component whose determinism the cache carries
(``agent_architecture.md`` §5); there is no day in it to decompose to.

Run::

    uv run python scripts/rekey_weather_cache.py            # report, write nothing
    uv run python scripts/rekey_weather_cache.py --apply    # re-key and delete the windows
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from harness.run_case import EVAL_CACHE_DIR  # noqa: E402
from water_assistant_agent.assistant.cache import ResponseCache  # noqa: E402
from water_assistant_agent.assistant.tools.weather_client import (  # noqa: E402
    ARCHIVE_URL,
    _build_params,
    days_in_window,
)


def is_archive(entry: Mapping[str, Any]) -> bool:
    """Whether *entry* is an Open-Meteo one — the shape test ``capture_cache`` uses.

    By shape rather than by a recorded label, because the entries are written by
    the clients and no client writes one.
    """
    request = entry.get("request")
    return isinstance(request, Mapping) and "url" in request


def day_entries(entry: Mapping[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """One ``(request, response)`` pair per day of *entry*'s window, keyed by day.

    The request is rebuilt through :func:`_build_params` — the client's own — from
    the coordinates the entry was captured at, so a re-keyed day is a key the
    running client actually asks for and not one this script invented. An entry
    whose parameters that function cannot reproduce is returned empty and reported
    by the caller: it was captured under a request shape this deployment no longer
    sends, and re-keying it would commit an entry nothing will ever look up.
    """
    params = dict(entry["request"]["params"])
    latitude, longitude = params["latitude"], params["longitude"]
    start, end = str(params["start_date"]), str(params["end_date"])
    if entry["request"]["url"] != ARCHIVE_URL or _build_params(
        latitude, longitude, start, end
    ) != params:
        return {}

    response = entry["response"]
    rows = {str(row["Date"]): row for row in response["data"]}
    split: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for day in days_in_window(start, end):
        row = rows.get(day)
        if row is None:
            # A window the backend answered short. Nothing to file for that day,
            # and inventing one would put a hole into the assembled window later.
            continue
        split[day] = (
            {"url": ARCHIVE_URL, "params": _build_params(latitude, longitude, day, day)},
            {**response, "data": [row]},
        )
    return split


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the day entries and delete the window entries they replace",
    )
    parser.add_argument("--cache-dir", type=Path, default=EVAL_CACHE_DIR)
    args = parser.parse_args(argv)

    cache = ResponseCache(args.cache_dir)
    paths = sorted(args.cache_dir.glob("*.json"))
    print(f"Re-keying {len(paths)} committed entries in {args.cache_dir}")

    days: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    conflicts: dict[str, list[str]] = {}
    windows: list[Path] = []
    unrecognized: list[str] = []
    gr2l = reproduced = 0

    for path in paths:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if not is_archive(entry):
            gr2l += 1
            continue
        split = day_entries(entry)
        if not split:
            unrecognized.append(path.name)
            continue
        for day, (request, response) in split.items():
            held = days.get(day)
            if held is not None and held[1] != response:
                conflicts.setdefault(day, []).append(path.name)
                continue
            days[day] = (request, response)
        if len(split) > 1:
            windows.append(path)
            continue
        # A window that was already one day is the pass's own control: the split
        # has to reproduce it, key and bytes alike, or the day entries this
        # writes are not what a one-day fetch would have recorded.
        (request, response), = split.values()
        if cache.key_for(request) == path.stem and response == entry["response"]:
            reproduced += 1
        else:
            conflicts.setdefault(f"{path.stem} (already per-day)", []).append(path.name)

    print(f"  {gr2l} GR2L entries, untouched")
    print(f"  {len(paths) - gr2l} Open-Meteo entries → {len(days)} distinct days")
    print(f"  {len(windows)} multi-day windows to delete")
    print(f"  {reproduced} already per-day, each re-derived identical to what is committed")
    if unrecognized:
        print(f"  {len(unrecognized)} entries whose request shape this client no longer sends:")
        for name in unrecognized[:10]:
            print(f"    {name}")

    if conflicts:
        print(f"\n{len(conflicts)} day(s) disagree across the windows that hold them:")
        for day, sources in sorted(conflicts.items())[:10]:
            print(f"  {day}: {', '.join(sources[:4])}")
        print(
            "\nThe day is not a sound cache unit for this data, and the re-key is "
            "refused. Nothing has been written."
        )
        return 1

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return 0

    for request, response in days.values():
        cache.put(request, response)
    for path in windows:
        path.unlink()
    print(f"\n{len(days)} day entries written, {len(windows)} window entries deleted.")
    print(f"{sum(1 for _ in args.cache_dir.glob('*.json'))} entries now committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
