"""The repaired candidate, and what makes it a third arm rather than a rewrite (T136).

The one registered search selected a candidate whose single rewritten GR2L tool
description names a tool no toolset resolves. The repair is committed beside the
text it repairs; what is tested here is that it stays a *repair* — that the
defects are gone, that the routing content that moved the score is not, and that
none of it can be reported as the optimized arm.

Several of these read the committed files rather than a fixture, deliberately: a
test asserting a repair against a fixture would pass while the committed one said
something else, which is the only failure that matters here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.candidates import (
    CANDIDATE_COMPONENTS,
    baseline_texts,
    register_candidates,
)
from harness.measure import (
    BASELINE_ARM,
    OPTIMIZED_ARM,
    REPAIRED_ARM,
    arm_versions,
)
from harness.preregistration import MEASUREMENT, REPAIR_MEASUREMENT, load
from harness.repair import (
    DIFF_FILE,
    REPAIRED_COMPONENT,
    RepairDriftError,
    repair_diff,
    repaired_text,
    repaired_texts,
    selected_text,
    verify,
)
from water_assistant_agent.assistant.toolset import GREEN_ROOF_TOOL, TOOL_NAMES

PROMPT_PREFIXED = "agent_tool_predict_green_roof_water_balance_tool"
"""What the search wrote: the registered prompt name, not the callable."""


# ── The three defects, each checkable without a model ────────────────────────


def test_the_selected_text_names_a_tool_no_toolset_resolves() -> None:
    """The defect being repaired, asserted on the text as the search registered it.

    Pinned here so the repair below is measured against a stated starting point
    rather than against a memory of one.
    """
    selected = selected_text()

    assert selected.count(PROMPT_PREFIXED) == 5
    # The name the search wrote is the registered prompt's, and no toolset
    # resolves it: `component_name` reaches the reflection model prefixed.
    assert PROMPT_PREFIXED not in TOOL_NAMES
    # The real callable never appears on its own: every occurrence of it is
    # inside the prefixed name.
    assert selected.count(GREEN_ROOF_TOOL) == selected.count(PROMPT_PREFIXED)


def test_the_repair_names_the_callable_and_never_the_prompt() -> None:
    """A tool text naming a callable outside ``TOOL_NAMES`` is wrong on its face."""
    repaired = repaired_text()

    assert PROMPT_PREFIXED not in repaired
    assert GREEN_ROOF_TOOL in repaired


def test_every_tool_the_repair_names_is_a_real_one() -> None:
    """The check T137 generalizes, run here on the one text this task repairs."""
    repaired = repaired_text()

    named = {name for name in TOOL_NAMES if name in repaired}
    assert named  # it does name tools; the point is which
    for name in named:
        assert f"agent_tool_{name}" not in repaired


def test_the_repair_drops_the_root_instructions_contract() -> None:
    """Two copies of the answer contract, where a disagreement would be invisible.

    The selected text's copy already disagrees — it adds a ``final_text`` key and
    an ``"error"`` status the contract does not have — which is why the repair
    deletes the section rather than reconciling it.
    """
    selected, repaired = selected_text(), repaired_text()

    assert "## Response Format" in selected
    assert "final_text" in selected
    assert "## Response Format" not in repaired
    assert "final_text" not in repaired


def test_the_repair_keeps_the_routing_content_that_moved_the_score() -> None:
    """Surgical means measured, not tidied.

    That rewrite is the whole difference between candidate 1 and the winner and it
    moved trajectory 0.76 → 0.87, so a repair that deleted the routing content
    would discard the gain the third arm exists to measure. All four
    discriminations survive; what changes is that they say when *this* tool
    applies rather than which tool to pick.
    """
    repaired = repaired_text()

    for discrimination in (
        "How much rain fell last week?",
        "Will it rain tomorrow?",
        "What is the soil moisture?",
        "What if we got 50mm rain?",
    ):
        assert discrimination in repaired
    # The two siblings are still named — a routing rule that cannot say where the
    # query should go instead is not a routing rule.
    assert "text_to_sql_agent" in repaired
    assert "get_weather_forecast_tool" in repaired


def test_the_repair_is_a_repair_and_not_a_rewrite() -> None:
    """Most of the selected text survives; the diff is the record of what did not."""
    selected, repaired = selected_text(), repaired_text()

    kept = [
        line
        for line in repaired.splitlines()
        if line.strip() and line in selected.splitlines()
    ]
    assert len(kept) > len(repaired.splitlines()) / 2


# ── The committed diff is generated, not typed ───────────────────────────────


def test_the_committed_diff_is_the_diff_between_the_two_committed_texts() -> None:
    """A hand-edited diff records something other than what was changed."""
    assert DIFF_FILE.read_text(encoding="utf-8") == repair_diff()


# ── The repair is held to the candidate it repairs ───────────────────────────


def test_a_moved_registered_text_is_drift_and_not_a_new_starting_point(
    registry: str,
) -> None:
    """The repair's starting point is the selected candidate or it is nothing."""
    versions = register_candidates(baseline_texts())

    with pytest.raises(RepairDriftError, match="not the text registered"):
        verify(versions)


