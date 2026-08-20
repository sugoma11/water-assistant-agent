"""Pure async client for the GR2L green-roof water-balance endpoint.

No ADK imports. Holds the four fixed roof-type presets for this deployment
(``ROOF_PRESETS``), resolves them into a full :class:`RoofParameters`, and POSTs
a :class:`Gr2lRequest` to ``{gr2l_api_base_url}/predict_gr2l``. See
``gr2l_tool.md`` for the parameter derivation and endpoint contract.

This is the *endpoint* layer: it supplies no weather of its own — ``run_gr2l``
takes the daily rows its caller has already resolved. The self-contained,
agent-facing wrapper that does fetch them lives in :mod:`gr2l`.

Site geometry is not hard-coded here either; the caller passes ``lat``/``long``/
``hoehe_nn`` (the tool wrapper takes them from :mod:`site`) so this module stays
reusable for another facility.
"""

from typing import Any

import httpx
import structlog

from water_assistant_agent.assistant.cache import Canary, ResponseCache
from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.tools.roofs import (
    MODELLED_ROOFS,
    ROOFS,
    gr2l_preset_values,
)
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    Gr2lRequest,
    Gr2lResultRow,
    RoofParameters,
)

logger = structlog.get_logger(__name__)

# A fixed, arbitrary probe request — never a real site's data — sent on every live
# fill to prove the service answers today what it is committed to have answered
# when the cache was captured. Its own first response becomes the committed
# canary entry; every later miss re-fetches it and the two must match byte for
# byte, or the run is treated as talking to a service that has moved
# (`decisions.md` § The response cache). Joins `eval/pins.json` in a later packet.
CANARY_REQUEST: dict[str, Any] = Gr2lRequest(
    data=[
        DailyWeatherRow(
            Date="2000-01-01",
            tm=10.0,
            tx=15.0,
            tn=5.0,
            rf=70.0,
            precip=0.0,
            w=5.0,
            gs=1000.0,
        )
    ],
    hoehe_nn=100.0,
    lat=50.0,
    long=10.0,
    SH=7,
    Ssubmin=0.9,
    Ssubmax=16.0,
    Sret=0,
    Sretmax=0,
    theta_01=8.0,
    theta_02=0,
    kg=1,
    albedo=0.2,
    open_water=False,
).model_dump(exclude_none=True)

# GR2L's upstream model service uses a 90 s timeout for the whole hop.
_TIMEOUT_SECONDS = 90.0

_GRAVEL_REASON = (
    "The gravel roof has no substrate layer, so the green-roof water-balance model "
    "cannot simulate it — its substrate height and storage bounds do not exist. Its "
    "measured sensor data (soil moisture, outflow, temperature) can still be queried."
)

_WETLAND_REASON = (
    "The wetland roof is outside the green-roof water-balance model's scope: its store "
    "is a fleece mat with water ponded above it, whose soil-moisture sensor saturates "
    "well below the ponding height, so the model's water-content contract cannot "
    "describe it — and that sensor has been failed since 2026-03-12, leaving nothing "
    "to start a simulation from. Its measured sensor data (soil moisture, outflow, "
    "temperature) can still be queried."
)

# Roof segments this facility has but the agent-facing tool cannot model, mapped
# to the reason. Both stay first-class roofs for the measured sensor data, so
# each is a scope limit to report, not a failure. Keys cover the names each goes
# by in the database and in German usage (principle 4); they are matched against
# a `normalize_roof_type`d argument, so case and surrounding space are already
# gone by the time this table is consulted.
#
# The wetland is here rather than expressed in `roof_type`'s type, and that is
# deliberate: a `Literal` would reach the model as a schema enum, and the
# abstention family asks precisely for a roof the tool declines — unaskable if
# the agent cannot name it (`agent_architecture.md` §3.4).
NON_MODELLABLE_ROOFS: dict[str, str] = {
    **dict.fromkeys(("gravel", "gravel_roof", "kies", "kiesdach", "kd", "qgravel"), _GRAVEL_REASON),
    **dict.fromkeys(
        ("wetland", "wetland_roof", "sumpf", "sumpfdach", "sumpf2", "qwetland"), _WETLAND_REASON
    ),
}

