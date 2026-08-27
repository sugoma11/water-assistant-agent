"""The three splits, and the constraints §1.7 says are enforced in generation (T113).

**Per-parameter disjointness is asserted here, not inspected.** Two ways, because
they fail differently: exhaustively over the *samplers* — 200 draws per template
per split, which reaches every member of every value pool — and over the
*emitted cases* of a real generation pass, which is the property the suite
actually has. The first would pass if a template were never instantiated; the
second would pass if a pool were never fully explored.

The generation passes here use the templates that answer from the pinned database
alone, so this file needs no live service. The model families are covered by the
sampler half, which is where their parameters are cut.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import date, timedelta
from functools import lru_cache
from random import Random
from typing import Any

import pytest

from eval.generation import splits as S
from eval.generation.instantiate import daily_rain, event_draw, generate
from eval.generation.templates import (
    BAND_END,
    BAND_START,
    FORWARD_HORIZONS,
    HEATWAVE_HORIZONS,
    HORIZONS,
    HOT_DAY_THRESHOLDS,
    MOISTURE_LEVELS,
    MOISTURE_THRESHOLDS,
    OVERRIDE_HORIZONS,
    PAST_HORIZONS,
    RAIN_HORIZONS,
    RAIN_THRESHOLDS,
    STATED_MOISTURE,
    TEMPLATES,
    THRESHOLDS,
    Template,
    Undrawable,
    _complete_months,
    _months,
    _striped,
    as_of_at,
    band_days,
    rain_events,
)
from harness.run_case import make_case_context

OFFLINE = ("T01", "T02", "T03", "T04", "T05", "T12", "T15a", "T24b")
"""Templates whose oracle reads the pinned database and nothing else.

Enough to exercise every retrospective draw shape the cut touches — a month, a
14-day period, a single day, a rain event — without a live GR2L or a weather
fetch, which is what keeps this file's generation passes runnable anywhere.
"""


@lru_cache(maxsize=1)
def _bound() -> dict[str, Template]:
    """The registry with T12's event pool bound to the record, as generation binds it."""
    ctx = make_case_context(as_of_at(band_days()[-1]), allow_live=False)
    events = rain_events(asyncio.run(daily_rain(ctx)))
    return {**TEMPLATES, "T12": dataclasses.replace(TEMPLATES["T12"], draw=event_draw(events))}


def _drawn(split: str, draws: int = 200) -> dict[tuple[str, str], set[Any]]:
    """Every value each (template, parameter) can take in *split*, by sampling it out.

    No oracle and no filter: what is under test is the pool a split may draw from,
    and a value rejected by a predicate is still a value the other split must not
    see.
    """
    rng = Random(f"disjointness:{split}")
    pools = S.pools()[split]
    values: dict[tuple[str, str], set[Any]] = {}
    for name, template in sorted(_bound().items()):
        if split not in template.splits:
            continue
        for fragment in template.strata(split) or ({},):
            for _ in range(draws):
                try:
                    _, params = template.draw(rng, pools, fragment)
                except Undrawable:
                    continue
                for key, value in {**fragment, **params}.items():
                    values.setdefault((name, key), set()).add(
                        value if isinstance(value, (str, int, float, bool)) else str(value)
                    )
    return values


# --- as_of: a stripe, never a cut -------------------------------------------------


def test_the_day_pools_partition_the_band():
    """Three disjoint pools whose union is the band, so no day is lost or shared."""
    pools = S.day_pools()
    band = set(band_days())
    assert set().union(*pools.values()) == band
    assert sum(len(pool) for pool in pools.values()) == len(band)
    for left in pools:
        for right in pools:
            if left != right:
                assert not (set(pools[left]) & set(pools[right]))


