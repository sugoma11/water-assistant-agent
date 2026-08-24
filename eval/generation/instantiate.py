"""The draw loop: sample, filter, answer, and reject until the bools balance.

One instance is one pass of :func:`draw_one`, and everything §1.6 asks of a case
happens inside it, in this order:

1. **Draw** an ``as_of`` day from the split's pool and the template's parameters
   from it. A day that admits no draw — a September ``as_of`` with no completed
   February behind it — is an :class:`~eval.generation.templates.Undrawable`, not
   a rejection.
2. **Filter the inputs** through :mod:`eval.generation.filters`, over the
   ``(table, column, window)`` set the template declares for that draw and
   through the case's own as-of view.
3. **Assert the case invariants** T104 wrote — the roof pool and every period
   parameter against ``as_of``. The harness checks these at run time; a generator
   that emitted a case failing one has produced a case nothing can score.
4. **Answer** through :data:`eval.oracles.ORACLES`. An
   :class:`~eval.oracles.base.OracleInputError` is §1.6's *oracle-validity* half —
   a draw with more than one defensible reading — and is resampled exactly like a
   failed predicate. Four of them are handed over by T110 as expected rather than
   as defects: T08 and T20 refuse a heatwave run crossing the window's edge, T23 a
   window whose store has saturated, T25 a draw its two gold routes answer
   differently, and T26 a tie.
5. **Balance**, on the bool templates only: a draw whose answer class is already
   full inside this split is put back.

**Nothing here steers the abstention share.** It is a consequence of the template
ledger (:func:`~eval.generation.templates.abstention_share`) and is reported by
:func:`GenerationRun.report`, never sampled towards.

**Failing a predicate resamples the parameters and never drops the template**, so
§1.7's ``templates × m`` holds by construction: a template that cannot fill its
``m`` raises :class:`Exhausted` rather than quietly emitting fewer cases.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from random import Random
from typing import Any, Callable

from jsonschema import Draft202012Validator

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.site import site_day_expr

from eval.generation import filters
from eval.generation.templates import (
    TEMPLATES,
    Draw,
    Template,
    Undrawable,
    after_window,
    as_of_at,
    band_days,
    rain_events,
    roof_pool_for,
)
from eval.oracles import ORACLES
from eval.oracles.base import OracleInputError
from harness.assertions import CaseAssertionError, assert_case, pool_members
from harness.run_case import make_case_context

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = REPO_ROOT / "eval" / "schema" / "case.schema.json"

MAX_ATTEMPTS = 400
"""Draws allowed per instance before a template is declared unfillable.

Sized off the tightest case in the catalog rather than picked round: T04 needs
roughly 6:1 oversampling of wet days for its "yes" class (``findings.md``
§ Zero-outflow days dominate the band), and the wetland's 17 non-zero days in the
whole band make its own tail far longer than that. A ceiling this high costs
nothing on a template that fills in four draws and turns "the pool is too tight"
into a loud failure instead of a short split.
"""


class Exhausted(RuntimeError):
    """A template could not fill its ``m`` inside :data:`MAX_ATTEMPTS`.

    Raised rather than returning fewer cases, because §1.7's ``n`` is
    ``templates × m`` and a split that is quietly one case short makes every
    share in the catalog wrong by an amount nothing reports.
    """


class Unemittable(RuntimeError):
    """The oracle answered, and the case schema cannot carry what it answered.

    Not a rejection and deliberately not resampled: every draw of the template
    fails identically, so putting it back 400 times and then reporting "the pool
    is too tight" would name the wrong cause. Two frozen surfaces disagree about
    the shape of an answer, and the fix is a specification change in one of them
    rather than a different draw.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Rejected:
    """One draw that did not become a case, and what stopped it."""

    template_id: str
    split: str
    reason: str
    """``coverage`` | ``plausibility`` | ``frozenness`` | ``oracle`` | ``balance``
    | ``undrawable`` | ``invariant``."""

    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class Instance:
    """One accepted case and the split it was drawn for.

    The split rides *beside* the case rather than inside it: ``inputs`` and
    ``expectations`` are the whole of §6.1's envelope, and a third key would be a
    field the schema does not admit and a scorer could read.
    """

    split: str
    case: dict[str, Any]

    @property
    def template_id(self) -> str:
        return str(self.case["inputs"]["template_id"])

    @property
    def answer(self) -> Any:
        return self.case["expectations"]["answer"]

    @property
    def abstains(self) -> bool:
        return self.case["expectations"]["status"] == "not_available"


