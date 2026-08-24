"""The generation filters and the draw loop, against the record (T111).

**Tested against the record's own named cases, not against synthetic data.**
``questions.md`` §1.6 states three predicates and ``findings.md`` measures each
one on a specific window of ``data/water.duckdb``: the dead ``QWetland`` record
from 2026-03-12, the ``QWetland`` episode frozen at a plausible 77.160 %θ,
``Sumpf2_Efflux``'s 3474 consecutive hours at 0.000, the two whole-system
lysimeter outages and the four single-lysimeter dates. A synthetic fixture would
show that the code implements the rule; only these windows show that the rule
separates the record's real faults from its real health, which is the claim §1.6
actually makes.

The healthy half matters as much as the faulty half. A predicate that rejects
everything rejects the dead sensor too, so every faulty-window assertion here has
a healthy counterpart on the same column or the same table.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from random import Random
from zoneinfo import ZoneInfo

import pytest

from eval.generation.filters import (
    FROZEN_RUN,
    POINT_MIN_ROWS,
    Requirement,
    check,
    frozenness_applies,
    measure,
    seed_day,
    seed_requirement,
)
from eval.generation.instantiate import case_envelope, generate, validate
from eval.generation.templates import (
    BAND_END,
    BAND_START,
    M,
    TEMPLATES,
    Template,
    abstention_share,
    as_of_at,
    assert_cards_exist,
    band_days,
    ledger,
    mentions_raw_column,
    raw_column_names,
    t24a_roof_pool,
)
from eval.oracles import ORACLES
from eval.oracles.counterfactual import VARIANT_ALBEDO_AND_RAIN, VARIANT_RAIN_CROSS_ROOF
from eval.oracles.pins import stamp
from eval.oracles.presentation import MEASURED_PAIR, MODEL_OVERLAY, NON_MODELLABLE_OVERLAY
from harness.run_case import make_case_context
from water_assistant_agent.assistant.tools.roofs import ROOFS

BERLIN = ZoneInfo("Europe/Berlin")

BAND_CEILING = datetime(2026, 4, 24, 23, 0, tzinfo=BERLIN)
"""An ``as_of`` at the band's ceiling, so every window in the record is visible.

