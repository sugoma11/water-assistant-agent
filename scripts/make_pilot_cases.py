"""Emit ``eval/cases/pilot.json`` — the three pilot templates, hand-instantiated.

**A stopgap, and named as one.** T111 is the real generator: it samples parameters
under §1.6's filters, balances the bools by rejection sampling and cuts the splits.
T107 runs before all of that and needs cases for T01, T07 and T09 anyway, so the
draws here are *chosen* rather than sampled — written down in :data:`DRAWS`, one
short list, so the pilot set is reproducible from the repository instead of being
a JSON blob nobody can re-derive. When T111 lands, this script goes.

**What is hand-written is the draw, not the answer.** Everything in
``expectations`` that could disagree with the tools is computed: the answer, the
unit and the pins come from ``eval/oracles``, through the same context a rollout
gets. What is copied in is the per-template constant half the catalog fixes — the
gold trajectory, the must-not set, the cards, the tolerance (``questions.md`` §2,
``agent_architecture.md`` §6.1) — which is exactly the split T114 will keep.

The emitted file is validated against ``eval/schema/case.schema.json`` before it
is written, so a case that would not survive T010's schema never lands.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.oracles import ORACLES  # noqa: E402
from harness.assertions import assert_case  # noqa: E402
from harness.run_case import _as_instant, make_case_context  # noqa: E402

OUT = REPO_ROOT / "eval" / "cases" / "pilot.json"
SCHEMA = REPO_ROOT / "eval" / "schema" / "case.schema.json"

TEMPLATES: dict[str, dict[str, Any]] = {
    # questions.md §2 A — T01: numeric (L, ±2 % rel), Traj {text_to_sql_agent},
    # Must-not `lookup_reference` (train's only distractor slot for it).
    "T01": {
        "roof_pool": "P1f",
        "answer_metric": "scored",
        "tolerance": {"kind": "rel", "value": 0.02},
        "expected_tool_calls": [{"name": "text_to_sql_agent"}],
        "must_not_tools": ["lookup_reference"],
        "gold_cards": [],
        "argument_checks": [],
    },
    # questions.md §2 E — T07: bool, Traj {calc_irrigation}, Cards [] (the
    # documentation cue is deleted, not reworded — T009).
    "T07": {
        "roof_pool": "P2",
        "answer_metric": "scored",
        "tolerance": {"kind": "exact"},
        "expected_tool_calls": [{"name": "calc_irrigation"}],
        "must_not_tools": [],
        "gold_cards": [],
        "argument_checks": [],
    },
    # questions.md §2 D — T09: bool, Traj {predict_green_roof_water_balance_tool}.
    "T09": {
        "roof_pool": "P2",
        "answer_metric": "scored",
        "tolerance": {"kind": "exact"},
        "expected_tool_calls": [{"name": "predict_green_roof_water_balance_tool"}],
        "must_not_tools": [],
        "gold_cards": [],
        "argument_checks": [],
    },
}

DRAWS: list[dict[str, Any]] = [
    # T01 — the month is complete at as_of, which the oracle enforces and the
    # `period_param_within_as_of` assertion cannot see ("2025-07" carries no day).
    {
        "case_id": "T01-0001",
        "template_id": "T01",
        "as_of": "2025-08-14T08:00:00+02:00",
        "language": "en",
        "question": "What was the total outflow of the gravel roof in July 2025?",
        "params": {"roof": "gravel", "month": "2025-07"},
    },
    {
        "case_id": "T01-0002",
        "template_id": "T01",
        "as_of": "2025-10-10T09:00:00+02:00",
        "language": "en",
        "question": (
            "What was the total outflow of the non-irrigated extensive roof in "
            "September 2025?"
        ),
        "params": {"roof": "non_irrigated_extensive", "month": "2025-09"},
    },
    {
        "case_id": "T01-0003",
        "template_id": "T01",
        "as_of": "2026-03-05T09:00:00+01:00",
        "language": "en",
        "question": "What was the total outflow of the wetland roof in February 2026?",
        "params": {"roof": "wetland", "month": "2026-02"},
    },
    # T07 — "right now" from as_of, so the seven-day refill horizon always reaches
    # past the cut and the forcing falls to the Archive whole. Two false and one
    # true, so a candidate that always answers one way scores 1/3 rather than 1.
    {
        "case_id": "T07-0001",
        "template_id": "T07",
        "as_of": "2025-07-20T07:00:00+02:00",
        "language": "en",
        "question": "Does the non-irrigated extensive roof need irrigation right now?",
        "params": {"roof": "non_irrigated_extensive"},
    },
    {
        "case_id": "T07-0002",
        "template_id": "T07",
        "as_of": "2026-03-10T07:00:00+01:00",
        "language": "en",
        "question": "Does the irrigated extensive roof need irrigation right now?",
        "params": {"roof": "irrigated_extensive"},
    },
    {
        "case_id": "T07-0003",
        "template_id": "T07",
        "as_of": "2025-08-05T07:00:00+02:00",
        "language": "en",
        "question": "Does the semi-intensive roof need irrigation right now?",
        "params": {"roof": "semi_intensive"},
    },
    # T09 — one true and two false. The thresholds are picked to clear §1.6's
    # oracle-validity margin: the modelled minima are 12.25, 13.71 and 9.02 %θ,
    # each several points away from its own threshold, so none of these three
    # turns on a rounding.
    {
        "case_id": "T09-0001",
        "template_id": "T09",
        "as_of": "2025-07-20T07:00:00+02:00",
        "language": "en",
        "question": (
            "Will the soil moisture of the non-irrigated extensive roof fall below "
            "20 %θ over the next 3 days?"
        ),
        "params": {"roof": "non_irrigated_extensive", "thr": 20, "d": 3},
    },
    {
        "case_id": "T09-0002",
        "template_id": "T09",
        "as_of": "2026-03-10T07:00:00+01:00",
        "language": "en",
        "question": (
            "Will the soil moisture of the irrigated extensive roof fall below "
            "10 %θ over the next 3 days?"
        ),
        "params": {"roof": "irrigated_extensive", "thr": 10, "d": 3},
    },
    {
        "case_id": "T09-0003",
        "template_id": "T09",
        "as_of": "2025-08-05T07:00:00+02:00",
        "language": "en",
        "question": (
            "Will the soil moisture of the semi-intensive roof fall below 5 %θ "
            "over the next 2 days?"
        ),
        "params": {"roof": "semi_intensive", "thr": 5, "d": 2},
    },
]


async def build(*, allow_live: bool) -> list[dict[str, Any]]:
    """One case per draw, answers materialized through the oracles.

    One event loop for the whole set: ``gr2l_client`` holds its ``httpx``
    client in a module-level singleton bound to the loop that made it, so a loop
    per case fails on the second live GR2L call (``run_case.run_case``'s note).
    """
    cases: list[dict[str, Any]] = []
    for draw in DRAWS:
        template = TEMPLATES[draw["template_id"]]
        inputs = {key: value for key, value in draw.items() if key != "question"}
        inputs["question"] = draw["question"]
        ctx = make_case_context(_as_instant(draw["as_of"]), allow_live=allow_live)
        answer = await ORACLES[draw["template_id"]](inputs, ctx)

        case = {
            "inputs": {
                "question": draw["question"],
                "as_of": draw["as_of"],
                "case_id": draw["case_id"],
                "template_id": draw["template_id"],
                "params": draw["params"],
                "language": draw["language"],
            },
            "expectations": {
                **answer.expectations(),
                "answer_metric": template["answer_metric"],
                "tolerance": template["tolerance"],
                "expected_tool_calls": template["expected_tool_calls"],
                "must_not_tools": template["must_not_tools"],
                "gold_cards": template["gold_cards"],
                "argument_checks": template["argument_checks"],
            },
        }
        assert_case(case, roof_pool=template["roof_pool"])
        cases.append(case)
    return sorted(cases, key=lambda case: case["inputs"]["case_id"])


def main() -> int:
    allow_live = "--replay" not in sys.argv
    cases = asyncio.run(build(allow_live=allow_live))

    validator = Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    )
    failed = False
    for case in cases:
        for error in validator.iter_errors(case):
            failed = True
            print(f"INVALID {case['inputs']['case_id']}: {error.message}")
    if failed:
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(cases, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for case in cases:
        expectations = case["expectations"]
        print(
            f"{case['inputs']['case_id']}  {expectations['answer']!r} "
            f"{expectations['unit'] or ''}".rstrip()
        )
    print(f"\n{len(cases)} cases → {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
