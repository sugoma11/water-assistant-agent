"""Run one prompt-optimization search — ``just search``.

The thin end of :mod:`harness.optimize`: parse a budget, run the search, print
what §7 reads it beside. Everything of substance is in the module, because the
search and the measurement run have to reach the same rollout through the same
construction and a driver that assembled its own would be the one way to make
them differ.

**What a run needs standing up.** The MLflow registry and tracking server
(``just dev-up``), because the candidate surface *is* the prompt registry and
``optimize_prompts`` logs per iteration against it. The task model's and the
reflection model's endpoint. And the GR2L service, because the search runs in
record mode — a miss on a window the candidate chose is recorded rather than
excluded (§5), and recording is gated on a canary the service has to answer.

**A short run first.** ``--limit`` searches over the first few captured train
records and ``--max-metric-calls`` bounds the rollouts; the pair is the smoke
test that the whole loop closes. A real run is the whole split at a budget
pre-registered before any test number is seen (T129).

Usage::

    just search                       # the whole train split at the default budget
    just search-smoke                 # 3 records, 8 rollouts — does the loop close?
    uv run python scripts/run_search.py --limit 20 --max-metric-calls 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import mlflow  # noqa: E402

from harness.optimize import (  # noqa: E402
    DEFAULT_MAX_METRIC_CALLS,
    OPTIMIZED_ARM,
    run_search,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--split", default="train", help="the committed split to search over"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="search over at most this many captured records (default: all)",
    )
    parser.add_argument(
        "--max-metric-calls",
        type=int,
        default=DEFAULT_MAX_METRIC_CALLS,
        help="GEPA's rollout budget, pre-registered per run",
    )
    parser.add_argument("--arm", default=OPTIMIZED_ARM, help="the arm's name")
    parser.add_argument(
        "--progress", action="store_true", help="show GEPA's progress bar"
    )
    args = parser.parse_args(argv)

    print(f"Registry: {mlflow.get_registry_uri()}")
    search = run_search(
        split=args.split,
        limit=args.limit,
        max_metric_calls=args.max_metric_calls,
        arm=args.arm,
        display_progress_bar=args.progress,
    )

    result = search.result
    print(f"\nSearched {search.records} record(s) of {args.split}")
    print(f"Reflection model: {search.reflection_model}")
    print(f"Selection score:  {result.initial_eval_score} → {result.final_eval_score}")
    for label, scores in (
        ("initial", result.initial_eval_score_per_scorer),
        ("final", result.final_eval_score_per_scorer),
    ):
        # Per scorer, unblended — what reflection read. Selection ran on the
        # scalar above, which is the one number the Pareto front sees (§7).
        print(f"  {label:<8}{scores}")
    for prompt in result.optimized_prompts:
        print(f"  optimized {prompt.name} → v{prompt.version} ({len(prompt.template)} chars)")

    print(f"\n{search.residuals.summary()}")
    if search.residuals.residual_cases:
        print(
            "Residual failures are declared 0s, not exclusions. Compare this count "
            "against the other arm's before reading the scores (§7)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