@dataclasses.dataclass
class GenerationRun:
    """Everything one generation pass produced, cases and evidence alike.

    The rejections are kept rather than counted away: the filters' specificity is
    a measured property (``findings.md`` § Validity-predicate specificity), and a
    run that cannot say which predicate did the work cannot show it was the right
    one.
    """

    instances: list[Instance] = dataclasses.field(default_factory=list)
    rejections: list[Rejected] = dataclasses.field(default_factory=list)
    attempts: dict[tuple[str, str], int] = dataclasses.field(default_factory=dict)
    answered: dict[tuple[str, str], dict[bool, int]] = dataclasses.field(
        default_factory=dict
    )
    """Every draw a bool template *answered*, by class — accepted or balance-rejected.

    The class prior of the pool, which is a different number from
    :meth:`oversampling` and the one §1.6 quotes. T04's minority class needs
    roughly 6:1 (:meth:`draws_per_class`); the total draws per accepted case run
    higher, because once a class fills, every further draw of it is put back.
    """

    def for_split(self, split: str) -> list[dict[str, Any]]:
        """The cases this run produced for *split*, in the order they were drawn."""
        return [item.case for item in self.instances if item.split == split]

    def balance(self, template_id: str) -> dict[str, dict[bool, int]]:
        """Per split, how the answers of *template_id* fell across the two classes."""
        table: dict[str, dict[bool, int]] = {}
        for item in self.instances:
            if item.template_id != template_id or not isinstance(item.answer, bool):
                continue
            table.setdefault(item.split, {True: 0, False: 0})[item.answer] += 1
        return table

    def oversampling(self, template_id: str, split: str) -> float:
        """Draws made per case accepted — the ratio §1.6 predicts at roughly 6:1 for T04."""
        accepted = sum(
            1
            for item in self.instances
            if item.template_id == template_id and item.split == split
        )
        return self.attempts.get((template_id, split), 0) / accepted if accepted else 0.0

    def draws_per_class(self, template_id: str, split: str) -> dict[bool, float]:
        """Answered draws per draw of each class — the pool's own prior.

        ``{True: 6.4}`` reads "one wet day in every 6.4 draws that reached an
        answer", which is the oversampling §1.6 states for T04 and is a property of
        the record rather than of the sampler.
        """
        counts = self.answered.get((template_id, split), {})
        total = sum(counts.values())
        return {
            klass: (total / count if count else float("inf"))
            for klass, count in counts.items()
        }

    def report(self) -> dict[str, Any]:
        """The numbers a generation pass is checked by, in one object."""
        reasons: dict[str, int] = {}
        for rejection in self.rejections:
            reasons[rejection.reason] = reasons.get(rejection.reason, 0) + 1
        splits = sorted({item.split for item in self.instances})
        return {
            "n": {split: len(self.for_split(split)) for split in splits},
            "abstentions": {
                split: sum(
                    1 for item in self.instances if item.split == split and item.abstains
                )
                for split in splits
            },
            "rejections": reasons,
            "attempts": sum(self.attempts.values()),
        }


def case_envelope(
    template: Template,
    *,
    case_id: str,
    as_of: datetime,
    params: Mapping[str, Any],
    materialized: Mapping[str, Any],
) -> dict[str, Any]:
    """One case's two halves — ``inputs`` and ``expectations`` — assembled.

    The split §6.1 draws: everything in ``expectations`` that could disagree with
    the tools is *computed* (the answer, the unit, the pins, and here the status),
    and everything that is a per-template constant is copied in unchanged. The
    only per-draw constant is family H's ``argument_checks``, which follow the
    sampled variant's series shape and are the template's own function of the
    parameters rather than an oracle's output.

    ``language`` is ``en`` and ``question`` is the canonical rendering. T112 owns
    the paraphrases, the EN/DE balance and the style pools; what this writes is
    the one unambiguous form a paraphrase is a paraphrase *of*.
    """
    inputs = {
        "question": template.render(params),
        "as_of": as_of.isoformat(),
        "case_id": case_id,
        "template_id": template.template_id,
        "params": dict(params),
        "language": "en",
    }
    expectations: dict[str, Any] = {
        **materialized,
        "answer_metric": template.answer_metric,
        "expected_tool_calls": [
            dict(call) for call in template.expected_tool_calls(params)
        ],
        "must_not_tools": list(template.must_not_tools),
        "gold_cards": list(template.gold_cards),
        "argument_checks": [dict(check) for check in template.argument_checks(params)],
    }
    # A tolerance sits exactly where the answer is a number: the schema requires
    # one there and admits none elsewhere, and a null answer carrying a vacuous
    # `exact` would say the answer metric compares something.
    if template.tolerance is not None and isinstance(
        expectations["answer"], (int, float)
    ):
        expectations["tolerance"] = dict(template.tolerance)
    return {"inputs": inputs, "expectations": expectations}


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))


