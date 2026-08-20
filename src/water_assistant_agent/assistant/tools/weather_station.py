"""Station weather: the site's half-hourly ``wetter`` record as GR2L-ready daily rows.

The second of the two weather sources (``agent_architecture.md`` §3.3). Windows
the site's own record covers entirely are answered from real instruments at the
roofs' own height instead of a ~31 km ERA5 cell — no network, no response cache,
and nothing that can see past a case's ``as_of``, because every row arrives
through the caller's executor and a case's executor is bounded at its cut.

The derivation is ``weather_tool.md`` § Station source and is **part of the
pinned surface** (§5): changing any of it moves every oracle downstream. Six of
the seven fields are aggregations over the day's 48 half-hourly rows; ``tn`` is
estimated, because the record carries no ``Tmin`` column.

============  ============================================================
``tm``        ``avg(Tmean)``
``tx``        ``max(Tmax)``
``tn``        ``min(2·Tmean − Tmax)`` — estimated
``rf``        ``avg(RH)``
``precip``    ``sum(Rain)``
``w``         ``avg(windspeed) × 3.6`` — m/s → km/h, sentinel-free samples only
``gs``        ``sum(Rad_SW) × 1800 / 10⁴`` — W/m² half-hourly → J/cm²/day
============  ============================================================

Three properties this module deliberately does **not** have. It applies no
calibration: the station reads ~26 % low on shortwave against the site's own
pyranometers and the overlap makes the factor computable, which is exactly why
``decisions.md`` § No fitted correction between the instrument and the oracle
forbids fitting it. It fills no gaps and switches to no other source per day: an
incomplete day is simply not served, and a window containing one falls to the
Archive **whole**, so every window keeps exactly one provenance. The measured
offsets — estimated ``tn``, low ``gs``, undercaught ``precip`` — are stated
scope limits (``agent_architecture.md`` §8), disclosed rather than removed.
"""

from datetime import date

import structlog

from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow
from water_assistant_agent.assistant.tools.site import site_day_expr

logger = structlog.get_logger(__name__)

STATION_SOURCE = "station"
"""The provenance this source stamps on a :class:`~..tools.schemas.WeatherResult`."""

ROWS_PER_COMPLETE_DAY = 48
"""Half-hourly rows a complete day carries. A day with any other count is not served.

The record holds 479 of its 481 whole days complete; both shortfalls are
spring-forward Sundays (2025-03-30 and 2026-03-29, the latter inside the
catalog's ``as_of`` band). Grouped in local days the rule also drops the
fall-back Sunday, which carries 50 half-hours rather than 48 — a 25-hour day is
as far from this count as a 23-hour one, and the alternative is a DST-aware
expected count that no part of the specification asks for. Either way the day is
not *served*, not silently patched: the window falls to the Archive whole.
"""

_WIND_SENTINEL_FLOOR = 0.0
"""Wind samples below this are the logger's ``−7999`` sentinel and its remnants.

``findings.md`` names ``−7999``, and 69 rows carry it exactly — but the record's
half-hourly values are themselves means of finer samples, so a half-hour that
mixed sentinel and real readings lands anywhere between: ``−7954.5``,
``−3365.9``, ``−54.99``. Filtering on equality would leave those in, and one
``−3365.9`` among 48 samples puts a day's mean wind near ``−70`` m/s. Wind speed
cannot be negative, so the sign is the filter, and it cannot discard a real
reading.
"""

_WIND_MS_TO_KMH = 3.6
"""m/s → km/h. GR2L divides ``w`` by 3.6 (``weather_tool.md``), so it wants km/h."""

_HALF_HOUR_SECONDS = 1800.0
_J_PER_M2_PER_J_PER_CM2 = 1e4
"""W/m² over a half hour → J/m²; 1 J/cm² = 10⁴ J/m². GR2L wants ``gs`` in J/cm²/day."""