def test_no_split_holds_two_consecutive_days():
    """The definition of a stripe at stride 3, and what a cut point cannot satisfy.

    A contiguous split has one run 100 days long; this has none longer than a
    single day, so nothing in the record — a storm, a heatwave, an outage — can
    fall entirely inside one split's ``as_of`` pool.
    """
    for split, pool in S.day_pools().items():
        assert all(
            (later - earlier).days > 1 for earlier, later in zip(pool, pool[1:])
        ), split


def test_every_split_spans_the_whole_band_and_every_month_of_it():
    """§1.7's reason for striping: a contiguous cut strands summer on one side.

    Each pool reaches from the first days of the band to the last and touches all
    11 calendar months the band contains, so T02, T08 and T20 can balance on
    either side of the cut.
    """
    for split, pool in S.day_pools().items():
        assert pool[0] <= BAND_START + timedelta(days=2)
        assert pool[-1] >= BAND_END - timedelta(days=2)
        assert len({(day.year, day.month) for day in pool}) == 11
        assert 105 <= len(pool) <= 115


# --- The value stripe ---------------------------------------------------------------


def test_a_value_pool_is_striped_rather_than_cut_at_a_point():
    """Both sides span the same range, which a median cut would destroy.

    A pool cut at its middle gives train the low thresholds and test_seen the
    high ones — the split would then be confounded with the difficulty of the
    draw, and a bool template could have one class out of reach on one side.
    """
    train, seen, holdout = (S.pools()[split] for split in S.SPLITS)
    left, right = train.of(MOISTURE_THRESHOLDS), seen.of(MOISTURE_THRESHOLDS)
    assert not (set(left) & set(right))
    assert set(left) | set(right) == set(MOISTURE_THRESHOLDS)
    assert min(left) == min(MOISTURE_THRESHOLDS)
    assert max(right) >= max(MOISTURE_THRESHOLDS) - 3
    assert holdout.of(MOISTURE_THRESHOLDS) == MOISTURE_THRESHOLDS


def test_the_holdout_takes_every_pool_whole():
    """Its novelty is its templates, so a shared value carries no answer with it.

    A third stripe would thin every pool by another third — T20 has to reach both
    classes out of five horizons — and would confound a template-transfer failure
    with an unseen parameter value, which is not the one axis a holdout entry is
    supposed to move.
    """
    holdout = S.pools()["test_unseen"]
    assert holdout.parity is None
    assert holdout.of(FORWARD_HORIZONS) == FORWARD_HORIZONS


def test_the_month_stripe_is_a_property_of_the_month():
    """The defect T113 found in its own output, as a standing regression test.

    Striping each table's month list by position put ``2025-07`` at index 2 of
    ``outflow``'s eleven months (train) and index 11 of ``swc``'s twenty
    (test_seen), so T24a — which draws from both, by variant — had one parameter
    overlapping between the splits. The stripe is now taken over the widest list
    and narrowed afterwards.
    """
    train, seen = S.pools()["train"], S.pools()["test_seen"]
    for table in ("swc", "outflow"):
        assert set(_months(table, train)).issubset(_complete_months(table))
        assert set(_months(table, seen)).issubset(_complete_months(table))
    # Every month a split can draw, through either table, on one side only.
    left = set(_months("swc", train)) | set(_months("outflow", train))
    right = set(_months("swc", seen)) | set(_months("outflow", seen))
    assert not (left & right)
    assert "2025-07" in left or "2025-07" in right


LADDERED: dict[str, tuple[tuple[Any, ...], ...]] = {
    "d": (FORWARD_HORIZONS, RAIN_HORIZONS, PAST_HORIZONS, HEATWAVE_HORIZONS, OVERRIDE_HORIZONS),
    "thr": (HOT_DAY_THRESHOLDS, MOISTURE_THRESHOLDS, RAIN_THRESHOLDS),
    "x": (STATED_MOISTURE, MOISTURE_THRESHOLDS),
}
"""The three parameters the catalog feeds from more than one pool, and their pools.

Written out rather than derived, because what is under test is that the ladder in
:mod:`eval.generation.templates` covers every pool a draw site actually passes it:
a registry derived from the same constants would agree with itself.
"""

