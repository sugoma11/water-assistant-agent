"""The projection, and the drift between it and the committed cards (T068).

Two halves, and they fail differently.

**The projection tests are green today** and assert it against the constants
themselves — that ``values_for`` reads ``rules_constants.py`` and ``roofs.py``
rather than carrying its own copy of a number, that both units of a threshold are
the same threshold, and that the scope blocks are derived from the tables instead
of hand-listed.

**The drift tests are red until T080 authors the cards.** They are parametrized
over ``RENDERED_TOPICS`` — the seven cards the specification says are rendered —
so an unauthored card is a failure rather than a test that never ran.

The drift assertion is equality in *both* directions, which is what makes the
card's silences enforceable: a card may not omit a value the constants have, and
may not carry one they do not. ``irrigation_dose`` is the case that needs it —
the per-roof depth is owed by the site and stands empty, and a card that invented
a plausible one would otherwise pass.
"""

import duckdb
import pytest

from water_assistant_agent.assistant.knowledge.rendered import (
    EVAL_POLICY_KEY,
    MM_DECIMALS,
    RenderedBlocks,
    values_for,
)
from water_assistant_agent.assistant.knowledge.store import (
    CARD_TOPICS,
    RENDERED_TOPICS,
    Card,
    CardStoreError,
    load_card,
)
from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    HEAT_THRESHOLD_C,
    HEATWAVE_MIN_CONSECUTIVE_DAYS,
    OUTFLOW_EPSILON_MM,
    REFILL_HORIZON_HOURS,
    RETENTION_TARGET,
    ROOF_RULES,
    rules_for,
)
from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    ROOFS,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.swc import theta_pct_to_mm

STATIC_TOPICS = tuple(topic for topic in CARD_TOPICS if topic not in RENDERED_TOPICS)

DB_PATH = "data/water.duckdb"
_SITE_DAY = "((timestamp) AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Berlin')::DATE"


# --- The projection reads the constants ---------------------------------------


def test_every_rendered_topic_has_a_projection() -> None:
    """The specification's list of rendered cards and this module's, held equal.

    A rendered card with no projection has no drift test, which is the failure
    mode the label exists to prevent.
    """
    for topic in RENDERED_TOPICS:
        values_for(topic)


@pytest.mark.parametrize("topic", STATIC_TOPICS)
def test_a_static_card_has_no_projection(topic: str) -> None:
    """T081 on the code side.

    ``data_freshness`` is here with the three cards that have no constants behind
    them at all. A projection for it would mean a database source in a module
    that has two, and a database read inside a store that promises none
    (``decisions.md`` § Retrieval).
    """
    with pytest.raises(KeyError, match="No projection for card"):
        values_for(topic)


def test_an_unknown_card_names_the_rendered_ones() -> None:
    with pytest.raises(KeyError, match="irrigation_rule"):
        values_for("not_a_card")


def test_the_rule_card_carries_the_four_conditions_it_turns_on() -> None:
    """Read from the constants, so a threshold moving moves the card with it."""
    assert values_for("irrigation_rule").values == {
        "heat_threshold_c": HEAT_THRESHOLD_C,
        "decision_horizon_hours": DECISION_HORIZON_HOURS,
        "refill_horizon_hours": REFILL_HORIZON_HOURS,
        "refill_outflow_epsilon_mm": OUTFLOW_EPSILON_MM,
    }


def test_the_rule_card_states_no_wind_condition() -> None:
    """T17a's mechanism: the absence has to be visible inside the card.

    The abstention is grounded in the rule's own list of conditions, not inferred
    from the topic vocabulary — which is what keeps it distinct from the
    unsignalled-abstention holdout (``decisions.md`` § Retrieval).
    """
    blocks = values_for("irrigation_rule")
    assert not [key for key in blocks.values if "wind" in key]


