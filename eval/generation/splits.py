"""The three splits: what each may draw, and how it is checked afterwards (T113).

``questions.md`` §1.7 states six generation constraints and is explicit that they
are **enforced in generation and not achieved by independent resampling**. That
distinction is this module: a split is a :class:`~eval.generation.templates.Pools`
handed to the draw loop, so a value belonging to the other split is never drawn
at all. Nothing here filters a case after the fact, and nothing re-rolls until a
constraint happens to hold.

**Two axes are cut, four are shared, and the cut is a stripe on both of them.**

* ``as_of`` — :func:`day_pools` deals the band out day by day, so each split holds
  every third day of the whole ~2025-06-01 → 2026-04-24 band. Disjoint but
  interleaved: a contiguous cut strands summer on one side and makes T02, T08 and
  T20 unbalanceable (§1.7).
* every **discrete value pool** — :attr:`Pools.parity` takes every other member by
  position, so train and test_seen share no ``thr``, ``month``, ``event``,
  ``alias``, ``mm``, ``a``, ``x``, ``tmax``, ``y``, ``d`` or ``offset``. Position
  is read off the *parameter's* ladder rather than off the pool at hand
  (:func:`~eval.generation.templates._striped`), because several of those names
  are fed by more than one pool and a value must fall on the same side through
  every one of them. A ``{period}`` is a continuum and is cut where it touches
  the day stripe: its end day is one of the split's own.
* **roof** is shared, deliberately: the §1.8 pools are 3–5 wide and confounded
  with modellability, so disjoint pools would confound roof generalization with
  tool availability.
* the **``variant``** axis is shared on the same footing, stratified inside a
  split and never resampled — a disjoint variant axis moves a whole probe into
  one split, which is what the holdout list is for.
* **language** is balanced 50/50 within each split and reported as a stratum
  (:mod:`eval.generation.paraphrases`).
* **paraphrase style pools** are disjoint between train and test, which is that
  module's half of the same memorization guard.

**The holdout takes every pool whole.** Its novelty is its templates: T16b, T20,
T22, T23 and the rest never appear in train at all, so a shared value carries no
answer with it. Cutting it a third stripe would thin every pool by a further
third — T20 has to reach both classes out of five horizons — and would confound a
template-transfer failure with an unseen parameter value, which is exactly what
"the b-side of a train-side pair moving one named axis" forbids.

The verification half of the module (:func:`sampled_values`,
:func:`parameter_values`, :func:`overlaps`, :func:`value_overlaps`,
:func:`stripe_report`) reads the *emitted cases* rather than the sampler, because
the exit criterion is a property of the suite and not of the code that wrote it.
It asks the disjointness question at **two** keys, and the coarser one is not
redundant: a parameter drawn from several pools can be disjoint inside every
template while a value sits on both sides of the cut (T138).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from eval.generation.templates import Pools, band_days

SPLITS: tuple[str, ...] = ("train", "test_seen", "test_unseen")
"""The three splits, in the order the stripe deals days to them."""

PARITY: Mapping[str, int | None] = {"train": 0, "test_seen": 1, "test_unseen": None}
"""Which half of every discrete value pool each split takes; ``None`` is the whole."""

SHARED_AXES: frozenset[str] = frozenset({"roof", "roof_a", "roof_b", "variant", "table"})
"""Parameters §1.7 shares between train and test_seen rather than cutting.