The filters read through the as-of view, which is the property under test
elsewhere in this file; these predicate tests want the whole record in scope so
that a rejection is the predicate's and not the cut's.
"""


def _ctx(as_of: datetime = BAND_CEILING):
    return make_case_context(as_of, allow_live=False)


def _judge(requirement: Requirement, as_of: datetime = BAND_CEILING) -> tuple[str, ...]:
    """The predicates *requirement* fails, as names — ``()`` when it is accepted."""
    return tuple(
        rejection.predicate for rejection in asyncio.run(check(_ctx(as_of), [requirement]))
    )


def _point(table: str, column: str, day: str) -> Requirement:
    return Requirement(table, column, date.fromisoformat(day), date.fromisoformat(day), "point")


def _period(table: str, column: str, start: str, end: str) -> Requirement:
    return Requirement(
        table, column, date.fromisoformat(start), date.fromisoformat(end), "period"
    )


# --- Plausibility: the dead QWetland record ------------------------------------
#
# `findings.md` § The `QWetland` sensor is dead from 2026-03-12: a flat ~0.00 %θ
# to the end of the record, where the healthy record runs 4–96 %θ around a
# ~86 %θ saturation plateau. Zero is physically impossible for a ponded fleece
# mat, and the committed floor of 2.0 %θ sits in the gap.


def test_bounds_reject_the_dead_wetland_record():
    """The first dead day is rejected on bounds, which is what the floor is for."""
    assert "plausibility" in _judge(_point("swc", "QWetland", "2026-03-12"))


@pytest.mark.parametrize("day", ["2026-03-20", "2026-04-01", "2026-04-24"])
def test_bounds_reject_every_sampled_day_inside_the_dead_stretch(day: str):
    assert "plausibility" in _judge(_point("swc", "QWetland", day))


def test_bounds_accept_the_healthy_wetland_record():
    """January's coherent recharge is not a fault, and nothing may flag it.

    The month `findings.md` describes as sound — 4 %θ climbing to the documented
    ~86 %θ plateau — so this is the false-positive check the floor has to pass in
    order for the rejection above to mean anything.
    """
    assert _judge(_period("swc", "QWetland", "2026-01-01", "2026-01-31")) == ()


def test_bounds_do_not_reject_the_gravel_roofs_honest_near_zero():
    """`QGravel` sits at a band median of 0.06 %θ and is not a dead sensor.

    §1.6's "per column, never global": a floor derived from a planted roof would
    reject the gravel roof's whole record, which is why `roofs.py` gives it
    `Bounds(0.0, 20.0)` and the wetland `Bounds(2.0, 100.0)`.
    """
    assert _judge(_period("swc", "QGravel", "2025-07-01", "2025-07-31")) == ()


# --- Frozenness: the episode the bounds cannot see -----------------------------
#
# `findings.md` § `QWetland` also froze once at a plausible value: exactly
# 77.160 %θ for 312 consecutive rows (156 h), 2025-05-22 to 2025-05-28. Inside
# the bounds, so only a run test catches it.


def test_frozenness_catches_the_plausible_frozen_episode():
    frozen = _period("swc", "QWetland", "2025-05-22", "2025-05-28")
    assert "frozenness" in _judge(frozen)


def test_the_frozen_episode_is_inside_the_bounds_that_miss_it():
    """The half of the claim that makes the run test necessary rather than spare.

    77.160 %θ is a plausible reading for this column, so the episode has to be
    invisible to plausibility — otherwise the frozenness predicate is catching
    something the bounds already had.
    """
    frozen = _period("swc", "QWetland", "2025-05-22", "2025-05-28")
    assert "plausibility" not in _judge(frozen)
    reading = asyncio.run(measure(_ctx(), frozen))
    assert reading.level_min is not None
    assert 76.0 < reading.level_min < 78.0
    assert reading.longest_run >= FROZEN_RUN


def test_frozenness_has_no_false_positive_on_a_healthy_state_sensor():
    """Zero false positives on every healthy state column, per §1.6.

    `findings.md` measures the longest identical run on any healthy state sensor
    at 17 samples (8.5 h, `QEx1`), against the 24-sample test — so an August
    month on all three substrate roofs and all five soil-temperature probes must
    come back clean.
    """
    for column in ("QEx1", "QEx2", "QIn"):
        assert _judge(_period("swc", column, "2025-08-01", "2025-08-31")) == (), column
    for column in ("TGravel", "TEx1", "TEx2", "TIn", "TWetland"):
        assert _judge(_period("tsoil", column, "2025-08-01", "2025-08-31")) == (), column


# --- Frozenness applies to state columns, and to nothing else ------------------


def test_the_wetlands_3474_zero_hours_are_not_rejected():
    """`Sumpf2_Efflux` holds 0.000 for 3474 consecutive hours and is telling the truth.

    §1.6 is explicit that the run test is applied to state columns only: a flux at
    rest is a fact about the weather, not about the sensor. The window is clipped
    to the band's own start, since 2025-04-15 is outside it — what is asserted is
    that a stretch dominated by that 6948-row zero run survives every predicate.
    """
    quiet = _period("outflow", "Sumpf2_Efflux", "2025-06-01", "2025-09-09")
    reading = asyncio.run(measure(_ctx(), quiet))
    assert reading.longest_run > 4000
    assert _judge(quiet) == ()


def test_frozenness_does_not_apply_to_a_flux_or_to_the_station():
    for column in ("Kies_Efflux", "Extensiv1_Efflux", "Sumpf2_Efflux"):
        assert not frozenness_applies("outflow", column)
    assert not frozenness_applies("wetter", "Rain")
    assert not frozenness_applies("radiation", "KD_SWdown")


def test_frozenness_does_not_apply_to_the_gravel_roofs_moisture_column():
    """Excluded because the roof has no substrate store, read off `roofs.py`.

    Derived rather than spelled `QGravel`: a segment with no
    `substrate_height_cm` has nothing whose state that column could be, and the
    exclusion moves with the table if a roof gains or loses a store.
    """
    assert ROOFS["gravel"].substrate_height_cm is None
    assert not frozenness_applies("swc", "QGravel")
    for name in ("irrigated_extensive", "non_irrigated_extensive", "semi_intensive", "wetland"):
        assert frozenness_applies("swc", ROOFS[name].columns["swc"]), name


# --- Coverage: the two whole-system outages and the four thin days -------------
#
# `findings.md` § Two whole-system lysimeter outages: 2025-10-02→05 and
# 2025-10-24→11-27 remove `outflow`, `swc` and `tsoil` together, bracketed by
# partial days at 36, 8, 27 and 25–27 rows.


@pytest.mark.parametrize("day", ["2025-10-02", "2025-10-03", "2025-10-04", "2025-10-05"])
def test_coverage_rejects_the_first_whole_system_outage(day: str):
    assert "coverage" in _judge(_point("swc", "QEx1", day))


@pytest.mark.parametrize("day", ["2025-10-24", "2025-11-01", "2025-11-15", "2025-11-27"])
def test_coverage_rejects_the_second_whole_system_outage(day: str):
    assert "coverage" in _judge(_point("swc", "QEx1", day))


@pytest.mark.parametrize(
    "day", ["2025-10-01", "2025-10-06", "2025-10-23", "2025-11-28"]
)
def test_coverage_rejects_the_four_single_lysimeter_bracket_days(day: str):
    """The four dates bracketing the outages, at 36, 8, 27 and 25–27 rows.

    Each sits under the 44-row floor, which is the whole reason the floor is 44
    and not, say, 24: these are the days that carry *some* data and would
    otherwise be answered from a fraction of one.
    """
    reading = asyncio.run(measure(_ctx(), _point("swc", "QEx1", day)))
    assert reading.rows < POINT_MIN_ROWS
    assert "coverage" in _judge(_point("swc", "QEx1", day))


def test_coverage_accepts_a_healthy_day_anywhere_in_the_band():
    for day in ("2025-06-15", "2025-08-08", "2026-01-20", "2026-04-10"):
        assert _judge(_point("swc", "QEx1", day)) == (), day


def test_the_outage_is_a_gap_and_not_only_a_row_deficit():
    """A period straddling the 35-day outage fails on the single-gap rule too.

    Coverage alone would pass a long enough window with a contiguous hole in it,
    which is precisely the shape both outages make; §1.6 carries the 24 h ceiling
    for that reason.
    """
    rejections = asyncio.run(
        check(_ctx(), [_period("swc", "QEx1", "2025-10-24", "2025-11-27")])
    )
    details = " ".join(str(rejection) for rejection in rejections)
    assert "gap" in details


def test_the_spring_forward_sunday_is_not_an_outage():
    """2026-03-29 is missing exactly its 02:00 and 02:30 rows, and still counts.

    An ingest artifact rather than a fault (`findings.md` § Spring-forward
    artifact), and the four rows of slack in the 44-row floor are what keep it a
    usable day.
    """
    assert _judge(_point("wetter", "Rain", "2026-03-29")) == ()


# --- The as-of view, and where a seed anchors ----------------------------------


def test_a_predicate_never_passes_on_data_the_case_cannot_see():
    """§1.6's "evaluated through the as-of view", checked by moving the cut.

    The same healthy day is accepted at an `as_of` after it and rejected at one
    before it, because the bounded view holds none of its rows. A filter reading
    the pinned file directly would accept both.
    """
    day = _point("swc", "QEx1", "2026-01-20")
    assert _judge(day, as_of=datetime(2026, 1, 21, 23, tzinfo=BERLIN)) == ()
    assert "coverage" in _judge(day, as_of=datetime(2026, 1, 10, 23, tzinfo=BERLIN))


def test_seed_day_is_the_earlier_of_the_window_start_and_as_of():
    """Architecture §3.4's rule, and §1.6's anchor for the seed-bearing families."""
    as_of = date(2026, 4, 20)
    assert seed_day(date(2026, 4, 20), as_of) == as_of  # a forward window seeds today
    assert seed_day(date(2026, 4, 10), as_of) == date(2026, 4, 10)  # retrospective
    assert seed_day(date(2026, 4, 25), as_of) == as_of


def test_a_retrospective_seed_is_checked_at_the_windows_own_start():
    """The anchor is load-bearing rather than a restatement of `as_of`.

    A window opening inside the 35-day outage seeds from a day with no row at
    all, and anchoring at `as_of` — a healthy December day — would clear it. The
    two requirements name different days, and only the earlier one is rejected.
    """
    as_of = date(2025, 12, 1)
    at_start = seed_requirement("irrigated_extensive", date(2025, 11, 10), as_of)
    at_as_of = seed_requirement("irrigated_extensive", date(2025, 12, 1), as_of)
    assert at_start.start == date(2025, 11, 10)
    assert at_as_of.start == as_of
    cut = datetime(2025, 12, 1, 23, tzinfo=BERLIN)
    assert "coverage" in _judge(at_start, as_of=cut)
    assert _judge(at_as_of, as_of=cut) == ()


def test_a_seed_requirement_reads_the_roofs_own_column():
    assert seed_requirement("Kiesdach", date(2026, 1, 5), date(2026, 1, 5)).column == "QGravel"
    with pytest.raises(ValueError, match="No roof answers to"):
        seed_requirement("nonesuch", date(2026, 1, 5), date(2026, 1, 5))


# --- Balance ------------------------------------------------------------------


def _offline(*names: str) -> dict[str, Template]:
    return {name: TEMPLATES[name] for name in names}


def test_t04_balances_fifty_fifty_within_each_split():
    """§1.6's binding balance case, on the catalog's tightest date pool.

    Daily outflow is exactly zero on 75–94 % of band days, so an unbalanced
    sampler would answer "no" on nearly every instance. `m` is even in train, so
    2/2 is exact; test_seen's 5 is odd and lands 2/3, which is the closest a
    five-instance split gets.
    """
    run = asyncio.run(generate(templates=_offline("T04"), splits=("train", "test_seen")))
    assert run.balance("T04")["train"] == {True: 2, False: 2}
    train_yes, train_no = 2, 2
    assert train_yes == train_no
    seen = run.balance("T04")["test_seen"]
    assert sorted(seen.values()) == [2, 3]
    assert seen[True] + seen[False] == M["test_seen"]


def test_t04s_pool_needs_roughly_six_to_one_oversampling():
    """§1.6's "roughly 6:1", measured on the record rather than on one run's draws.

    The ratio is a property of the band — daily outflow is exactly zero on
    75–94 % of days depending on roof, and the wetland has 17 non-zero days in
    the whole band (`findings.md` § Zero-outflow days dominate) — so it is
    counted straight off the (roof, day) pool T04 draws from. A handful of draws
    from one seed is far too small a sample to assert a prior against.
    """
    day = "((timestamp) AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Berlin')::DATE"
    columns = [ROOFS[name].columns["outflow"] for name in ("gravel", "irrigated_extensive",
                                                           "non_irrigated_extensive", "wetland")]
    wet = dry = 0
    for column in columns:
        result = _ctx().db.execute_query(
            f'SELECT count(*), sum(CASE WHEN total > 0 THEN 1 ELSE 0 END) FROM '
            f'(SELECT {day} AS d, sum("{column}") AS total FROM outflow '
            f"WHERE {day} BETWEEN DATE '2025-06-01' AND DATE '2026-04-24' GROUP BY 1)"
        )
        days, wet_days = result.rows[0]
        wet += int(wet_days or 0)
        dry += int(days) - int(wet_days or 0)
    assert wet, "the record carries no wet day at all"
    assert 5.0 <= (wet + dry) / wet <= 8.0


def test_t04_reaches_both_classes_from_that_pool():
    """The sampler's half of the claim: the 1-in-6 class is actually drawn.

    Together with the 50/50 balance above this is the whole of the rule — the
    prior is the record's, and rejection sampling is what turns it into an even
    split.
    """
    run = asyncio.run(generate(templates=_offline("T04"), splits=("train", "test_seen")))
    answered = {True: 0, False: 0}
    for split in ("train", "test_seen"):
        for klass, count in run.answered[("T04", split)].items():
            answered[klass] += count
    assert answered[True] and answered[False]


def test_a_constant_value_is_not_a_validity_test():
    """§1.6's explicit non-rule, checked where deleting it would delete a class.

    A dry day is a flat zero on the roof's whole efflux column, and it is the
    "no" class T04's balance needs. Rejecting constant days would remove 217 of
    289 `Kies_Efflux` days (`findings.md`), so the sampled dry days must survive
    every predicate.
    """
    run = asyncio.run(generate(templates=_offline("T04"), splits=("train",)))
    dry = [item for item in run.instances if item.answer is False]
    assert dry, "no dry day survived, so the 'no' class was filtered away"
    for item in dry:
        params = item.case["inputs"]["params"]
        requirement = TEMPLATES["T04"].requires(params, datetime.fromisoformat(
            item.case["inputs"]["as_of"]
        ))
        assert _judge(requirement[0]) == ()


# --- The abstention share, and the ledger it falls out of ----------------------


def test_the_abstention_share_is_what_the_catalog_states():
    """13.0 / 12.8 / 28.6 / 16.0 %, per §1.6 — a consequence, never a target.

    The registry has no mechanism that could steer this: the share is
    `abstentions ÷ n` over `templates × m`, so it moves only when a template is
    authored or retired. That is §1.7's derivation rule, and testing the stated
    number is what keeps the two in step.
    """
    share = abstention_share()
    assert round(share["train"] * 100, 1) == 13.0
    assert round(share["test_seen"] * 100, 1) == 12.8
    assert round(share["test_unseen"] * 100, 1) == 28.6
    assert round(share["overall"] * 100, 1) == 16.0


def test_the_ledger_is_templates_times_m():
    """§1.7's sizing table, recomputed rather than copied: 25×4, 25×5, 7×8."""
    table = ledger()
    assert table["train"] == {"templates": 25, "m": 4, "n": 100, "abstentions": 13}
    assert table["test_seen"] == {"templates": 25, "m": 5, "n": 125, "abstentions": 16}
    assert table["test_unseen"] == {"templates": 7, "m": 8, "n": 56, "abstentions": 16}
    assert sum(row["n"] for row in table.values()) == 281


