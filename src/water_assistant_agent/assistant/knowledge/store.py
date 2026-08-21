"""The card schema and the reader over the packaged YAML store.

Reference lookup is an exact card read over a closed set (``decisions.md``
§ Retrieval): the agent names a topic, this module reads that card off disk, and
nothing ranks, chunks or indexes anything. What is left to get right is therefore
the *shape* — which is what a card promises a reader — and this module is where
that shape is enforced.

**One file, two authorships.** A card carries hand-written prose in ``text:`` and
machine-checkable numbers in ``values:`` / ``applies_to:`` / ``not_applicable:``.
The generated half is not written by a program; it is written by a person and
held equal to :func:`~.rendered.values_for` by a test (T068). That is deliberate:
a generator would own the file and the prose would have to move out of it, and
the prose is the half a reader of the card actually reads.

**Pure, and the purity is load-bearing.** No network, no database, no cache
entry, no clock, no ADK — a card lookup is a function of files
``agent_architecture.md`` §5 hashes, which is what lets a lookup case replay with
zero cache entries. ``data_freshness`` is ``static`` for exactly this reason: its
values are record dates, and rendering them would have put a database read here
(``decisions.md`` § Retrieval).
"""

import functools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

CARDS_DIR = Path(__file__).parent / "cards"
"""The packaged store, one YAML file per card, baked into the image.

Editing a card requires a rollout, which is what keeps it inside the pinned
surface (``agent_architecture.md`` §3.2).
"""

CARD_SUFFIX = ".yaml"

Provenance = Literal["rendered", "static"]
"""Where a card's ``values:`` block gets its authority.

``rendered`` means it equals what ``rules_constants.py`` and ``roofs.py``
produce, asserted by the drift test — those two modules and nothing else
(``decisions.md`` § Retrieval). ``static`` means it is pinned somewhere other
than there: ``roof_directory``, ``sensor_reference`` and ``et0_method`` have no
constants behind them at all, and ``data_freshness``'s record dates are held by
the database hash and the station-derivation pin, verified in the pin check.
"""

CARD_TOPICS: tuple[str, ...] = (
    "irrigation_rule",
    "irrigation_threshold",
    "substrate_hydraulics",
    "irrigation_dose",
    "heatwave_definition",
    "retention_target",
    "roof_reference_ranges",
    "data_freshness",
    "roof_directory",
    "sensor_reference",
    "et0_method",
)
"""The eleven topics, in ``agent_architecture.md`` §3.2's order.

**This is the twin, not the original.** The vocabulary the model sees lives in
``lookup_reference``'s ``topic: Literal[...]`` annotation, because the framework
renders a literal type into the function declaration while a docstring is
candidate-owned — a candidate that rewrote the topic list away would disable the
reference route while still looking optimizable (``decisions.md`` § Retrieval).
So this tuple exists to be *compared* against two things a test holds it to: the
signature's literal (T083) and the card files on disk. It is not a place to add
a topic; adding one here without adding the card and the literal is what the
``enum == card keys`` test exists to catch.
"""

RENDERED_TOPICS: tuple[str, ...] = (
    "irrigation_rule",
    "irrigation_threshold",
    "substrate_hydraulics",
    "irrigation_dose",
    "heatwave_definition",
    "retention_target",
    "roof_reference_ranges",
)
"""The seven cards whose generated blocks :func:`~.rendered.values_for` produces.

The other four are ``static`` and carry no drift test. Kept here rather than
inferred from the committed files' ``provenance:`` fields on purpose: inferring
it would mean a card that silently lost its ``rendered`` label also silently lost
the test that holds its numbers to the constants.
"""