LADDERS: dict[str, tuple[Any, ...]] = {"d": HORIZONS, "thr": THRESHOLDS, "x": MOISTURE_LEVELS}


@pytest.mark.parametrize("param", sorted(LADDERED))
def test_a_multi_pool_parameter_is_striped_over_its_ladder(param: str):
    """T138's defect, as a standing regression test beside the month one.

    ``{d}`` reached five horizon lists, each striped over itself and each
    internally disjoint, and the same day landed on different sides through
    different lists: ``d = 3`` was train's through T15b's pool and test_seen's
    through the forward one, so train ∩ test_seen on ``{d}`` was ``{3, 4, 5, 6}``
    in a suite every per-template check passed. The stripe is now taken over the
    parameter's ladder and narrowed to the pool afterwards.
    """
    train, seen, holdout = (S.pools()[split] for split in S.SPLITS)
    ladder = LADDERS[param]
    left: set[Any] = set()
    right: set[Any] = set()
    for pool in LADDERED[param]:
        assert set(pool) <= set(ladder), "the ladder does not cover this pool"
        drawn = {side: set(_striped(pool, ladder, side)) for side in (train, seen)}
        # Neither side may be emptied: a pool one split cannot draw from is a cut,
        # not a stripe, and PAST_HORIZONS is the one this nearly happened to.
        assert len(drawn[train]) >= 2 and len(drawn[seen]) >= 2
        assert not (drawn[train] & drawn[seen])
        assert drawn[train] | drawn[seen] == set(pool)
        assert _striped(pool, ladder, holdout) == tuple(pool)
        left |= drawn[train]
        right |= drawn[seen]
    assert not (left & right)


def test_the_ladders_keep_a_pools_own_value_object():
    """``8.0`` stays a float, so the emitted JSON does not change shape.

    A ladder is a set union and deduplicates ``8`` against ``8.0`` — either may
    survive that — so the stripe reads *position* off the ladder and the value
    off the pool.
    """
    drawn = _striped(STATED_MOISTURE, MOISTURE_LEVELS, S.pools()["train"])
    assert drawn and all(isinstance(value, float) for value in drawn)


# --- Per-parameter disjointness, over the samplers ------------------------------------


def test_no_sampled_parameter_can_be_drawn_by_both_splits():
    """§1.7's hard constraint, exhaustively over the pools rather than over a run.

    200 draws per template per split reaches every member of every value pool, so
    a parameter that *could* be shared is shared here. Without this the
    memorized-constant detector fails open: the reflective record carries the gold
    answers, so a candidate can accrete a constant and only disjoint values
    detect it.
    """
    train, seen = _drawn("train"), _drawn("test_seen")
    overlaps = {
        key: train[key] & seen[key]
        for key in train.keys() & seen.keys()
        if key[1] not in S.SHARED_AXES and (train[key] & seen[key])
    }
    assert overlaps == {}


def test_no_parameter_value_reaches_both_splits_through_different_templates():
    """§1.7's rule at the key it is written in: "∅ on every sampled param" (T138).

    Strictly stronger than the test above, and the one that fails on the defect
    that test cannot see: five pools feeding ``{d}``, each striped over itself,
    each internally disjoint, and ``d = 3`` on both sides of the cut. ``{thr}``
    was one draw away from the same thing at 25.
    """
    train, seen = _drawn("train"), _drawn("test_seen")

    def by_param(values: dict[tuple[str, str], set[Any]]) -> dict[str, set[Any]]:
        merged: dict[str, set[Any]] = {}
        for (_, param), drawn in values.items():
            if param not in S.SHARED_AXES:
                merged.setdefault(param, set()).update(drawn)
        return merged

    left, right = by_param(train), by_param(seen)
    overlaps = {
        param: shared
        for param, values in left.items()
        if (shared := values & right.get(param, set()))
    }
    assert overlaps == {}
    assert {"d", "thr", "x"} <= left.keys(), "the multi-pool parameters are drawn"


