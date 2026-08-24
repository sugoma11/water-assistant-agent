"""The EN and DE surfaces, against the four rules a paraphrase is held to (T112).

Every rule here is checked **on rendered questions over real draws**, not on the
sketches alone: a sketch is correct or not only once its parameters are in it, and
three of the four rules — the route cue, the window, the roof vocabulary — are
properties of the finished sentence. The draw loop is exercised without oracles,
so this file needs no service and no model run; what it needs is the record, for
T12's event pool.

**The German half is where the ambiguity lives**, which is why it has its own
section: the site has two extensive roofs and five roofs in total, so "das
Extensivdach" and "das Dach" are natural German that names nothing, and neither
is visible to any other check in this repository.
"""

from __future__ import annotations

import asyncio
import dataclasses
from functools import lru_cache
from random import Random
from typing import Any

from eval.generation import paraphrases as P
from eval.generation.instantiate import event_draw, generate
from eval.generation.templates import (
    M,
    TEMPLATES,
    Pools,
    Template,
    Undrawable,
    band_days,
    mentions_raw_column,
    rain_events,
    shapes,
)
from harness.run_case import make_case_context
from water_assistant_agent.assistant.toolset import LOOKUP_TOOL

DRAWS_PER_SHAPE = 12
"""Draws per shape. Enough that every value pool is reached, cheap because no
oracle runs: what varies a rendering is the parameters, and a template with a
seven-value pool shows all seven well inside a dozen draws."""


@lru_cache(maxsize=1)
def _bound() -> dict[str, Template]:
    """The registry with T12's event pool read off the record, as generation binds it."""
    from eval.generation.instantiate import daily_rain

    ctx = make_case_context(
        __import__("eval.generation.templates", fromlist=["as_of_at"]).as_of_at(
            band_days()[-1]
        ),
        allow_live=False,
    )
    events = rain_events(asyncio.run(daily_rain(ctx)))
    return {**TEMPLATES, "T12": dataclasses.replace(TEMPLATES["T12"], draw=event_draw(events))}


@lru_cache(maxsize=1)
def _rendered() -> tuple[tuple[Template, dict[str, Any], str, str, str], ...]:
    """Every (template, params, shape, style, question) this catalog can produce.

    One tuple per rendered surface, over :data:`DRAWS_PER_SHAPE` draws of every
    shape in every register that shape's splits reach. The canonical rendering
    rides along in the tests that need it, computed from the same draw so the
    comparison is about the paraphrase rather than about two different cases.
    """
    rng = Random(20260824)
    # The whole band, undivided: a surface is checked over every draw the
    # catalog admits, not over one split's stripe of them.
    days = Pools(band_days())
    out: list[tuple[Template, dict[str, Any], str, str, str]] = []
    for name in sorted(_bound()):
        template = _bound()[name]
        for split in sorted(template.splits):
            for fragment in template.strata(split) or ({},):
                for _ in range(DRAWS_PER_SHAPE):
                    try:
                        _, drawn = template.draw(rng, days, fragment)
                    except Undrawable:
                        continue
                    params = {**fragment, **drawn}
                    shape = template.shape(params)
                    for style in P.SURFACES[shape]:
                        choice = P.Choice(P.STYLES[style].language, style)
                        out.append(
                            (template, params, shape, style, P.render(template, params, choice))
                        )
    return tuple(out)


def _german() -> list[tuple[Template, dict[str, Any], str, str, str]]:
    return [row for row in _rendered() if P.STYLES[row[3]].language == "de"]


# --- The pools ------------------------------------------------------------------


