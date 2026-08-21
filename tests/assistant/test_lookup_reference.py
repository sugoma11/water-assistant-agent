"""``lookup_reference``: the vocabulary, the card, the exclusion, the replay (T084).

The packet's test surface is split across three files, and the split follows what
each half can fail on rather than convenience.

**The store half is already covered and is not repeated here.** Card round-trip
and schema validation, ``enum == card keys`` and the no-numerals rule live in
``test_knowledge_store.py`` (pulled forward by T082); every rendered card's
``values:`` against ``values_for(card_id)`` lives in ``test_knowledge_rendered.py``
beside the projection it asserts against (T068). Duplicating them here would give
the same rule two homes and let one of them rot.

**What this file adds is the tool**, and one link the other two could not make:
the vocabulary the *model* sees is the ``Literal`` in ``lookup_reference``'s
signature, not :data:`CARD_TOPICS`, so the chain "signature literal == CARD_TOPICS
== card files on disk" was open at its first link until now. That link is the
validity condition — a docstring is candidate-owned and a signature is not — so
it is asserted through ADK's own declaration rather than by reading the
annotation and hoping the framework agrees.

The rest is behaviour §3.2 fixes: a known topic returns its card whole, ``roof``
narrows the numbers and never the scope blocks, an unknown argument is an
``invalid_argument`` that says what would have been valid, and nothing anywhere
returns ``not_available``. Last comes the packet's exit criterion, run the way a
case runs it: through ``build_toolset``, in replay, with the network nailed shut.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args, get_type_hints
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml
from google.adk.tools.function_tool import FunctionTool

from water_assistant_agent.assistant.cache import ResponseCache
from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.knowledge import store
from water_assistant_agent.assistant.knowledge.store import (
    CARD_TOPICS,
    CardStoreError,
    load_card,
    load_cards,
)
from water_assistant_agent.assistant.ports import QueryResult
from water_assistant_agent.assistant.tools.reference import make_lookup_reference_tool
from water_assistant_agent.assistant.tools.roofs import ROOFS
from water_assistant_agent.assistant.tools.weather_client import make_weather_client
from water_assistant_agent.assistant.toolset import (
    LOOKUP_TOOL,
    TOOL_NAMES,
    build_toolset,
)

AS_OF = datetime(2026, 3, 15, 11, 0, tzinfo=ZoneInfo("Europe/Berlin"))
"""One of T035b's two bounds. Nothing here reads it — which is the point: the
context carries a clock because every context does, and this tool never asks."""

CARD_FIELDS = ("topic", "title", "provenance", "text", "values", "applies_to", "not_applicable")
"""The card's own blocks, under the card's own names, plus the ``id`` rename."""

ROOF_KEYED_TOPICS = (
    "irrigation_threshold",
    "substrate_hydraulics",
    "irrigation_dose",
    "roof_reference_ranges",
)
"""The four cards that keep a separate set of numbers per roof segment.

