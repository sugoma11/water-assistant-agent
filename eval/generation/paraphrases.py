"""The EN and DE question surfaces, and the four rules a paraphrase is held to (T112).

``questions.md`` §1.6 asks for "EN + DE, 50/50 within each split, style pools
disjoint between train and test, colloquial German included". A paraphrase is
therefore not decoration: it is the only channel through which a candidate's
German routing accuracy is measured (§1.7 reports language as a stratum), and it
is the one part of a case that can silently change what the case *asks*.

**Four invariants, all machine-checked, and each one a way a paraphrase can stop
being a paraphrase:**

* **The route cue is invariant.** A question that names the documentation routes
  to ``lookup_reference``; one asking only for a decision or a measured number
  routes to the tool that computes it. So a paraphrase may neither add nor remove
  a documentary reference, in either language (§1.6). Checked *against the
  canonical rendering of the same draw* rather than against a flag, which is what
  makes it a statement about the paraphrase rather than about the catalog:
  :func:`names_documentation` of the surface must equal that of
  ``template.render(params)``.
* **The window is restated, never redenoted.** "in den nächsten sieben Tagen" is
  ``d = 7``; "nächste Woche" is a calendar week beginning Monday and is a
  different window (``decisions.md`` § Forward horizons are counted in days).
  :func:`names_calendar_period` must likewise agree with the canonical, which
  keeps the "coming week" that families F state as a *given value* and forbids
  the one that would replace a sampled window.
* **Roofs are named in the agent's vocabulary or in alias-covered natural
  language, never by raw column name** (§1.6) —
  :func:`~eval.generation.templates.mentions_raw_column`, the same check T111
  applies to the canonical form.
* **No German surface refers ambiguously.** The site has two extensive roofs, so
  "das Extensivdach" names neither; :func:`ambiguous_german` carries that class of
  defect, and the parameter-coverage rule below carries the other one.

**Every parameter the shape draws appears in every surface of it.** A paraphrase
that drops ``{thr}`` asks a question with no answer, which is the same defect as
an ambiguous roof and is caught by the same test.

**Style pools are disjoint between train and test, and both sides carry a
colloquial German register.** The disjointness is a memorization guard beside
per-parameter disjointness (§1.7): no surface form a candidate met in train is
met again in test. Withholding a whole *register* is a different thing and is not
what the rule asks for — a suite whose colloquial German lived only in test would
confound German register with style novelty, so train carries
``de_umgangssprachlich`` and test carries ``de_knapp``, two colloquial registers
that share no sketch.

**The style is not a case field**, because ``case.schema.json``'s ``inputs``
admits none. It does not need to be: :func:`plan` is a pure function of the split
and the template's position in the registry, so the style of any emitted case is
recoverable exactly.
"""

from __future__ import annotations

import dataclasses
import re
import string
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Literal

from water_assistant_agent.assistant.tools.roofs import resolve_roof

from eval.generation.templates import Template

Language = Literal["en", "de"]
Side = Literal["train", "test"]


def side_of(split: str) -> Side:
    """Which style pool *split* draws from — train's, or the one both tests share.

    ``test_seen`` and ``test_unseen`` share a pool because §1.7's rule is
    "disjoint between train and test": the holdout's novelty is its templates,
    and giving it a third style pool would confound a template-transfer failure
    with an unseen surface form.
    """
    return "train" if split == "train" else "test"


@dataclasses.dataclass(frozen=True, slots=True)
class Style:
    """One register, in one language, on one side of the train/test cut."""

    name: str
    language: Language
    side: Side
    register: str


STYLES: Mapping[str, Style] = {
    style.name: style
    for style in (
        Style("en_direct", "en", "train", "the plain question, as an operator types it"),
        Style("en_operator", "en", "train", "telegraphic; names the roof in the agent's own vocabulary"),
        Style("en_polite", "en", "test", "a polite request wrapped around the same question"),
        Style("en_context", "en", "test", "one sentence of context, then the question"),
        Style("de_standard", "de", "train", "Standarddeutsch, vollständiger Satz, ISO-Datum"),
        Style("de_umgangssprachlich", "de", "train", "umgangssprachlich, gesprochene Wortstellung, deutsches Datum"),
        Style("de_hoeflich", "de", "test", "höfliche Bitte, vollständiger Satz, ISO-Datum"),
        Style("de_knapp", "de", "test", "knapp und umgangssprachlich, Stichworte, deutsches Datum"),
    )
}
"""The eight registers. Two per (side, language), so every split uses both of its.

The two colloquial German ones sit on opposite sides on purpose (module
docstring). ``de_standard``/``de_hoeflich`` write windows in ISO dates and
``de_umgangssprachlich``/``de_knapp`` in the German convention (``03.07.2025``),
so the suite carries both date conventions on a stated axis instead of choosing
one silently — :func:`date_convention` is what a test reads that off.
"""


def styles_for(side: Side, language: Language) -> tuple[str, ...]:
    """The registers *side* offers in *language*, in the order instances take them."""
    return tuple(
        name
        for name, style in STYLES.items()
        if style.side == side and style.language == language
    )


def date_convention(style: str) -> str:
    """``iso`` or ``german`` — which date form this register's windows are written in."""
    return "german" if style in ("de_umgangssprachlich", "de_knapp") else "iso"


@dataclasses.dataclass(frozen=True, slots=True)
class Choice:
    """The surface one instance is asked in: a language and a register in it."""

    language: Language
    style: str


# --- The language and style plan ----------------------------------------------


def plan(split: str, templates: Mapping[str, Template]) -> dict[str, tuple[Choice, ...]]:
    """Per template, the surface of each of its instances in *split*.

    **Positional, never sampled**, for the reason §1.7 stratifies the ``variant``
    axis: language is a reported stratum, and a stratum drawn at random is a
    stratum whose balance is a property of the seed.

    Languages alternate inside a template and the *starting* language alternates
    with the template's position in the registry. On an even ``m`` that is 50/50
    inside every template and therefore inside the split; on ``test_seen``'s
    ``m = 5`` a template lands 3/2 and the alternating start makes the split
    63/62 — the closest a 125-case split gets to even, since 125 is odd. Train
    lands 50/50 and test_unseen 28/28 exactly.
    """
    side = side_of(split)
    carried = [name for name in sorted(templates) if split in templates[name].splits]
    plans: dict[str, tuple[Choice, ...]] = {}
    for position, name in enumerate(carried):
        count = templates[name].instances(split)
        used: dict[str, int] = {"en": 0, "de": 0}
        choices: list[Choice] = []
        for index in range(count):
            language: Language = "en" if (index + position) % 2 == 0 else "de"
            pool = styles_for(side, language)
            choices.append(Choice(language, pool[used[language] % len(pool)]))
            used[language] += 1
        plans[name] = tuple(choices)
    return plans


def language_counts(plans: Mapping[str, Sequence[Choice]]) -> dict[str, int]:
    """How many instances a plan puts in each language — the reported stratum."""
    counts = {"en": 0, "de": 0}
    for choices in plans.values():
        for choice in choices:
            counts[choice.language] += 1
    return counts


# --- Rendering ----------------------------------------------------------------

MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)

