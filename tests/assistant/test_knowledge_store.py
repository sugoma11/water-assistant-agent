"""The card schema, the reader, and the two rules that hold over every card (T082).

Three populations here, and they behave differently on purpose.

**The schema and reader tests are green today.** They build cards in a temporary
directory, so they test the shape without needing the store to be populated.

**The store-wide tests are red until T080 authors the cards**, and that is the
state this packet ships in: ``enum == card keys`` and the no-numerals rule are
driven by :data:`CARD_TOPICS` — the specification — rather than by whatever
happens to be on disk. Parametrizing over the *files* would have made an empty
store collect zero tests and report green, which is the one outcome worse than
red: a vacuous pass on the rule that is supposed to catch a missing card. Each
failure names its own missing card.

The drift tests live in ``test_knowledge_rendered.py`` beside the projection they
assert against.
"""

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from water_assistant_agent.assistant.knowledge import store
from water_assistant_agent.assistant.knowledge.store import (
    CARD_TOPICS,
    RENDERED_TOPICS,
    Card,
    CardStoreError,
    load_card,
    load_cards,
)

VALID_CARD = {
    "id": "irrigation_rule",
    "title": "When the irrigation rule waters a roof",
    "provenance": "rendered",
    "text": "The rule waters when the store is low, the day is hot and no refill is due.",
    "values": {"heat_threshold_c": 24.0},
    "applies_to": ["irrigated_extensive"],
    "not_applicable": {"gravel": "no substrate store to read"},
}

NUMERAL = re.compile(r"\d")
"""Any digit. The rule is about numerals, not about numbers being *mentioned* —
"three consecutive days" is prose, "3 days" is a second copy of a constant with
nothing holding it to :mod:`rules_constants`."""


def _write_store(tmp_path: Path, *cards: dict) -> Path:
    """Write *cards* into a temporary store directory and return it."""
    for card in cards:
        (tmp_path / f"{card['id']}.yaml").write_text(yaml.safe_dump(card), encoding="utf-8")
    return tmp_path


@pytest.fixture
def temp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the loader at a temporary directory, cache cleared on both sides.

    :func:`load_cards` is cached because the real store is immutable at runtime;
    a test that swaps the directory has to clear it, and has to clear it again
    afterwards or it leaks a temporary store into the rest of the session.
    """

    def _install(*cards: dict) -> Path:
        directory = _write_store(tmp_path, *cards)
        monkeypatch.setattr(store, "CARDS_DIR", directory)
        load_cards.cache_clear()
        return directory

    yield _install
    load_cards.cache_clear()


# --- The schema ---------------------------------------------------------------


def test_a_full_card_round_trips() -> None:
    """Every field survives validation with its declared type."""
    card = Card.model_validate(VALID_CARD)
    assert card.id == "irrigation_rule"
    assert card.provenance == "rendered"
    assert card.values == {"heat_threshold_c": 24.0}
    assert card.applies_to == ["irrigated_extensive"]
    assert card.not_applicable == {"gravel": "no substrate store to read"}
    assert card.model_dump() == VALID_CARD


def test_the_three_generated_blocks_default_to_empty() -> None:
    """A card that carries only prose is valid — the static cards are that shape."""
    card = Card.model_validate(
        {
            "id": "et0_method",
            "title": "How reference evapotranspiration is computed",
            "provenance": "static",
            "text": "The routine follows the site's own convention rather than the textbook.",
        }
    )
    assert card.values == {}
    assert card.applies_to == []
    assert card.not_applicable == {}


def test_an_unknown_key_is_rejected() -> None:
    """The misspelling case, which is why ``extra`` is forbidden.

    ``not_applicible`` would otherwise load as a card with no exclusions at all,
    and T17b's probe would be silently answerable — the failure arriving as
    silence instead of as an error.
    """
    with pytest.raises(ValidationError, match="not_applicible"):
        Card.model_validate({**VALID_CARD, "not_applicible": {"wetland": "excluded"}})


@pytest.mark.parametrize("missing", ["id", "title", "provenance", "text"])
def test_the_four_required_fields_are_required(missing: str) -> None:
    payload = {key: value for key, value in VALID_CARD.items() if key != missing}
    with pytest.raises(ValidationError, match=missing):
        Card.model_validate(payload)


def test_provenance_admits_exactly_two_values() -> None:
    """A third label would be a card claiming a guarantee no test enforces."""
    with pytest.raises(ValidationError):
        Card.model_validate({**VALID_CARD, "provenance": "derived"})


def test_a_card_is_frozen() -> None:
    """The store is read-only at runtime; a caller mutating a shared card would
    change what every later lookup returns in that process."""
    card = Card.model_validate(VALID_CARD)
    with pytest.raises(ValidationError):
        card.title = "something else"


def test_not_applicable_reasons_must_be_strings() -> None:
    """A bare list of excluded roofs states the exclusion without stating why,
    and the why is what T17b asks the agent to read."""
    with pytest.raises(ValidationError):
        Card.model_validate({**VALID_CARD, "not_applicable": {"wetland": None}})


# --- The reader ---------------------------------------------------------------


def test_the_loader_keys_cards_by_id(temp_store) -> None:
    temp_store(VALID_CARD, {**VALID_CARD, "id": "et0_method", "provenance": "static"})
    cards = load_cards()
    assert set(cards) == {"irrigation_rule", "et0_method"}
    assert cards["irrigation_rule"].title == VALID_CARD["title"]


def test_a_card_whose_id_disagrees_with_its_filename_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the card is unreachable under the topic it names."""
    (tmp_path / "retention_target.yaml").write_text(
        yaml.safe_dump({**VALID_CARD, "id": "irrigation_rule"}), encoding="utf-8"
    )
    monkeypatch.setattr(store, "CARDS_DIR", tmp_path)
    load_cards.cache_clear()
    try:
        with pytest.raises(CardStoreError, match="filename stem"):
            load_cards()
    finally:
        load_cards.cache_clear()