def test_two_of_the_abstentions_come_from_t24as_variant_axis():
    """The ledger's only per-instance abstention entry (§1.7).

    Five whole templates abstain; T24a's non-modellable variant contributes two
    more as a stratified slice of one template's `variant` axis, which is why the
    template ledger is untouched by them.
    """
    whole = {"T17a", "T17b", "T18a", "T18b", "T27"}
    assert {name for name, t in TEMPLATES.items() if t.abstains} == whole
    assert not TEMPLATES["T24a"].abstains
    assert TEMPLATES["T24a"].abstentions_in("train") == 1
    assert TEMPLATES["T24a"].abstentions_in("test_seen") == 1
    assert TEMPLATES["T24a"].abstentions_in("test_unseen") == 0


def test_the_holdout_carries_the_seven_templates_the_catalog_names():
    assert {name for name, t in TEMPLATES.items() if t.splits == frozenset({"test_unseen"})} == {
        "T16b",
        "T17b",
        "T18b",
        "T20",
        "T22",
        "T23",
        "T26",
    }


# --- The registry itself -------------------------------------------------------


def test_every_template_has_an_oracle_and_every_oracle_a_template():
    """Keyed alike, so a template with no oracle fails at generation, not at emission."""
    assert sorted(TEMPLATES) == sorted(ORACLES)


