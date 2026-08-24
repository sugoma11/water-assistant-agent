"""The ET0 port against the routine it is a port of (T061).

Two independent standards of evidence, because a port can fail in two ways.
Term by term against a second transcription of ``GR2L_function.R:35-71``, which
catches a slipped constant that the final number might absorb; and against
``ET_PM`` as the GR2L service returned it, committed here as a fixture, which
catches a reading of the R that is internally consistent and still wrong.

**The fixture is a historical capture, and since 2026-08-24 it is no longer what
the service returns.** Upstream commit ``3e7405a`` moved the R's ``Rnl`` line to
absolute temperature and the deployed build followed it; this port did not, so it
remains faithful to the checkout as it stood when these four values were served
(``findings.md`` § External sources on this machine). Both assertions below
therefore still hold and still mean what they say — the port reproduces *that* R,
term by term and end to end. What they no longer establish is agreement with the
endpoint today, and whether the port should follow the R is an open decision
rather than a bug (``decisions.md`` § No fitted correction between the instrument
and the oracle).

The served values are a fixture rather than a live call on purpose: the endpoint
is not part of this suite (``agent_architecture.md`` §3.5 — irrigation is fully
offline), and a test that fetched them would stop testing the port the first day
the container is down.
"""

import math
from datetime import date

import pytest

from water_assistant_agent.assistant.et_fao56 import (
    GR2L_ALBEDO,
    PRESSURE_KPA,
    REFERENCE_ALBEDO,
    et0_fao56,
    et0_for_row,
    et0_terms,
)
from water_assistant_agent.assistant.tools.schemas import DailyWeatherRow

SITE_LATITUDE = 51.353484
SITE_ELEVATION_M = 142.0

# Four days spread across the year, in the units `DailyWeatherRow` carries:
# `w` km/h, `gs` J/cm²/day. Midsummer, midwinter, a spring day, a heat day.
DAYS = [
    DailyWeatherRow(Date="2025-06-10", tm=18.4, tx=25.1, tn=11.2, rf=62.0, precip=0.0, w=9.0, gs=2400.0),
    DailyWeatherRow(Date="2025-12-21", tm=1.2, tx=3.4, tn=-2.1, rf=91.0, precip=4.2, w=14.5, gs=180.0),
    DailyWeatherRow(Date="2026-03-15", tm=8.0, tx=13.0, tn=2.0, rf=70.0, precip=1.0, w=7.2, gs=1100.0),
    DailyWeatherRow(Date="2025-08-01", tm=24.9, tx=33.2, tn=16.8, rf=45.0, precip=0.0, w=5.5, gs=2750.0),
]

# `ET_PM` as `POST /predict_gr2l` returned it for exactly those four rows, at
# this site's geometry and `albedo=0.2` — the value the R hard-codes. Captured
# 2026-08-20 against the same endpoint `gr2l_canary_response_sha256` is pinned
# against (`findings.md` § External sources on this machine). The service rounds
# to four decimals, which is the tolerance below.
SERVED_ET_PM = {
    "2025-06-10": 5.6745,
    "2025-12-21": 0.3936,
    "2026-03-15": 2.1606,
    "2025-08-01": 7.6684,
}
SERVICE_ROUNDING = 5e-5