def test_the_style_pools_are_disjoint_between_train_and_test():
    """§1.7's rule, at both levels it can be broken: the register and the sketch.

    A shared register would be a shared surface form by another name, and a
    sketch shared across two register names would defeat the rule while passing
    a check on the names alone.
    """
    train = {name for name, style in P.STYLES.items() if style.side == "train"}
    test = {name for name, style in P.STYLES.items() if style.side == "test"}
    assert train and test and not (train & test)

    train_sketches = {
        sketch
        for pool in P.SURFACES.values()
        for style, sketch in pool.items()
        if style in train
    }
    test_sketches = {
        sketch
        for pool in P.SURFACES.values()
        for style, sketch in pool.items()
        if style in test
    }
    assert not (train_sketches & test_sketches)


def test_both_sides_carry_a_colloquial_german_register():
    """"Colloquial German included" (§1.6), on both sides of a disjoint cut.

    The register cannot live on one side only: in test alone it would confound
    German register with style novelty, in train alone the suite would never test
    it. Two colloquial registers sharing no sketch is what satisfies both.
    """
    assert P.STYLES["de_umgangssprachlich"].side == "train"
    assert P.STYLES["de_knapp"].side == "test"
    for side in ("train", "test"):
        assert len(P.styles_for(side, "de")) == 2
        assert len(P.styles_for(side, "en")) == 2


def test_every_shape_carries_every_register_its_splits_reach():
    """No shape falls back to nothing, and no pool holds a register nothing draws.

    A holdout template has no train instances, so its pool is the test registers
    exactly — a train register on it would be a surface form no case is ever
    asked in.
    """
    for name, template in sorted(TEMPLATES.items()):
        sides = {P.side_of(split) for split in template.splits}
        expected = {
            style for style, meta in P.STYLES.items() if meta.side in sides
        }
        for shape in shapes(template):
            assert set(P.SURFACES[shape]) == expected, f"{name} / {shape}"
    registry_shapes = {shape for t in TEMPLATES.values() for shape in shapes(t)}
    assert set(P.SURFACES) == registry_shapes


def test_the_two_date_conventions_are_a_stated_axis():
    """Each register writes windows one way, and which way is a property of the register.

    German writes ``03.07.2025`` and the record writes ``2025-07-03``; both reach
    a candidate, which is deliberate, and the axis is the register rather than the
    draw so a failure is attributable.
    """
    windows = ("date", "period", "past_period", "event")
    for pool in P.SURFACES.values():
        for style, sketch in pool.items():
            if P.STYLES[style].language != "de":
                continue
            fields = {
                field
                for field in P.placeholders(sketch)
                if field.split("_de")[0] in windows
            }
            iso = {field for field in fields if field.endswith("_de_iso")}
            if P.date_convention(style) == "german":
                assert not iso, (style, sketch)
            else:
                assert fields == iso, (style, sketch)


# --- The language plan ----------------------------------------------------------


def test_language_is_balanced_within_each_split():
    """§1.7's stratum: 50/50 in train, 28/28 in the holdout, 63/62 in test_seen.

    125 is odd, so test_seen cannot be halved; the alternating start makes it
    63/62, which is the closest a 125-case split gets and is stated rather than
    rounded away.
    """
    counts = {
        split: P.language_counts(P.plan(split, TEMPLATES))
        for split in ("train", "test_seen", "test_unseen")
    }
    assert counts["train"] == {"en": 50, "de": 50}
    assert counts["test_seen"] == {"en": 63, "de": 62}
    assert counts["test_unseen"] == {"en": 28, "de": 28}
    assert sum(sum(row.values()) for row in counts.values()) == 281


def test_every_template_is_asked_in_both_languages_in_every_split_it_carries():
    """A per-split balance made of monolingual templates would report the same 50/50.

    It would also mean a template's German performance was never measured, which
    is what the stratum exists to measure.
    """
    for split in ("train", "test_seen", "test_unseen"):
        for name, choices in P.plan(split, TEMPLATES).items():
            languages = {choice.language for choice in choices}
            assert languages == {"en", "de"}, f"{name} in {split}"
            assert len(choices) == M[split]