``roof`` and its pair form because the pools are narrow and confounded with
modellability; ``variant`` and ``table`` because they are the stratified axis and
name distinct probes rather than values of one quantity. ``alias`` is **not**
here: §1.7 lists it among the parameters that must be disjoint, and the stripe
keeps both non-modellable roofs on both sides because their spellings are
interleaved in the pool's own order.
"""


def day_pools(days: Sequence[date] | None = None) -> dict[str, tuple[date, ...]]:
    """The band dealt out to the three splits, one day at a time.

    **A stripe of one day is the finest there is, and that is the point.** What
    the interleaving protects against clusters on a multi-day scale — a storm, a
    heatwave, a sensor outage — so a stripe wider than that episode hands whole
    episodes to one split and brings back the seasonal imbalance a contiguous cut
    has. At stride 3 no split is ever more than two days from any event in the
    record, and each holds a third of every week and every month of the band.
    """
    band = tuple(days) if days is not None else band_days()
    return {
        split: tuple(
            day for index, day in enumerate(band) if SPLITS[index % len(SPLITS)] == split
        )
        for split in SPLITS
    }


def pools(days: Sequence[date] | None = None) -> dict[str, Pools]:
    """One :class:`~eval.generation.templates.Pools` per split — what generation takes."""
    return {
        split: Pools(days=pool, parity=PARITY[split])
        for split, pool in day_pools(days).items()
    }


# --- Verification, over the emitted cases ---------------------------------------


def _drawn(
    cases: Iterable[Mapping[str, Any]],
    key: Callable[[str, str], Any],
    ignore: frozenset[str],
) -> dict[Any, set[Any]]:
    """Every value each *key* took, read off the cases themselves.

    Unhashable values are stringified rather than dropped — a parameter that ever
    becomes a list is still a parameter a candidate could memorize.
    """
    values: dict[Any, set[Any]] = {}
    for case in cases:
        inputs = case["inputs"]
        for name, value in (inputs.get("params") or {}).items():
            if name in ignore:
                continue
            values.setdefault(key(inputs["template_id"], name), set()).add(
                value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
            )
    return values


def _shared(left: Mapping[Any, set[Any]], right: Mapping[Any, set[Any]]) -> dict[Any, set[Any]]:
    """The keys the two sides both reached, with the values they both reached.

    Only the keys that actually overlap are returned, so the whole report is
    ``{}`` when the constraint holds and names the offending key when it does not.
    """
    return {
        key: common
        for key, values in left.items()
        if (common := values & right.get(key, set()))
    }


def sampled_values(
    cases: Iterable[Mapping[str, Any]], *, ignore: frozenset[str] = SHARED_AXES
) -> dict[tuple[str, str], set[Any]]:
    """Every value each (template, parameter) took.

    Keyed by template because that is the unit *memorization* works in: a
    memorized constant is memorized for the template it was seen under.
    """
    return _drawn(cases, lambda template, name: (template, name), ignore)


def parameter_values(
    cases: Iterable[Mapping[str, Any]], *, ignore: frozenset[str] = SHARED_AXES
) -> dict[str, set[Any]]:
    """Every value each parameter took, across every template that drew it.

    The coarser key, and the one §1.7 states the rule in: "train ∩ test_seen = ∅
    on every sampled param". It is what :func:`sampled_values` cannot see — a
    parameter fed by several pools can be disjoint inside every template and
    shared across them, which is the shape of the ``{d}`` defect (T138).
    """
    return _drawn(cases, lambda template, name: name, ignore)


def overlaps(
    train: Iterable[Mapping[str, Any]], test_seen: Iterable[Mapping[str, Any]]
) -> dict[tuple[str, str], set[Any]]:
    """Per-(template, parameter) values the two splits share — ``{}`` is the rule kept."""
    return _shared(sampled_values(train), sampled_values(test_seen))


def value_overlaps(
    train: Iterable[Mapping[str, Any]], test_seen: Iterable[Mapping[str, Any]]
) -> dict[str, set[Any]]:
    """Per-parameter values the two splits share — the stripe read as a property of the value.

    Strictly stronger than :func:`overlaps`, and the check that would have caught
    ``{d}``: five horizon pools, each striped over itself, each internally
    disjoint, and ``d = 3`` on train's side through T15b while it was on
    test_seen's through T09, T13, T14 and T21 (``decisions.md § Value pools are
    striped, and the holdout takes them whole``, the validity condition).
    """
    return _shared(parameter_values(train), parameter_values(test_seen))


def as_of_days(cases: Iterable[Mapping[str, Any]]) -> set[date]:
    """The site days the cases were cut at, as dates."""
    return {datetime.fromisoformat(case["inputs"]["as_of"]).date() for case in cases}


def stripe_report(by_split: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, Any]:
    """Evidence that ``as_of`` is striped rather than cut, from the cases alone.

    Three numbers per split, and each one distinguishes a stripe from a cut: the
    span (a cut split covers part of the band, a striped one covers nearly all of
    it), the longest run of consecutive days it holds (a cut has one long run, a
    stripe has none longer than a day), and the number of calendar months it
    touches (a cut misses whole months, a stripe touches every month it can
    reach).
    """
    report: dict[str, Any] = {}
    for split, cases in by_split.items():
        days = sorted(as_of_days(cases))
        if not days:
            report[split] = {"n": 0}
            continue
        longest = run = 1
        for earlier, later in zip(days, days[1:]):
            run = run + 1 if (later - earlier).days == 1 else 1
            longest = max(longest, run)
        report[split] = {
            "days": len(days),
            "first": days[0].isoformat(),
            "last": days[-1].isoformat(),
            "longest_consecutive_run": longest,
            "months": len({(day.year, day.month) for day in days}),
        }
    return report


def language_stratum(
    by_split: Mapping[str, Iterable[Mapping[str, Any]]]
) -> dict[str, dict[str, int]]:
    """The EN/DE counts per split, reported because DE routing accuracy is measured."""
    return {
        split: {
            language: sum(
                1 for case in cases if case["inputs"].get("language") == language
            )
            for language in ("en", "de")
        }
        for split, cases in ((split, list(cases)) for split, cases in by_split.items())
    }


__all__ = [
    "PARITY",
    "SHARED_AXES",
    "SPLITS",
    "as_of_days",
    "day_pools",
    "language_stratum",
    "overlaps",
    "parameter_values",
    "pools",
    "sampled_values",
    "stripe_report",
    "value_overlaps",
]
