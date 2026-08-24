"""Oracles for family E's card-grounded chains (``questions.md`` §2 E).

The four templates whose answer needs **a definition and a measurement**: T08
and T20 apply the heatwave definition to the record and to the forecast, T12
applies the retention target to a rain event, and T25 bridges a past day and a
future one. T07 and T11 are family E's other half and live beside the ladder they
walk (:mod:`eval.oracles.irrigation`).

**The definition is read from the constants, and the card is checked rather than
read.** ``rules_constants.py`` is the single definition and every ``rendered``
card's ``values:`` block is test-bound to it (T068), so reading the constant is
reading what the card must say — T06's argument, and the one that keeps an oracle
from agreeing with a drifted card. What each oracle here *does* read from the
card is the ground for its gold-card claim: T08 and T20 score
``heatwave_definition`` and T12 scores ``retention_target``, so a card that
stopped stating the value the question turns on would leave the gold route unable
to answer while the oracle went on producing a number.

**Both eval-policy constants are in this module's questions, and both are
labelled.** The heatwave duration and the retention target have no deployed
source (``rules_constants.py`` marks them ``EVAL POLICY``); the heat threshold and
the lysimeter area do. An answer that cited either of the first two as the site's
own would be reporting this testbed's convention as the site's policy, which is
what the cards' ``eval_policy`` lists exist to prevent.

**Where a window's edge could make the question two-valued, the draw is refused
rather than decided.** A heatwave run that crosses the edge of the window the
question names has two defensible readings — the run the window contains and the
run the record holds — and §1.6's oracle-validity filter discards exactly that.
Both readings are computed here, from one query or one fetch, and a disagreement
raises (:func:`heatwave_days`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.knowledge.store import load_card
from water_assistant_agent.assistant.rules_constants import (
    HEAT_THRESHOLD_C,
    HEATWAVE_MIN_CONSECUTIVE_DAYS,
    RETENTION_TARGET,
)
from water_assistant_agent.assistant.tools.roofs import LYSIMETER_AREA_M2
from water_assistant_agent.assistant.tools.site import site_day_expr
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
    beyond_horizon,
    resolve_window,
)

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.model_chain import forecast_days_for
from eval.oracles.pins import stamp
from eval.oracles.sql import (
    STATION_AIR_TEMPERATURE_MAX,
    STATION_RAIN,
    period_window,
    roof_column,
    station_column,
    within_as_of,
)

HEATWAVE_CARD = "heatwave_definition"
RETENTION_CARD = "retention_target"


def heatwave_runs(
    hot: Sequence[bool], min_days: int = HEATWAVE_MIN_CONSECUTIVE_DAYS
) -> list[tuple[int, int]]:
    """Index ranges of the qualifying runs in *hot*, as ``[start, end)`` pairs.

    A run is maximal and consecutive, and qualifies at *min_days* or longer —
    the card's "a run of consecutive days … lasting at least the minimum number
    of days". A day with no reading is not a hot day and therefore breaks a run,
    which is the conservative direction: §1.6's coverage filter is what keeps a
    thinly covered window from being sampled at all, and a gap silently *joining*
    two runs would manufacture a heatwave.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate([*hot, False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= min_days:
                runs.append((start, index))
            start = None
    return runs


def heatwave_days(
    daily_max: Mapping[date, float], window: tuple[date, date], described: str
) -> int:
    """How many days of *window* sit inside a heatwave, refusing an ambiguous edge.

    *daily_max* covers the window **and a margin of ``min_days - 1`` on each
    side**, which is what makes the ambiguity visible instead of silently
    resolved. Two readings are computed over it:

    * the **window** reading — runs detected among the window's own days, which
      is what a candidate counting inside the period the question names gets;
    * the **record** reading — runs detected over the margin too, then
      intersected with the window, which is how ``findings.md`` § What the record
      supports as a heatwave definition counted the record's 53 days.

    They differ exactly when a run crosses an edge of the window, and then the
    question has two defensible answers, so the draw is refused (§1.6). Neither
    reading is wrong, which is precisely why one of them cannot simply be
    chosen — the same shape as the hours-versus-days disagreement T107 measured
    (``decisions.md`` § Forward horizons are counted in days).
    """
    start, end = window
    span = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    margin = HEATWAVE_MIN_CONSECUTIVE_DAYS - 1
    wide = [
        start + timedelta(days=offset - margin)
        for offset in range(len(span) + 2 * margin)
    ]

    def hot(days: Iterable[date]) -> list[bool]:
        return [daily_max.get(day, float("-inf")) >= HEAT_THRESHOLD_C for day in days]

    inside = sum(
        end_index - start_index for start_index, end_index in heatwave_runs(hot(span))
    )
    marked = {
        wide[index]
        for start_index, end_index in heatwave_runs(hot(wide))
        for index in range(start_index, end_index)
    }
    across = len(marked & set(span))

    if inside != across:
        raise OracleInputError(
            f"a heatwave run crosses the edge of {described}: counted inside the "
            f"window it is {inside} days, and counted over the record it is {across}. "
            "The question has two defensible answers (questions.md §1.6)."
        )
    return inside


def heatwave_definition_card() -> dict[str, Any]:
    """The card's stated definition, checked against the constants it renders from.

    Read for the *ground* rather than for the numbers: T08 and T20 both score
    ``heatwave_definition`` as a gold card, so the correct trajectory includes
    fetching it, and a card that stopped stating the duration would leave that
    route unable to answer while these oracles went on producing numbers. The
    duration is **authored eval policy** and the card marks it as such; the
    threshold is the deployed controller's.
    """
    card = load_card(HEATWAVE_CARD)
    values = card.values or {}
    stated = {
        "heat_threshold_c": values.get("heat_threshold_c"),
        "min_consecutive_days": values.get("min_consecutive_days"),
    }
    expected = {
        "heat_threshold_c": HEAT_THRESHOLD_C,
        "min_consecutive_days": HEATWAVE_MIN_CONSECUTIVE_DAYS,
    }
    if stated != expected:
        raise OracleInputError(
            f"the {HEATWAVE_CARD} card states {stated} where the constants say "
            f"{expected}; the gold route reads the card and this oracle reads the "
            "constants, so the two would answer different questions."
        )
    if "min_consecutive_days" not in (values.get("eval_policy") or ()):
        raise OracleInputError(
            f"the {HEATWAVE_CARD} card no longer marks min_consecutive_days as eval "
            "policy, so an answer quoting it would attribute this testbed's "
            "convention to the site."
        )
    return expected


async def _daily_station_maxima(
    ctx: ScenarioContext, start: date, end: date
) -> dict[date, float]:
    """The station's highest air temperature per site day over ``[start, end]``.

    The same two aggregations T02 performs and in the same order — the day's
    maximum first, then whatever reads it — over the same checked column and the
    same day expression the schema block hands the sub-agent
    (``eval.oracles.sql``). T08's route is the sub-agent writing this query, so
    borrowing the station derivation's completeness rule here would answer a
    question the gold trajectory does not ask (``sql.station_column``).
    """
    column = station_column(STATION_AIR_TEMPERATURE_MAX)
    day = site_day_expr()
    result = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT {day} AS at, max("{column}") AS hottest FROM wetter '  # noqa: S608 - identifier checked, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}' "
        f"GROUP BY 1 ORDER BY 1",
    )
    return {at: float(hottest) for at, hottest in result.rows if hottest is not None}


async def t08_heatwave_days(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T08 — how many heatwave days a past period held, as the manual defines one.

    The chain the catalog names: the definition from the card's two constants,
    then a count over the station's daily record. Both halves have to be right
    and they fail differently — a candidate that invents a duration counts the
    wrong days off the right record, and one that reads the card and then counts
    half-hourly rows counts the right definition over the wrong grouping.

    **The count is of days, not of runs**, which is what the question asks: three
    days at 24 °C is one heatwave and three heatwave days. Over the pinned record
    the two differ by a factor of about four (``findings.md``).

    **The threshold is "reaches or exceeds"** — the card's own words, so a day
    sitting exactly on 24.0 °C is a heatwave day. That is the opposite convention
    from T02's strict "exceed", and deliberately: T02 asks its own question with
    its own threshold in it, while this one applies a definition that is written
    down.

    **The window is a sampled period rather than a calendar month, and that is
    the boundary guard's doing.** A run crossing the edge of the window makes the
    count two-valued (:func:`heatwave_days`), and over calendar months the record
    offers 10 of which 3 are refused for exactly that — short of the 9 disjoint
    draws §1.7 needs, with six of the survivors answering zero. Sampled periods
    put the pool back: 287 of 315 fourteen-day windows in the band are accepted
    and 82 of those answer non-zero (``findings.md``). It also makes T08 the
    card-grounded twin of T02, which counts its own threshold over its own
    ``{period}``.

    Params:
        period: ``YYYY-MM-DD..YYYY-MM-DD``, ending at or before ``as_of``.
    """
    (period,) = required_params(inputs, "period")
    definition = heatwave_definition_card()
    start, end = period_window(str(period))
    within_as_of(ctx, end, f"the period {period}")

    margin = timedelta(days=HEATWAVE_MIN_CONSECUTIVE_DAYS - 1)
    daily_max = await _daily_station_maxima(ctx, start - margin, end + margin)
    if not any(start <= day <= end for day in daily_max):
        raise OracleInputError(
            f"wetter holds no daily maximum over {period}; the coverage filter should "
            "have rejected this draw (questions.md §1.6)."
        )
    days = heatwave_days(daily_max, (start, end), f"the period {period}")

    return OracleAnswer(
        answer=days,
        unit="count",
        pins=stamp(),
        detail={
            "window": f"{start}..{end}",
            "definition": definition,
            "days_with_readings": sum(1 for day in daily_max if start <= day <= end),
            "hot_days": sum(
                1
                for day, value in daily_max.items()
                if start <= day <= end and value >= HEAT_THRESHOLD_C
            ),
        },
    )


def retention_target_card(roof: str) -> float:
    """The target this event is judged against, grounded in the card that states it.

    Three conditions, and the third is the one a bare value read would miss:
    the card states a target, marks it eval policy — the site deploys none — and
    **does not exclude this roof**. The semi-intensive roof is under
    ``not_applicable:`` because it has no lysimeter, so its retention is unknown
    rather than poor, and a case sampling it would have no oracle at all
    (``questions.md`` §1.8's P1f).
    """
    card = load_card(RETENTION_CARD)
    values = card.values or {}
    if values.get("retention_fraction") != RETENTION_TARGET:
        raise OracleInputError(
            f"the {RETENTION_CARD} card states a target of "
            f"{values.get('retention_fraction')!r} where the constants say "
            f"{RETENTION_TARGET}; the gold route reads the card."
        )
    if "retention_fraction" not in (values.get("eval_policy") or ()):
        raise OracleInputError(
            f"the {RETENTION_CARD} card no longer marks retention_fraction as eval "
            "policy, so an answer quoting it would attribute this testbed's target "
            "to the site."
        )
    if roof in card.not_applicable:
        raise OracleInputError(
            f"the {RETENTION_CARD} card excludes {roof} ({card.not_applicable[roof]}), "
            "so its retention is unknown rather than measurable; T12's pool is P1f."
        )
    return float(RETENTION_TARGET)


async def t12_retention_above_target(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T12 — did this roof retain more of a rain event than the manual's target?

    ``(rain − outflow) / rain`` over the event's window, against the target the
    ``retention_target`` card states. **No area factor**: every lysimeter
    collects 1 m², so a litre in the tipping bucket is already a millimetre of
    depth over the collecting surface, and multiplying by an area is the mistake
    the card exists to prevent. The constant is asserted rather than assumed —
    a collection area that moved would make litres and millimetres different
    numbers and every retention here silently wrong.

    **The window is the event plus its drainage day, and it is named in the
    parameter.** A lysimeter keeps draining after the rain stops, so a window cut
    at the last wet day books that outflow to no event and overstates retention;
    ``scripts/count_t12_rain_events.py`` applies the tail when it enumerates the
    11 qualifying events, and what reaches this oracle is the window that script
    reports. The oracle answers over the days it is given rather than re-deriving
    the tail, so the case file carries the window the answer was computed over and
    ``period_param_within_as_of`` can see both of its ends.

    **A retention outside [0, 1] is refused, not reported.** Outflow above
    rainfall over a closed window is physically impossible and means the gauge
    undercaught or a previous event was still draining in; it is one of the
    qualification rules that produced the event table, and enforcing it here is
    what stops an impossible value being written into the gold set as truth.

    Params:
        roof: any spelling ``roofs.py`` resolves; pool P1f (has a lysimeter).
        event: the event's window, ``YYYY-MM-DD..YYYY-MM-DD``, drainage day
            included.
    """
    roof_name, event = required_params(inputs, "roof", "event")
    roof, column = roof_column(roof_name, "outflow", "P1f")
    target = retention_target_card(roof.name)
    start, end = period_window(str(event))
    within_as_of(ctx, end, f"the event {event}")

    if LYSIMETER_AREA_M2 != 1.0:
        raise OracleInputError(
            f"the lysimeter collection area is now {LYSIMETER_AREA_M2} m², so a litre "
            "of outflow is no longer a millimetre of depth and this retention needs "
            "an area factor it does not have."
        )

    day = site_day_expr()
    rain_column = station_column(STATION_RAIN)
    rain = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT sum("{rain_column}") FROM wetter '  # noqa: S608 - identifier checked, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'",
    )
    outflow = await asyncio.to_thread(
        ctx.db.execute_query,
        f'SELECT sum("{column}") FROM outflow '  # noqa: S608 - identifier from roofs.py, dates are `date`
        f"WHERE {day} BETWEEN DATE '{start.isoformat()}' AND DATE '{end.isoformat()}'",
    )
    rain_mm = rain.rows[0][0] if rain.rows else None
    outflow_mm = outflow.rows[0][0] if outflow.rows else None
    if rain_mm is None or outflow_mm is None or float(rain_mm) <= 0.0:
        raise OracleInputError(
            f"the event {event} has no rain or no outflow reading for {roof.name} "
            f"(rain {rain_mm!r}, outflow {outflow_mm!r}); the coverage filter should "
            "have rejected this draw (questions.md §1.6)."
        )

    retention = (float(rain_mm) - float(outflow_mm)) / float(rain_mm)
    if not 0.0 <= retention <= 1.0:
        raise OracleInputError(
            f"{roof.name} shed {outflow_mm:.3f} mm over an event that dropped "
            f"{rain_mm:.3f} mm, so its retention is {retention:.3f} — outside [0, 1] "
            "and physically impossible over a closed window (t12_rain_events.md)."
        )

    return OracleAnswer(
        answer=bool(retention > target),
        unit=None,
        pins=stamp(),
        detail={
            "roof": roof.name,
            "column": column,
            "window": f"{start}..{end}",
            "rain_mm": round(float(rain_mm), 3),
            "outflow_mm": round(float(outflow_mm), 3),
            "retention": round(retention, 4),
            "target": target,
        },
    )


async def t20_forecast_heatwave(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T20 — does the coming *d* days' forecast qualify as a heatwave?

    T08's definition applied forward, and the holdout's only hybrid chain: the
    card supplies the rule and ``get_weather_forecast_tool`` supplies the rows,
    with ``text_to_sql_agent`` listed as a distractor because the database holds
    no future. T08 is the train-side parent that teaches the same card against the
    database.

    **The rows come through ``ctx.weather``**, the composite that resolves the
    source from the window alone, and the daily maximum is each row's ``tx`` — the
    same field T14 reads for the same reason: ``tm`` is the day's average and a
    heatwave is defined on the maximum.

    **The horizon is a whole day count the question names**, like every other
    forward window in the catalog. "The coming week" was the earlier phrasing and
    it is the redenotation ``decisions.md`` § Forward horizons are counted in days
    forbids: a calendar week beginning Monday is a different set of days from the
    seven beginning today, and here that difference can decide the answer, since
    three consecutive days is the whole rule.

    **A run crossing the window's far edge refuses the draw**, through the same
    :func:`heatwave_days` guard T08 uses. The oracle fetches ``d + 2`` days so the
    ambiguity is visible; the two extra days are never counted, only used to see
    whether a run at the end continues. The *near* edge needs no such margin and
    gets none: yesterday is not forecast, so a hot spell already under way cannot
    make the coming days qualify, and the guard reads the days before the window
    as cold because for this question they are.

    Params:
        d: the horizon in whole days, counting today as day 1.
    """
    (days,) = required_params(inputs, "d")
    definition = heatwave_definition_card()
    horizon = forecast_days_for(days)
    today = ctx.as_of.date()

    start, end = resolve_window(forecast_days=horizon, today=today)
    margin = HEATWAVE_MIN_CONSECUTIVE_DAYS - 1
    _, wide_end = resolve_window(forecast_days=horizon + margin, today=today)
    if beyond_horizon(wide_end, today):
        raise OracleInputError(
            f"seeing whether a run at the end of {start}..{end} continues needs "
            f"{margin} days past it, which is beyond the tool's "
            f"{FORECAST_HORIZON_DAYS}-day limit; draw a shorter horizon."
        )

    result = await ctx.weather.fetch(start_date=start, end_date=wide_end)
    if not result.data:
        raise OracleInputError(
            f"the forecast over {start}..{wide_end} came back with no rows at all."
        )
    daily_max = {date.fromisoformat(row.Date): float(row.tx) for row in result.data}
    window = (date.fromisoformat(start), date.fromisoformat(end))
    marked = heatwave_days(daily_max, window, f"the forecast {start}..{end}")

    return OracleAnswer(
        answer=bool(marked >= HEATWAVE_MIN_CONSECUTIVE_DAYS),
        unit=None,
        pins=stamp(weather_source=[result.source]),
        detail={
            "window": f"{start}..{end}",
            "definition": definition,
            "heatwave_days": marked,
            "max_tx_c": round(
                max(
                    value
                    for day, value in daily_max.items()
                    if window[0] <= day <= window[1]
                ),
                2,
            ),
            "source": result.source,
        },
    )


async def t25_tomorrow_warmer_than_yesterday(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T25 — will tomorrow be warmer than yesterday?

    The bridge template, and the only one whose **gold trajectory admits two
    routes**: the two-call route reads yesterday and tomorrow separately, and the
    single-call route asks for one ``past_days=1, forecast_days=2`` window. The
    docstring recommends the second for exactly this shape, so both are gold.

    **The two routes can disagree, and the oracle refuses the draw where they
    do.** Source resolution is whole-window (``CompositeWeatherClient``): asked
    for yesterday alone the composite serves the station, and asked for
    yesterday-through-tomorrow it falls to the Archive whole, because the record
    holds no future. So yesterday's maximum arrives from the site's own
    instruments on one gold route and from the reanalysis on the other, with a
    measured warm bias between them (``findings.md`` § Weather sources) — and a
    draw whose answer turns on that bias would score a candidate wrong for taking
    a route the catalog calls correct. Both readings are computed here and a
    disagreement raises: §1.6's oracle-validity filter, applied to the one thing
    in this template that is genuinely two-valued.

    Two windows are therefore fetched, and ``pins.weather_source`` carries
    whatever the two resolved to — one entry where they agreed, two where they
    did not, which is the case the pin's array shape exists for.

    **The comparison is strict** and it is maxima against maxima: tomorrow's
    ``tx`` against yesterday's, both daily highs. "Warmer" over a day is about the
    peak, and ``tm`` would answer a different question with the same rows.

    Params: none — both days are named relative to ``as_of`` and neither is
        sampled.
    """
    today = ctx.as_of.date()
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()

    apart = await ctx.weather.fetch(start_date=yesterday, end_date=yesterday)
    ahead = await ctx.weather.fetch(start_date=tomorrow, end_date=tomorrow)
    together = await ctx.weather.fetch(start_date=yesterday, end_date=tomorrow)
    if not apart.data or not ahead.data:
        raise OracleInputError(
            f"the weather came back with no row for {yesterday} or for {tomorrow}, so "
            "there is nothing to compare."
        )

    combined = {row.Date: row for row in together.data}
    if yesterday not in combined or tomorrow not in combined:
        raise OracleInputError(
            f"the combined window {yesterday}..{tomorrow} is missing one of its two "
            "ends, so the single-call route could not answer this case at all."
        )

    split_route = ahead.data[-1].tx > apart.data[0].tx
    joint_route = combined[tomorrow].tx > combined[yesterday].tx
    if split_route != joint_route:
        raise OracleInputError(
            f"the two gold routes disagree: reading {yesterday} on its own gives "
            f"{apart.data[0].tx:.2f} °C from the {apart.source}, and reading it inside "
            f"{yesterday}..{tomorrow} gives {combined[yesterday].tx:.2f} °C from the "
            f"{together.source}, which flips the answer. The question has two "
            "defensible answers (questions.md §1.6)."
        )

    return OracleAnswer(
        answer=bool(split_route),
        unit=None,
        pins=stamp(weather_source=[apart.source, ahead.source]),
        detail={
            "yesterday": yesterday,
            "yesterday_tx_c": round(float(apart.data[0].tx), 2),
            "yesterday_source": apart.source,
            "tomorrow": tomorrow,
            "tomorrow_tx_c": round(float(ahead.data[-1].tx), 2),
            "tomorrow_source": ahead.source,
            "combined_yesterday_tx_c": round(float(combined[yesterday].tx), 2),
            "combined_source": together.source,
        },
    )
