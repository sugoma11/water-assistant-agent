"""FAO-56 Penman-Monteith reference evapotranspiration, ported from GR2L's R.

``DailyWeatherRow`` carries no ``et0`` and its schema is frozen, so the
irrigation bucket has to compute its own forcing (``agent_architecture.md``
§3.5). It could compute it from the textbook — and it deliberately does not.

**This is a port, not an implementation.** Every line below is
``gr2l_model/R/GR2L_function.R:34-74`` in the weinbau API checkout
(``findings.md`` § External sources on this machine), transcribed term for term
including the places where that routine departs from FAO-56. The reason is
comparability: the same site runs the R endpoint and this testbed, and an
irrigation answer here has to be readable against a GR2L run there. Two
implementations that are each defensible on their own terms and disagree by 20 %
are worse than one convention applied twice — a difference in a decision could
then be the model, the units, or the ET0, with no way to tell which.

Its input row is a :class:`~.tools.schemas.DailyWeatherRow`, so the units are
that contract's and not FAO-56's: ``w`` in **km/h** (the R divides by 3.6) and
``gs`` in **J/cm²/day** (the R's two-step conversion is exactly ×0.01 into
MJ/m²/day). ``weather_tool.md`` derives both from the station record for the
same reason.

The one deliberate difference is **albedo**: the R now takes it as an argument
(:71, defaulting to 0.2), while ET0 is by definition the reference crop's, at 0.23
(``agent_architecture.md`` §3.5) — roof-specific throttling is the stress
coefficient's job, not the forcing's. It is a parameter here, defaulting to
:data:`REFERENCE_ALBEDO`; passing :data:`GR2L_ALBEDO` reproduces the R exactly,
which is what the port's test asserts.

**Scope limits carried over deliberately.** Each of these is the R's, kept so the
two languages agree, and none is a bug introduced here:

- ``Pressure <- 100`` kPa at :46 is fixed rather than derived from elevation.
  FAO-56 eq. 7 gives 99.63 kPa at this site's 142 m, so ``gamma`` runs 0.37 %
  high; across four days spread over the year that moves ET0 by at most 0.10 %.
  It is the smallest of the four by a wide margin, and it would take ~2000 m of
  elevation to reach 4 %.
- ``R_a`` at :67 omits the solar constant ``Gsc``, which :61 defines and never
  uses, so extraterrestrial radiation comes out ~12× (1/0.082) too large. It
  reaches ET0 only through ``Rs/Rso`` inside the cloudiness factor, which is
  therefore pinned near its floor: the net-longwave term behaves as a small
  constant gain instead of tracking cloud cover.
- ``es`` at :38 divides by ``238 + tm`` where FAO-56 eq. 11 has 237.3 — while
  ``Delta`` at :52, computing the slope of that same curve, does use 237.3.

**A third departure was here and is gone, because the R fixed it.** ``Rnl`` took
``tx**4 + tn**4 / 2`` in degrees Celsius where eq. 39 averages both fourth powers
in kelvin; upstream commit ``3e7405a`` corrected it — along with the precedence
slip in the same expression — and this port followed on 2026-08-24 at the site's
direction. The two remaining departures interact with it in a way worth keeping
in view: because ``Gsc`` still pins the cloudiness factor negative, ``Rnl`` enters
as a *gain*, so correcting its magnitude **raised** ET0 rather than lowering it,
by 0.19–0.45 mm/day depending on the day (``findings.md``).

Together the survivors still make the absolute ET0 the R's convention rather than
FAO-56's, the missing ``Gsc`` doing most of it (``findings.md`` § External sources
on this machine). What they do not do is vary between the two implementations,
which is the property the irrigation decision needs. **The corollary is that this
change did retune the controller**, which is why it was the site's call and not
this repository's: the trigger levels in :mod:`.rules_constants` are unchanged and
are still the deployed ones, so the same thresholds are now compared against a
larger ET (``decisions.md`` § No fitted correction between the instrument and the
oracle). ``agent_architecture.md`` §8 is where that bound belongs in prose.

Checked against the running service, not only against the source: for four days
at ``albedo=0.2`` the endpoint's own ``ET_PM`` and this function agree to within
5e-5 mm, which is the rounding in the response. Those four days are committed as
a fixture in ``tests/assistant/test_et_fao56.py`` — the service is not part of
this test suite, and a test that called it would stop testing the port the day
the container is down.

Pure and local: no I/O, no clock, no settings. An oracle imports this function
and gets the number the tool used.
"""

import dataclasses
import math
from datetime import date

from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow

