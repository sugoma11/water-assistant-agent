"""Register the repaired candidate as the third arm — ``just repair-register`` (T136).

The one registered search selected candidate 4, whose whole difference from
candidate 1 is one rewritten GR2L tool description — and that description names a
tool no toolset resolves, legislates for the other tools from inside one tool's
declaration, and restates the root instruction's answer contract. The repair is
committed in ``eval/repair/`` beside the text it repairs and the diff between
them; this script is what puts it in the registry.

**It registers a third arm and never overwrites the second.** §7's registration
defines the optimized arm as whatever candidate the one registered search
selects, so the repaired candidate is registered under its own versions and
recorded in ``eval/repaired_candidate.json`` — which
:func:`~harness.measure.arm_versions` reads only when a caller asks for it by
name. Nothing here can move the optimized arm: the six untouched components are
read back at the optimized arm's own versions and re-registered only if the
registry does not already hold that text, which for byte-identical text it does.

**The repaired arm has no numbers until it is measured**, and measuring it needs
the budget decision T134 owns. Registering it is what makes the measurement
possible; it is not the measurement.

Usage::

    just repair-diff        # regenerate eval/repair/gr2l.diff from the two texts
    just repair-check       # hold the committed repair to the registered candidate
    just repair-register    # register it and record eval/repaired_candidate.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

import mlflow  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from harness.candidates import (  # noqa: E402
    CANDIDATE_COMPONENTS,
    candidate_uri,
    register_candidates,
)
from harness.measure import (  # noqa: E402
    OPTIMIZED_ARM_FILE,
    REPAIRED_ARM_FILE,
    write_optimized_arm,
)
from harness.repair import (  # noqa: E402
    DIFF_FILE,
    REPAIRED_COMPONENT,
    RepairDriftError,
    repaired_text,
    repaired_texts,
    selected_text,
    verify,
    write_diff,
)

load_dotenv()

COMMIT_MESSAGE = (
    "the selected candidate with its GR2L tool text repaired: the real callable "
    "named, scoped to the tool it declares, the root instruction's contract "
    "dropped (T136)"
)


class _Prompt:
    """What :func:`~harness.measure.write_optimized_arm` reads off a registration."""

    def __init__(self, name: str, version: int) -> None:
        self.name = name
        self.version = version


def optimized_versions() -> dict[str, int]:
    """The versions the registered search selected, read from the file it wrote."""
    if not OPTIMIZED_ARM_FILE.exists():
        raise FileNotFoundError(
            f"No optimized candidate recorded at {_shown(OPTIMIZED_ARM_FILE)}. "
            "There is nothing to repair until the registered search has selected "
            "something."
        )
    recorded = json.loads(OPTIMIZED_ARM_FILE.read_text(encoding="utf-8"))["versions"]
    return {name: int(version) for name, version in recorded.items()}


def diff() -> int:
    """Regenerate the committed diff from the two committed texts."""
    path = write_diff()
    print(f"Wrote {_shown(path)} ({len(path.read_text(encoding='utf-8'))} chars)")
    return 0


def check() -> int:
    """Hold the committed repair to the candidate it repairs, and to its own diff."""
    versions = optimized_versions()
    try:
        verify(versions)
    except RepairDriftError as drift:
        print(f"DRIFTED  {drift}")
        return 1
    selected, repaired = selected_text(), repaired_text()
    print(f"ok       selected  {len(selected)} chars, as registered at v{versions[REPAIRED_COMPONENT]}")
    print(f"ok       repaired  {len(repaired)} chars")
    print(f"ok       diff      {_shown(DIFF_FILE)} matches")
    return 0


def register() -> int:
    """Register the repaired candidate and record the versions it landed at."""
    versions = optimized_versions()
    texts = repaired_texts(versions)
    print(f"Registering {len(texts)} components against {mlflow.get_registry_uri()}")
    registered = register_candidates(texts, commit_message=COMMIT_MESSAGE)
    for component in CANDIDATE_COMPONENTS:
        moved = "  <- repaired" if component == REPAIRED_COMPONENT else ""
        print(
            f"  {component:<38} {candidate_uri(component, registered[component])}"
            f"  ({len(texts[component])} chars){moved}"
        )
    write_optimized_arm(
        [_Prompt(*item) for item in _named(registered)],
        path=REPAIRED_ARM_FILE,
        arm="repaired",
        repaired_component=REPAIRED_COMPONENT,
        repairs=json.loads(OPTIMIZED_ARM_FILE.read_text(encoding="utf-8"))["versions"],
        note=(
            "T136. A third arm, never the optimized one: §7 defines that arm as "
            "whatever the one registered search selects."
        ),
    )
    print(f"\nRecorded the repaired arm in {_shown(REPAIRED_ARM_FILE)}.")
    return 0


def _named(versions: dict[str, int]) -> list[tuple[str, int]]:
    from harness.candidates import PROMPT_NAMES

    return [(PROMPT_NAMES[component], versions[component]) for component in versions]


def _shown(path: Path) -> str:
    """*path* relative to the repository when it is inside it, absolute otherwise."""
    return str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify the committed repair against the registered candidate and the diff",
    )
    mode.add_argument(
        "--diff",
        action="store_true",
        help="regenerate eval/repair/gr2l.diff from the two committed texts",
    )
    args = parser.parse_args(argv)

    if args.diff:
        return diff()
    if args.check:
        return check()
    return register()


if __name__ == "__main__":
    raise SystemExit(main())