VARIABLES_DE: Mapping[str, tuple[str, str]] = {
    "soil temperature": ("die Bodentemperatur", "Bodentemperatur"),
    "soil moisture": ("die Bodenfeuchte", "Bodenfeuchte"),
    "air pressure": ("der Luftdruck", "Luftdruck"),
    "snow depth": ("die Schneehöhe", "Schneehöhe"),
    "evapotranspiration": ("die Verdunstung", "Verdunstung"),
    "dew point": ("der Taupunkt", "Taupunkt"),
}
"""T18b's ``{variable}`` pool in German, with the article, because German has one.

Carried with the article rather than assembled from a gender table: the pool is
six authored strings (``templates.UNSERVED_VARIABLES``) and a gender table would
be a second place for the same six facts to be wrong in.
"""

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_MONTH = re.compile(r"^\d{4}-\d{2}$")
_ISO_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")


def german_forms(label_de: str) -> dict[str, str]:
    """One roof's German label in the four forms a question needs.

    Every label is a neuter ``-dach`` compound, two of them carrying a strong
    adjective (``bewässertes Extensivdach``), so a sketch that writes
    "vom {roof}" against the bare label produces "vom bewässertes Extensivdach".
    The adjective is declined here instead:

    * ``das`` — nominative/accusative with the article, "das bewässerte Extensivdach"
    * ``dem`` — dative with the article, "dem bewässerten Extensivdach"
    * ``des`` — genitive with the article, "des bewässerten Extensivdachs"
    * ``obl`` — the oblique form without one, for the contractions a question
      actually uses: "vom bewässerten Extensivdach", "beim …", "am …"

    Each also comes capitalized (``Das``, ``Bare``, …) for the sketches that open
    on the roof: "das unbewässerte Extensivdach liegt bei …" is a German sentence
    beginning in lower case, and the capital cannot be applied to the rendered
    question afterwards — an English surface may legitimately open on
    ``non_irrigated_extensive``, which is the agent's own vocabulary and not a
    word to be capitalized.
    """
    adjective, _, noun = label_de.rpartition(" ")
    weak = adjective[:-1] if adjective.endswith("es") else adjective
    oblique = f"{adjective[:-2]}en" if adjective.endswith("es") else adjective
    joined = f"{weak} {noun}".strip()
    joined_oblique = f"{oblique} {noun}".strip()
    forms = {
        "": label_de,
        "das": f"das {joined}",
        "dem": f"dem {joined_oblique}",
        "des": f"des {oblique + ' ' if oblique else ''}{noun}s",
        "obl": joined_oblique,
    }
    capitalized = {
        (case.capitalize() or "Bare"): f"{form[0].upper()}{form[1:]}"
        for case, form in forms.items()
    }
    return {**forms, **capitalized}


def _month_forms(value: str) -> dict[str, str]:
    year, index = (int(part) for part in value.split("-"))
    return {
        "en": f"{MONTHS_EN[index - 1]} {year}",
        "de": f"{MONTHS_DE[index - 1]} {year}",
        "de_iso": f"{MONTHS_DE[index - 1]} {year}",
    }


def _day_forms(value: str) -> dict[str, str]:
    day = date.fromisoformat(value)
    return {
        "en": f"{day.day} {MONTHS_EN[day.month - 1]} {day.year}",
        "de": f"{day:%d.%m.%Y}",
        "de_iso": value,
    }


