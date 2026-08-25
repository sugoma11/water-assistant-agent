"""Register the handwritten baseline as the candidate surface's seed — ``just candidates``.

The baseline is **two things at once**: the reference arm §7 measures against,
and the seed candidate GEPA starts its search from. They are the same seven
strings, and the only way to keep them the same is to have one registration and
two readers of it — a second copy for the reference arm would drift from the
seed the first time either was touched, and the drift would show up as a search
that improved on something nobody was measuring (T125).

So this script registers, once, and pins what the registry assigned:

* ``register`` (the default) registers one prompt version per component from
  :func:`~harness.candidates.baseline_texts` and writes the version numbers into
  ``eval/pins.json`` under ``candidate_prompt_versions``. Byte-identical text is
  **not** re-registered, so re-running it is a no-op rather than a new version.
* ``--check`` reads the pinned versions back and compares them with the
  handwritten text. This is the anti-drift check with the registry in the loop:
  ``just pins`` hashes the text this repo *would* register, and this hashes what
  was *actually* registered under the pinned versions. A pin pointing at the
  wrong version passes the first and fails the second.

Needs a reachable MLflow registry (``MLFLOW_TRACKING_URI``, ``just dev-up``).

Usage::

    just candidates             # register the seed and pin its versions
    just candidates-check       # verify the pinned versions still hold the seed
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

from harness.candidates import (  # noqa: E402
    CANDIDATE_COMPONENTS,
    PROMPT_NAMES,
    VERSIONS_PIN,
    baseline_texts,
    candidate_uri,
    read_candidate,
    register_candidates,
    seed_versions,
)

PINS_PATH = REPO_ROOT / "eval" / "pins.json"


def register(commit_message: str | None) -> int:
    """Register the seed candidate and pin the versions the registry assigned."""
    texts = baseline_texts()
    print(f"Registering {len(texts)} components against {mlflow.get_registry_uri()}")
    versions = register_candidates(texts, commit_message=commit_message)
    for component in CANDIDATE_COMPONENTS:
        print(
            f"  {component:<38} {candidate_uri(component, versions[component])}"
            f"  ({len(texts[component])} chars)"
        )
    _write_versions(versions)
    print(f"\nPinned {VERSIONS_PIN} in {_shown(PINS_PATH)}.")
    return 0


def check() -> int:
    """Compare the pinned versions' registered text with the handwritten text."""
    texts = baseline_texts()
    versions = seed_versions(PINS_PATH)
    drifted: list[str] = []
    for component in CANDIDATE_COMPONENTS:
        version = versions[component]
        registered = read_candidate(component, version)
        if registered == texts[component]:
            print(f"ok       {component}: {PROMPT_NAMES[component]} v{version}")
            continue
        drifted.append(component)
        print(f"DRIFTED  {component}: {PROMPT_NAMES[component]} v{version}")
        print(f"           registered {len(registered)} chars, handwritten {len(texts[component])}")

    print(f"\n{len(CANDIDATE_COMPONENTS) - len(drifted)} matched, {len(drifted)} drifted.")
    if drifted:
        print(
            "\nThe reference arm and the search's seed are no longer the same text.\n"
            "Re-register with `just candidates`, and treat anything measured against\n"
            "the pinned versions as measured against a different baseline."
        )
        return 1
    return 0


def _shown(path: Path) -> str:
    """*path* relative to the repository when it is inside it, absolute otherwise."""
    return str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)


def _write_versions(versions: dict[str, int]) -> None:
    """Write *versions* into the pin file, leaving every other pin exactly as it is.

    A targeted edit rather than a `check_pins --write`, which recomputes the
    whole file: this script knows one slot and should touch one slot.
    """
    document = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    document["pins"][VERSIONS_PIN] = dict(sorted(versions.items()))
    PINS_PATH.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the pinned versions still hold the handwritten text",
    )
    parser.add_argument(
        "--commit-message",
        default="the handwritten baseline: reference arm and search seed (T125)",
        help="the registry commit message for a newly registered version",
    )
    args = parser.parse_args()
    return check() if args.check else register(args.commit_message)


if __name__ == "__main__":
    raise SystemExit(main())