def test_every_register_of_a_side_is_actually_used():
    """Both registers per (side, language), rather than one and a spare."""
    for split in ("train", "test_seen", "test_unseen"):
        used = {choice.style for choices in P.plan(split, TEMPLATES).values() for choice in choices}
        assert used == {
            style for style, meta in P.STYLES.items() if meta.side == P.side_of(split)
        }


def test_the_plan_is_positional_and_never_sampled():
    """No RNG reaches it, so the style of any emitted case is recoverable exactly.

    ``case.schema.json`` admits no style field, which is only acceptable while
    this holds.
    """
    assert P.plan("train", TEMPLATES) == P.plan("train", TEMPLATES)
    planned = P.plan("train", TEMPLATES)
    carried = [name for name in sorted(TEMPLATES) if "train" in TEMPLATES[name].splits]
    # The rule rather than one literal tuple: languages alternate inside a
    # template, the starting language alternates with the template's position,
    # and the register alternates inside each language.
    for position, name in enumerate(carried):
        first = "en" if position % 2 == 0 else "de"
        second = "de" if first == "en" else "en"
        assert planned[name] == (
            P.Choice(first, P.styles_for("train", first)[0]),
            P.Choice(second, P.styles_for("train", second)[0]),
            P.Choice(first, P.styles_for("train", first)[1]),
            P.Choice(second, P.styles_for("train", second)[1]),
        ), name


# --- The route cue ---------------------------------------------------------------


def test_a_paraphrase_neither_adds_nor_removes_a_documentary_reference():
    """§1.6's route cue, over every surface of every shape, in both languages.

    Checked against the canonical rendering of the *same draw*: the rule is that
    the paraphrase carries what the template's own question carries, and a flag
    would only say what the catalog believes.
    """
    for template, params, shape, style, question in _rendered():
        canonical = template.render(params)
        assert P.names_documentation(question) == P.names_documentation(canonical), (
            f"{shape} / {style}: {question}"
        )


def test_the_documentary_reference_lands_exactly_where_the_gold_route_is_a_lookup():
    """The T07 / T16a / T16b defect, as a standing check rather than a memory.

    Four templates name the documentation, and every one of them has
    ``lookup_reference`` in its gold trajectory. The converse half is what T07 and
    T11 broke once: a question naming the manual against a `calc_irrigation` gold
    set cues the route away from the answer it is scored on.
    """
    rng = Random(4)
    naming = set()
    for name, template in sorted(_bound().items()):
        for split in sorted(template.splits):
            for fragment in template.strata(split) or ({},):
                for _ in range(4):
                    try:
                        _, drawn = template.draw(rng, Pools(band_days()), fragment)
                    except Undrawable:
                        continue
                    if P.names_documentation(template.render({**fragment, **drawn})):
                        naming.add(name)
    assert naming == {"T08", "T12", "T16a", "T20"}
    for name in naming:
        gold = {call["name"] for call in TEMPLATES[name].expected_tool_calls({})}
        assert LOOKUP_TOOL in gold, name
    for name in ("T07", "T11", "T16b"):
        assert name not in naming


def test_the_documentation_cue_reads_german_compounds():
    """Why the cue is stems rather than words: German writes it as one noun.

    ``Betriebshandbuchs`` is the same cue as ``the manual``, and a word list would
    need every compound the site's own German can build.
    """
    assert P.names_documentation("laut Betriebshandbuch")
    assert P.names_documentation("nach der Definition des Betriebshandbuchs")
    assert P.names_documentation("what does the operations manual say")
    assert not P.names_documentation("Muss das Kiesdach morgen bewässert werden?")
    assert not P.names_documentation("would it stay above the irrigation threshold")


# --- The window ------------------------------------------------------------------