def _range_forms(start: str, end: str) -> dict[str, str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return {
        "en": f"{start} to {end}",
        "de": f"{first:%d.%m.%Y} bis {last:%d.%m.%Y}",
        "de_iso": f"{start} bis {end}",
    }


def context(params: Mapping[str, Any], language: Language) -> dict[str, Any]:
    """Everything a sketch of *language* may substitute, derived from one draw.

    Three families of key beyond the raw parameters:

    * **roofs** — ``{roof}`` is the display label of the language being written,
      ``{roof_id}`` the agent's own vocabulary (``non_irrigated_extensive``), and
      German adds :func:`german_forms`' four cases. ``{alias}`` is resolved the
      same way: the parameter is a *spelling* the tools' scope table matches, and
      the question text names the roof (``questions.md`` §2 T27).
    * **windows** — ``{period}`` stays the parameter's own ``a..b`` form, and
      ``_en`` / ``_de`` / ``_de_iso`` add the prose forms. The two German ones are
      the same window in two date conventions, never a different window: a
      paraphrase restates, it does not redenote.
    * **T18b's variable**, which needs its German article (:data:`VARIABLES_DE`).
    """
    values: dict[str, Any] = dict(params)
    for key in ("roof", "roof_a", "roof_b", "alias"):
        name = params.get(key)
        if not isinstance(name, str):
            continue
        segment = resolve_roof(name)
        if segment is None:
            continue
        values[f"{key}_id"] = segment.name
        if language == "en":
            values[key] = segment.label_en
            continue
        forms = german_forms(segment.label_de)
        values[key] = forms[""]
        for case, form in forms.items():
            if case:
                values[f"{key}_{case}"] = form
    for key, value in list(params.items()):
        if not isinstance(value, str):
            continue
        if _ISO_MONTH.match(value):
            forms = _month_forms(value)
        elif _ISO_DAY.match(value):
            forms = _day_forms(value)
        elif (span := _ISO_RANGE.match(value)) is not None:
            forms = _range_forms(span.group(1), span.group(2))
        else:
            continue
        values[f"{key}_en"] = forms["en"]
        values[f"{key}_de"] = forms["de"]
        values[f"{key}_de_iso"] = forms["de_iso"]
    variable = params.get("variable")
    if isinstance(variable, str) and variable in VARIABLES_DE:
        values["variable_de"], values["variable_de_bare"] = VARIABLES_DE[variable]
    return values


def surface(shape: str, style: str) -> str:
    """The sketch *shape* is asked in under *style*, raising where there is none."""
    try:
        return SURFACES[shape][style]
    except KeyError as exc:  # pragma: no cover - a registry gap, caught by tests
        raise KeyError(f"no {style} surface for shape {shape}") from exc


def render(template: Template, params: Mapping[str, Any], choice: Choice) -> str:
    """The question one instance is shown, in the chosen language and register."""
    return surface(template.shape(params), choice.style).format(
        **context(params, choice.language)
    )


def placeholders(sketch: str) -> frozenset[str]:
    """The ``{name}`` fields *sketch* substitutes."""
    return frozenset(
        field for _, field, _, _ in string.Formatter().parse(sketch) if field
    )


# --- The four rules -------------------------------------------------------------

DOCUMENTATION_STEMS: tuple[str, ...] = (
    "manual",
    "handbook",
    "handbuch",
    "betriebsanleitung",
    "documentation",
    "dokumentation",
    "regelwerk",
    "richtlinie",
    "vorschrift",
)
"""What "names the documentation" is, as stems rather than words.

Stems because German compounds: ``Betriebshandbuch``, ``Betriebshandbuchs`` and
``Handbuch`` are one cue and would be three entries in a word list, and the next
compound would be a fourth. The cue this catches is the explicit half of §1.6's
route cue; the other half — asking what a documented value *is* — is a property
of the question's subject rather than of its wording, which is why the rule is
stated as "neither add nor remove" against the canonical rather than as a flag.
"""

CALENDAR_STEMS: tuple[str, ...] = (
    "week",
    "woche",
    "wochen",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag",
    "weekend",
    "wochenende",
    "fortnight",
)
"""Calendar-relative expressions, which denote a window a horizon parameter does not.

"Nächste Woche" is the seven days from Monday and ``{d} = 7`` is the seven from
today; "last week" is T19's backward twin of the same defect. Month *names* are
deliberately absent: a ``{month}`` parameter written "Juli 2025" denotes exactly
the parameter, which is a restatement.
"""

AMBIGUOUS_GERMAN: tuple[str, ...] = (
    "das dach",
    "dem dach",
    "des dachs",
    "des daches",
    "vom dach",
    "am dach",
    "beim dach",
    "auf dem dach",
    "das extensivdach",
    "dem extensivdach",
    "des extensivdachs",
    "vom extensivdach",
    "am extensivdach",
    "beim extensivdach",
)
"""Definite German noun phrases that name no single roof.

Five segments sit on one building, so "das Dach" is five roofs; two of them are
extensive, so "das Extensivdach" is two. Both read as perfectly natural German,
which is exactly why the check is mechanical. The *plural* extensive phrase is a
different matter and is handled by :func:`ambiguous_german`'s allowance: "die
beiden Extensivdächer" names both, and both is what T03, T06 and T24b ask about.
"""

FIXED_PAIR_SHAPES: frozenset[str] = frozenset({"T03", "T06", "T24b"})
"""Shapes whose subject *is* the two extensive roofs, so naming them as a pair resolves.

T03 and T24b compare them by construction and T06 asks for the threshold that
governs both. Anywhere else, a plural extensive phrase is a question that has not
said which roof it means.
"""

PLURAL_EXTENSIVE: tuple[str, ...] = (
    "die extensivdächer",
    "die beiden extensivdächer",
    "beide extensivdächer",
    "den beiden extensivdächern",
    "der beiden extensivdächer",
)


def _has_stem(text: str, stems: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(stem in lowered for stem in stems)


def names_documentation(text: str) -> bool:
    """Whether *text* points at the documentation — §1.6's explicit route cue."""
    return _has_stem(text, DOCUMENTATION_STEMS)


def names_calendar_period(text: str) -> bool:
    """Whether *text* names a calendar week or weekday, which redenotes a window."""
    return _has_stem(text, CALENDAR_STEMS)


def ambiguous_german(text: str, *, shape: str = "") -> tuple[str, ...]:
    """The ambiguous German references in *text* — empty is the rule kept.

    *shape* admits the plural extensive phrase where the shape's subject is the
    pair itself (:data:`FIXED_PAIR_SHAPES`).
    """
    lowered = text.lower()
    found = [phrase for phrase in AMBIGUOUS_GERMAN if phrase in lowered]
    if shape not in FIXED_PAIR_SHAPES:
        found.extend(phrase for phrase in PLURAL_EXTENSIVE if phrase in lowered)
    return tuple(sorted(set(found)))


# --- The pools ------------------------------------------------------------------
#
# One entry per shape (`templates.shapes`), one sketch per register. A holdout
# template carries the test pool only: it has no train instances, and inventing a
# train register for it would be a style pool nothing draws from.
#
# The German half is written to the site's own vocabulary — `Bodenfeuchte`,
# `Abfluss`, `Rückhalt`, `bewässern` — and every roof reference goes through
# `german_forms`, so the declension follows the roof rather than the sketch.

SURFACES: Mapping[str, Mapping[str, str]] = {
    # --- A. Pure SQL ----------------------------------------------------------
    "T01": {
        "en_direct": "What was the total outflow of the {roof} in {month_en}?",
        "en_operator": "Outflow total, {roof_id}, {month} — how much?",
        "en_polite": (
            "Could you tell me how much water ran off the {roof} in total during "
            "{month_en}?"
        ),
        "en_context": (
            "I am writing up {month_en}. What did the {roof} shed in total that month?"
        ),
        "de_standard": "Wie viel Wasser ist im {month_de} insgesamt vom {roof_obl} abgeflossen?",
        "de_umgangssprachlich": (
            "Sag mal, wie viel ist im {month_de} eigentlich alles vom {roof_obl} "
            "runtergekommen?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, wie viel Wasser {roof_das} im {month_de} insgesamt "
            "abgegeben hat?"
        ),
        "de_knapp": "Abfluss {roof}, {month_de} — Summe?",
    },
    "T02": {
        "en_direct": (
            "On how many days from {period_en} did the maximum air temperature exceed "
            "{thr} °C?"
        ),
        "en_operator": "Days with max air temperature above {thr} °C, {period} — count?",
        "en_polite": (
            "Could you count the days from {period_en} on which the maximum air "
            "temperature went above {thr} °C?"
        ),
        "en_context": (
            "We are checking the heat load over {period_en}. On how many of those days "
            "did the maximum air temperature exceed {thr} °C?"
        ),
        "de_standard": (
            "An wie vielen Tagen vom {period_de_iso} lag die maximale Lufttemperatur "
            "über {thr} °C?"
        ),
        "de_umgangssprachlich": (
            "Wie viele Tage waren vom {period_de} denn über {thr} °C Höchsttemperatur?"
        ),
        "de_hoeflich": (
            "Könnten Sie zählen, an wie vielen Tagen vom {period_de_iso} die "
            "Tageshöchsttemperatur {thr} °C überschritten hat?"
        ),
        "de_knapp": "Tage über {thr} °C Maximum, {period_de} — wie viele?",
    },
    "T03": {
        "en_direct": (
            "What was the mean soil-moisture difference between the irrigated and the "
            "non-irrigated extensive roof from {period_en}?"
        ),
        "en_operator": (
            "Mean soil-moisture gap, irrigated_extensive against "
            "non_irrigated_extensive, {period}?"
        ),
        "en_polite": (
            "Could you work out the average soil-moisture difference between the "
            "irrigated and the non-irrigated extensive roof from {period_en}?"
        ),
        "en_context": (
            "For the irrigation write-up covering {period_en}: on average, how far "
            "apart were the irrigated and the non-irrigated extensive roof in soil "
            "moisture?"
        ),
        "de_standard": (
            "Wie groß war vom {period_de_iso} der mittlere Bodenfeuchteunterschied "
            "zwischen dem bewässerten und dem unbewässerten Extensivdach?"
        ),
        "de_umgangssprachlich": (
            "Wie weit lagen das bewässerte und das unbewässerte Extensivdach vom "
            "{period_de} bei der Bodenfeuchte im Schnitt auseinander?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir den mittleren Bodenfeuchteunterschied zwischen dem "
            "bewässerten und dem unbewässerten Extensivdach vom {period_de_iso} nennen?"
        ),
        "de_knapp": (
            "Mittlere Bodenfeuchtedifferenz bewässertes gegen unbewässertes "
            "Extensivdach, {period_de}?"
        ),
    },
    "T04": {
        "en_direct": "Did the {roof} produce any outflow on {date_en}?",
        "en_operator": "Any outflow at all, {roof_id}, {date}?",
        "en_polite": (
            "Could you check whether the {roof} produced any outflow at all on "
            "{date_en}?"
        ),
        "en_context": (
            "I am going through the runoff log for {date_en}. Did the {roof} produce "
            "any outflow that day?"
        ),
        "de_standard": "Ist am {date_de_iso} überhaupt Wasser vom {roof_obl} abgeflossen?",
        "de_umgangssprachlich": "Kam am {date_de} eigentlich irgendwas vom {roof_obl} runter?",
        "de_hoeflich": (
            "Könnten Sie prüfen, ob am {date_de_iso} Abfluss vom {roof_obl} verzeichnet "
            "wurde?"
        ),
        "de_knapp": "Abfluss {roof} am {date_de}: ja oder nein?",
    },
    "T05": {
        "en_direct": "On which day in {month_en} did the {roof} have its highest outflow?",
        "en_operator": "Peak outflow day, {roof_id}, {month}?",
        "en_polite": (
            "Could you tell me which day in {month_en} brought the highest outflow from "
            "the {roof}?"
        ),
        "en_context": (
            "For the {month_en} summary: on which single day did the {roof} shed the "
            "most water?"
        ),
        "de_standard": "An welchem Tag im {month_de} hatte {roof_das} seinen höchsten Abfluss?",
        "de_umgangssprachlich": (
            "An welchem Tag ist im {month_de} am meisten vom {roof_obl} runtergekommen?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, an welchem Tag im {month_de} der Abfluss {roof_des} "
            "am größten war?"
        ),
        "de_knapp": "Stärkster Abflusstag {roof}, {month_de}?",
    },
    "T15a": {
        "en_direct": "How much rain fell from {past_period_en}?",
        "en_operator": "Rain total, {past_period}?",
        "en_polite": "Could you tell me how much rain fell from {past_period_en}?",
        "en_context": (
            "We are reconciling the water balance for {past_period_en}. How much rain "
            "fell over that window?"
        ),
        "de_standard": "Wie viel Regen ist vom {past_period_de_iso} gefallen?",
        "de_umgangssprachlich": "Wie viel hat es vom {past_period_de} eigentlich geregnet?",
        "de_hoeflich": "Könnten Sie mir die Regenmenge vom {past_period_de_iso} nennen?",
        "de_knapp": "Niederschlagssumme {past_period_de}?",
    },
    # --- B. Pure reference lookup ---------------------------------------------
    "T06": {
        "en_direct": "What is the soil-moisture threshold for irrigating the extensive roofs?",
        "en_operator": "Irrigation threshold for the extensive roofs — what value?",
        "en_polite": (
            "Could you tell me the soil-moisture threshold at which the extensive roofs "
            "are irrigated?"
        ),
        "en_context": (
            "A colleague asked me at what soil moisture the extensive roofs get "
            "irrigated. What is the threshold?"
        ),
        "de_standard": "Ab welcher Bodenfeuchte werden die Extensivdächer bewässert?",
        "de_umgangssprachlich": (
            "Ab wie viel Bodenfeuchte wird bei den Extensivdächern eigentlich bewässert?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir den Bodenfeuchte-Schwellenwert für die Bewässerung der "
            "Extensivdächer nennen?"
        ),
        "de_knapp": "Bewässerungsschwelle Extensivdächer — welcher Wert?",
    },
    "T17a": {
        "en_direct": "What is the maximum wind speed at which irrigation must be shut off?",
        "en_operator": "Wind cut-off for irrigation — what speed?",
        "en_polite": (
            "Could you tell me above which wind speed irrigation has to be switched off?"
        ),
        "en_context": (
            "We had gusts up on the roof yesterday. Above what wind speed must "
            "irrigation be shut off?"
        ),
        "de_standard": "Ab welcher Windgeschwindigkeit muss die Bewässerung abgeschaltet werden?",
        "de_umgangssprachlich": "Bei wie viel Wind muss die Bewässerung eigentlich aus?",
        "de_hoeflich": (
            "Könnten Sie mir sagen, ab welcher Windgeschwindigkeit die Bewässerung "
            "abzuschalten ist?"
        ),
        "de_knapp": "Windgrenze für die Bewässerungsabschaltung?",
    },
    "T17b": {
        "en_polite": (
            "Could you tell me the soil-moisture irrigation threshold for the wetland "
            "roof?"
        ),
        "en_context": (
            "The wetland roof looked dry this morning. What is its soil-moisture "
            "irrigation threshold?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir den Bodenfeuchte-Schwellenwert für die Bewässerung des "
            "Sumpfdachs nennen?"
        ),
        "de_knapp": "Bewässerungsschwelle Sumpfdach — welcher Wert?",
    },
    # --- C. Pure weather -------------------------------------------------------
    "T13": {
        "en_direct": "Is more than {thr} mm of rain expected in the next {d} days?",
        "en_operator": "More than {thr} mm of rain in the next {d} days — expected?",
        "en_polite": (
            "Could you check whether more than {thr} mm of rain is expected over the "
            "next {d} days?"
        ),
        "en_context": (
            "We are deciding whether to hold off on irrigating. Is more than {thr} mm of "
            "rain expected in the next {d} days?"
        ),
        "de_standard": "Werden in den nächsten {d} Tagen mehr als {thr} mm Regen erwartet?",
        "de_umgangssprachlich": "Soll es in den nächsten {d} Tagen mehr als {thr} mm regnen?",
        "de_hoeflich": (
            "Könnten Sie prüfen, ob in den nächsten {d} Tagen mehr als {thr} mm "
            "Niederschlag erwartet werden?"
        ),
        "de_knapp": "Mehr als {thr} mm Regen in den nächsten {d} Tagen?",
    },
    "T14": {
        "en_direct": "What is the highest temperature forecast for the next {d} days?",
        "en_operator": "Highest forecast temperature, next {d} days?",
        "en_polite": (
            "Could you tell me the highest temperature forecast over the next {d} days?"
        ),
        "en_context": (
            "We are planning roof work. What is the highest temperature forecast for the "
            "next {d} days?"
        ),
        "de_standard": "Welche Höchsttemperatur ist für die nächsten {d} Tage vorhergesagt?",
        "de_umgangssprachlich": "Wie warm soll es in den nächsten {d} Tagen maximal werden?",
        "de_hoeflich": (
            "Könnten Sie mir die höchste für die nächsten {d} Tage vorhergesagte "
            "Temperatur nennen?"
        ),
        "de_knapp": "Höchsttemperatur nächste {d} Tage?",
    },
    "T15b": {
        "en_direct": "How much rain will fall in the next {d} days?",
        "en_operator": "Rain total, next {d} days?",
        "en_polite": (
            "Could you tell me how much rain is expected to fall over the next {d} days?"
        ),
        "en_context": (
            "We are scheduling the next irrigation. How much rain will fall over the "
            "next {d} days?"
        ),
        "de_standard": "Wie viel Regen fällt in den nächsten {d} Tagen?",
        "de_umgangssprachlich": "Wie viel soll es in den nächsten {d} Tagen regnen?",
        "de_hoeflich": (
            "Könnten Sie mir sagen, wie viel Niederschlag in den nächsten {d} Tagen "
            "erwartet wird?"
        ),
        "de_knapp": "Regenmenge nächste {d} Tage?",
    },
    "T18a": {
        "en_direct": "What will the temperature be in {ahead_days} days?",
        "en_operator": "Temperature in {ahead_days} days?",
        "en_polite": (
            "Could you tell me what the temperature will be in {ahead_days} days?"
        ),
        "en_context": (
            "We are booking a maintenance slot {ahead_days} days out. What will the "
            "temperature be then?"
        ),
        "de_standard": "Wie warm wird es in {ahead_days} Tagen?",
        "de_umgangssprachlich": "Was wird es denn in {ahead_days} Tagen für eine Temperatur?",
        "de_hoeflich": (
            "Könnten Sie mir sagen, welche Temperatur in {ahead_days} Tagen zu erwarten "
            "ist?"
        ),
        "de_knapp": "Temperatur in {ahead_days} Tagen?",
    },
    "T18b": {
        "en_polite": (
            "Could you give me the forecast {variable} for the next {d} days?"
        ),
        "en_context": (
            "For the sensor report I still need the forecast {variable} over the next "
            "{d} days."
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, wie {variable_de} für die nächsten {d} Tage "
            "vorhergesagt ist?"
        ),
        "de_knapp": "Vorhersage {variable_de_bare}, nächste {d} Tage?",
    },
    # --- D. Model chains -------------------------------------------------------
    "T09": {
        "en_direct": (
            "Will the soil moisture of the {roof} fall below {thr} %θ over the next "
            "{d} days?"
        ),
        "en_operator": "{roof_id}: soil moisture below {thr} %θ within {d} days?",
        "en_polite": (
            "Could you check whether the {roof}'s soil moisture drops below {thr} %θ in "
            "the next {d} days?"
        ),
        "en_context": (
            "We are watching this dry spell. Does the {roof} fall below {thr} %θ soil "
            "moisture over the next {d} days?"
        ),
        "de_standard": (
            "Fällt die Bodenfeuchte {roof_des} in den nächsten {d} Tagen unter {thr} %θ?"
        ),
        "de_umgangssprachlich": (
            "Geht {roof_das} in den nächsten {d} Tagen unter {thr} %θ Bodenfeuchte "
            "runter?"
        ),
        "de_hoeflich": (
            "Könnten Sie prüfen, ob die Bodenfeuchte {roof_des} in den nächsten {d} "
            "Tagen unter {thr} %θ sinkt?"
        ),
        "de_knapp": "{roof_Bare}: Bodenfeuchte unter {thr} %θ in den nächsten {d} Tagen?",
    },
    "T10": {
        "en_direct": (
            "What is the minimum soil moisture predicted for the {roof} over the next "
            "{d} days?"
        ),
        "en_operator": "{roof_id}: predicted minimum soil moisture, next {d} days?",
        "en_polite": (
            "Could you tell me the lowest soil moisture predicted for the {roof} over "
            "the next {d} days?"
        ),
        "en_context": (
            "Before we set the irrigation plan: what is the lowest soil moisture the "
            "{roof} is predicted to reach over the next {d} days?"
        ),
        "de_standard": (
            "Welche minimale Bodenfeuchte wird für {roof_das} in den nächsten {d} Tagen "
            "vorhergesagt?"
        ),
        "de_umgangssprachlich": (
            "Wie weit geht die Bodenfeuchte {roof_des} in den nächsten {d} Tagen runter?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir die niedrigste für {roof_das} in den nächsten {d} Tagen "
            "vorhergesagte Bodenfeuchte nennen?"
        ),
        "de_knapp": "Minimale Bodenfeuchte {roof}, nächste {d} Tage?",
    },
    "T19": {
        "en_direct": (
            "How far off was the soil-moisture model for the {roof} over the last {d} "
            "days, on average?"
        ),
        "en_operator": "{roof_id}: mean soil-moisture model error over the last {d} days?",
        "en_polite": (
            "Could you tell me how far the soil-moisture model was off for the {roof} "
            "over the last {d} days, on average?"
        ),
        "en_context": (
            "We are validating the model. On average, how far off was its soil moisture "
            "for the {roof} over the last {d} days?"
        ),
        "de_standard": (
            "Wie stark lag das Bodenfeuchtemodell {roof_des} in den letzten {d} Tagen im "
            "Mittel daneben?"
        ),
        "de_umgangssprachlich": (
            "Wie weit war das Modell bei der Bodenfeuchte {roof_des} in den letzten {d} "
            "Tagen im Schnitt daneben?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir die mittlere Abweichung des Bodenfeuchtemodells {roof_des} "
            "über die letzten {d} Tage nennen?"
        ),
        "de_knapp": "Mittlere Modellabweichung Bodenfeuchte {roof}, letzte {d} Tage?",
    },
    # --- E. Hybrid / full chain ------------------------------------------------
    "T07": {
        "en_direct": "Does the {roof} need irrigation right now?",
        "en_operator": "{roof_id}: irrigate now?",
        "en_polite": (
            "Could you check whether the {roof} needs irrigating at the moment?"
        ),
        "en_context": "I am up on the roof now. Does the {roof} need irrigation?",
        "de_standard": "Muss {roof_das} jetzt bewässert werden?",
        "de_umgangssprachlich": "Muss {roof_das} gerade gegossen werden?",
        "de_hoeflich": "Könnten Sie prüfen, ob {roof_das} im Moment bewässert werden muss?",
        "de_knapp": "{roof_Bare}: jetzt bewässern?",
    },
    "T08": {
        "en_direct": (
            "How many heatwave days, as defined in the operations manual, occurred from "
            "{period_en}?"
        ),
        "en_operator": "Heatwave days per the operations manual, {period} — count?",
        "en_polite": (
            "Could you count the heatwave days from {period_en}, using the definition in "
            "the operations manual?"
        ),
        "en_context": (
            "For the summer report covering {period_en}: how many days count as heatwave "
            "days under the operations manual's definition?"
        ),
        "de_standard": (
            "Wie viele Hitzetage im Sinne des Betriebshandbuchs gab es vom "
            "{period_de_iso}?"
        ),
        "de_umgangssprachlich": (
            "Wie viele Hitzetage nach der Definition im Betriebshandbuch waren vom "
            "{period_de} dabei?"
        ),
        "de_hoeflich": (
            "Könnten Sie die Hitzetage vom {period_de_iso} nach der Definition des "
            "Betriebshandbuchs zählen?"
        ),
        "de_knapp": "Hitzetage laut Betriebshandbuch, {period_de} — Anzahl?",
    },
    "T11": {
        "en_direct": "Does the {roof} need irrigation tomorrow?",
        "en_operator": "{roof_id}: irrigate tomorrow?",
        "en_polite": (
            "Could you check whether the {roof} will need irrigating tomorrow?"
        ),
        "en_context": (
            "I am planning tomorrow's round. Does the {roof} need irrigation then?"
        ),
        "de_standard": "Muss {roof_das} morgen bewässert werden?",
        "de_umgangssprachlich": "Muss {roof_das} morgen gegossen werden?",
        "de_hoeflich": "Könnten Sie prüfen, ob {roof_das} morgen bewässert werden muss?",
        "de_knapp": "{roof_Bare}: morgen bewässern?",
    },
    "T12": {
        "en_direct": (
            "Was the retention of the {roof} during {event_en} above the target in the "
            "operations manual?"
        ),
        "en_operator": "{roof_id} retention over {event}: above the manual's target?",
        "en_polite": (
            "Could you check whether the {roof}'s retention during {event_en} stayed "
            "above the manual's target?"
        ),
        "en_context": (
            "We are reviewing the rain event of {event_en}. Did the {roof} hold back "
            "more than the operations manual's retention target?"
        ),
        "de_standard": (
            "Lag der Rückhalt {roof_des} vom {event_de_iso} über dem Zielwert des "
            "Betriebshandbuchs?"
        ),
        "de_umgangssprachlich": (
            "Hat {roof_das} vom {event_de} mehr zurückgehalten, als das Betriebshandbuch "
            "vorgibt?"
        ),
        "de_hoeflich": (
            "Könnten Sie prüfen, ob der Rückhalt {roof_des} vom {event_de_iso} über dem "
            "Zielwert des Betriebshandbuchs lag?"
        ),
        "de_knapp": "Rückhalt {roof}, {event_de}: über dem Zielwert des Betriebshandbuchs?",
    },
    "T20": {
        "en_polite": (
            "Could you tell me whether the forecast for the next {d} days qualifies as a "
            "heatwave under the operations manual's definition?"
        ),
        "en_context": (
            "We may have to plan extra irrigation. Does the forecast for the next {d} "
            "days qualify as a heatwave under the operations manual's definition?"
        ),
        "de_hoeflich": (
            "Könnten Sie prüfen, ob die Vorhersage für die nächsten {d} Tage nach der "
            "Definition des Betriebshandbuchs eine Hitzewelle ist?"
        ),
        "de_knapp": "Nächste {d} Tage laut Betriebshandbuch eine Hitzewelle?",
    },
    "T25": {
        "en_direct": "Will tomorrow be warmer than yesterday?",
        "en_operator": "Tomorrow warmer than yesterday?",
        "en_polite": "Could you tell me whether tomorrow will be warmer than yesterday?",
        "en_context": (
            "I am comparing the two ends of this stretch. Will tomorrow be warmer than "
            "yesterday?"
        ),
        "de_standard": "Wird es morgen wärmer als gestern?",
        "de_umgangssprachlich": "Wird es morgen wärmer als gestern, oder eher nicht?",
        "de_hoeflich": "Könnten Sie mir sagen, ob es morgen wärmer wird als gestern?",
        "de_knapp": "Morgen wärmer als gestern?",
    },
    # --- F. Given-values controls ----------------------------------------------
    "T16a": {
        "en_direct": (
            "The {roof} is at {x} %θ, {tmax} °C is expected over the next 48 h and {y} "
            "mm of rain over the coming week — what does the operations manual say, "
            "should we irrigate?"
        ),
        "en_operator": (
            "{roof_id} at {x} %θ, {tmax} °C over 48 h, {y} mm over the coming week — per "
            "the operations manual, irrigate?"
        ),
        "en_polite": (
            "The {roof} is at {x} %θ, with {tmax} °C expected over the next 48 h and {y} "
            "mm of rain over the coming week. Could you tell me what the operations "
            "manual says — should we irrigate?"
        ),
        "en_context": (
            "Readings in front of me: the {roof} at {x} %θ, {tmax} °C over the next 48 h, "
            "{y} mm of rain over the coming week. What does the operations manual say "
            "about irrigating?"
        ),
        "de_standard": (
            "{roof_Das} steht bei {x} %θ, für die nächsten 48 h sind {tmax} °C angesagt "
            "und für die kommende Woche {y} mm Regen — was sagt das Betriebshandbuch, "
            "sollen wir bewässern?"
        ),
        "de_umgangssprachlich": (
            "{roof_Das} ist bei {x} %θ, die nächsten 48 h {tmax} °C, die kommende Woche "
            "{y} mm Regen — was steht dazu im Betriebshandbuch, bewässern oder nicht?"
        ),
        "de_hoeflich": (
            "{roof_Das} liegt bei {x} %θ, für die nächsten 48 h werden {tmax} °C erwartet "
            "und für die kommende Woche {y} mm Niederschlag. Könnten Sie mir sagen, was "
            "das Betriebshandbuch dazu vorsieht — bewässern?"
        ),
        "de_knapp": (
            "{roof_Bare}: {x} %θ, 48 h {tmax} °C, kommende Woche {y} mm — laut "
            "Betriebshandbuch bewässern?"
        ),
    },
    "T16b": {
        "en_polite": (
            "The {roof} is at {x} %θ, with {tmax} °C expected over the next 48 h and {y} "
            "mm of rain over the coming week. Could you tell me whether we should "
            "irrigate?"
        ),
        "en_context": (
            "Readings in front of me: the {roof} at {x} %θ, {tmax} °C over the next 48 h, "
            "{y} mm of rain over the coming week. Should we irrigate?"
        ),
        "de_hoeflich": (
            "{roof_Das} liegt bei {x} %θ, für die nächsten 48 h werden {tmax} °C erwartet "
            "und für die kommende Woche {y} mm Niederschlag. Könnten Sie mir sagen, ob "
            "wir bewässern sollen?"
        ),
        "de_knapp": "{roof_Bare}: {x} %θ, 48 h {tmax} °C, kommende Woche {y} mm — bewässern?",
    },
    # --- G. Counterfactuals -----------------------------------------------------
    "T21": {
        "en_direct": (
            "If {mm} mm of rain falls on day {offset} of the next {d} days, what is the "
            "minimum soil moisture of the {roof} over that window?"
        ),
        "en_operator": (
            "{roof_id}, {mm} mm forced on day {offset} of {d}: minimum soil moisture over "
            "the window?"
        ),
        "en_polite": (
            "Could you work out the minimum soil moisture of the {roof} over the next "
            "{d} days, assuming {mm} mm of rain falls on day {offset}?"
        ),
        "en_context": (
            "A shower is possible on day {offset} of the next {d} days. If it brings "
            "{mm} mm, what is the lowest soil moisture the {roof} reaches over that "
            "window?"
        ),
        "de_standard": (
            "Wenn am Tag {offset} der nächsten {d} Tage {mm} mm Regen fallen, wie hoch "
            "ist dann die minimale Bodenfeuchte {roof_des} in diesem Zeitraum?"
        ),
        "de_umgangssprachlich": (
            "Angenommen, am Tag {offset} der nächsten {d} Tage kommen {mm} mm runter — "
            "wie tief geht die Bodenfeuchte {roof_des} in dem Zeitraum?"
        ),
        "de_hoeflich": (
            "Könnten Sie berechnen, welche minimale Bodenfeuchte {roof_das} in den "
            "nächsten {d} Tagen erreicht, wenn am Tag {offset} {mm} mm Regen fallen?"
        ),
        "de_knapp": (
            "{roof_Bare}, {mm} mm an Tag {offset} von {d}: minimale Bodenfeuchte im Zeitraum?"
        ),
    },
    "T22": {
        "en_polite": (
            "Could you tell me what soil moisture is predicted for the {roof} tomorrow "
            "under the current forecast but with albedo {a}?"
        ),
        "en_context": (
            "We are considering a lighter surface on the {roof}. Under the current "
            "forecast but with albedo {a}, what soil moisture is predicted for it "
            "tomorrow?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, welche Bodenfeuchte für {roof_das} morgen "
            "vorhergesagt wird, wenn man bei der aktuellen Vorhersage die Albedo auf {a} "
            "setzt?"
        ),
        "de_knapp": "{roof_Bare} morgen, aktuelle Vorhersage, aber Albedo {a}: Bodenfeuchte?",
    },
    "T23": {
        "en_polite": (
            "If the {roof}'s soil moisture had been {x} %θ {d} days ago, could you tell "
            "me where it would be now?"
        ),
        "en_context": (
            "Suppose the {roof} had started at {x} %θ {d} days ago. Where would its soil "
            "moisture be now?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, wo die Bodenfeuchte {roof_des} heute läge, wenn sie "
            "vor {d} Tagen bei {x} %θ gelegen hätte?"
        ),
        "de_knapp": "{roof_Bare}: vor {d} Tagen {x} %θ — wo läge die Bodenfeuchte jetzt?",
    },
    "T26:albedo_and_rain": {
        "en_polite": (
            "If albedo were {a} and {mm} mm fell on day {offset} of the next {d} days, "
            "could you tell me whether the {roof} would stay above the irrigation "
            "threshold?"
        ),
        "en_context": (
            "Two changes at once: albedo {a}, and {mm} mm of rain on day {offset} of the "
            "next {d} days. Would the {roof} stay above the irrigation threshold?"
        ),
        "de_hoeflich": (
            "Könnten Sie prüfen, ob {roof_das} über der Bewässerungsschwelle bliebe, wenn "
            "die Albedo {a} wäre und am Tag {offset} der nächsten {d} Tage {mm} mm "
            "fielen?"
        ),
        "de_knapp": (
            "Albedo {a}, {mm} mm an Tag {offset} von {d}: bleibt {roof_das} über der "
            "Bewässerungsschwelle?"
        ),
    },
    "T26:rain_cross_roof": {
        "en_polite": (
            "If albedo were {a} and {mm} mm of rain fell on day {offset} of the next "
            "{d} days, could you tell me whether the {roof_a} would end up wetter "
            "than the {roof_b}?"
        ),
        "en_context": (
            "Same two changes on both roofs: albedo {a}, and {mm} mm of rain on day "
            "{offset} of the next {d} days. Does the {roof_a} end up wetter than the "
            "{roof_b}?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, ob {roof_a_das} am Ende feuchter wäre als "
            "{roof_b_das}, wenn die Albedo {a} wäre und am Tag {offset} der nächsten "
            "{d} Tage {mm} mm Regen fielen?"
        ),
        "de_knapp": (
            "Albedo {a}, {mm} mm an Tag {offset} von {d}: Endet {roof_a_das} feuchter "
            "als {roof_b_das}?"
        ),
    },
    "T26:train_taught": {
        "en_polite": (
            "If {mm} mm of rain falls on day {offset} of the next {d} days, could you "
            "tell me whether the {roof_a} ends up wetter than the {roof_b}?"
        ),
        "en_context": (
            "Same rain on both: {mm} mm on day {offset} of the next {d} days. Does the "
            "{roof_a} end up wetter than the {roof_b}?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir sagen, ob {roof_a_das} am Ende feuchter ist als "
            "{roof_b_das}, wenn am Tag {offset} der nächsten {d} Tage {mm} mm Regen "
            "fallen?"
        ),
        "de_knapp": (
            "{mm} mm an Tag {offset} von {d}: Endet {roof_a_das} feuchter als "
            "{roof_b_das}?"
        ),
    },
    # --- H. Presentation --------------------------------------------------------
    "T24a:measured_pair:swc": {
        "en_direct": (
            "Show me how the soil moisture of the {roof_a} and the {roof_b} developed in "
            "{month_en}."
        ),
        "en_operator": "Plot soil moisture, {roof_a_id} and {roof_b_id}, {month}.",
        "en_polite": (
            "Could you show me how the soil moisture of the {roof_a} and the {roof_b} "
            "developed over {month_en}?"
        ),
        "en_context": (
            "I am preparing the {month_en} review. Show the soil moisture of the {roof_a} "
            "and the {roof_b} over that month."
        ),
        "de_standard": (
            "Zeig mir, wie sich die Bodenfeuchte {roof_a_des} und {roof_b_des} im "
            "{month_de} entwickelt hat."
        ),
        "de_umgangssprachlich": (
            "Zeig mal den Bodenfeuchteverlauf vom {roof_a_obl} und vom {roof_b_obl} im "
            "{month_de}."
        ),
        "de_hoeflich": (
            "Könnten Sie mir den Verlauf der Bodenfeuchte {roof_a_des} und {roof_b_des} "
            "im {month_de} zeigen?"
        ),
        "de_knapp": "Bodenfeuchteverlauf {roof_a} und {roof_b}, {month_de} — als Grafik.",
    },
    "T24a:measured_pair:outflow": {
        "en_direct": (
            "Show me how much water ran off the {roof_a} and the {roof_b} in {month_en}."
        ),
        "en_operator": "Plot outflow, {roof_a_id} and {roof_b_id}, {month}.",
        "en_polite": (
            "Could you show me how much water ran off the {roof_a} and the {roof_b} over "
            "{month_en}?"
        ),
        "en_context": (
            "For the runoff section of the {month_en} review: show how much water ran off "
            "the {roof_a} and the {roof_b}."
        ),
        "de_standard": (
            "Zeig mir, wie viel Wasser im {month_de} vom {roof_a_obl} und vom "
            "{roof_b_obl} abgeflossen ist."
        ),
        "de_umgangssprachlich": (
            "Zeig mal, wie viel im {month_de} vom {roof_a_obl} und vom {roof_b_obl} "
            "runtergekommen ist."
        ),
        "de_hoeflich": (
            "Könnten Sie mir den Abflussverlauf {roof_a_des} und {roof_b_des} im "
            "{month_de} zeigen?"
        ),
        "de_knapp": "Abflussverlauf {roof_a} und {roof_b}, {month_de} — als Grafik.",
    },
    "T24a:model_overlay": {
        "en_direct": (
            "Plot the measured soil moisture of the {roof} against the model's "
            "prediction for {month_en}."
        ),
        "en_operator": "Plot measured against modelled soil moisture, {roof_id}, {month}.",
        "en_polite": (
            "Could you plot the measured soil moisture of the {roof} against the model's "
            "prediction for {month_en}?"
        ),
        "en_context": (
            "We are checking the model against the sensors. Plot the {roof}'s measured "
            "soil moisture and the model's prediction for {month_en}."
        ),
        "de_standard": (
            "Stell die gemessene Bodenfeuchte {roof_des} der Modellvorhersage für "
            "{month_de} gegenüber."
        ),
        "de_umgangssprachlich": (
            "Zeig mal die gemessene Bodenfeuchte {roof_des} zusammen mit der "
            "Modellvorhersage für {month_de}."
        ),
        "de_hoeflich": (
            "Könnten Sie die gemessene Bodenfeuchte {roof_des} und die Modellvorhersage "
            "für {month_de} zusammen darstellen?"
        ),
        "de_knapp": "Grafik {roof}, {month_de}: gemessene Bodenfeuchte gegen Modell.",
    },
    "T24a:non_modellable_overlay": {
        "en_direct": (
            "Plot the {alias}'s measured soil moisture against the model's prediction "
            "for {month_en}."
        ),
        "en_operator": "Plot measured against modelled soil moisture, {alias_id}, {month}.",
        "en_polite": (
            "Could you plot the {alias}'s measured soil moisture against the model's "
            "prediction for {month_en}?"
        ),
        "en_context": (
            "For the {month_en} review I also need the {alias}: measured soil moisture "
            "against the model's prediction."
        ),
        "de_standard": (
            "Stell die gemessene Bodenfeuchte {alias_des} der Modellvorhersage für "
            "{month_de} gegenüber."
        ),
        "de_umgangssprachlich": (
            "Zeig mal die gemessene Bodenfeuchte {alias_des} zusammen mit der "
            "Modellvorhersage für {month_de}."
        ),
        "de_hoeflich": (
            "Könnten Sie die gemessene Bodenfeuchte {alias_des} und die Modellvorhersage "
            "für {month_de} zusammen darstellen?"
        ),
        "de_knapp": "Grafik {alias}, {month_de}: gemessene Bodenfeuchte gegen Modell.",
    },
    "T24b": {
        "en_direct": (
            "What was the mean soil-moisture difference between the two extensive roofs "
            "in {month_en}?"
        ),
        "en_operator": "Mean soil-moisture gap, the two extensive roofs, {month}?",
        "en_polite": (
            "Could you tell me the mean soil-moisture difference between the two "
            "extensive roofs in {month_en}?"
        ),
        "en_context": (
            "For the {month_en} irrigation summary: on average, how far apart were the "
            "two extensive roofs in soil moisture?"
        ),
        "de_standard": (
            "Wie groß war im {month_de} der mittlere Bodenfeuchteunterschied zwischen den "
            "beiden Extensivdächern?"
        ),
        "de_umgangssprachlich": (
            "Wie weit lagen die beiden Extensivdächer im {month_de} bei der Bodenfeuchte "
            "im Schnitt auseinander?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir den mittleren Bodenfeuchteunterschied zwischen den beiden "
            "Extensivdächern im {month_de} nennen?"
        ),
        "de_knapp": "Mittlere Bodenfeuchtedifferenz beide Extensivdächer, {month_de}?",
    },
    # --- I. Modelling availability ----------------------------------------------
    "T27:predicted_minimum": {
        "en_direct": (
            "What is the minimum soil moisture predicted for the {alias} over the next "
            "{d} days?"
        ),
        "en_operator": "{alias_id}: predicted minimum soil moisture, next {d} days?",
        "en_polite": (
            "Could you tell me the lowest soil moisture predicted for the {alias} over "
            "the next {d} days?"
        ),
        "en_context": (
            "We are extending the dry-down watch to the {alias}. What is its predicted "
            "minimum soil moisture over the next {d} days?"
        ),
        "de_standard": (
            "Welche minimale Bodenfeuchte wird für {alias_das} in den nächsten {d} Tagen "
            "vorhergesagt?"
        ),
        "de_umgangssprachlich": (
            "Wie weit geht die Bodenfeuchte {alias_des} in den nächsten {d} Tagen runter?"
        ),
        "de_hoeflich": (
            "Könnten Sie mir die niedrigste für {alias_das} in den nächsten {d} Tagen "
            "vorhergesagte Bodenfeuchte nennen?"
        ),
        "de_knapp": "Minimale Bodenfeuchte {alias}, nächste {d} Tage?",
    },
    "T27:irrigation": {
        "en_direct": "Does the {alias} need irrigation tomorrow?",
        "en_operator": "{alias_id}: irrigate tomorrow?",
        "en_polite": "Could you check whether the {alias} needs irrigating tomorrow?",
        "en_context": (
            "Tomorrow's round covers the {alias} as well. Does the {alias} need "
            "irrigation?"
        ),
        "de_standard": "Muss {alias_das} morgen bewässert werden?",
        "de_umgangssprachlich": "Muss {alias_das} morgen gegossen werden?",
        "de_hoeflich": "Könnten Sie prüfen, ob {alias_das} morgen bewässert werden muss?",
        "de_knapp": "{alias_Bare}: morgen bewässern?",
    },
    "T27:forced_rain": {
        "en_direct": (
            "If {mm} mm fell tomorrow, what would the {alias}'s minimum soil moisture be "
            "over the next {d} days?"
        ),
        "en_operator": (
            "{alias_id}, {mm} mm forced tomorrow: minimum soil moisture over {d} days?"
        ),
        "en_polite": (
            "Could you tell me what the {alias}'s minimum soil moisture would be over the "
            "next {d} days if {mm} mm fell tomorrow?"
        ),
        "en_context": (
            "A front is coming through. If it drops {mm} mm tomorrow, what is the "
            "{alias}'s minimum soil moisture over the next {d} days?"
        ),
        "de_standard": (
            "Wenn morgen {mm} mm fielen, wie niedrig wäre die Bodenfeuchte {alias_des} in "
            "den nächsten {d} Tagen?"
        ),
        "de_umgangssprachlich": (
            "Angenommen, morgen kommen {mm} mm runter — wie tief geht die Bodenfeuchte "
            "{alias_des} in den nächsten {d} Tagen?"
        ),
        "de_hoeflich": (
            "Könnten Sie berechnen, welche minimale Bodenfeuchte {alias_das} in den "
            "nächsten {d} Tagen hätte, wenn morgen {mm} mm fielen?"
        ),
        "de_knapp": "{alias_Bare}, morgen {mm} mm: minimale Bodenfeuchte über {d} Tage?",
    },
}
"""Shape → register → sketch. 39 shapes, eight registers, 276 surfaces.

A holdout shape carries the four test registers only; every other shape carries
all eight. :func:`plan` never asks for a register the shape's splits do not
reach, and a test asserts the pools are exactly that shape.
"""


SHAPE_PARAMS_FIXED_BY_STRATA: frozenset[str] = frozenset({"variant", "table"})
"""Parameters that choose the shape rather than appearing inside it.

They are the stratified axis (§1.7), so they are answered by *which* sketch is
used rather than by a placeholder in it — which is why the "every parameter
appears in every surface" rule skips exactly these two.
"""


__all__ = [
    "AMBIGUOUS_GERMAN",
    "CALENDAR_STEMS",
    "DOCUMENTATION_STEMS",
    "SHAPE_PARAMS_FIXED_BY_STRATA",
    "STYLES",
    "SURFACES",
    "VARIABLES_DE",
    "Choice",
    "Style",
    "ambiguous_german",
    "context",
    "date_convention",
    "german_forms",
    "language_counts",
    "names_calendar_period",
    "names_documentation",
    "placeholders",
    "plan",
    "render",
    "side_of",
    "styles_for",
    "surface",
]