REFERENCE_ALBEDO = 0.23
"""The FAO-56 reference crop's albedo — what ET0 means, and this port's default."""

GR2L_ALBEDO = 0.2
"""The R's own default, ``albedo = 0.2`` at ``GR2L_function.R:25``; passing it reproduces the R."""

# The R's "Define constants" block, :44-49, transcribed with its own arithmetic
# rather than pre-multiplied: `c_P` is written as 1.013e-3 × 1e6 there, and a
# reader checking this file against that one should not have to evaluate
# anything to see they agree. The comments in the R label these MJ kg⁻¹ °C⁻¹ and
# MJ kg⁻¹; the values are J, which is what makes `gamma` come out in kPa/°C.
_C_P = 1.013 * 10 ** (-3) * 10**6
PRESSURE_KPA = 100.0
"""``Pressure <- 100`` at :46 — fixed, not derived from elevation. See above."""

_EPSILON = 0.622
_LAMBDA = 2.45 * 10**6
_GAMMA = _C_P * PRESSURE_KPA / (_EPSILON * _LAMBDA)
"""The psychrometric constant, ~0.0665 kPa/°C. Fixed, because the pressure is."""

_SIGMA_MJ = 4.903 * 10 ** (-9)
"""Stefan-Boltzmann, MJ K⁻⁴ m⁻² day⁻¹ (:59)."""

_KELVIN = 273.16
"""°C → K for the Stefan-Boltzmann term (:70), FAO-56 eq. 39's own offset.

273.16 rather than 273.15 because that is what eq. 39 prints and what the R
writes; the difference is 0.01 K on a fourth power and reaches ET0 far below the
service's rounding.
"""

_WIND_KMH_TO_MS = 3.6
"""``u2 <- w / 3.6`` at :42 — the row's ``w`` is km/h, as ``weather_tool.md`` says."""

_J_PER_CM2_TO_MJ_PER_M2 = 8.64
"""The first half of :56's ``gs`` conversion; the whole of it is ×0.01."""


@dataclasses.dataclass(frozen=True, slots=True)
class Et0Terms:
    """Every intermediate the R computes, in its own order and units.

    Returned rather than kept local so the port can be checked *term for term*
    against the R — which is the exit criterion — instead of only at the last
    line, where four compensating errors would look like agreement.
    """

    es: float
    """Saturation vapour pressure, kPa (:38)."""

    ea: float
    """Actual vapour pressure, kPa (:39)."""

    u2: float
    """Wind speed at 2 m, m/s (:42)."""

    delta: float
    """Slope of the saturation vapour-pressure curve, kPa/°C (:52)."""

    gamma: float
    """Psychrometric constant, kPa/°C (:49) — constant, at the fixed pressure."""

    rs: float
    """Incoming shortwave radiation, MJ/m²/day (:56)."""

    ra: float
    """Extraterrestrial radiation (:67) — missing ``Gsc``; see the module docstring."""

    rso: float
    """Clear-sky radiation, from :data:`ra` (:68)."""

    rnl: float
    """Net longwave radiation, MJ/m²/day (:70)."""

    rn: float
    """Net radiation, MJ/m²/day (:71) — the one line albedo enters."""

    et0_mm: float
    """Reference evapotranspiration, mm/day (:74) — the R's ``ET_PM``."""


