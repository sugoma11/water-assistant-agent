"""Substrate water state: θ ↔ mm conversion and the measured day-1 seed.

GR2L's state is substrate water *storage* in mm, but everything a researcher
says or reads — the SMT100 sensors, the ops manual, the answers we are scored on
— is volumetric water content in %θ. Converting between the two is this
deployment's job, not the agent's and not the LLM's: the GR2L tool takes %θ,
converts here before calling the model, and converts the model's mm back to %θ
on the way out.

The relation is GR2L's own depth scaling, ``S_mm = (θ% / 100) × SH_mm``, keyed on
each roof's substrate height ``SH`` — the same arithmetic that produced the
``Ssubmin``/``Ssubmax`` presets from this site's record (see ``gr2l_tool.md``).

This module also owns the *default* seed. GR2L's generic ``theta_01 = 20 mm``
is not usable here: it sits above ``Ssubmax`` for three of the four roof types,
so a simulation that fell back to it started from a saturated roof. Instead the
day-1 state comes from the roof's own sensor — the latest reading at or before
the window opens — and when no trustworthy reading exists the call fails rather
than inventing one.

No ADK imports: this is a pure seam the tool wrapper and any oracle can share.
"""

import dataclasses
import functools
from datetime import date, datetime

import structlog

from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
    create_duckdb_connection,
)
from water_assistant_agent.assistant.settings import get_settings

logger = structlog.get_logger(__name__)

# Which `swc` column carries each modelled roof. Also the allow-list that makes
# the column safe to interpolate into the query below.
ROOF_SWC_COLUMNS: dict[str, str] = {
    "wetland": "QWetland",
    "non_irrigated_extensive": "QEx2",
    "irrigated_extensive": "QEx1",
    "semi_intensive": "QIn",
}

# Roofs whose storage cannot be expressed as %θ. The wetland's store is a 17 mm
# fleece mat plus water ponded above it to the 90 mm standpipe height; its sensor
# saturates near 86 % θ (~14.7 mm), so above the mat θ is not recoverable from
# mm. It reports mm only — see gr2l_tool.md "Wetland specifics".
MM_ONLY_ROOFS: frozenset[str] = frozenset({"wetland"})

# Readings from this date onward are not trustworthy for the given column. The
# QWetland sensor fails / drains to near-zero from 2026-02 (April 2026 averages
# 0.03 % θ), which is also why the wetland's Ssubmin was derived excluding it.
_SENSOR_UNRELIABLE_FROM: dict[str, date] = {"QWetland": date(2026, 2, 1)}

# A seed older than this still runs, but is reported as stale so the answer can
# say so: substrate moisture has a memory of days, not months.
STALE_AFTER_DAYS = 7

_MM_PER_CM = 10.0


class SwcUnavailableError(RuntimeError):
    """No trustworthy soil-moisture reading exists to seed the window."""


@dataclasses.dataclass(frozen=True, slots=True)
class MeasuredSwc:
    """A sensor reading used as the day-1 substrate state."""

    theta_pct: float
    measured_at: datetime
    age_days: int

    @property
    def is_stale(self) -> bool:
        """True when the reading is too old to describe the window's start."""
        return self.age_days > STALE_AFTER_DAYS


def theta_pct_to_mm(theta_pct: float, sh_cm: float) -> float:
    """Volumetric water content (%θ) → substrate water storage (mm)."""
    return theta_pct / 100.0 * sh_cm * _MM_PER_CM


def mm_to_theta_pct(storage_mm: float, sh_cm: float) -> float:
    """Substrate water storage (mm) → volumetric water content (%θ)."""
    return storage_mm / (sh_cm * _MM_PER_CM) * 100.0


class _ExecutorHolder:
    """Module-level singleton holder for the read-only DuckDB executor."""

    instance: DuckDbQueryExecutor | None = None


def _get_executor() -> DuckDbQueryExecutor:
    """Lazily create and cache the module-level executor."""
    if _ExecutorHolder.instance is None:
        factory = functools.partial(
            create_duckdb_connection,
            db_path=get_settings().duckdb_path,
        )
        _ExecutorHolder.instance = DuckDbQueryExecutor(connection_factory=factory)
    return _ExecutorHolder.instance


