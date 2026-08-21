"""Check every rendered card against the constants — the ``just cards-check`` check.

A reference card restates numbers that live in ``rules_constants.py`` and
``roofs.py``, which makes it a second copy. The drift test in
``tests/assistant/test_knowledge_rendered.py`` is what keeps the two equal; this
script is the same comparison run for a person, and its whole reason to exist is
the failure output: it prints the ``values:`` / ``applies_to:`` /
``not_applicable:`` block the card *should* carry, ready to paste, instead of an
assertion diff to transcribe by hand.

Three states per card. **Current** — the committed blocks equal the projection.
**Drifted** — they do not, which is the failure this script exists for; it exits
non-zero and prints the correct block. **Missing** — the card has not been
authored yet (T080), reported as missing and counted as a failure, because a
topic in the vocabulary with no card behind it is a route the model can take into
an error.

Static cards are not checked: their values are pinned elsewhere — ``roof_directory``,
``sensor_reference`` and ``et0_method`` have no constants behind them at all, and
``data_freshness``'s record dates are held by the database hash and the
station-derivation pin, verified by ``just pins`` (``decisions.md`` § Retrieval).

Usage::

    just cards-check
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from water_assistant_agent.assistant.knowledge.rendered import values_for  # noqa: E402
from water_assistant_agent.assistant.knowledge.store import (  # noqa: E402
    RENDERED_TOPICS,
    CardStoreError,
    load_card,
)


def _indent(block: str) -> str:
    return "\n".join(f"    {line}" if line else "" for line in block.rstrip().splitlines())


def check() -> int:
    """Compare each rendered card with its projection; return the exit status."""
    drifted: list[str] = []
    missing: list[str] = []
    unprojected: list[str] = []

    for topic in RENDERED_TOPICS:
        try:
            expected = values_for(topic)
        except KeyError:
            # A card the specification calls rendered with nothing rendering it:
            # reported rather than raised, so one unfinished projection does not
            # hide the state of the other six.
            unprojected.append(topic)
            print(f"  NO PROJECTION  {topic}")
            continue
        try:
            card = load_card(topic)
        except CardStoreError:
            missing.append(topic)
            continue
        if (
            card.values == expected.values
            and card.applies_to == expected.applies_to
            and card.not_applicable == expected.not_applicable
        ):
            print(f"  current   {topic}")
            continue
        drifted.append(topic)
        print(f"  DRIFTED   {topic}")

    for topic in missing:
        print(f"  MISSING   {topic}")

    for topic in drifted:
        print(f"\n{topic}.yaml should carry:\n")
        print(_indent(values_for(topic).as_yaml()))

    if unprojected:
        print(
            f"\n{len(unprojected)} rendered card(s) have no projection, so nothing holds "
            "their values to the constants."
        )
    if missing:
        print(
            f"\n{len(missing)} card(s) not authored yet — T080 writes the eleven cards; "
            "the prose is hand-written and only the blocks above are checked."
        )
    if drifted:
        print(
            f"\n{len(drifted)} card(s) drifted from the constants. Paste the block(s) above "
            "over the card's generated half; the `text:` prose is hand-written and stays."
        )
    if not missing and not drifted and not unprojected:
        print(f"\nAll {len(RENDERED_TOPICS)} rendered cards match the constants.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(check())