def normalize_roof_type(roof_type: str) -> str:
    """Fold *roof_type* to the one spelling every table here is keyed by.

    Called **once, at the tool's entry**, and every lookup downstream — the
    non-modellable table, the presets, the soil-moisture column, the echoed
    ``roof_type`` — uses the result. Normalizing at one call site out of five is
    what this replaces: ``" Semi_Intensive "`` used to pass the scope check and
    then fail as an unknown roof type, which is a different outcome for the same
    request depending on where in the wrapper it happened to be read.
    """
    return roof_type.strip().lower()


# The four roof types this deployment models, projected out of `roofs.ROOFS`
# (`agent_architecture.md` §1 principle 4) — the values below are that table's,
# rendered under GR2L's own argument names by `roofs.gr2l_preset_values`. None
# has a retention layer, so
# Sret/Sretmax/theta_02 are pinned to 0 and kg to 1 (not the model's generic
# defaults). Ssubmin/Ssubmax are measured from this site's soil-moisture record.
# The wetland's store is a 17 mm water-storage mat (recycled polypropylene
# fleece) plus water ponded above it up to the 9 cm outlet standpipes, so its
# Ssubmax=90 is structural (standpipe height) while Ssubmin=1.3 is the sensor
# p1 (7.55% θ × 17 mm). open_water=True makes GR2L evaporate at the potential
# rate (no Ssub/Ssubmax throttling) while still tracking the water balance;
# albedo=0.06 is the open-water value. Caveat: all segments are drip-irrigated
# and irrigation is NOT in the weather input, so wetland Ssub predictions from
# rain alone are a lower bound. See gr2l_tool.md "Roof types and their
# parameters".
#
# Every value here is a physical property of the installed roof and must not be
# varied per call -- except `albedo`, which is a per-roof DEFAULT the caller may
# override (see `resolve_roof_parameters`).
#
# The `wetland` entry is retained and **unreachable**: `NON_MODELLABLE_ROOFS`
# takes every name it goes by, so no layer-1 call gets here with it, while layer
# 2 still accepts it for a caller reading a run in mm throughout. Its values are
# pinned (`agent_architecture.md` §5, `gr2l_roof_presets_sha256`), so deleting
# them would move the pin — and with it the GR2L canary's comparability — for a
# branch nothing takes.
ROOF_PRESETS: dict[str, dict[str, float | bool]] = {
    name: gr2l_preset_values(ROOFS[name]) for name in MODELLED_ROOFS
}


class Gr2lConfigError(RuntimeError):
    """Raised when the GR2L base URL / API key are not configured."""