@pytest.mark.parametrize("roof", sorted(ROOF_RULES))
def test_both_units_of_a_threshold_are_the_same_threshold(roof: str) -> None:
    """The millimetre column is the %θ one converted, at the card's precision.

    Stating both units is the card's job; stating two *different* numbers would
    reproduce the deployed controller's own unit bug in the documentation meant
    to explain it (``irrigation_tool.md`` § Units).
    """
    rules = rules_for(roof)
    stated = values_for("irrigation_threshold").values[roof]
    height = rules.substrate_height_cm
    assert stated["wilting_pct"] == rules.wilting_pct
    assert stated["dry_pct"] == rules.dry_pct
    assert stated["wilting_mm"] == round(theta_pct_to_mm(stated["wilting_pct"], height), MM_DECIMALS)
    assert stated["dry_mm"] == round(theta_pct_to_mm(stated["dry_pct"], height), MM_DECIMALS)


def test_field_capacity_is_on_the_hydraulics_card_not_the_threshold_card() -> None:
    """The split that keeps either card from being named after one constant, and
    keeps a store property out of a list of triggers."""
    thresholds = values_for("irrigation_threshold").values
    hydraulics = values_for("substrate_hydraulics").values
    assert all("capacity_pct" not in roof for roof in thresholds.values())
    assert all("capacity_pct" in roof for roof in hydraulics.values())


@pytest.mark.parametrize("roof", sorted(ROOF_RULES))
def test_the_hydraulics_card_carries_the_depth_the_conversion_uses(roof: str) -> None:
    """One depth, from ``roofs.py``, and the millimetres derived through it."""
    rules = rules_for(roof)
    stated = values_for("substrate_hydraulics").values[roof]
    assert stated["substrate_height_cm"] == ROOFS[roof].substrate_height_cm
    assert stated["capacity_mm"] == round(
        theta_pct_to_mm(rules.capacity_pct, stated["substrate_height_cm"]), MM_DECIMALS
    )
    assert stated["residual_mm"] == round(
        theta_pct_to_mm(rules.residual_pct, stated["substrate_height_cm"]), MM_DECIMALS
    )


def test_the_hydraulics_card_states_one_models_storage_not_two() -> None:
    """GR2L's per-roof storage is in ``roofs.py`` and stays out of this card.

    The flat field capacity is the deployed controller's and disagrees with what
    GR2L measures, on purpose (``decisions.md`` § No fitted correction between
    the instrument and the oracle). Both in one block would let an answer quote
    one as the other.
    """
    for stated in values_for("substrate_hydraulics").values.values():
        assert not {"ssubmin", "ssubmax", "Ssubmin", "Ssubmax"} & set(stated)


def test_the_dose_card_states_a_valve_time_and_no_invented_depth() -> None:
    """Owed by the site, so absent rather than null (``rules_constants.py``).

    A null invites a reader to treat it as zero, and an invented depth would be
    indistinguishable from the site's own once quoted back in an answer.
    """
    for roof, stated in values_for("irrigation_dose").values.items():
        assert stated["valve_minutes"] == rules_for(roof).valve_minutes
        assert ("dose_mm" in stated) == (rules_for(roof).dose_mm is not None)


def test_the_heatwave_card_marks_only_the_duration_as_eval_policy() -> None:
    """The threshold is the deployed controller's; the duration is this thesis's.

    ``rules_constants.py`` requires the distinction to survive into any card
    rendered from it — otherwise an answer cites the testbed's convention as the
    site's manual.
    """
    values = values_for("heatwave_definition").values
    assert values["heat_threshold_c"] == HEAT_THRESHOLD_C
    assert values["min_consecutive_days"] == HEATWAVE_MIN_CONSECUTIVE_DAYS
    assert values[EVAL_POLICY_KEY] == ["min_consecutive_days"]


def test_the_retention_card_marks_its_target_as_eval_policy() -> None:
    values = values_for("retention_target").values
    assert values["retention_fraction"] == RETENTION_TARGET
    assert values["lysimeter_area_m2"] == LYSIMETER_AREA_M2
    assert values[EVAL_POLICY_KEY] == ["retention_fraction"]