def test_the_disjointness_check_would_see_an_overlap():
    """Not vacuous: the same comparison on the shared axes finds plenty.

    Roof and the stratified `variant` are shared deliberately, so they overlap by
    construction — which is what shows the assertion above is comparing something.
    """
    train, seen = _drawn("train"), _drawn("test_seen")
    shared = {
        key: train[key] & seen[key]
        for key in train.keys() & seen.keys()
        if key[1] in S.SHARED_AXES
    }
    assert any(shared.values())
    assert shared[("T09", "roof")]
    assert shared[("T24a", "variant")]


def test_every_parameter_the_catalog_names_is_actually_cut():
    """§1.7 names six by hand; each is checked to be present *and* disjoint.

    A parameter that stopped being sampled would pass the disjointness test above
    by absence, and this is what says it did not.
    """
    train, seen = _drawn("train"), _drawn("test_seen")
    for key in (
        ("T02", "thr"),
        ("T09", "thr"),
        ("T13", "thr"),
        ("T01", "month"),
        ("T05", "month"),
        ("T12", "event"),
        ("T21", "mm"),
        ("T27", "d"),
        ("T27", "alias"),
    ):
        assert len(train[key]) > 1, key
        assert len(seen[key]) > 1, key
        assert not (train[key] & seen[key]), key


def test_both_non_modellable_roofs_survive_the_alias_stripe():
    """`{alias}` is cut, and the roofs behind it are not.

    T27 probes modellability as a property of the roof, so a stripe that gave
    train the gravel spellings and test_seen the wetland ones would turn a value
    cut into a roof cut. The two roofs' spellings interleave in the pool's own
    order, so both survive on both sides.
    """
    from water_assistant_agent.assistant.tools.roofs import resolve_roof

    train, seen = _drawn("train"), _drawn("test_seen")
    for values in (train[("T27", "alias")], seen[("T27", "alias")]):
        roofs = {resolve_roof(str(value)).name for value in values}
        assert roofs == {"gravel", "wetland"}


# --- Per-parameter disjointness, over an emitted suite ---------------------------------


@lru_cache(maxsize=1)
def _offline_run():
    """One generation pass over :data:`OFFLINE`, bound to a context that cannot fetch.

    ``allow_live=False`` is not caution, it is the claim: these templates answer
    from the pinned database, so a run that reached the network would fail here
    rather than quietly recording a cache entry the capture pass (T116) owns.
    """
    contexts: dict[Any, Any] = {}

    def context_for(as_of):
        if as_of not in contexts:
            contexts[as_of] = make_case_context(as_of, allow_live=False)
        return contexts[as_of]

    return asyncio.run(
        generate(
            templates={name: TEMPLATES[name] for name in OFFLINE},
            context_for=context_for,
        )
    )


def test_a_generated_suite_carries_no_parameter_overlap():
    """The same rule as a property of the cases, which is where it has to hold.

    `overlaps` reads `inputs.params` — the field §6.1 carries so that
    per-parameter disjointness is checkable after the fact — so this is the check
    a reviewer can run against the committed files without trusting the sampler.
    """
    run = _offline_run()
    train, seen = run.for_split("train"), run.for_split("test_seen")
    assert train and seen
    assert S.overlaps(train, seen) == {}
    assert S.value_overlaps(train, seen) == {}
    assert run.report()["parameter_overlaps"] == {}
    assert run.report()["value_overlaps"] == {}
    # And the values are there to overlap: every one of these templates writes a
    # parameter into every case it produced.
    values = S.sampled_values(train)
    assert {key[1] for key in values} >= {"month", "period", "date", "event"}


def test_every_case_carries_an_as_of_from_its_own_stripe():
    """The exact form of "`as_of` is a striped partition, never a cut point".

    Checked per case rather than per pool: a run that ignored the pools it was
    handed would still produce a plausible-looking date range.
    """
    run = _offline_run()
    pools = S.day_pools()
    for split in ("train", "test_seen"):
        days = S.as_of_days(run.for_split(split))
        assert days
        assert days <= set(pools[split]), split