def test_no_paraphrase_redenotes_a_window():
    """A restatement keeps the window; "nächste Woche" replaces it.

    ``decisions.md`` § Forward horizons are counted in days: a calendar week
    beginning Monday is not the seven days beginning today, and the oracle
    resolves the parameter rather than the prose. Families F keep their "coming
    week" because there the week is a *stated value*, which is why the rule is
    stated against the canonical rather than as a ban.
    """
    for template, params, shape, style, question in _rendered():
        canonical = template.render(params)
        assert P.names_calendar_period(question) == P.names_calendar_period(canonical), (
            f"{shape} / {style}: {question}"
        )


def test_the_catalogs_own_german_example_for_t15a_is_a_redenotation():
    """The check is not vacuous, and the case that proves it is in `questions.md`.

    §2 T15a offers "Wie viel hat es letzte Woche geregnet?" as its DE example. The
    template's parameter is an explicit ``{past_period}`` window, so that sentence
    names a different set of days on every reading — the backward twin of the
    "nächste Woche" T15b's own note forbids. It is not used, and this is what
    would catch it if it were.
    """
    assert P.names_calendar_period("Wie viel hat es letzte Woche geregnet?")
    canonical = TEMPLATES["T15a"].render({"past_period": "2025-09-04..2025-09-10"})
    assert not P.names_calendar_period(canonical)
    for _, _, shape, _, question in _rendered():
        if shape == "T15a":
            assert "woche" not in question.lower()


# --- The roof vocabulary ----------------------------------------------------------


def test_no_paraphrase_names_a_roof_by_its_raw_column():
    """§1.6, over the paraphrases now as well as the canonical form (T111).

    The rule binds hardest here: `{alias}` is drawn from spellings half of which
    *are* database columns, and a German surface that substituted the parameter
    verbatim would say "QGravel" in a sentence that reads perfectly otherwise.
    """
    for _, _, shape, style, question in _rendered():
        assert mentions_raw_column(question) == (), f"{shape} / {style}: {question}"


def test_a_roof_is_named_in_the_agents_vocabulary_or_in_the_display_label():
    """The two forms §1.6 admits, and both are in use rather than one.

    The operator registers name the roof as the agent does
    (`non_irrigated_extensive`); every other register uses the display label of
    the language it is written in.
    """
    operator = [
        question for _, _, _, style, question in _rendered() if style == "en_operator"
    ]
    assert any("non_irrigated_extensive" in question for question in operator)
    assert any(
        "non-irrigated extensive green roof" in question
        for _, _, _, style, question in _rendered()
        if style == "en_direct"
    )
    assert any(
        "unbewässerten Extensivdach" in question
        for _, _, _, style, question in _rendered()
        if style == "de_standard"
    )


# --- The German pool: the spot-check §1.6 asks for before splits are cut -----------


def test_no_german_surface_refers_ambiguously():
    """Five roofs on one building, two of them extensive — so "das Dach" names none.

    The spot-check the packet asks for, mechanized: every German surface of every
    shape over :data:`DRAWS_PER_SHAPE` draws.
    """
    for _, _, shape, style, question in _german():
        assert P.ambiguous_german(question, shape=shape) == (), f"{shape} / {style}: {question}"


def test_the_ambiguity_check_catches_the_sentences_it_is_for():
    """Not vacuous: the phrases it forbids are natural German a paraphrase invites.

    Each of these is a sentence someone would write, and each names either five
    roofs or two.
    """
    assert P.ambiguous_german("Muss das Dach morgen bewässert werden?")
    assert P.ambiguous_german("Wie viel ist vom Extensivdach abgeflossen?")
    assert P.ambiguous_german("Wie feucht ist das Extensivdach?")
    assert P.ambiguous_german("Wie weit lagen die Extensivdächer auseinander?", shape="T09")


def test_the_pair_templates_may_name_the_two_extensive_roofs_as_a_pair():
    """Where the subject *is* the pair, naming it as a pair resolves rather than blurs.

    T03 and T24b compare the two by construction and T06 asks for the threshold
    governing both, so "die beiden Extensivdächer" is exactly the referent. The
    same phrase anywhere else is the defect above.
    """
    phrase = "Wie weit lagen die beiden Extensivdächer auseinander?"
    assert P.ambiguous_german(phrase, shape="T24b") == ()
    assert P.ambiguous_german(phrase, shape="T03") == ()
    assert P.ambiguous_german(phrase, shape="T09") != ()