def et0_terms(
    *,
    tm: float,
    tx: float,
    tn: float,
    rf: float,
    w: float,
    gs: float,
    day_of_year: int,
    latitude: float,
    elevation_m: float,
    albedo: float = REFERENCE_ALBEDO,
) -> Et0Terms:
    """Every step of ``GR2L_function.R:35-71`` for one day, in that order.

    Arguments carry the R's names where it has them: *tm* mean, *tx* max and
    *tn* min air temperature in °C, *rf* relative humidity in %, *w* wind speed
    in **km/h**, *gs* global radiation in **J/cm²/day**. *day_of_year* is the R's
    ``df$doy``, *latitude* its ``lat`` in degrees and *elevation_m* its
    ``hoehe_nn``.

    Raises ``ValueError`` on an albedo outside 0-1 and on a latitude where
    ``omega_s`` is undefined — a polar day or night, where ``-tan(φ)tan(δ)``
    leaves [-1, 1] and the R would produce ``NaN`` and carry it silently into
    every downstream field.
    """
    if not 0.0 <= albedo <= 1.0:
        raise ValueError(f"albedo must be between 0.0 and 1.0, got {albedo}.")

    # :36-37 — saturation and actual vapour pressure. The 238 is the R's; FAO-56
    # eq. 11 has 237.3, which `delta` below does use.
    es = 0.6108 * math.exp(17.27 * tm / (238 + tm))
    ea = rf / 100 * es

    # :40 — wind at 2 m.
    u2 = w / _WIND_KMH_TO_MS

    # :50 — slope of the saturation vapour-pressure curve.
    delta = (4098 * (0.6108 * math.exp(17.27 * tm / (tm + 237.3)))) / (tm + 237.3) ** 2

    # :53-54 — J/cm²/day → MJ/m²/day, in the R's two steps.
    rs = gs / _J_PER_CM2_TO_MJ_PER_M2
    rs = rs / 1e6 * 86400

    # :58-66 — extraterrestrial and clear-sky radiation. `Gsc` is defined at :59
    # and does not appear at :65; the omission is carried, not repaired.
    phi = math.pi / 180 * latitude
    d_r = 1 + 0.033 * math.cos(2 * math.pi / 365 * day_of_year)
    delta_klein = 0.409 * math.sin((2 * math.pi / 365 * day_of_year) - 1.39)
    sunset_cosine = -math.tan(phi) * math.tan(delta_klein)
    if not -1.0 <= sunset_cosine <= 1.0:
        raise ValueError(
            f"No sunset-hour angle at latitude {latitude} on day {day_of_year}: "
            "the sun neither rises nor sets, and ET0 is undefined here."
        )
    omega_s = math.acos(sunset_cosine)
    ra = (
        24
        * 60
        / math.pi
        * d_r
        * (
            omega_s * math.sin(phi) * math.sin(delta_klein)
            + math.cos(phi) * math.cos(delta_klein) * math.sin(omega_s)
        )
    )
    rso = (0.75 + 2 * 10 ** (-5) * elevation_m) * ra

    # :70-71 — net longwave and net radiation, in **kelvin** and averaging both
    # fourth powers, as FAO-56 eq. 39 has it and as the R has had it since
    # `3e7405a`. The line it replaced read `tx**4 + tn**4 / 2` in °C, which was
    # two faults at once: the missing absolute temperature and a precedence slip
    # halving `tn**4` alone instead of the pair.
    rnl = (
        _SIGMA_MJ
        * (((tx + _KELVIN) ** 4 + (tn + _KELVIN) ** 4) / 2)
        * (0.34 - 0.14 * ea ** (1 / 2))
        * (1.35 * (rs / rso) - 0.35)
    )
    rn = rs * (1 - albedo) - rnl

    # :71 — FAO-56 Penman-Monteith.
    et0_mm = (0.408 * delta * rn + 900 / (273 + tm) * (_GAMMA * u2 * (es - ea))) / (
        delta + _GAMMA * (1 + 0.34 * u2)
    )

    return Et0Terms(
        es=es,
        ea=ea,
        u2=u2,
        delta=delta,
        gamma=_GAMMA,
        rs=rs,
        ra=ra,
        rso=rso,
        rnl=rnl,
        rn=rn,
        et0_mm=et0_mm,
    )


def et0_fao56(
    *,
    tm: float,
    tx: float,
    tn: float,
    rf: float,
    w: float,
    gs: float,
    day_of_year: int,
    latitude: float,
    elevation_m: float,
    albedo: float = REFERENCE_ALBEDO,
) -> float:
    """Reference evapotranspiration for one day, mm — :attr:`Et0Terms.et0_mm`.

    The whole-number answer; :func:`et0_terms` is the same computation with its
    intermediates exposed.
    """
    return et0_terms(
        tm=tm,
        tx=tx,
        tn=tn,
        rf=rf,
        w=w,
        gs=gs,
        day_of_year=day_of_year,
        latitude=latitude,
        elevation_m=elevation_m,
        albedo=albedo,
    ).et0_mm


def et0_for_row(
    row: DailyWeatherRow,
    *,
    latitude: float,
    elevation_m: float,
    albedo: float = REFERENCE_ALBEDO,
) -> float:
    """ET0 for one weather day, mm — the same row GR2L would be sent.

    The R derives its day of year from the ``Date`` column (``strftime("%j")``,
    :33); this does the same from the row's ISO date, so no caller computes a day
    number of its own. Raises ``ValueError`` on a ``Date`` that is not ISO.
    """
    day_of_year = date.fromisoformat(row.Date).timetuple().tm_yday
    return et0_fao56(
        tm=row.tm,
        tx=row.tx,
        tn=row.tn,
        rf=row.rf,
        w=row.w,
        gs=row.gs,
        day_of_year=day_of_year,
        latitude=latitude,
        elevation_m=elevation_m,
        albedo=albedo,
    )