def validate(case: Mapping[str, Any]) -> tuple[str, ...]:
    """Schema errors in *case*, as messages — empty when it would survive T010's schema."""
    payload = {key: case[key] for key in ("inputs", "expectations")}
    return tuple(error.message for error in _validator().iter_errors(payload))


async def daily_rain(ctx: ScenarioContext) -> dict[date, float]:
    """Station rain per site day, through the case's own as-of view.

    T12's event pool is a fact about the pinned record rather than a value pool,
    so it is read once per run and the events are enumerated from it.
    """
    day = site_day_expr()
    result = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT {day} AS day, sum("Rain") FROM wetter GROUP BY 1',  # noqa: S608 - fixed identifiers
    )
    return {row[0]: float(row[1] or 0.0) for row in result.rows}


def event_draw(events: Sequence[tuple[date, date]]) -> Draw:
    """T12's draw, bound to the record's own event windows.

    The window is the parameter (``questions.md`` §2 T12), so the case file
    carries the days the answer was computed over and
    ``period_param_within_as_of`` sees both of its ends. Every candidate event is
    offered: coverage is the filter's to reject and a retention outside [0, 1] is
    the oracle's, which is what keeps the qualification rule in one place instead
    of two.
    """

    def build(rng: Random, fixed: Mapping[str, Any]) -> tuple[dict[str, Any], date]:
        if not events:
            raise Undrawable("the record holds no rain event of the catalog's depth")
        start, end = rng.choice(list(events))
        return (
            {
                "roof": rng.choice(list(pool_members("P1f"))),
                "event": f"{start.isoformat()}..{end.isoformat()}",
            },
            end,
        )

    return after_window(build)


async def bind_record(
    ctx: ScenarioContext, templates: Mapping[str, Template]
) -> dict[str, Template]:
    """*templates* with the draws that need the record itself wired in.

    Only T12's, today. Its ``{event}`` pool is the pinned rain record's and cannot
    be written as a value pool without duplicating the definition the catalog
    already states; binding it here keeps the registry declarative and the record
    read exactly once.
    """
    bound = dict(templates)
    if "T12" in bound:
        events = rain_events(await daily_rain(ctx))
        bound["T12"] = dataclasses.replace(bound["T12"], draw=event_draw(events))
    return bound


ContextFactory = Callable[[datetime], ScenarioContext]


def cached_contexts(*, allow_live: bool = True) -> ContextFactory:
    """One :class:`ScenarioContext` per distinct ``as_of``, reused across draws.

    Each context opens a DuckDB connection and builds five as-of views, and a
    rejection-sampled template draws the same day many times over; building one
    per attempt would spend most of a run on ``ATTACH``.
    """
    contexts: dict[datetime, ScenarioContext] = {}

    def factory(as_of: datetime) -> ScenarioContext:
        if as_of not in contexts:
            contexts[as_of] = make_case_context(as_of, allow_live=allow_live)
        return contexts[as_of]

    return factory


async def draw_one(
    template: Template,
    *,
    split: str,
    rng: Random,
    days: Sequence[date],
    context_for: ContextFactory,
    fixed: Mapping[str, Any],
    case_id: str,
    quota: Mapping[bool, int] | None,
    counts: dict[bool, int],
    run: GenerationRun,
) -> dict[str, Any] | None:
    """One attempt at one instance: the five steps in the module docstring.

    Returns the case, or ``None`` when the draw was rejected — the caller
    resamples. Every rejection is recorded on *run* with the predicate that
    caused it, which is what makes the filters' specificity measurable after the
    fact rather than only assertable.
    """
    key = (template.template_id, split)
    run.attempts[key] = run.attempts.get(key, 0) + 1

    try:
        as_of, drawn = template.draw(rng, days, fixed)
    except Undrawable as exc:
        run.rejections.append(
            Rejected(template.template_id, split, "undrawable", str(exc))
        )
        return None
    params = {**dict(fixed), **drawn}

    ctx = context_for(as_of)
    rejections = await filters.check(ctx, template.requires(params, as_of))
    if rejections:
        run.rejections.extend(
            Rejected(template.template_id, split, rejection.predicate, str(rejection))
            for rejection in rejections
        )
        return None

    inputs = {
        "question": template.render(params),
        "as_of": as_of.isoformat(),
        "case_id": case_id,
        "template_id": template.template_id,
        "params": params,
        "language": "en",
    }
    try:
        materialized = (await ORACLES[template.template_id](inputs, ctx)).expectations()
    except OracleInputError as exc:
        run.rejections.append(Rejected(template.template_id, split, "oracle", str(exc)))
        return None

    answer = materialized["answer"]
    if isinstance(answer, bool):
        tally = run.answered.setdefault(key, {True: 0, False: 0})
        tally[answer] += 1
    if quota is not None and isinstance(answer, bool) and counts[answer] >= quota[answer]:
        run.rejections.append(
            Rejected(
                template.template_id,
                split,
                "balance",
                f"the {answer} class is full at {counts[answer]} of {quota[answer]}",
            )
        )
        return None

    case = case_envelope(
        template, case_id=case_id, as_of=as_of, params=params, materialized=materialized
    )
    try:
        assert_case(case, roof_pool=roof_pool_for(template, params))
    except CaseAssertionError as exc:
        run.rejections.append(
            Rejected(template.template_id, split, "invariant", str(exc))
        )
        return None
    errors = validate(case)
    if errors:
        raise Unemittable(
            f"{case_id} ({template.template_id}) does not satisfy "
            f"eval/schema/case.schema.json: {'; '.join(errors)}"
        )

    if isinstance(answer, bool):
        counts[answer] += 1
    return case