def _r_routine(row: DailyWeatherRow, day_of_year: int, albedo: float) -> dict[str, float]:
    """A second transcription of ``GR2L_function.R:35-71``, read off the R again.

    Deliberately not sharing a line with the module under test: it names the
    R's own variables, keeps its two-step radiation conversion and its
    departures from FAO-56 (238 in ``es``, no ``Gsc`` in ``R_a``, ``Rnl`` in
    °C with ``tn**4`` alone halved), and computes nothing the R does not.
    """
    tm, tx, tn, rf, w, gs = row.tm, row.tx, row.tn, row.rf, row.w, row.gs
    es = 0.6108 * math.exp(17.27 * tm / (238 + tm))
    ea = rf / 100 * es
    u2 = w / 3.6
    c_p = 1.013 * 10 ** (-3) * 10**6
    pressure = 100
    epsilon = 0.622
    lambda_ = 2.45 * 10**6
    gamma = c_p * pressure / (epsilon * lambda_)
    delta = (4098 * (0.6108 * math.exp(17.27 * tm / (tm + 237.3)))) / (tm + 237.3) ** 2
    rs = gs / 8.64
    rs = rs / 1e6 * 86400
    sigma_mj = 4.903 * 10 ** (-9)
    z_nn = SITE_ELEVATION_M
    phi = math.pi / 180 * SITE_LATITUDE
    j = day_of_year
    d_r = 1 + 0.033 * math.cos(2 * math.pi / 365 * j)
    delta_klein = 0.409 * math.sin((2 * math.pi / 365 * j) - 1.39)
    omega_s = math.acos(-math.tan(phi) * math.tan(delta_klein))
    r_a = (
        24
        * 60
        / math.pi
        * d_r
        * (
            omega_s * math.sin(phi) * math.sin(delta_klein)
            + math.cos(phi) * math.cos(delta_klein) * math.sin(omega_s)
        )
    )
    rso = (0.75 + 2 * 10 ** (-5) * z_nn) * r_a
    rnl = (
        sigma_mj
        * (tx**4 + tn**4 / 2)
        * (0.34 - 0.14 * (ea) ** (1 / 2))
        * (1.35 * (rs / rso) - 0.35)
    )
    rn = rs * (1 - albedo) - rnl
    et_pm = (0.408 * delta * rn + 900 / (273 + tm) * (gamma * u2 * (es - ea))) / (
        delta + gamma * (1 + 0.34 * u2)
    )
    return {
        "es": es,
        "ea": ea,
        "u2": u2,
        "delta": delta,
        "gamma": gamma,
        "rs": rs,
        "ra": r_a,
        "rso": rso,
        "rnl": rnl,
        "rn": rn,
        "et0_mm": et_pm,
    }


def _day_of_year(row: DailyWeatherRow) -> int:
    """The R's ``strftime("%j")``, counted here rather than taken from the module."""
    day = date.fromisoformat(row.Date)
    return (day - date(day.year, 1, 1)).days + 1


# --- Term for term ------------------------------------------------------------


@pytest.mark.parametrize("row", DAYS, ids=[row.Date for row in DAYS])
@pytest.mark.parametrize("term", ["es", "ea", "u2", "delta", "gamma", "rs", "ra", "rso", "rnl", "rn", "et0_mm"])
def test_every_term_matches_the_r_routine(row: DailyWeatherRow, term: str) -> None:
    """The exit criterion, one intermediate at a time.

    Asserting only ``ET_PM`` would let two errors cancel — a radiation
    conversion off by a factor and a net-radiation term compensating it is
    exactly the shape that survives an end-to-end check.
    """
    expected = _r_routine(row, _day_of_year(row), GR2L_ALBEDO)
    terms = et0_terms(
        tm=row.tm,
        tx=row.tx,
        tn=row.tn,
        rf=row.rf,
        w=row.w,
        gs=row.gs,
        day_of_year=_day_of_year(row),
        latitude=SITE_LATITUDE,
        elevation_m=SITE_ELEVATION_M,
        albedo=GR2L_ALBEDO,
    )

    assert getattr(terms, term) == pytest.approx(expected[term], rel=1e-12)


def test_the_carried_simplifications_are_the_ones_the_r_makes() -> None:
    """The three that a well-meaning cleanup would "fix" first.

    Fixed pressure at 100 kPa (:44), extraterrestrial radiation without ``Gsc``
    (:59, :65), and net longwave taken over Celsius fourth powers (:67). Each is
    a documented scope limit; each is asserted here so removing one is a test
    failure rather than a quiet improvement that ends comparability with the
    endpoint.
    """
    row = DAYS[0]
    terms = et0_terms(
        tm=row.tm,
        tx=row.tx,
        tn=row.tn,
        rf=row.rf,
        w=row.w,
        gs=row.gs,
        day_of_year=161,
        latitude=SITE_LATITUDE,
        elevation_m=SITE_ELEVATION_M,
    )

    assert PRESSURE_KPA == 100.0
    # FAO-56 eq. 7 at 142 m is 99.63 kPa; the fixed value is not it.
    assert PRESSURE_KPA != pytest.approx(101.3 * ((293 - 0.0065 * 142) / 293) ** 5.26, rel=1e-3)
    # With `Gsc` = 0.0820 MJ m⁻² min⁻¹ present, `R_a` would be 1/0.082 smaller.
    assert terms.ra > 400
    # Which pins the cloudiness factor near its floor, so net longwave is a
    # small *gain* rather than the loss FAO-56 computes.
    assert terms.rnl < 0
    assert terms.rn > terms.rs * (1 - REFERENCE_ALBEDO)


