"""Oracles for family C — the pure weather templates (``questions.md`` §2 C).

Five templates, three numbers and two abstentions, and one thing they all share:
**the window is resolved by layer 1's own resolver and the rows come through
``ctx.weather``.** Both halves are load-bearing.

* :func:`~..weather_client.resolve_window` is what both tool wrappers call
  before touching a client, so a window the oracle answered over and a window the
  case is scored on are one resolution rather than two. It is also why every
  forward-looking template here counts in **days**: the rows are daily, an hourly
  horizon has to become a day count somewhere, and T107 measured what happens
  when the oracle and the candidate each do that conversion privately
  (:func:`~.model_chain.forecast_days_for`, whose rule is one rule across the
  four templates that resolve a forward window).
* ``ctx.weather`` is the composite that picks the station or the Archive from the
  window alone. Calling ``fetch_daily_weather`` instead would answer every case
  from the Archive while the tool answered some of them from the site's own
  instruments — a measured difference, not a notional one (``findings.md``
  § Weather source measurements) — and would leave ``pins.weather_source``
  claiming a provenance the answer did not have.

**The two abstentions are abstentions for different reasons, and neither is the
other's.** T18a's window reaches past the tool's 16-day horizon, so the *tool*
types ``not_available`` and the agent relays it. T18b names a quantity the daily
row does not carry, and the tool has no variable argument to refuse — so the
abstention is the agent's alone, with no tool signal behind it. Each oracle
therefore asserts that the *other* one's trigger did not fire: T18a checks that
the window really is past the horizon, and T18b checks that it is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from water_assistant_agent.assistant.context import ScenarioContext
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow
from water_assistant_agent.assistant.tools.weather_client import (
    FORECAST_HORIZON_DAYS,
    beyond_horizon,
    resolve_window,
)

from eval.oracles.base import OracleAnswer, OracleInputError, required_params
from eval.oracles.model_chain import forecast_days_for
from eval.oracles.pins import stamp


def daily_row_fields() -> dict[str, str]:
    """The seven daily quantities the weather tool returns, and what each is.

    Read off :class:`DailyWeatherRow` — the model the tool actually returns —
    rather than off the tool's docstring, which is candidate-owned and therefore
    cannot be ground truth for anything (``agent_architecture.md`` §6). A
    candidate that deleted the docstring's variable list would otherwise move
    T18b's answer.
    """
    return {
        name: str(field.description or "")
        for name, field in DailyWeatherRow.model_fields.items()
        if name != "Date"
    }


def _tokens(text: str) -> set[str]:
    """*text* as a set of lowercase word-ish tokens, punctuation dropped."""
    return {
        token
        for token in "".join(
            character if character.isalnum() else " " for character in text.lower()
        ).split()
        if token
    }


def served_field(variable: str) -> str | None:
    """The daily field *variable* asks for, or ``None`` if the row carries none.

    Deliberately **generous about what counts as served**, because the two
    mistakes are not symmetric. Refusing a draw the tool could have answered
    costs a resample; recording one as an abstention writes a false abstention
    into the gold set, and the false-abstention rate is precisely the metric that
    could then never see it.

    So a match is either the field's own name (``tx``) or a description whose
    words cover the question's (``"max temperature"`` → ``"Max temperature,
    °C"``). "Soil temperature" matches nothing, because no description mentions
    soil; bare "temperature" matches, because the row carries three of them and
    the question would be answerable under any of them.

    **It is a guard, not a synonym dictionary.** A word the descriptions do not
    use passes it — "rain" for ``precip``, "sunshine" for ``gs``, and even
    "maximum" where the description writes "Max". So T18b's ``{variable}`` pool
    stays authored (``questions.md`` §2 C) rather than sampled freely over any
    noun, and what this catches is a pool drifting onto the row's *own*
    vocabulary, which is the way the template would decay.
    """
    asked = _tokens(variable)
    if not asked:
        return None
    for name, description in daily_row_fields().items():
        if asked == {name.lower()} or asked <= _tokens(description):
            return name
    return None


def _forward_window(inputs: Mapping[str, Any], ctx: ScenarioContext) -> tuple[str, str]:
    """Resolve ``d`` days forward from the case's ``as_of``, and check the horizon.

    The horizon check is the one that keeps family C's three answerable templates
    apart from T18a: a window past it is not a hard case, it is T18a's case, and
    a number computed over one would be a number the tool refuses to produce.
    """
    (days,) = required_params(inputs, "d")
    today = ctx.as_of.date()
    start, end = resolve_window(forecast_days=forecast_days_for(days), today=today)
    if beyond_horizon(end, today):
        raise OracleInputError(
            f"a {days}-day horizon ends {end}, past the tool's {FORECAST_HORIZON_DAYS}-day "
            "limit; the tool would type not_available and the case is T18a's, not this one."
        )
    return start, end


async def _forward_rows(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> tuple[list[DailyWeatherRow], str, dict[str, Any]]:
    """The window's daily rows through ``ctx.weather``, with the detail they carry."""
    start, end = _forward_window(inputs, ctx)
    result = await ctx.weather.fetch(start_date=start, end_date=end)
    if not result.data:
        raise OracleInputError(
            f"the weather over {start}..{end} came back with no rows at all, so there "
            "is nothing to answer from."
        )
    return (
        list(result.data),
        result.source,
        {"window": f"{start}..{end}", "days": len(result.data), "source": result.source},
    )