def test_malformed_yaml_names_the_file(temp_store, tmp_path: Path) -> None:
    temp_store()
    (tmp_path / "irrigation_rule.yaml").write_text("id: [unclosed\n", encoding="utf-8")
    load_cards.cache_clear()
    with pytest.raises(CardStoreError, match="irrigation_rule.yaml is not valid YAML"):
        load_cards()


def test_a_card_that_is_not_a_mapping_is_an_error(temp_store, tmp_path: Path) -> None:
    temp_store()
    (tmp_path / "irrigation_rule.yaml").write_text("- a list\n- of things\n", encoding="utf-8")
    load_cards.cache_clear()
    with pytest.raises(CardStoreError, match="must be a YAML mapping"):
        load_cards()


def test_load_card_echoes_the_topics_that_do_have_a_card(temp_store) -> None:
    """The same courtesy the tool extends on an unknown topic: say what was valid."""
    temp_store(VALID_CARD)
    with pytest.raises(CardStoreError, match="Available: irrigation_rule"):
        load_card("heatwave_definition")


def test_a_missing_store_directory_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "CARDS_DIR", tmp_path / "nowhere")
    load_cards.cache_clear()
    try:
        with pytest.raises(CardStoreError, match="card store directory is missing"):
            load_cards()
    finally:
        load_cards.cache_clear()


def test_the_packaged_store_directory_exists() -> None:
    """It ships empty until T080, but it ships."""
    assert store.CARDS_DIR.is_dir()


# --- The vocabulary -----------------------------------------------------------


def test_the_topic_list_has_no_duplicates() -> None:
    assert len(CARD_TOPICS) == len(set(CARD_TOPICS)) == 11


def test_rendered_topics_are_topics() -> None:
    assert set(RENDERED_TOPICS) <= set(CARD_TOPICS)


def test_data_freshness_is_not_rendered() -> None:
    """T081, pinned as a test rather than left as prose.

    Its values are record dates, so rendering it would give ``values_for()`` a
    database source and put a database read inside a store specified as a pure
    function of packaged files (``decisions.md`` § Retrieval). The decision is
    one line of a document and one absence from a tuple; this is the line that
    notices if the absence is undone.
    """
    assert "data_freshness" not in RENDERED_TOPICS
    assert set(CARD_TOPICS) - set(RENDERED_TOPICS) == {
        "data_freshness",
        "roof_directory",
        "sensor_reference",
        "et0_method",
    }


# --- Over the committed store: red until T080 ---------------------------------


def _committed_card(topic: str) -> Card:
    """The committed card for *topic*, failing with what is missing and who owns it."""
    try:
        return load_card(topic)
    except CardStoreError as exc:
        pytest.fail(f"{exc} T080 authors the eleven cards; this test is red until it lands.")


def test_the_topic_vocabulary_equals_the_card_keys() -> None:
    """``enum == card keys``, in both directions.

    A topic with no card is a route the model can take into a ``CardStoreError``;
    a card with no topic is a file nothing can ever reach.
    """
    committed = set(load_cards())
    assert set(CARD_TOPICS) - committed == set(), "topics with no card file"
    assert committed - set(CARD_TOPICS) == set(), "card files no topic reaches"


@pytest.mark.parametrize("topic", CARD_TOPICS)
def test_no_card_states_a_numeral_in_its_prose(topic: str) -> None:
    """§3.2's authoring rule, over every topic the vocabulary promises.

    A threshold written into ``text:`` is a second copy of a constant that no
    drift test holds — the exact failure ``rules_constants.py`` exists to end.
    """
    card = _committed_card(topic)
    found = NUMERAL.findall(card.text)
    assert not found, f"{topic}'s text carries numerals {found}; numbers belong in values:"