def _record_bounds(column: str) -> tuple[datetime | None, datetime | None]:
    """First and last timestamp carrying a reading for *column*."""
    result = _get_executor().execute_query(
        f'SELECT min(timestamp), max(timestamp) FROM swc WHERE "{column}" IS NOT NULL'  # noqa: S608 - column from ROOF_SWC_COLUMNS
    )
    if not result.rows:
        return None, None
    first, last = result.rows[0]
    return first, last


def latest_measured_swc(roof_type: str, window_start: date) -> MeasuredSwc:
    """Latest trustworthy %θ reading for *roof_type* at or before *window_start*.

    Raises :class:`SwcUnavailableError` when the window opens before the sensor
    record starts, or when every candidate reading falls inside a period the
    sensor is known to have failed. Failing is deliberate: a wrong day-1 state
    propagates silently through the whole simulation, so the tool would rather
    return nothing than a seed nobody measured.

    Raises ``KeyError``-equivalent ``ValueError`` on an unmodelled roof type.
    """
    column = ROOF_SWC_COLUMNS.get(roof_type)
    if column is None:
        valid = ", ".join(sorted(ROOF_SWC_COLUMNS))
        raise ValueError(f"No soil-moisture column for roof_type {roof_type!r}. Valid: {valid}.")

    # Readings are taken at or before the day the window opens; the seed
    # describes the roof's state going into day 1. The failure bound is strict —
    # a sensor unreliable *from* a date has no good reading on that date.
    upper_bound = datetime.combine(window_start, datetime.max.time())
    unreliable_from = _SENSOR_UNRELIABLE_FROM.get(column)
    conditions = [
        f'"{column}" IS NOT NULL',
        f"timestamp <= TIMESTAMP '{upper_bound:%Y-%m-%d %H:%M:%S}'",
    ]
    if unreliable_from is not None:
        conditions.append(f"timestamp < TIMESTAMP '{unreliable_from:%Y-%m-%d} 00:00:00'")

    query = (
        f'SELECT timestamp, "{column}" FROM swc '  # noqa: S608 - column from ROOF_SWC_COLUMNS
        f"WHERE {' AND '.join(conditions)} ORDER BY timestamp DESC LIMIT 1"
    )
    result = _get_executor().execute_query(query)

    if not result.rows:
        raise SwcUnavailableError(_unavailable_message(roof_type, column, window_start, unreliable_from))

    measured_at, theta_pct = result.rows[0]
    age_days = (window_start - measured_at.date()).days
    logger.debug(
        "Seeding GR2L from measured SWC",
        roof_type=roof_type,
        column=column,
        theta_pct=theta_pct,
        measured_at=measured_at.isoformat(),
        age_days=age_days,
    )
    return MeasuredSwc(theta_pct=float(theta_pct), measured_at=measured_at, age_days=age_days)


def _unavailable_message(
    roof_type: str,
    column: str,
    window_start: date,
    unreliable_from: date | None,
) -> str:
    """Explain which gap left the window without a usable seed."""
    first, last = _record_bounds(column)
    if first is None:
        return (
            f"No soil-moisture record exists for the {roof_type} roof, so its "
            f"substrate state on {window_start:%Y-%m-%d} cannot be established."
        )
    span = f"{first:%Y-%m-%d} to {last:%Y-%m-%d}"
    if unreliable_from is not None and window_start >= unreliable_from:
        return (
            f"The {roof_type} roof's soil-moisture sensor has been unreliable since "
            f"{unreliable_from:%Y-%m-%d}, and there is no earlier reading to establish "
            f"its substrate state on {window_start:%Y-%m-%d} (record: {span})."
        )
    return (
        f"No soil-moisture measurement exists for the {roof_type} roof at or before "
        f"{window_start:%Y-%m-%d}, so its substrate state at the start of the window "
        f"cannot be established (record: {span})."
    )