def test_the_as_of_report_distinguishes_a_stripe_from_a_cut():
    """The three numbers a reviewer reads: span, longest run, months touched.

    A contiguous split of the same size would show one long run and a handful of
    adjacent months; a stripe shows runs of one and months spread over the band.
    The span is a property of the pool rather than of a 32-case sample of it, and
    is asserted on the pool above.
    """
    run = _offline_run()
    report = S.stripe_report(
        {split: run.for_split(split) for split in ("train", "test_seen")}
    )
    for split in ("train", "test_seen"):
        assert report[split]["longest_consecutive_run"] == 1, split
        assert report[split]["months"] >= 6, split
        assert (
            date.fromisoformat(report[split]["last"])
            - date.fromisoformat(report[split]["first"])
        ) > timedelta(days=180), split


def test_t12_holds_disjoint_event_sets_in_train_and_test_seen():
    """The catalog's claim that eleven qualifying events cover 4 + 5 with two spare.

    T12's event pool is the record's rather than a value list, so this is the one
    cut that could fail on the data instead of on the code: the stripe has to
    leave each side enough *qualifying* events, and qualification is the filter's
    and the oracle's to decide.
    """
    run = _offline_run()
    events = {
        split: {
            case["inputs"]["params"]["event"]
            for case in run.for_split(split)
            if case["inputs"]["template_id"] == "T12"
        }
        for split in ("train", "test_seen")
    }
    assert events["train"] and events["test_seen"]
    assert not (events["train"] & events["test_seen"])
    assert run.balance("T12")["train"] == {True: 2, False: 2}
    assert sorted(run.balance("T12")["test_seen"].values()) == [2, 3]


def test_roof_is_shared_between_the_splits_deliberately():
    """§1.7: disjoint roof pools would confound roof generalization with tool scope.

    The §1.8 pools are 3–5 wide and confounded with modellability, so the axis is
    shared — and it has to be *seen* to be shared, or the claim is only in prose.
    """
    run = _offline_run()
    roofs = {
        split: {
            case["inputs"]["params"].get("roof")
            for case in run.for_split(split)
            if case["inputs"]["params"].get("roof")
        }
        for split in ("train", "test_seen")
    }
    assert roofs["train"] & roofs["test_seen"]


def test_language_is_reported_as_a_stratum():
    """A measured quantity has to be in the report, not only in the plan."""
    run = _offline_run()
    stratum = run.report()["languages"]
    for split in ("train", "test_seen"):
        counts = stratum[split]
        assert counts["en"] and counts["de"]
        assert abs(counts["en"] - counts["de"]) <= len(OFFLINE)


def test_the_ledger_still_holds_under_the_cut():
    """Every template fills its `m` from its own stripe, which is the risk the cut adds.

    A pool cut in half can be a pool too small — T01's months and T12's events are
    the two that come closest — so the sizes are re-asserted after the cut rather
    than assumed to survive it.
    """
    run = _offline_run()
    for split, m in (("train", 4), ("test_seen", 5)):
        cases = run.for_split(split)
        assert len(cases) == m * len(OFFLINE)
        per_template = {name: 0 for name in OFFLINE}
        for case in cases:
            per_template[case["inputs"]["template_id"]] += 1
        assert set(per_template.values()) == {m}


@pytest.mark.parametrize("split", ["train", "test_seen"])
def test_case_ids_are_unique_and_carry_their_template(split: str):
    """Two splits draw the same template, and the instance numbers may not collide."""
    run = _offline_run()
    ids = [case["inputs"]["case_id"] for case in run.for_split(split)]
    assert len(ids) == len(set(ids))
    for case in run.for_split(split):
        assert case["inputs"]["case_id"].startswith(case["inputs"]["template_id"] + "-")
