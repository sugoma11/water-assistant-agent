"""Warm ``eval/cache`` across the windows a candidate might plausibly ask for.

**Why this exists.** T107's pilot replayed cleanly only because the capture pass
happened to record the windows the replay asked for. That is luck, not a
property: the cache is keyed on the request, so a repeat that reads the horizon
one day differently is a miss, a miss under replay is a ``CacheMissError``, and
that surfaces as an ``upstream`` error — which **excludes the rollout**. The bias
runs the wrong way. A candidate that varies its windows gets its messiest runs
dropped from the denominator, which can raise its mean; exclusion rate would
track candidate consistency rather than harness health.

So the fix is to make a miss unlikely rather than to reinterpret one: capture the
neighbouring windows too, and replay hits whatever sane reading the candidate
brings.

**Only the day count is warmed, not the argument form**, and that is not a
shortcut. Both tool wrappers run ``resolve_window`` *before* touching a client,
so ``forecast_days=3`` and an explicit ``today..today+2`` reach the cache as the
same resolved request and share one key. The form the candidate chose is already
irrelevant by the time a key exists; only the days it denotes are not.

Belongs in T116 when that lands — this is the pilot-sized version of the same
pass, kept separate from case generation because warming a cache is not
generating a case. It reads the committed ``eval/cases/pilot.json``, which stays
as the pilot's record; the hand-instantiation script that wrote that file was
``make_pilot_cases.py``, retired by T111 (:mod:`eval.generation`).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import json  # noqa: E402

from harness.run_case import _as_instant, make_case_context  # noqa: E402
from water_assistant_agent.assistant.rules_constants import (  # noqa: E402
    REFILL_HORIZON_HOURS,
    horizon_rows,
)
from water_assistant_agent.assistant.tools.gr2l import run_roof_model  # noqa: E402
from water_assistant_agent.assistant.tools.gr2l_client import (  # noqa: E402
    normalize_roof_type,
)
from water_assistant_agent.assistant.tools.irrigation import DAY_HOURS  # noqa: E402
from water_assistant_agent.assistant.tools.weather_client import (  # noqa: E402
    resolve_window,
)

CASES = REPO_ROOT / "eval" / "cases" / "pilot.json"
NEIGHBOURHOOD = (-1, 0, 1)
"""How far either side of the oracle's own horizon to warm.

±1 day covers the readings the pilot actually observed — a candidate treating
"the next d days" as reaching d days *past* today rather than d days *including*
it. It is not a claim that no candidate can ask for anything else; a miss is
still possible and still excludes, which is why §7 publishes the exclusion count
per arm rather than assuming this pass made it zero.
"""


def cache_files() -> set[str]:
    return {p.name for p in (REPO_ROOT / "eval" / "cache").glob("*.json")}


async def warm_case(case: dict[str, Any]) -> None:
    inputs = case["inputs"]
    template, params = inputs["template_id"], inputs["params"]
    case_id = inputs["case_id"]
    ctx = make_case_context(_as_instant(inputs["as_of"]), allow_live=True)
    today = ctx.as_of.date()

    if template == "T01":
        # Pure SQL against the pinned database — nothing goes through the cache.
        print(f"  {case_id}: no cached dependency")
        return

    if template == "T07":
        # The refill horizon is the tool's own constant, not an argument the
        # candidate picks, so the only drift here is a candidate that calls the
        # weather tool alongside `calc_irrigation` on a nearby window.
        base = horizon_rows(REFILL_HORIZON_HOURS, step_hours=DAY_HOURS)
        for delta in NEIGHBOURHOOD:
            span = base + delta
            if span < 1:
                continue
            start, end = resolve_window(forecast_days=span, today=today)
            await ctx.weather.fetch(start_date=start, end_date=end)
            print(f"  {case_id}: warmed weather {start}..{end} ({span}d)")
        return

    if template == "T09":
        roof = normalize_roof_type(str(params["roof"]))
        for delta in NEIGHBOURHOOD:
            span = int(params["d"]) + delta
            if span < 1:
                continue
            start, end = resolve_window(forecast_days=span, today=today)
            # Through run_roof_model, the tool's own route: this records the
            # weather window AND the GR2L run keyed on those exact forcing rows.
            await run_roof_model(ctx, roof, start, end)
            print(f"  {case_id}: warmed model {start}..{end} ({span}d)")
        return

    raise SystemExit(f"No warming rule for template {template!r}.")


async def main() -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    before = cache_files()
    for case in cases:
        await warm_case(case)
    added = cache_files() - before
    print(f"\n{len(added)} new cache entries ({len(before)} → {len(before) + len(added)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
