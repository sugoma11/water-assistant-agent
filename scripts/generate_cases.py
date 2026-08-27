"""Generate the evaluation suite and emit the three split files (T112–T114).

One command from the catalog to ``eval/cases/{train,test_seen,test_unseen}.json``.
It draws every template through §1.6's filters, answers each draw through its
oracle, cuts the splits per §1.7, asks each instance in its planned language and
register, and writes the three files sorted and pretty-printed.

**The cases are generated and never hand-edited** (``eval/schema/README.md``), so
this script is the only thing that writes them, and a re-run with the same seed
against the same pinned surfaces rewrites the same bytes. ``--check`` is that
claim as an exit code: it generates, serializes, and compares against what is
committed without writing anything.

**Answering the model families needs the live GR2L service and the weather
Archive**, because an oracle imports the tool's own code and the tool fetches. The
responses land in ``--cache-dir``, which defaults to a scratch directory rather
than ``eval/cache/``: that directory is the capture pass's to fill and commit
(T116), and a generation run touches every draw it *rejected* as well as every
case it kept. ``--offline`` restricts the run to the templates the pinned database
alone can answer, which is what a machine with no service can do.

Run::

    uv run python scripts/generate_cases.py
    uv run python scripts/generate_cases.py --check
    uv run python scripts/generate_cases.py --offline --out /tmp/cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.generation import emit  # noqa: E402
from eval.generation.instantiate import cached_contexts, generate  # noqa: E402
from eval.generation.templates import TEMPLATES  # noqa: E402

DEFAULT_CACHE = ROOT / ".generation-cache"
"""Where a generation run records what it fetched.

Deliberately not ``eval/cache/``: that one is the replay cache the measurement
pass reads and T116 commits, and it holds the requests the *cases* make. A
generation run makes many more — every rejected draw fetched too — and mixing
them would commit responses no case will ever ask for.
"""

OFFLINE_TEMPLATES: tuple[str, ...] = (
    "T01", "T02", "T03", "T04", "T05", "T06", "T08", "T12", "T15a", "T17a",
    "T17b", "T24b",
)
"""Templates whose oracles read the pinned database and the card store only.

Family C reads weather, D/E/G reach GR2L, H's model overlay runs the series
through the plot tool's trigger; each needs a live surface the first time it is
seen. What is left is a real generation pass over four families, which is enough
to exercise the loop end to end without a service.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=emit.CASES_DIR,
        help="directory the three split files are written to",
    )
    parser.add_argument(
        "--seed", type=int, default=20260824, help="the draw seed; the same seed rewrites the same bytes"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="where live responses are recorded; never eval/cache/, which T116 owns",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="only the templates the pinned database can answer, for a machine with no service",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="generate and compare against the committed files; write nothing, exit 1 on a difference",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the generation report as JSON as well as printing it",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    templates = (
        {name: TEMPLATES[name] for name in OFFLINE_TEMPLATES}
        if args.offline
        else TEMPLATES
    )
    run = asyncio.run(
        generate(
            templates=templates,
            seed=args.seed,
            context_for=cached_contexts(
                allow_live=not args.offline, cache_dir=args.cache_dir
            ),
        )
    )

    report = run.report()
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if report["parameter_overlaps"] or report["value_overlaps"]:
        print(
            "\nRefusing to emit: train and test_seen share a sampled parameter value, "
            "which opens the memorized-constant detector (questions.md §1.7). "
            "`value_overlaps` alone means the value sits on both sides through two "
            "different pools of one parameter — a ladder is missing "
            "(`decisions.md § Value pools are striped, and the holdout takes them "
            "whole`).",
            file=sys.stderr,
        )
        return 1

    if args.check:
        differences = []
        for split, filename in emit.SPLIT_FILES.items():
            cases = run.for_split(split)
            if not cases:
                continue
            path = args.out / filename
            proposed = emit.serialize(cases)
            committed = path.read_text(encoding="utf-8") if path.exists() else None
            if committed != proposed:
                differences.append(
                    f"{filename}: {'differs' if committed else 'is not committed'}"
                )
        if differences:
            print("\n" + "\n".join(differences), file=sys.stderr)
            return 1
        print("\nThe committed files are byte-identical to a fresh generation pass.")
        return 0

    written = emit.emit(run, directory=args.out)
    for split, path in written.items():
        print(f"\n{split}: {len(run.for_split(split))} cases → {path}")

    if args.report is not None:
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