def test_only_the_two_authored_constants_are_marked_eval_policy() -> None:
    """The marker is per key and only where it is true — the site's numbers stay
    unmarked, so the mark means something when it appears."""
    marked = {
        topic: values_for(topic).values.get(EVAL_POLICY_KEY, [])
        for topic in ("irrigation_rule", "irrigation_threshold", "substrate_hydraulics",
                      "irrigation_dose", "heatwave_definition", "retention_target")
    }
    assert {topic: keys for topic, keys in marked.items() if keys} == {
        "heatwave_definition": ["min_consecutive_days"],
        "retention_target": ["retention_fraction"],
    }


# --- The scope blocks are derived from the tables ------------------------------


@pytest.mark.parametrize(
    "topic",
    ["irrigation_rule", "irrigation_threshold", "substrate_hydraulics", "irrigation_dose"],
)
def test_the_irrigation_cards_apply_to_the_roofs_the_rule_has(topic: str) -> None:
    assert values_for(topic).applies_to == list(ROOF_RULES)


@pytest.mark.parametrize(
    "topic",
    ["irrigation_rule", "irrigation_threshold", "substrate_hydraulics", "irrigation_dose"],
)
def test_the_irrigation_cards_exclude_the_wetland_with_a_reason(topic: str) -> None:
    """T17b's mechanism, on every card that could be asked the wetland question.

    The exclusion has to be *stated*, not merely implied by the wetland's absence
    from ``applies_to``: the card returns whole, and the agent reads a
    confidently worded extensive-roof rule beside it.
    """
    excluded = values_for(topic).not_applicable
    assert set(excluded) == {"gravel", "wetland"}
    assert "fleece" in excluded["wetland"]
    assert all(reason.strip() for reason in excluded.values())


def test_the_excluded_roofs_are_the_ones_with_no_rules() -> None:
    """Derived, so a roof gaining rules leaves every exclusion block at once."""
    excluded = set(values_for("irrigation_rule").not_applicable)
    assert excluded == set(ROOFS) - set(ROOF_RULES)


def test_retention_applies_to_the_roofs_with_a_lysimeter() -> None:
    """A different exclusion from a different fact: instrumentation, not substrate.

    The semi-intensive roof has soil-moisture rules and no lysimeter, so it is
    inside every irrigation card and outside this one — which only comes out
    right because each card derives its own scope
    (``findings.md`` § Not every roof is instrumented).
    """
    blocks = values_for("retention_target")
    assert blocks.applies_to == list(roofs_with_column("outflow"))
    assert set(blocks.not_applicable) == {"semi_intensive"}


# --- The reference ranges, against the record they are cut from (T069) ---------


@pytest.mark.parametrize("roof", sorted(ROOF_RULES))
def test_a_band_edge_is_a_constant_and_never_a_new_number(roof: str) -> None:
    """Every one of the four edges is imported, from one of the two sources.

    The floor and ceiling are the roof's ``swc`` plausibility bounds and the two
    interior cuts are the rule's dry threshold and field capacity. A card whose
    bands were rounded, widened or fitted would be a fifth statement of the
    site's policy with nothing holding it.
    """
    rules = rules_for(roof)
    bounds = ROOFS[roof].bounds["swc"]
    stated = values_for("roof_reference_ranges").values[roof]
    assert stated["low_pct"] == {"from": bounds.low, "to": rules.dry_pct}
    assert stated["normal_pct"] == {"from": rules.dry_pct, "to": rules.capacity_pct}
    assert stated["high_pct"] == {"from": rules.capacity_pct, "to": bounds.high}


@pytest.mark.parametrize("roof", sorted(ROOF_RULES))
def test_the_three_bands_are_contiguous_and_ordered(roof: str) -> None:
    """They tile the plausible envelope with no gap and no overlap.

    A gap would be a reading the card has no word for; an overlap would give one
    reading two answers.
    """
    stated = values_for("roof_reference_ranges").values[roof]
    low, normal, high = stated["low_pct"], stated["normal_pct"], stated["high_pct"]
    assert low["from"] < low["to"] == normal["from"] < normal["to"] == high["from"]
    assert high["from"] < high["to"]