def test_every_german_surface_opens_with_a_capital():
    """A German sentence beginning "das bewässerte Extensivdach liegt bei …" is wrong.

    It cannot be repaired after rendering: an English surface may legitimately
    open on `non_irrigated_extensive`, which is a vocabulary token rather than a
    word. :func:`~eval.generation.paraphrases.german_forms` therefore carries the
    capitalized forms and the sketches use them where they open on the roof.
    """
    for _, _, shape, style, question in _german():
        assert question[:1].isupper() or question[:1].isdigit(), f"{shape} / {style}: {question}"


def test_german_declines_the_adjective_with_the_case():
    """"vom bewässertes Extensivdach" is what a bare label produces; this is the fix."""
    forms = P.german_forms("bewässertes Extensivdach")
    assert forms["das"] == "das bewässerte Extensivdach"
    assert forms["dem"] == "dem bewässerten Extensivdach"
    assert forms["des"] == "des bewässerten Extensivdachs"
    assert forms["obl"] == "bewässerten Extensivdach"
    assert forms["Das"] == "Das bewässerte Extensivdach"
    plain = P.german_forms("Kiesdach")
    assert (plain["das"], plain["dem"], plain["des"], plain["obl"]) == (
        "das Kiesdach",
        "dem Kiesdach",
        "des Kiesdachs",
        "Kiesdach",
    )


def test_the_german_variable_pool_carries_its_article():
    """German has genders and T18b's pool is English nouns; the map bridges them."""
    from eval.generation.templates import UNSERVED_VARIABLES

    assert set(P.VARIABLES_DE) == set(UNSERVED_VARIABLES)
    assert P.VARIABLES_DE["air pressure"][0] == "der Luftdruck"
    assert P.VARIABLES_DE["snow depth"][0] == "die Schneehöhe"


# --- Underspecification -----------------------------------------------------------


def test_every_parameter_a_shape_draws_appears_in_every_surface_of_it():
    """A paraphrase that drops `{thr}` asks a question with no answer.

    The same defect as an ambiguous roof, one level up: the oracle answers the
    parameters, so a surface that does not carry one is scored against a number
    the question never named. `variant` and `table` are excluded because they
    choose the sketch rather than appear in it.
    """
    for template, params, shape, style, _ in _rendered():
        fields = P.placeholders(P.SURFACES[shape][style])
        carried = {field.split("_")[0] for field in fields}
        carried |= {"_".join(field.split("_")[:2]) for field in fields}
        for name in set(params) - P.SHAPE_PARAMS_FIXED_BY_STRATA:
            assert name in carried, f"{shape} / {style} drops {name}"


def test_every_surface_renders_over_every_draw():
    """No sketch names a field its shape's draws do not carry."""
    assert len(_rendered()) > 1500
    assert all(question.strip() for *_, question in _rendered())


# --- End to end -------------------------------------------------------------------


def test_a_generation_run_asks_each_instance_in_its_planned_surface():
    """The wiring, on the one template that needs no service to answer.

    Train's four instances of T04 are two English and two German, in the two
    train registers, and each carries the language it was asked in.
    """
    run = asyncio.run(
        generate(templates={"T04": TEMPLATES["T04"]}, splits=("train", "test_seen"))
    )
    train = run.for_split("train")
    assert [case["inputs"]["language"] for case in train] == ["en", "de", "en", "de"]
    questions = [case["inputs"]["question"] for case in train]
    assert len({question for question in questions}) == 4
    for case in run.instances:
        language = case.case["inputs"]["language"]
        question = case.case["inputs"]["question"]
        assert ("Abfluss" in question or "Kam am" in question or "Ist am" in question) == (
            language == "de"
        ), question