def test_every_gold_card_resolves_in_the_store():
    assert_cards_exist()


def test_every_bool_template_the_catalog_names_is_balanced():
    """Eight in train and two more in the holdout, which is why train's `m` is even."""
    balanced = {name for name, t in TEMPLATES.items() if t.balanced}
    assert balanced == {"T04", "T07", "T09", "T11", "T12", "T13", "T16a", "T16b", "T20", "T25"}
    assert len([n for n in balanced if "train" in TEMPLATES[n].splits]) == 8
    assert M["train"] % 2 == 0


def test_family_h_takes_its_pool_from_the_sampled_variant():
    """§1.8's H rows: P1 on a state pair, P1f on a flux pair, P2 on the overlay.

    Per variant rather than per template, because a plot is only as modellable as
    its most demanding series and only as widely instrumented as its narrowest.
    """
    assert t24a_roof_pool({"variant": MEASURED_PAIR, "table": "swc"}) == "P1"
    assert t24a_roof_pool({"variant": MEASURED_PAIR, "table": "outflow"}) == "P1f"
    assert t24a_roof_pool({"variant": MODEL_OVERLAY}) == "P2"
    assert t24a_roof_pool({"variant": NON_MODELLABLE_OVERLAY}) is None


def test_no_question_names_a_roof_by_its_raw_column():
    """§1.6's roof vocabulary, over every template's rendering of every draw shape.

    The rule binds on the *question*, not on the parameters, and the two come
    apart exactly where it matters: T27 and T24a(iii) draw `{alias}` from the
    spellings that reach the tools' scope table, and half of those are database
    columns. Rendering is exercised directly rather than through a generation
    run, because the rule is a property of the sketch and its substitutions — no
    oracle has to answer for a question to be checkable.
    """
    # §1.6's own three examples, one of each form the rule covers.
    assert {"extensiv1", "sumpf2", "qgravel"} <= raw_column_names()
    rng = Random(11)
    seen = 0
    for name, template in TEMPLATES.items():
        for _ in range(20):
            for fragment in template.strata("train") or ({},):
                try:
                    _, params = template.draw(rng, band_days(), fragment)
                except Exception:  # an undrawable shape renders nothing
                    continue
                question = template.render({**fragment, **params})
                assert mentions_raw_column(question) == (), f"{name}: {question}"
                seen += 1
    assert seen > 200


