"""The one physical site this deployment models.

Every roof segment lives on the same building, so location is a property of the
deployment, not something the agent chooses: the tool wrappers pin these values
instead of exposing ``latitude``/``longitude`` arguments the LLM could get wrong.
The clients (:mod:`weather_client`, :mod:`gr2l_client`) stay parameterized on
location and reusable; the one site fact :mod:`weather_client` does take from
here is the clock (see below).

``SITE_ELEVATION_M`` is the surveyed roof height above sea level and is preferred
over Open-Meteo's ``elevation``, which is the height of its ~1 km model grid cell
and can be off by tens of metres in a city.

``SITE_TIMEZONE`` is the wall clock the researchers (and the sensor timestamps)
use, so "today" means today at the site — not on whatever the server's clock is
set to. Production's "now" goes through :func:`site_now`: the date the agent is
told (:mod:`..prompts.temporal`) and the date each tool wrapper resolves its
window against (:func:`..tools.weather_client.resolve_window`) must be the same
one, or a window the agent treats as in-range can land on the wrong backend. The
weather client itself takes no clock of its own — window resolution and backend
selection both take the caller's already-resolved date as an explicit argument,
so the same code path serves production's site clock and a harness case's frozen
``as_of`` alike.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

SITE_NAME = "Leipzig green-roof facility"

SITE_LATITUDE = 51.353484
SITE_LONGITUDE = 12.432152
SITE_ELEVATION_M = 142.0
SITE_TIMEZONE = "Europe/Berlin"


def site_now() -> datetime:
    """Current timezone-aware datetime at the research facility."""
    return datetime.now(ZoneInfo(SITE_TIMEZONE))


def site_day_expr(column: str = "timestamp") -> str:
    """SQL turning a **naive UTC** timestamp *column* into the site's calendar day.

    A day is a ``SITE_TIMEZONE`` calendar day everywhere — the station
    derivation, the semantic layer, every oracle, the plot tool's aggregation —
    and the five tables hold naive UTC, so a conversion sits at every grouping
    site. It lives here, in one expression, for the reason ``decisions.md``
    § The day boundary gives: three call sites that each convert correctly today
    drift independently tomorrow. Grouping the raw column instead moves rain
    between days by one to two hours' worth, which is enough to move a peak-day
    argmax.
    """
    return f"(({column}) AT TIME ZONE 'UTC' AT TIME ZONE '{SITE_TIMEZONE}')::DATE"