def test_a_roof_needs_both_a_reading_and_a_rule_to_have_a_band() -> None:
    """Gravel and the wetland have an ``swc`` column and no bands.

    They are measurable and askable-about, so their exclusion is a real one to
    state rather than an absence of data — the same fact the irrigation cards
    exclude them for.
    """
    blocks = values_for("roof_reference_ranges")
    measurable = set(roofs_with_column("swc"))
    assert {"gravel", "wetland"} <= measurable
    assert set(blocks.applies_to) == measurable & set(ROOF_RULES)
    assert set(blocks.not_applicable) == {"gravel", "wetland"}


@pytest.mark.parametrize("roof", sorted(ROOF_RULES))
def test_every_band_carries_days_of_the_actual_record(roof: str) -> None:
    """The bands are cut from the ``swc`` record, so none of them may be empty on it.

    This is what separates a derived range from an arbitrary one: a ``normal``
    band no reading ever falls in, or a ``high`` band nothing reaches, would be a
    card confidently describing a roof it does not describe. Site-day means over
    the whole record, the level a reading is read at.

    It asserts non-degeneracy, not agreement: the edges are the site's policy and
    the record is free to sit lopsidedly inside them, which on the semi-intensive
    roof it emphatically does (``findings.md``).
    """
    column = ROOFS[roof].columns["swc"]
    bands = values_for("roof_reference_ranges").values[roof]
    with duckdb.connect(DB_PATH, read_only=True) as con:
        means = [
            value
            for (value,) in con.execute(
                f'SELECT avg("{column}") FROM swc WHERE "{column}" IS NOT NULL '  # noqa: S608 - column from ROOFS
                f"GROUP BY {_SITE_DAY}"
            ).fetchall()
            if value is not None
        ]
    assert means, f"no {column} readings in the record"
    for band, edges in bands.items():
        days = [value for value in means if edges["from"] <= value < edges["to"]]
        assert days, f"{roof}'s {band} band {edges} carries no day of the record"


# --- Shape ---------------------------------------------------------------------


def test_empty_blocks_are_left_out_of_the_yaml() -> None:
    """A card without exclusions carries no empty ``not_applicable:`` key."""
    printed = values_for("heatwave_definition").as_yaml()
    assert "not_applicable" not in printed
    assert "applies_to" not in printed


def test_the_yaml_keeps_the_projection_order() -> None:
    """``just cards-check`` prints a block a person pastes, so its order is the
    projection's rather than alphabetical."""
    printed = RenderedBlocks(values={"b": 1, "a": 2}).as_yaml()
    assert printed.index("b:") < printed.index("a:")


# --- Drift, against the committed store: red until T080 ------------------------


def _committed_card(topic: str) -> Card:
    try:
        return load_card(topic)
    except CardStoreError as exc:
        pytest.fail(f"{exc} T080 authors the eleven cards; this test is red until it lands.")


@pytest.mark.parametrize("topic", RENDERED_TOPICS)
def test_a_rendered_card_equals_the_projection(topic: str) -> None:
    """The drift test: the committed card's three blocks against the constants.

    Equality in both directions. A card may not omit a value the constants carry,
    and may not carry one they do not — the second half is what holds
    ``irrigation_dose``'s missing depth empty.

    ``just cards-check`` prints the correct block on failure, so the fix is a
    paste rather than a transcription.
    """
    card = _committed_card(topic)
    expected = values_for(topic)
    assert card.values == expected.values
    assert card.applies_to == expected.applies_to
    assert card.not_applicable == expected.not_applicable


@pytest.mark.parametrize("topic", RENDERED_TOPICS)
def test_a_rendered_card_declares_its_provenance(topic: str) -> None:
    """A card holding rendered values under a ``static`` label would carry the
    drift test's numbers while claiming to be pinned somewhere else."""
    assert _committed_card(topic).provenance == "rendered"


@pytest.mark.parametrize("topic", STATIC_TOPICS)
def test_a_static_card_declares_its_provenance(topic: str) -> None:
    assert _committed_card(topic).provenance == "static"