class _ClientHolder:
    """Module-level singleton holder for the shared httpx client."""

    instance: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazily create and cache a module-level ``httpx.AsyncClient``."""
    if _ClientHolder.instance is None:
        _ClientHolder.instance = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    return _ClientHolder.instance


def resolve_roof_parameters(
    roof_type: str,
    *,
    hoehe_nn: float,
    lat: float,
    long: float,
    theta_01: float,
    albedo: float | None = None,
) -> RoofParameters:
    """Expand a roof-type name into the full GR2L parameter set.

    ``theta_01`` is the day-1 substrate state **in mm**, and it is required: the
    model's generic 20 mm default sits above ``Ssubmax`` for three of the four
    roof types, so falling back to it silently started every simulation from a
    saturated roof. The tool wrapper resolves it from the roof's own sensor
    (:mod:`swc`) or from the caller, and converts %θ → mm on the way in.

    ``albedo`` is the one preset value a caller may replace, falling back to the
    roof's own default when ``None``: it describes the surface's *current*
    appearance (dry vegetation, a reflective coating, snow), not the roof's
    structure — see ``gr2l_tool.md`` "Overriding albedo". Every other preset
    value is physical and stays fixed.

    Raises ``ValueError`` listing the valid types on an unknown ``roof_type``,
    or when ``albedo`` falls outside 0.0-1.0.
    """
    preset = ROOF_PRESETS.get(roof_type)
    if preset is None:
        valid = ", ".join(sorted(ROOF_PRESETS))
        raise ValueError(f"Unknown roof_type {roof_type!r}. Valid types: {valid}.")

    values = dict(preset)
    if albedo is not None:
        if not 0.0 <= albedo <= 1.0:
            raise ValueError(f"albedo must be between 0.0 and 1.0, got {albedo}.")
        values["albedo"] = albedo

    return RoofParameters(
        theta_01=theta_01,
        hoehe_nn=hoehe_nn,
        lat=lat,
        long=long,
        **values,
    )


async def _post_gr2l(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    """Raw POST to ``{url}``; returns the parsed JSON payload."""
    response = await _get_client().post(url, json=body, headers=headers)
    response.raise_for_status()
    return response.json()


def _endpoint() -> tuple[str, dict[str, str]]:
    """The URL and headers every request to this service uses.

    One place builds them, so the canary probe cannot end up addressed to a
    different deployment than the requests whose comparability it is asserting.

    Raises :class:`Gr2lConfigError` when the base URL or key is unset.
    """
    settings = get_settings()
    if not settings.gr2l_api_base_url or not settings.gr2l_api_key:
        raise Gr2lConfigError(
            "GR2L is not configured: set WATER_ASSISTANT_GR2L_API_BASE_URL and "
            "GR2L_MODEL_API_KEY in the environment."
        )
    return (
        f"{settings.gr2l_api_base_url.rstrip('/')}/predict_gr2l",
        {"API-KEY": settings.gr2l_api_key, "Content-Type": "application/json"},
    )


async def fetch_canary() -> Any:
    """POST :data:`CANARY_REQUEST` to the live service and return its response.

    The service-version probe, run **live and deliberately**: it is what
    ``eval/pins.json``'s ``gr2l_canary_response_sha256`` is the hash of, and what
    every later cache fill re-fetches and compares against
    (``agent_architecture.md`` §5). It goes through the same :func:`_endpoint` and
    the same POST as any other request, so a pin captured here and a canary
    verified during a capture pass cannot diverge by construction.

    Closes the module client afterwards: the one caller is a script, and leaving
    a live connection behind an ``asyncio.run`` boundary is a warning at exit.
    """
    url, headers = _endpoint()
    try:
        return await _post_gr2l(url, headers, CANARY_REQUEST)
    finally:
        if _ClientHolder.instance is not None:
            await _ClientHolder.instance.aclose()
            _ClientHolder.instance = None


async def run_gr2l(
    rows: list[DailyWeatherRow],
    parameters: RoofParameters,
    *,
    cache: ResponseCache | None = None,
) -> list[Gr2lResultRow]:
    """POST daily weather + roof parameters to GR2L; return one row per day.

    ``rows`` must be chronological and contain one entry per day to simulate:
    the endpoint neither fetches nor extrapolates weather, and a day that is
    absent is simply not simulated. The first row is a seed day — it initialises
    ``Ssub``/``Sret`` from ``theta_01``/``theta_02`` and computes ``ET``, so its
    ``Qdown``/``Qup``/``OUT`` come back ``None``.

    When *cache* is given, the request is routed through it — keyed on
    ``data[]`` plus the roof parameters, gated on :data:`CANARY_REQUEST` matching
    the committed canary before any new entry records (`decisions.md` § The
    response cache) — and a cache hit issues no HTTP call at all. Omitting *cache*
    calls GR2L directly every time, unchanged from before this cache existed.

    Raises :class:`Gr2lConfigError` when unconfigured, ``httpx``/parse errors on a
    direct call, and :class:`~water_assistant_agent.assistant.cache.CacheMissError`
    / :class:`~water_assistant_agent.assistant.cache.CanaryMismatchError` on a
    cached call's respective failures (the ADK tool wrapper catches and converts
    to an ``ErrorResult``).
    """
    url, headers = _endpoint()
    request = Gr2lRequest(data=rows, **parameters.model_dump())
    canonical_request = request.model_dump(exclude_none=True)

    logger.debug("Calling GR2L", url=url, days=len(rows), cached=cache is not None)

    async def live_fetch() -> Any:
        return await _post_gr2l(url, headers, canonical_request)

    if cache is None:
        payload = await live_fetch()
    else:
        canary = Canary(CANARY_REQUEST, lambda: _post_gr2l(url, headers, CANARY_REQUEST))
        payload = await cache.fetch(canonical_request, live_fetch, canary=canary)

    return [Gr2lResultRow.model_validate(row) for row in payload.get("data", [])]