async def instantiate(
    template: Template,
    *,
    split: str,
    rng: Random,
    days: Sequence[date],
    context_for: ContextFactory,
    run: GenerationRun,
    start_index: int = 1,
    max_attempts: int = MAX_ATTEMPTS,
) -> list[dict[str, Any]]:
    """*m* instances of one template inside one split, balanced where it is a bool.

    **The balance quota is a cap on each class rather than a target for one of
    them**: neither class may take more than ``ceil(m / 2)`` of the split's
    instances, so an even ``m`` lands exactly 50/50 and an odd one lands 3/2 in
    whichever direction the record offered first. Preferring a direction would be
    a sampling choice nothing in the catalog asks for, where the cap is the whole
    of what "~50/50 within each split" says.
    """
    count = template.instances(split)
    if not count:
        return []
    strata = template.strata(split) or tuple({} for _ in range(count))
    if len(strata) != count:
        raise Exhausted(
            f"{template.template_id} stratifies {len(strata)} instances into {split}, "
            f"which carries {count}"
        )
    quota = {True: (count + 1) // 2, False: (count + 1) // 2} if template.balanced else None
    counts = {True: 0, False: 0}

    cases: list[dict[str, Any]] = []
    for index in range(count):
        for _ in range(max_attempts):
            case = await draw_one(
                template,
                split=split,
                rng=rng,
                days=days,
                context_for=context_for,
                fixed=strata[index],
                case_id=f"{template.template_id}-{start_index + index:04d}",
                quota=quota,
                counts=counts,
                run=run,
            )
            if case is not None:
                cases.append(case)
                break
        else:
            raise Exhausted(
                f"{template.template_id} filled {len(cases)} of {count} instances for "
                f"{split} in {max_attempts} draws per instance; the pool is too tight "
                "or a predicate is rejecting everything"
            )
    run.instances.extend(Instance(split=split, case=case) for case in cases)
    return cases


async def generate(
    *,
    templates: Mapping[str, Template] = TEMPLATES,
    splits: Iterable[str] = ("train", "test_seen", "test_unseen"),
    seed: int = 20260824,
    days: Sequence[date] | None = None,
    day_pools: Mapping[str, Sequence[date]] | None = None,
    context_for: ContextFactory | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> GenerationRun:
    """A whole pass over the registry, one template at a time.

    *day_pools* is where T113 arrives: ``as_of`` is a **striped** partition rather
    than a cut point, so a split's pool is a set of interleaved band days and this
    takes it per split. Absent, every split draws the whole band, which is the
    right default for a packet that is not cutting splits yet.

    *context_for* is injected so a caller can decide whether the run may reach the
    network at all: the measured families answer from the pinned database alone,
    and the model families need weather and GR2L.
    """
    pool = tuple(days) if days is not None else band_days()
    context_for = context_for or cached_contexts()
    run = GenerationRun()

    # One context is needed before the loop, to read the record-bound pools off.
    bound = await bind_record(context_for(as_of_at(pool[-1])), templates)

    for split in splits:
        split_days = tuple((day_pools or {}).get(split, pool))
        rng = Random(f"{seed}:{split}")
        for template_id in sorted(bound):
            await instantiate(
                bound[template_id],
                split=split,
                rng=rng,
                days=split_days,
                context_for=context_for,
                run=run,
                start_index=1 + sum(
                    1 for item in run.instances if item.template_id == template_id
                ),
                max_attempts=max_attempts,
            )
    return run


__all__ = [
    "Exhausted",
    "GenerationRun",
    "Instance",
    "Rejected",
    "Unemittable",
    "bind_record",
    "cached_contexts",
    "case_envelope",
    "daily_rain",
    "draw_one",
    "event_draw",
    "generate",
    "instantiate",
    "validate",
]