The others are keyed by quantity (``irrigation_rule``'s conditions) or by table
(``data_freshness``'s five record ends), and there is nothing for ``roof`` to
narrow on them — which this file asserts rather than assumes, because a table
name that happened to match a roof would otherwise be filtered as one.
"""

IRRIGATION_FAMILY = (
    "irrigation_rule",
    "irrigation_threshold",
    "substrate_hydraulics",
    "irrigation_dose",
    "roof_reference_ranges",
)
"""The cards a wetland question can land on — every one carries its exclusion."""


@pytest.fixture
def tool():
    """The production tool, built by its own factory."""
    return make_lookup_reference_tool()


def call(tool, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run one lookup to completion."""
    return asyncio.run(tool(*args, **kwargs))


def declaration_of(callable_: Any) -> Any:
    """The tool declaration ADK would send the model."""
    return FunctionTool(callable_)._get_declaration()


def topic_enum(callable_: Any) -> list[str]:
    """The ``topic`` vocabulary as it reaches the model, out of the declaration."""
    schema = declaration_of(callable_).parameters_json_schema
    return schema["properties"]["topic"]["enum"]


# --- The vocabulary lives in the signature ------------------------------------


def test_the_signature_carries_the_vocabulary_and_not_the_docstring(tool) -> None:
    """The first link of ``signature literal == CARD_TOPICS == card files``.

    :data:`CARD_TOPICS` is the twin; this annotation is what the model actually
    reads. Order included, because the declaration presents the enum in this
    order and ``agent_architecture.md`` §3.2 lists it in one.
    """
    annotation = get_type_hints(tool)["topic"]
    assert get_args(annotation) == CARD_TOPICS
    assert annotation == Literal[CARD_TOPICS]  # type: ignore[valid-type]


def test_adk_renders_the_vocabulary_into_the_tool_declaration(tool) -> None:
    """Asserted through the framework, because the framework is the claim.

    ``decisions.md`` § Retrieval rests the whole design on "the framework renders
    the literal type into the function declaration". Reading the annotation back
    would only prove Python stored it.
    """
    assert topic_enum(tool) == list(CARD_TOPICS)


def test_a_candidate_docstring_cannot_move_the_vocabulary() -> None:
    """§2's one exception, from the side that would exploit it.

    A candidate owns the guidance and can reword every sentence of it. What it
    must not be able to do is shorten the topic list — a candidate that dropped
    ``irrigation_threshold`` from its prose would disable the reference route
    while still scoring as an optimizable rewrite.
    """
    candidate = "CANDIDATE: look things up. Valid topics: none, ask the database."
    tools = build_toolset(_replay_context(None), {LOOKUP_TOOL: candidate})
    lookup = tools[TOOL_NAMES.index(LOOKUP_TOOL)]

    assert declaration_of(lookup).description == candidate
    assert declaration_of(lookup).name == LOOKUP_TOOL
    assert topic_enum(lookup) == list(CARD_TOPICS)


def test_the_tool_was_appended_and_never_moved() -> None:
    """Appended rather than inserted: declaration order is prompt text.

    It was the last entry when this packet landed and is now the fifth of six —
    which is the same claim, stated so that the *next* tool appended cannot
    quietly reorder the four that came before it. What must never happen is this
    tool moving; a tool arriving after it is the anticipated event.
    """
    assert TOOL_NAMES.count(LOOKUP_TOOL) == 1
    assert TOOL_NAMES.index(LOOKUP_TOOL) == 4


# --- A known topic returns the card whole -------------------------------------


@pytest.mark.parametrize("topic", CARD_TOPICS)
def test_every_topic_returns_its_card_whole(tool, topic: str) -> None:
    """Every block of the committed card, unchanged, under the card's own names."""
    card = load_card(topic)
    result = call(tool, topic)

    assert result["status"] == "success"
    assert result["topic"] == card.id
    assert result["title"] == card.title
    assert result["provenance"] == card.provenance
    assert result["text"] == card.text
    assert result["values"] == card.values
    assert result["applies_to"] == card.applies_to
    assert result["not_applicable"] == card.not_applicable


def test_the_result_carries_the_card_s_blocks_and_two_fields_about_the_filter(
    tool,
) -> None:
    """Pinned as a set, so a block cannot be dropped without a test noticing.

    A card whose ``not_applicable`` quietly stopped being serialized would read
    as a card with no exclusions — the T17b failure arriving as silence, one
    layer up from where ``extra="forbid"`` catches it in the file.
    """
    result = call(tool, "irrigation_threshold")
    assert set(result) == {"status", *CARD_FIELDS, "roof", "values_scoped_to_roof"}


@pytest.mark.parametrize("topic", CARD_TOPICS)
@pytest.mark.parametrize("roof", [None, *ROOFS])
def test_no_topic_and_roof_pair_ever_answers_not_available(
    tool, topic: str, roof: str | None
) -> None:
    """§3.2's "no roof-scoped ``not_available``", over the whole product.

    Typing the abstention at the tool level was the rejected alternative: it
    collapses the scope-exclusion template into the already-tested "relay a typed
    ``not_available``" behaviour and costs the catalog its strongest
    hallucination probe (``decisions.md`` § Retrieval). Eleven topics against
    five segments and no segment, and not one of the sixty-six may take that
    shortcut.
    """
    assert call(tool, topic, roof=roof)["status"] == "success"


# --- T17b: the exclusion survives being asked about ---------------------------


def test_the_wetland_exclusion_survives_a_wetland_scoped_threshold_lookup(tool) -> None:
    """T17b's mechanism, in one call.

    The scored behaviour is the agent reading an exclusion *beside* a confidently
    worded rule that does not apply, so both have to be in the payload: the three
    substrate roofs under ``values:``, the wetland under ``not_applicable:``
    (``questions.md`` T17b). A tool that narrowed ``values`` to the asked roof
    would return an empty block and a reason, which is a different and much
    easier question.
    """
    result = call(tool, "irrigation_threshold", roof="wetland")

    assert result["status"] == "success"
    assert set(result["values"]) == {
        "irrigated_extensive",
        "non_irrigated_extensive",
        "semi_intensive",
    }
    assert "wetland" in result["not_applicable"]
    assert "fleece" in result["not_applicable"]["wetland"]
    # The filter ran and found nothing to narrow to — said, not left to inference.
    assert result["roof"] == "wetland"
    assert result["values_scoped_to_roof"] is False


@pytest.mark.parametrize("topic", IRRIGATION_FAMILY)
def test_the_exclusion_survives_on_every_card_a_wetland_question_can_land_on(
    tool, topic: str
) -> None:
    """The probe must not depend on the agent picking one particular card."""
    result = call(tool, topic, roof="wetland")
    assert result["not_applicable"]["wetland"]
    assert "wetland" not in result["applies_to"]


def test_a_scoped_lookup_keeps_an_exclusion_that_is_not_about_substrate(tool) -> None:
    """``retention_target`` excludes the semi-intensive roof for a different reason.

    Instrumentation rather than substrate — it has no lysimeter — and the card
    derives its own scope for exactly that reason (``findings.md`` § Not every
    roof is instrumented). Scoping to it must return the reason, not silence.
    """
    result = call(tool, "retention_target", roof="semi_intensive")
    assert "lysimeter" in result["not_applicable"]["semi_intensive"]


# --- `roof` filters the numbers and nothing else ------------------------------


@pytest.mark.parametrize("topic", ROOF_KEYED_TOPICS)
@pytest.mark.parametrize("roof", ["irrigated_extensive", "non_irrigated_extensive", "semi_intensive"])
def test_a_scoped_lookup_narrows_the_values_to_that_roof(
    tool, topic: str, roof: str
) -> None:
    whole = call(tool, topic)
    scoped = call(tool, topic, roof=roof)

    assert set(scoped["values"]) == {roof}
    assert scoped["values"][roof] == whole["values"][roof]
    assert scoped["values_scoped_to_roof"] is True


@pytest.mark.parametrize("topic", ROOF_KEYED_TOPICS)
@pytest.mark.parametrize("roof", sorted(ROOFS))
def test_a_scoped_lookup_never_narrows_the_scope_blocks(
    tool, topic: str, roof: str
) -> None:
    """``applies_to`` and ``not_applicable`` are what a reader needs most when the
    roof asked about is the one outside, so neither is ever filtered."""
    whole = call(tool, topic)
    scoped = call(tool, topic, roof=roof)

    assert scoped["applies_to"] == whole["applies_to"]
    assert scoped["not_applicable"] == whole["not_applicable"]


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("irrigated_extensive", "irrigated_extensive"),
        ("Sumpfdach", "wetland"),
        ("EGR1", "irrigated_extensive"),
        ("QIn", "semi_intensive"),
        ("  Kiesdach ", "gravel"),
    ],
)
def test_a_roof_is_resolved_from_any_spelling_the_site_uses(
    tool, spelling: str, canonical: str
) -> None:
    """German names, site ids and column names reach the same segment.

    The German/English gap is relocated into the model rather than eliminated
    (``decisions.md`` § Retrieval), and this is the half of the relocation that
    is code: a question phrased in the site's own vocabulary must not need the
    model to translate a roof name before it can scope a card.
    """
    assert call(tool, "irrigation_threshold", roof=spelling)["roof"] == canonical