# --- Against the running service ----------------------------------------------


@pytest.mark.parametrize("row", DAYS, ids=[row.Date for row in DAYS])
def test_the_port_reproduces_what_the_endpoint_served(row: DailyWeatherRow) -> None:
    """The port against the deployed R, not only against the checked-out R.

    The checkout has no ``albedo`` argument and the service does, so the two are
    not the same build; this is the assertion that the ET routine did not move
    between them.
    """
    computed = et0_for_row(
        row,
        latitude=SITE_LATITUDE,
        elevation_m=SITE_ELEVATION_M,
        albedo=GR2L_ALBEDO,
    )

    assert computed == pytest.approx(SERVED_ET_PM[row.Date], abs=SERVICE_ROUNDING)


# --- Albedo -------------------------------------------------------------------


def test_the_default_is_the_reference_crop_not_the_r_constant() -> None:
    """ET0 is the reference crop's, at 0.23 (`agent_architecture.md` §3.5).

    The R's 0.2 is a green-roof value; roof-specific behaviour belongs to the
    stress coefficient, not to the forcing every roof shares.
    """
    assert REFERENCE_ALBEDO == 0.23
    row = DAYS[0]

    default = et0_for_row(row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M)
    explicit = et0_for_row(
        row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M, albedo=REFERENCE_ALBEDO
    )
    as_the_r_does = et0_for_row(
        row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M, albedo=GR2L_ALBEDO
    )

    assert default == explicit
    # A brighter surface reflects more, so the reference crop evaporates less.
    assert default < as_the_r_does


def test_albedo_enters_only_through_net_radiation() -> None:
    """Every other term is albedo-free, which is why one line carries it (:68)."""
    row = DAYS[3]
    shared = dict(
        tm=row.tm,
        tx=row.tx,
        tn=row.tn,
        rf=row.rf,
        w=row.w,
        gs=row.gs,
        day_of_year=213,
        latitude=SITE_LATITUDE,
        elevation_m=SITE_ELEVATION_M,
    )

    dark = et0_terms(**shared, albedo=0.05)
    bright = et0_terms(**shared, albedo=0.9)

    for term in ("es", "ea", "u2", "delta", "gamma", "rs", "ra", "rso", "rnl"):
        assert getattr(dark, term) == getattr(bright, term)
    assert dark.rn > bright.rn
    assert dark.et0_mm > bright.et0_mm


def test_an_albedo_outside_zero_to_one_is_refused() -> None:
    """The same bound `resolve_roof_parameters` puts on the GR2L argument."""
    with pytest.raises(ValueError, match="albedo"):
        et0_fao56(
            tm=10.0,
            tx=15.0,
            tn=5.0,
            rf=70.0,
            w=5.0,
            gs=1000.0,
            day_of_year=1,
            latitude=SITE_LATITUDE,
            elevation_m=SITE_ELEVATION_M,
            albedo=1.5,
        )


# --- The row adapter ----------------------------------------------------------


@pytest.mark.parametrize("row", DAYS, ids=[row.Date for row in DAYS])
def test_the_row_adapter_derives_the_day_of_year_the_r_does(row: DailyWeatherRow) -> None:
    """`strftime("%j")` on the `Date` column (:33), so no caller counts days itself."""
    assert et0_for_row(row, latitude=SITE_LATITUDE, elevation_m=SITE_ELEVATION_M) == et0_fao56(
        tm=row.tm,
        tx=row.tx,
        tn=row.tn,
        rf=row.rf,
        w=row.w,
        gs=row.gs,
        day_of_year=_day_of_year(row),
        latitude=SITE_LATITUDE,
        elevation_m=SITE_ELEVATION_M,
    )


def test_a_latitude_with_no_sunset_is_refused_rather_than_returned_as_nan() -> None:
    """The R would produce `NaN` here and carry it into every downstream field.

    Not a case this deployment can reach — the site is at 51.35° N — but the
    core is importable by an oracle, and a silent `NaN` in a water balance is
    the failure that takes longest to find.
    """
    with pytest.raises(ValueError, match="neither rises nor sets"):
        et0_fao56(
            tm=0.0,
            tx=2.0,
            tn=-5.0,
            rf=80.0,
            w=10.0,
            gs=100.0,
            day_of_year=172,
            latitude=80.0,
            elevation_m=SITE_ELEVATION_M,
        )
