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
``min(window_start, as_of)`` (:func:`seed_bound`) — and when no trustworthy
reading exists the call fails rather than inventing one.

No ADK imports and no settings read: this is a pure seam the tool wrapper and any
oracle can share. The database arrives as an executor argument — production passes
the settings-built singleton, a case passes ``ctx.db``, whose views are bounded at
that case's ``as_of`` — so a seed can never be read past the cut it is seeding
(``agent_architecture.md`` §5, ``decisions.md`` § The construction seam).
"""

import dataclasses
from datetime import UTC, date, datetime

import structlog

from water_assistant_agent.assistant.ports import ReadOnlyWarehouseQuery

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
    seed_at: datetime
    """The bound this reading was the latest one at or before — ``min(window_start, as_of)``.

    Carried so the rule is observable rather than inferred from which row came
    back: two windows that differ only in their case's ``as_of`` must be able to
    show *why* they were seeded differently.
    """

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


def _record_bounds(
    executor: ReadOnlyWarehouseQuery,
    column: str,
) -> tuple[datetime | None, datetime | None]:
    """First and last timestamp carrying a reading for *column*."""
    result = executor.execute_query(
        f'SELECT min(timestamp), max(timestamp) FROM swc WHERE "{column}" IS NOT NULL'  # noqa: S608 - column from ROOF_SWC_COLUMNS
    )
    if not result.rows:
        return None, None
    first, last = result.rows[0]
    return first, last


def seed_bound(window_start: date, as_of: datetime) -> datetime:
    """``min(window_start, as_of)`` — the instant a day-1 seed may be read at or before.

    The two halves fail different cases, which is why neither alone is the rule
    (``decisions.md`` § GR2L argument surface). Bounding at ``as_of`` alone seeds
    a retrospective window from a reading taken months after it closed; bounding
    at the window start alone lets a forecast case, whose window opens past its
    cut, read sensor data from after that cut. Taking the earlier of the two is
    the only bound that holds for both.

    *as_of* is an instant and is compared as one: it is converted to the naive
    UTC the five tables store, here rather than by the caller, for
    ``decisions.md`` § The as-of cut's reason — an aware value compared against a
    naive column renders in the *host's* session timezone. ``window_start`` is a
    site calendar day and contributes its own last instant, so a window opening
    today is still seeded from a reading taken earlier today.
    """
    as_of_utc = as_of.astimezone(UTC).replace(tzinfo=None) if as_of.tzinfo else as_of
    return min(datetime.combine(window_start, datetime.max.time()), as_of_utc)


def latest_measured_swc(
    executor: ReadOnlyWarehouseQuery,
    roof_type: str,
    window_start: date,
    *,
    as_of: datetime,
) -> MeasuredSwc:
    """Latest trustworthy %θ reading for *roof_type* at or before ``min(window_start, as_of)``.

    :func:`seed_bound` is the rule, and it is applied **here** rather than left to
    the executor's own bound. A case's as-of executor already hides post-cut rows,
    so for the tool the second half is belt and braces — but an oracle imports
    this function and may hand it an unbounded connection (``agent_architecture.md``
    §7), and the rule has to hold for whatever executor arrives. Every seeded
    component calls this one function for exactly that reason.

    Reads ``swc`` through *executor*, so which rows exist is also the caller's
    binding: a case's as-of executor cannot see a reading taken after its
    ``as_of`` whatever this function asks for.

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

    # The seed describes the roof's state going into day 1, and may not be read
    # past the case's cut. The failure bound is separate and strict — a sensor
    # unreliable *from* a date has no good reading on that date.
    upper_bound = seed_bound(window_start, as_of)
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
    result = executor.execute_query(query)

    if not result.rows:
        raise SwcUnavailableError(
            _unavailable_message(
                executor, roof_type, column, window_start, upper_bound, unreliable_from
            )
        )

    measured_at, theta_pct = result.rows[0]
    # Age is measured against the window's start, not against the seed bound: a
    # forecast case seeded at its cut is describing a roof that will have moved on
    # by the time the window opens, and staleness is what discloses that.
    age_days = (window_start - measured_at.date()).days
    logger.debug(
        "Seeding GR2L from measured SWC",
        roof_type=roof_type,
        column=column,
        theta_pct=theta_pct,
        measured_at=measured_at.isoformat(),
        seed_at=upper_bound.isoformat(),
        age_days=age_days,
    )
    return MeasuredSwc(
        theta_pct=float(theta_pct),
        measured_at=measured_at,
        age_days=age_days,
        seed_at=upper_bound,
    )


def _unavailable_message(
    executor: ReadOnlyWarehouseQuery,
    roof_type: str,
    column: str,
    window_start: date,
    seed_at: datetime,
    unreliable_from: date | None,
) -> str:
    """Explain which gap left the window without a usable seed.

    The day named is *seed_at*'s, not the window's: when ``as_of`` is the binding
    half of the rule the two differ, and a message naming a day the search never
    reached would send the agent looking for a reading that does exist.
    """
    first, last = _record_bounds(executor, column)
    seed_day = seed_at.date()
    if first is None:
        return (
            f"No soil-moisture record exists for the {roof_type} roof, so its "
            f"substrate state on {window_start:%Y-%m-%d} cannot be established."
        )
    span = f"{first:%Y-%m-%d} to {last:%Y-%m-%d}"
    if unreliable_from is not None and seed_day >= unreliable_from:
        return (
            f"The {roof_type} roof's soil-moisture sensor has been unreliable since "
            f"{unreliable_from:%Y-%m-%d}, and there is no earlier reading to establish "
            f"its substrate state on {window_start:%Y-%m-%d} (record: {span})."
        )
    return (
        f"No soil-moisture measurement exists for the {roof_type} roof at or before "
        f"{seed_day:%Y-%m-%d}, so its substrate state at the start of the window "
        f"cannot be established (record: {span})."
    )
