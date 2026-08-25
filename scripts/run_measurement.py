"""Run the measurement and report it — ``just measure``.

The registered run: two arms, three splits, three repeats, paired on identical
cases, with the LLM response cache off and the response cache replaying. The
statistics come off this path and never off the optimizer's internal scores
(``agent_architecture.md`` §7).

**Capture the task-model canary first.** ``just pins-task`` probes the live task
model and pins the hash of its reply. The agent under test is served under an
undated alias, so a provider-side swap moves every number here while leaving
every recorded surface identical — and a swap landing between the two arms is the
case this run's comparability actually rests on. Detecting it afterwards is the
most the mechanism ever offers, which is the argument for capturing immediately
before rather than at some convenient time.

**What a run needs standing up.** The MLflow tracking server and registry
(``just dev-up``), because both arms *are* registered prompt versions. The task
model's endpoint. Not the GR2L service and not Open-Meteo: the measurement path
replays, and a miss is an exclusion rather than a fetch.

**A re-analysis needs none of that.** The run writes its outcomes to
``eval/measurements/``, and ``--analyze`` renders the identical report from that
file, because the statistics are a function of the outcomes and nothing else.

Usage::

    just pins-task                       # first, and immediately before
    just measure                         # the registered run
    just measure-report <file>           # re-render a written measurement
    uv run python scripts/run_measurement.py --splits train --repeats 1 \\
        --exploratory                    # a short one, labelled as one
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import mlflow  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from harness.measure import (  # noqa: E402
    arm_versions,
    measure,
    outcomes_from,
    registered_protocol,
)
from harness.preregistration import PREREG_FILE, digest, load  # noqa: E402
from harness.stats import render  # noqa: E402

load_dotenv()

MEASUREMENTS = REPO_ROOT / "eval" / "measurements"


def show_preregistration() -> int:
    """Print what was registered and its digest, before anything runs."""
    print(f"{PREREG_FILE.relative_to(REPO_ROOT)}  sha256 {digest()}")
    print(json.dumps(load(), indent=2))
    return 0


def analyze(path: Path) -> int:
    """Render §7's report from a written measurement, with no model in reach."""
    print(f"Re-analysing {path}\n")
    print(render(outcomes_from(path)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--analyze",
        type=Path,
        default=None,
        help="re-render the report from a written measurement and exit",
    )
    parser.add_argument(
        "--show-preregistration",
        action="store_true",
        help="print the registration and its digest, and exit",
    )
    # Defaulted from the registration, not from the module's constants: the
    # registered protocol is what a bare `just measure` should run, and a default
    # that disagreed with it would make the honest invocation the refused one.
    registered_splits, registered_repeats = registered_protocol()
    parser.add_argument(
        "--splits",
        nargs="+",
        default=registered_splits,
        help="which splits to measure (default: the registered ones)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=registered_repeats,
        help="repeats per condition (default: the registered count)",
    )
    parser.add_argument(
        "--workers", type=int, default=1, help="rollouts in flight per condition"
    )
    parser.add_argument(
        "--exploratory",
        action="store_true",
        help=(
            "declare a run that deviates from the pre-registration. It runs, its "
            "deviations are logged with it, and it is not a test number."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the outcomes (default: eval/measurements/<stamp>.json)",
    )
    args = parser.parse_args(argv)

    if args.show_preregistration:
        return show_preregistration()
    if args.analyze is not None:
        return analyze(args.analyze)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or MEASUREMENTS / f"{stamp}.json"
    arms = arm_versions()

    print(f"Tracking:  {mlflow.get_tracking_uri()}")
    print(f"Prereg:    {digest()}")
    for arm, versions in arms.items():
        print(f"Arm {arm:<10} {json.dumps(dict(sorted(versions.items())))}")
    print(
        f"Conditions: {len(arms)} arm(s) × {len(args.splits)} split(s) × "
        f"{args.repeats} repeat(s)\n"
    )

    measurement = measure(
        arms=arms,
        splits=args.splits,
        repeats=args.repeats,
        workers=args.workers,
        exploratory=args.exploratory,
        run_name=f"measurement-{stamp}",
    )

    for condition in measurement.conditions:
        print(condition.summary())
        print(f"  {condition.ledger.summary()}")

    written = measurement.write(out)
    print(f"\nWritten to {written.relative_to(REPO_ROOT)}\n")
    print(render(measurement.outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
