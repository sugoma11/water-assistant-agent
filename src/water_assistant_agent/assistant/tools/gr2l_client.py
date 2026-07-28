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

import httpx
import structlog

from water_assistant_agent.assistant.settings import get_settings
from water_assistant_agent.assistant.tools.schemas import (
    DailyWeatherRow,
    Gr2lRequest,
    Gr2lResultRow,
    RoofParameters,
)

logger = structlog.get_logger(__name__)

# GR2L's upstream model service uses a 90 s timeout for the whole hop.
_TIMEOUT_SECONDS = 90.0

# Roof segments this facility has but GR2L cannot model, mapped to the reason.
# The gravel roof has no substrate, so the SH / Ssubmin / Ssubmax the two-layer
# balance is built on do not exist for it — there is nothing to simulate. It
# stays a first-class roof for the measured sensor data, so this is a scope
# limit to report, not a failure. Keys cover the names it goes by in the
# database and in German usage.
NON_MODELLABLE_ROOFS: dict[str, str] = dict.fromkeys(
    ("gravel", "gravel_roof", "kies", "kiesdach", "kd", "qgravel"),
    "The gravel roof has no substrate layer, so the green-roof water-balance model "
    "cannot simulate it — its substrate height and storage bounds do not exist. Its "
    "measured sensor data (soil moisture, outflow, temperature) can still be queried.",
)

# The four roof types this deployment models. None has a retention layer, so
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
ROOF_PRESETS: dict[str, dict[str, float | bool]] = {
    "wetland": {"SH": 1.7, "Ssubmin": 1.3, "Ssubmax": 90, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.06, "open_water": True},  # noqa: E501
    "non_irrigated_extensive": {"SH": 7, "Ssubmin": 0.9, "Ssubmax": 16.0, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.2, "open_water": False},  # noqa: E501
    "irrigated_extensive": {"SH": 7, "Ssubmin": 3.3, "Ssubmax": 22.8, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.2, "open_water": False},  # noqa: E501
    "semi_intensive": {"SH": 15, "Ssubmin": 6.3, "Ssubmax": 45.6, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.2, "open_water": False},  # noqa: E501
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


async def run_gr2l(
    rows: list[DailyWeatherRow],
    parameters: RoofParameters,
) -> list[Gr2lResultRow]:
    """POST daily weather + roof parameters to GR2L; return one row per day.

    ``rows`` must be chronological and contain one entry per day to simulate:
    the endpoint neither fetches nor extrapolates weather, and a day that is
    absent is simply not simulated. The first row is a seed day — it initialises
    ``Ssub``/``Sret`` from ``theta_01``/``theta_02`` and computes ``ET``, so its
    ``Qdown``/``Qup``/``OUT`` come back ``None``.

    Raises :class:`Gr2lConfigError` when unconfigured and ``httpx``/parse errors
    otherwise (the ADK tool wrapper catches and converts to an ``ErrorResult``).
    """
    settings = get_settings()
    if not settings.gr2l_api_base_url or not settings.gr2l_api_key:
        raise Gr2lConfigError(
            "GR2L is not configured: set WATER_ASSISTANT_GR2L_API_BASE_URL and "
            "GR2L_MODEL_API_KEY in the environment."
        )

    request = Gr2lRequest(data=rows, **parameters.model_dump())
    url = f"{settings.gr2l_api_base_url.rstrip('/')}/predict_gr2l"
    headers = {"API-KEY": settings.gr2l_api_key, "Content-Type": "application/json"}

    logger.debug("Calling GR2L", url=url, days=len(rows))
    response = await _get_client().post(
        url,
        json=request.model_dump(exclude_none=True),
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    return [Gr2lResultRow.model_validate(row) for row in payload.get("data", [])]