def test_the_alias_pool_is_full_of_the_spellings_the_rule_forbids_a_question():
    """Why the check above is not vacuous: the parameter and the text differ.

    `{alias}` is drawn from the intersection of the alias map and the tools'
    scope table, and that pool genuinely contains raw column names — so a
    renderer substituting the parameter verbatim would break §1.6 on roughly half
    its draws.
    """
    from eval.generation.templates import _declined_aliases

    pool = set(_declined_aliases())
    assert pool & raw_column_names()


def test_the_band_is_the_catalogs_own():
    days = band_days()
    assert days[0] == BAND_START == date(2025, 6, 1)
    assert days[-1] == BAND_END == date(2026, 4, 24)
    assert len(days) == 328


def test_as_of_carries_the_sites_offset_and_never_z():
    """The schema requires a numeric offset, and `connect_asof` owns the UTC step."""
    stamp = as_of_at(date(2025, 7, 15)).isoformat()
    assert stamp.endswith("+02:00")
    assert as_of_at(date(2026, 1, 15)).isoformat().endswith("+01:00")


# --- What the packet found and did not fix -------------------------------------


def test_t26s_cross_roof_answer_is_a_shape_the_case_schema_cannot_carry():
    """Two frozen surfaces disagree about the shape of an answer, and it is recorded.

    `_t26_cross_roof` answers **the winning roof's canonical name** — deliberate,
    since a signed gap would need a convention about which way round it is
    written — and the case schema's `answer` admits a boolean, a number, an
    ISO-day string or null. `"semi_intensive"` matches none of the four, so
    T26(ii) and T26(iii) raise `Unemittable` rather than resampling: every draw
    fails identically and the repair is a specification change in the schema or
    in the oracle, both frozen as of T107.

    Asserted against the schema directly, with no model run behind it, so the
    test states the collision rather than depending on a service to reproduce it.
    The day either surface moves, this is what says so.
    """
    envelope = case_envelope(
        TEMPLATES["T26"],
        case_id="T26-0001",
        as_of=BAND_CEILING,
        params={"variant": VARIANT_RAIN_CROSS_ROOF, "d": 3, "mm": 30.0, "offset": 1,
                "roof_a": "irrigated_extensive", "roof_b": "semi_intensive"},
        materialized={
            "status": "answered",
            "answer": "semi_intensive",  # what the oracle returns on (ii) and (iii)
            "unit": None,
            "pins": {},
        },
    )
    errors = validate(envelope)
    assert any("semi_intensive" in message for message in errors), errors

    # And the variant that does not compare roofs is unaffected: (i) answers a
    # boolean against the dry threshold, so the holdout keeps a live T26 probe.
    ok = case_envelope(
        TEMPLATES["T26"],
        case_id="T26-0002",
        as_of=BAND_CEILING,
        params={"variant": VARIANT_ALBEDO_AND_RAIN, "d": 3, "mm": 30.0, "offset": 1,
                "roof": "irrigated_extensive", "a": 0.6},
        materialized={"status": "answered", "answer": True, "unit": None, "pins": stamp()},
    )
    assert validate(ok) == ()