@pytest.mark.parametrize("topic", ["irrigation_rule", "heatwave_definition", "data_freshness"])
def test_a_card_that_keeps_no_per_roof_numbers_is_not_filtered(tool, topic: str) -> None:
    """``data_freshness`` is the case that earns the rule.

    Its ``values:`` is keyed by *table* — ``swc``, ``tsoil``, ``outflow`` — and a
    filter that matched any key against a roof name rather than requiring every
    key to be one would be one table rename away from silently returning a
    single record end as if it were the whole card's.
    """
    whole = call(tool, topic)
    for roof in ROOFS:
        scoped = call(tool, topic, roof=roof)
        assert scoped["values"] == whole["values"]
        assert scoped["values_scoped_to_roof"] is False


# --- An unknown argument says what would have been valid ----------------------


def test_an_unknown_topic_names_every_valid_topic(tool) -> None:
    """§3's error contract: ``invalid_argument`` echoes what would have worked.

    The enum makes this unreachable through a well-formed function call, which is
    not the same as unreachable: the annotation is a declaration to the model, not
    a runtime check, and a model that emits a topic anyway must get back a list it
    can correct against rather than a ``KeyError``.
    """
    result = call(tool, "wind_shutoff")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_argument"
    assert "wind_shutoff" in result["error_details"]
    for topic in CARD_TOPICS:
        assert topic in result["error_details"]


def test_an_unknown_roof_names_the_five_segments(tool) -> None:
    """A spelling nothing on the building answers to is a fault in the call.

    Not a scope answer — that would be a roof-scoped ``not_available``, which §3.2
    forbids — and not something to pass over in silence either: ignoring it would
    serve every segment's numbers to a question that named one, with nothing in
    the result to say the filter never ran.
    """
    result = call(tool, "irrigation_threshold", roof="the mossy one")

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_argument"
    for roof in ROOFS:
        assert roof in result["error_details"]