class StationWeatherSource:
    """Derives daily station rows from ``wetter`` through one read-only executor.

    *executor* is the caller's binding and the only channel to the data: a case
    passes ``ctx.db``, whose ``wetter`` view is bounded at that case's ``as_of``,
    so coverage is tested **through the as-of view** and a window reaching past
    the cut can never resolve here (``decisions.md`` § Weather sources). Nothing
    in this class reads a clock, a setting or a connection of its own.
    """

    def __init__(self, executor: ReadOnlyWarehouseQuery) -> None:
        self._executor = executor

    def daily_rows(self, start_date: date, end_date: date) -> list[DailyWeatherRow]:
        """The record's **complete** days within ``[start_date, end_date]``, ascending.

        Days the record covers only partly are absent from the result rather
        than partially derived, so the caller's coverage test is simply whether
        it got a row per day asked for. Timestamps are converted to the site's
        calendar day *before* grouping (:func:`..tools.site.site_day_expr`);
        grouping the raw UTC column instead differs on 106 of the record's 482
        days by up to 6.664 mm of rain.
        """
        result = self._executor.execute_query(_derivation_query(start_date, end_date))
        rows = [
            DailyWeatherRow(
                Date=day.isoformat(),
                tm=tm,
                tx=tx,
                tn=tn,
                rf=rf,
                precip=precip,
                w=w,
                gs=gs,
            )
            for day, tm, tx, tn, rf, precip, w, gs in result.rows
        ]
        logger.debug(
            "Derived station weather days",
            window=(start_date.isoformat(), end_date.isoformat()),
            days=len(rows),
        )
        return rows

    def record_bounds(self) -> tuple[date | None, date | None]:
        """First and last **complete** day the executor can see, or ``(None, None)``.

        Read through the same executor as everything else, so a case's bounds
        are its as-of view's, not the pinned file's true end.
        """
        result = self._executor.execute_query(_BOUNDS_QUERY)
        if not result.rows or result.rows[0][0] is None:
            return None, None
        first, last = result.rows[0]
        return first, last


# The per-field aggregation, verbatim from `weather_tool.md` § Station source.
# `HAVING` carries the completeness rule *and* the one case the sentinel filter
# can create: a day whose every wind sample is a sentinel has no `w` at all, and
# a row with a null required field is not a row this source can serve.
_AGGREGATES = (
    'avg("Tmean")',
    'max("Tmax")',
    'min(2 * "Tmean" - "Tmax")',
    'avg("RH")',
    'sum("Rain")',
    f'avg(CASE WHEN "windspeed" >= {_WIND_SENTINEL_FLOOR} THEN "windspeed" END) '
    f"* {_WIND_MS_TO_KMH}",
    f'sum("Rad_SW") * {_HALF_HOUR_SECONDS} / {_J_PER_M2_PER_J_PER_CM2}',
)

_COMPLETE_DAY = (
    f"count(*) = {ROWS_PER_COMPLETE_DAY} "
    f'AND count(CASE WHEN "windspeed" >= {_WIND_SENTINEL_FLOOR} THEN "windspeed" END) > 0'
)

_BOUNDS_QUERY = (
    f"SELECT min(day), max(day) FROM ("  # noqa: S608 - no interpolated caller input
    f"SELECT {site_day_expr()} AS day FROM wetter "
    f"GROUP BY 1 HAVING {_COMPLETE_DAY})"
)


def _derivation_query(start_date: date, end_date: date) -> str:
    """The derivation over one window; *start_date* / *end_date* are ``date`` objects."""
    day = site_day_expr()
    return (
        f"SELECT {day} AS day, {', '.join(_AGGREGATES)} "  # noqa: S608 - dates are `date`
        f"FROM wetter "
        f"WHERE {day} BETWEEN DATE '{start_date.isoformat()}' AND DATE '{end_date.isoformat()}' "
        f"GROUP BY 1 HAVING {_COMPLETE_DAY} ORDER BY 1"
    )