class Card(BaseModel):
    """One reference card: what it is called, what it says, and what it excludes.

    ``extra="forbid"`` and ``frozen=True`` together are the schema half of
    ``agent_architecture.md`` §3.2. Forbidding extras matters more than it looks:
    a card is hand-edited YAML, and a misspelled ``not_applicable`` key would
    otherwise load as a card with *no* exclusions — which is precisely the T17b
    failure the block exists to prevent, arriving as silence rather than as an
    error.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    """The topic this card answers to — a member of :data:`CARD_TOPICS`.

    Also its filename stem. The loader checks the two agree, so a card cannot be
    reached under one name while claiming another.
    """

    title: str
    """A human-readable subject line. Cards name subjects, never single constants.

    That rule is a validity condition, not a style preference: a card named after
    a constant makes its absence inferable from the topic vocabulary alone, and
    the agent can then abstain without opening anything — collapsing the
    grounded-absence template into the unsignalled-abstention holdout
    (``decisions.md`` § Retrieval).
    """

    provenance: Provenance

    text: str
    """The hand-authored prose, carrying **no numerals**.

    Numbers live in :attr:`values` where a test holds them to the constants; a
    numeral in the prose is a second copy of a threshold with nothing checking
    it. Enforced as a test rather than here, because the failure a reader needs
    is "this card states a number the prose may not state", naming the card —
    not a validation error at load time on a file that is otherwise fine.
    """

    values: dict[str, Any] = Field(default_factory=dict)
    """The numbers, keyed by name; equal to ``values_for(id)`` on a rendered card."""

    applies_to: list[str] = Field(default_factory=list)
    """Canonical roof names this card's values hold for, or empty where roof-independent."""

    not_applicable: dict[str, str] = Field(default_factory=dict)
    """Roof name → why this card does not cover it, returned **with** the card.

    A mapping rather than a list because the reason is the whole point. Asking
    for the wetland's soil-moisture threshold returns the ``irrigation_threshold``
    card with the extensive-roof rule and the wetland's exclusion side by side,
    and the agent has to read the exclusion next to a confidently worded rule
    that does not apply — the catalog's strongest hallucination probe (T17b).
    A tool that filtered this block by ``roof`` would delete the probe, which is
    why ``lookup_reference`` never suppresses it.
    """


class CardStoreError(Exception):
    """A card file is missing, malformed, or not the card it claims to be.

    Not a tool error class: ``lookup_reference`` has no ``upstream`` outcome
    (``agent_architecture.md`` §3.2), because a store baked into the image either
    loaded at import or the image is broken. This surfaces in tests and at
    startup, never in a trajectory.
    """


def _read_card(path: Path) -> Card:
    """Parse and validate one card file, raising with the path on any failure."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CardStoreError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CardStoreError(
            f"{path.name} must be a YAML mapping with the card's fields at the top level, "
            f"got {type(raw).__name__}."
        )
    card = Card.model_validate(raw)
    if card.id != path.stem:
        raise CardStoreError(
            f"{path.name} declares id {card.id!r}. A card's id is its filename stem, so "
            f"that card is unreachable under the topic it names."
        )
    return card


@functools.cache
def load_cards() -> dict[str, Card]:
    """Every card in the packaged store, keyed by id.

    Cached because the store is immutable at runtime — it is baked into the image
    and editing it requires a rollout — so the read happens once per process and
    a lookup call touches no filesystem at all.

    Returns whatever is on disk, in filename order, and does **not** check the
    set against :data:`CARD_TOPICS`. That comparison is a test's job in both
    directions (a topic with no card, a card with no topic), and doing it here
    would make an incomplete store an import-time crash — which is the wrong
    failure while T080 is still authoring them.
    """
    if not CARDS_DIR.is_dir():
        raise CardStoreError(f"The card store directory is missing: {CARDS_DIR}.")
    return {
        card.id: card
        for card in (_read_card(path) for path in sorted(CARDS_DIR.glob(f"*{CARD_SUFFIX}")))
    }


def load_card(card_id: str) -> Card:
    """The card for *card_id*.

    Raises :class:`CardStoreError` naming the topics that *do* have a card, which
    is the same courtesy ``lookup_reference`` extends to the model on an unknown
    topic — an error that echoes what would have been valid
    (``agent_architecture.md`` §3).
    """
    cards = load_cards()
    card = cards.get(card_id)
    if card is None:
        available = ", ".join(sorted(cards)) or "none — the store is empty"
        raise CardStoreError(f"No card {card_id!r} in the store. Available: {available}.")
    return card