def test_an_unknown_topic_is_rejected_before_the_roof_is_looked_at(tool) -> None:
    """Both wrong: the topic is the one reported, because it is the routing fault."""
    result = call(tool, "wind_shutoff", roof="the mossy one")
    assert "wind_shutoff" in result["error_details"]


# --- The store is checked where a broken store belongs ------------------------


def test_a_store_that_cannot_answer_a_topic_fails_at_build_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.2 gives this tool no ``upstream`` class, so there is no honest error to
    return mid-call — the check has to happen before a rollout can reach it.

    This is the runtime counterpart of ``enum == card keys``: that test fails a
    commit, this one fails a boot, and between them a topic with no card behind
    it cannot become a route into a ``CardStoreError`` in the middle of a run.
    """
    (tmp_path / "irrigation_rule.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "irrigation_rule",
                "title": "The one card this store has",
                "provenance": "rendered",
                "text": "A ladder walked in one order.",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "CARDS_DIR", tmp_path)
    load_cards.cache_clear()
    try:
        with pytest.raises(CardStoreError, match="irrigation_threshold"):
            make_lookup_reference_tool()
    finally:
        load_cards.cache_clear()


def test_the_factory_takes_no_context() -> None:
    """The purity guarantee, made structural rather than promised.

    Every other factory closes over a ``ScenarioContext`` because it reads a
    clock, an executor or a weather client. A ``ctx`` parameter here would be
    unread today and an invitation to reach for ``ctx.db`` tomorrow, which is the
    single change that would end §5's "pure, exact, fully offline" without
    failing anything else.
    """
    assert not inspect.signature(make_lookup_reference_tool).parameters


# --- The exit criterion: a lookup case in replay ------------------------------


class NeverQueriedExecutor:
    """A ``ReadOnlyWarehouseQuery`` that fails rather than answering.

    The database half of "fully offline". A lookup that reached for the warehouse
    would still *work* against the pinned file, so an executor that merely
    recorded its queries would let the regression through on a green run.
    """

    def execute_query(self, query: str) -> QueryResult:
        raise AssertionError(f"lookup_reference queried the warehouse: {query}")


def _replay_context(cache_dir: Path | None) -> ScenarioContext:
    """A context in replay: nothing may call out, and there is nothing to read.

    ``allow_live=False`` is what a measurement pass runs under — a cache miss
    there is a hard :class:`~..cache.CacheMissError` rather than a quiet request
    (``decisions.md`` § The response cache) — so a tool that fetched anything
    fails loudly instead of being caught by an assertion afterwards.
    """
    executor = NeverQueriedExecutor()
    cache = None if cache_dir is None else ResponseCache(cache_dir)
    weather = make_weather_client(executor, cache, allow_live=cache is None)
    return ScenarioContext.bound(
        clock=lambda: AS_OF, db=executor, weather=weather, cache=cache
    )


def test_a_lookup_case_completes_in_replay_with_no_cache_entry_and_no_live_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P4's exit criterion, run the way a case runs it.

    Through ``build_toolset`` rather than the factory, because the criterion is
    about the tool a rollout is handed: the context carries a real replay cache
    and a warehouse that refuses, and ``httpx`` is nailed shut underneath both, so
    the only way this passes is by the card store being what §5 calls it — a pure
    function of packaged files.
    """

    async def no_live_calls(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("lookup_reference made a live HTTP call")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_live_calls)

    cache_dir = tmp_path / "cache"
    tools = build_toolset(_replay_context(cache_dir))
    lookup = tools[TOOL_NAMES.index(LOOKUP_TOOL)]

    # The case: a documented threshold, scoped to the roof it was asked about.
    result = call(lookup, "irrigation_threshold", roof="irrigated_extensive")

    assert result["status"] == "success"
    assert result["values"]["irrigated_extensive"]["dry_pct"]
    assert list(cache_dir.glob("*.json")) == [], "a lookup wrote a cache entry"


def test_the_whole_topic_vocabulary_replays_with_an_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not one of the eleven is the card that quietly needs the network.

    ``data_freshness`` is the one worth naming: its values are record dates, and
    it is ``static`` precisely so that reading it stays a file read rather than a
    database one (T081, ``decisions.md`` § Retrieval).
    """

    async def no_live_calls(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("lookup_reference made a live HTTP call")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_live_calls)

    cache_dir = tmp_path / "cache"
    tools = build_toolset(_replay_context(cache_dir))
    lookup = tools[TOOL_NAMES.index(LOOKUP_TOOL)]

    for topic in CARD_TOPICS:
        assert call(lookup, topic)["status"] == "success"
    assert list(cache_dir.glob("*.json")) == []