def test_the_repaired_candidate_differs_from_the_arm_it_repairs_in_one_string(
    registry: str,
) -> None:
    """Six components read back at the optimized arm's own versions, byte for byte."""
    texts = dict(baseline_texts())
    texts[REPAIRED_COMPONENT] = selected_text()
    versions = register_candidates(texts)

    repaired = repaired_texts(versions)

    assert set(repaired) == set(CANDIDATE_COMPONENTS)
    assert repaired[REPAIRED_COMPONENT] == repaired_text()
    for component in CANDIDATE_COMPONENTS:
        if component != REPAIRED_COMPONENT:
            assert repaired[component] == texts[component]


# ── A third arm, and never the optimized one ─────────────────────────────────


def test_the_repaired_arm_is_not_in_the_registered_measurement() -> None:
    """§7 defines the optimized arm as whatever the search selected, not a repair."""
    assert load()[MEASUREMENT]["arms"] == [BASELINE_ARM, OPTIMIZED_ARM]
    assert REPAIRED_ARM not in load()[MEASUREMENT]["arms"]


def test_the_repaired_arm_has_a_registration_of_its_own() -> None:
    """A commitment nothing checks is a note; a third arm nobody registered is worse."""
    registered = load()[REPAIR_MEASUREMENT]

    assert registered["arms"] == [BASELINE_ARM, OPTIMIZED_ARM, REPAIRED_ARM]
    # Everything the two registrations share, they share: the repaired arm is a
    # third condition of the same measurement, not a different measurement.
    for key in ("splits", "repeats", "temperature", "seed", "llm_cache",
                "response_cache_mode", "pairing"):
        assert registered[key] == load()[MEASUREMENT][key]


def test_the_third_arm_is_asked_for_by_name_and_never_implied(
    tmp_path: Path,
) -> None:
    """A run measuring the two registered arms is the registered protocol."""
    optimized = tmp_path / "optimized.json"
    optimized.write_text(
        json.dumps({"versions": {c: 5 for c in CANDIDATE_COMPONENTS}}),
        encoding="utf-8",
    )
    repaired = tmp_path / "repaired.json"
    repaired.write_text(
        json.dumps({"versions": {c: 6 for c in CANDIDATE_COMPONENTS}}),
        encoding="utf-8",
    )

    without = arm_versions(optimized_path=optimized, repaired_path=repaired)
    with_it = arm_versions(
        optimized_path=optimized, repaired_path=repaired, repaired=True
    )

    assert REPAIRED_ARM not in without
    assert with_it[REPAIRED_ARM] == {c: 6 for c in CANDIDATE_COMPONENTS}


def test_an_unregistered_repair_is_refused_rather_than_defaulted(
    tmp_path: Path,
) -> None:
    """No fallback to ``@latest``: that is a candidate nobody repaired and nobody selected."""
    optimized = tmp_path / "optimized.json"
    optimized.write_text(
        json.dumps({"versions": {c: 5 for c in CANDIDATE_COMPONENTS}}),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="repair-register"):
        arm_versions(
            optimized_path=optimized,
            repaired_path=tmp_path / "missing.json",
            repaired=True,
        )