async def t13_rain_expected(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T13 — is more than *thr* mm of rain expected over the next *d* days?

    The window's ``precip`` total against the threshold. **Strict**, matching the
    question's "more than": a total sitting exactly on the threshold is a "no".
    §1.6's oracle-validity filter keeps a sampled draw away from its own
    boundary, so this decides no case that survives generation.

    The total is the window's, not a per-day maximum: "more than 5 mm within the
    next three days" asks whether that much rain arrives at all, which is what
    the tool's own rows sum to.

    Params:
        thr: the threshold in millimetres.
        d: the horizon in whole days, counting today as day 1.
    """
    (threshold,) = required_params(inputs, "thr")
    rows, source, detail = await _forward_rows(inputs, ctx)
    total = round(sum(row.precip for row in rows), 3)

    return OracleAnswer(
        answer=bool(total > float(threshold)),
        unit=None,
        pins=stamp(weather_source=[source]),
        detail={**detail, "threshold_mm": float(threshold), "total_precip_mm": total},
    )


async def t14_forecast_max_temperature(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T14 — the highest temperature forecast over the next *d* days, in °C.

    The maximum of ``tx`` over the window's rows, which is the highest *daily
    maximum* and not a maximum of means: ``tm`` is the day's average and a
    question about how hot it will get is asking about the peak. The row carries
    both, so choosing is the whole of the oracle's content here.

    Params:
        d: the horizon in whole days, counting today as day 1.
    """
    rows, source, detail = await _forward_rows(inputs, ctx)
    hottest = max(row.tx for row in rows)

    return OracleAnswer(
        answer=round(float(hottest), 2),
        unit="°C",
        pins=stamp(weather_source=[source]),
        detail={**detail, "field": "tx", "hottest_day": max(rows, key=lambda r: r.tx).Date},
    )


async def t15b_future_rain(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T15b — how much rain will fall over the next *d* days, in millimetres.

    T15a's tense twin, and the half of the pair where the must-not binds: the
    database holds no future, so ``text_to_sql_agent`` cannot answer this and is
    listed as a distractor, where the reverse is not true and T15a lists none
    (``decisions.md`` § Trajectory scoring and routing probes).

    **The horizon is a day count and not a period range**, and that is forced
    rather than chosen: a future window written ``start..end`` names days past
    the case's own ``as_of``, which ``period_param_within_as_of`` rejects — so
    every instance of the earlier ``{future_period}`` phrasing would have been
    unrunnable (T104, and T18a's ``ahead_days`` for the same reason).

    Params:
        d: the horizon in whole days, counting today as day 1.
    """
    rows, source, detail = await _forward_rows(inputs, ctx)
    total = round(sum(row.precip for row in rows), 3)

    return OracleAnswer(
        answer=total,
        unit="mm",
        pins=stamp(weather_source=[source]),
        detail={**detail, "field": "precip"},
    )


async def t18a_unservable_window(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T18a — a well-formed forecast window beyond the 16-day horizon.

    The abstention is **tool-signalled**: the window resolves cleanly, so this is
    not an argument fault, and the tool types ``not_available`` with a reason the
    agent relays as the contract status. Keeping the two apart is what makes the
    false-abstention rate readable (``decisions.md`` § Typed abstention) — an
    ``invalid_argument`` here would be a fumble the candidate could fix, and a
    scope limit is not.

    **The horizon is read, not restated.** ``FORECAST_HORIZON_DAYS`` and
    ``beyond_horizon`` are layer 1's own, so a horizon that moves moves this
    oracle with it rather than leaving a case that asserts the old limit.

    **Nothing is fetched here and nothing may be.** The horizon check fires on
    the resolved window before any client is reached, so this oracle touches
    neither the station nor the Archive nor the response cache — which is what
    lets T18a be declared a zero-cache-entry case (T107). An oracle that fetched
    first and checked after would record an entry for a window the tool never
    serves.

    Params:
        ahead_days: how far past ``as_of`` the question reaches. Carried as a
            count rather than as a date so T104's ``period_param_within_as_of``
            assertion does not read it as a day the case must be able to see —
            the whole point of the template is that it cannot.
    """
    (ahead_days,) = required_params(inputs, "ahead_days")
    today = ctx.as_of.date()
    target = today + timedelta(days=int(ahead_days))
    start, end = resolve_window(
        start_date=target.isoformat(), end_date=target.isoformat(), today=today
    )

    if not beyond_horizon(end, today):
        raise OracleInputError(
            f"{end} is within {FORECAST_HORIZON_DAYS} days of {today}, so the tool "
            "would serve it and the case is not an abstention at all."
        )

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available",
        pins=stamp(),
        detail={
            "window": f"{start}..{end}",
            "horizon_days": FORECAST_HORIZON_DAYS,
            "days_past_horizon": (target - today).days - FORECAST_HORIZON_DAYS,
        },
    )


async def t18b_missing_variable(
    inputs: Mapping[str, Any], ctx: ScenarioContext
) -> OracleAnswer:
    """T18b — a forecast quantity the daily row does not carry. There is none to give.

    The abstention with **no tool signal**, which is the whole of what it tests.
    The tool takes no variable argument: ask it for soil temperature and it
    returns the same seven fields it always returns, none of which is soil
    temperature, and nothing anywhere types a ``not_available``. So the gold
    trajectory is empty and the correct behaviour is to decline before calling —
    T18a's transfer target, from an abstention the tool hands over to one the
    agent has to reach on its own.

    **Two things are asserted, and the second is the one that matters.** The
    quantity is outside :func:`daily_row_fields`, generously matched
    (:func:`served_field`) so a draw the tool could have answered is refused
    rather than recorded. And the *window* is inside the horizon: past it the
    tool would abstain on the window, the case would become T18a wearing T18b's
    words, and a candidate could pass it without ever noticing the variable.

    **Nothing is fetched**, for T18a's reason: the correct trajectory makes no
    call, so an oracle that made one would be the only thing in the case that
    did.

    Params:
        variable: the quantity the question asks for, in the question's own
            words.
        d: the horizon in whole days, inside the tool's 16-day limit.
    """
    (variable,) = required_params(inputs, "variable")
    _forward_window(inputs, ctx)

    served = served_field(str(variable))
    if served is not None:
        raise OracleInputError(
            f"the daily row carries {variable!r} as {served!r} "
            f"({daily_row_fields()[served]}), so the tool answers this and the case "
            "would record a false abstention as truth."
        )

    return OracleAnswer(
        answer=None,
        unit=None,
        status="not_available",
        pins=stamp(),
        detail={
            "asked_for": str(variable),
            "fields_served": sorted(daily_row_fields()),
            "horizon_days": FORECAST_HORIZON_DAYS,
        },
    )
