"""The one roof table, and the two projections that used to be tables (T060).

Three things this file is here to catch. That repointing ``ROOF_SWC_COLUMNS``
and ``ROOF_PRESETS`` at :mod:`roofs` moved no value — asserted against the
literals as they stood before the repoint, and against
``gr2l_roof_presets_sha256``, which sees a retype the literals do not. That every
column name in the table is a column the pinned database actually has. And that
the plausibility bounds admit the healthy record while rejecting the one stretch
of it that is known to be a dead sensor.
"""

import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from water_assistant_agent.assistant.tools.gr2l_client import (
    NON_MODELLABLE_ROOFS,
    ROOF_PRESETS,
    normalize_roof_type,
)
from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    MODELLED_ROOFS,
    RADIATION_BOUNDS,
    RADIATION_COLUMN_SUFFIXES,
    ROOFS,
    VERSION,
    column_bounds,
    radiation_column,
    resolve_roof,
    roofs_with_column,
)
from water_assistant_agent.assistant.tools.swc import ROOF_SWC_COLUMNS

DB_PATH = "data/water.duckdb"
PINS_PATH = Path("eval/pins.json")

# The two projections exactly as they read before T060 repointed them. Restated
# rather than derived, so a failure shows which value moved.
SWC_COLUMNS_BEFORE = {
    "wetland": "QWetland",
    "non_irrigated_extensive": "QEx2",
    "irrigated_extensive": "QEx1",
    "semi_intensive": "QIn",
}
PRESETS_BEFORE = {
    "wetland": {"SH": 1.7, "Ssubmin": 1.3, "Ssubmax": 90, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.06, "open_water": True},  # noqa: E501
    "non_irrigated_extensive": {"SH": 7, "Ssubmin": 0.9, "Ssubmax": 16.0, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.2, "open_water": False},  # noqa: E501
    "irrigated_extensive": {"SH": 7, "Ssubmin": 3.3, "Ssubmax": 22.8, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.2, "open_water": False},  # noqa: E501
    "semi_intensive": {"SH": 15, "Ssubmin": 6.3, "Ssubmax": 45.6, "Sret": 0, "Sretmax": 0, "theta_02": 0, "kg": 1, "albedo": 0.2, "open_water": False},  # noqa: E501
}

# The dead stretch of `swc.QWetland`: a flat ~0 %θ from here to the record's end,
# where the healthy record runs 4–96 %θ (`findings.md` § The `QWetland` sensor is
# dead from 2026-03-12).
QWETLAND_DEAD_FROM = "2026-03-12"

_SITE_DAY = "((timestamp) AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Berlin')::DATE"


def _daily_means(table: str, column: str, where: str = "TRUE") -> list[float]:
    """Every site-day mean of *column*, the level a plausibility bound is read at."""
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = connection.execute(
            f'SELECT avg("{column}") FROM {table} '  # noqa: S608 - names come from ROOFS
            f"WHERE {where} GROUP BY {_SITE_DAY}"
        ).fetchall()
    finally:
        connection.close()
    return [float(value) for (value,) in rows]


# --- No value changes ---------------------------------------------------------


def test_the_swc_columns_are_unchanged_by_the_repoint() -> None:
    """The exit criterion's first half: one table feeds `swc`, with no value changes."""
    assert ROOF_SWC_COLUMNS == SWC_COLUMNS_BEFORE


def test_the_presets_are_unchanged_by_the_repoint() -> None:
    """And its second half, against the literals the presets were written as."""
    assert ROOF_PRESETS == PRESETS_BEFORE


def test_the_presets_hash_to_the_committed_pin() -> None:
    """Equality is not enough: `90 == 90.0` but the two hash differently.

    ``gr2l_roof_presets_sha256`` is the hash of this table's canonical JSON, and
    the GR2L canary's comparability hangs off it (``agent_architecture.md`` §5).
    Building the presets from typed fields is exactly the kind of change that
    could silently promote an int, so the pin is recomputed here the way
    ``scripts/check_pins.py`` computes it.
    """
    canonical = json.dumps(ROOF_PRESETS, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    committed = json.loads(PINS_PATH.read_text(encoding="utf-8"))["pins"]
    assert digest == committed["gr2l_roof_presets_sha256"]


def test_the_gravel_roof_is_in_neither_projection() -> None:
    """It is a roof, and it has no substrate — so no %θ seed and no GR2L store.

    Both projections exclude it for that one reason, which is now stated once (a
    ``gr2l`` of ``None``) instead of by two independently maintained key lists.
    """
    assert "gravel" in ROOFS
    assert ROOFS["gravel"].gr2l is None
    assert "gravel" not in ROOF_SWC_COLUMNS
    assert "gravel" not in ROOF_PRESETS
    assert "gravel" not in MODELLED_ROOFS


def test_the_module_is_versioned_for_the_pin() -> None:
    """`roofs_version` is read out of the source by `just pins`; it needs to exist."""
    assert VERSION
    assert isinstance(VERSION, str)


# --- Absence is structural ----------------------------------------------------


def test_the_semi_intensive_roof_has_no_column_in_two_of_the_five_tables() -> None:
    """No lysimeter and no radiation mast, so the key is missing rather than null.

    A ``None`` would be a value a caller can forget to check; a missing key
    raises where it is read (``findings.md`` § Not every roof is instrumented).
    """
    semi_intensive = ROOFS["semi_intensive"]

    assert set(semi_intensive.columns) == {"swc", "tsoil"}
    assert "outflow" not in semi_intensive.columns
    assert "radiation" not in semi_intensive.columns
    assert semi_intensive.lysimeter_area_m2 is None
    with pytest.raises(KeyError):
        radiation_column(semi_intensive, "SWdown")


def test_the_sampling_pools_are_read_off_the_table() -> None:
    """P1 is the roofs on `swc`; P1f is the roofs on `outflow` (`questions.md` §1.6)."""
    assert len(roofs_with_column("swc")) == 5
    assert len(roofs_with_column("tsoil")) == 5
    assert set(roofs_with_column("outflow")) == set(roofs_with_column("radiation"))
    assert "semi_intensive" not in roofs_with_column("outflow")
    assert len(roofs_with_column("outflow")) == 4


def test_no_roof_carries_a_column_in_the_station_table() -> None:
    """`wetter` is the station: one set of columns shared by every roof."""
    assert roofs_with_column("wetter") == ()


def test_every_lysimeter_collects_one_square_metre() -> None:
    """So a litre of outflow is a millimetre of depth, and no area factor exists.

    The constant is carried once and derived from having a lysimeter at all, so
    the uninstrumented roof cannot acquire a collection area by transcription
    (``findings.md`` § The lysimeter collection area).
    """
    with_lysimeter = [roof for roof in ROOFS.values() if roof.lysimeter_area_m2 is not None]

    assert {roof.name for roof in with_lysimeter} == set(roofs_with_column("outflow"))
    assert all(roof.lysimeter_area_m2 == 1.0 for roof in with_lysimeter)
    assert LYSIMETER_AREA_M2 == 1.0


# --- The names are the database's ---------------------------------------------


def test_every_column_in_the_table_exists_in_the_pinned_database() -> None:
    """Including the six a radiation mast carries under its prefix.

    The table is now the only place these names are written, so a typo in one of
    them would not be caught by the code that used to hold a second copy.
    """
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        actual = {
            (table, column)
            for table, column in connection.execute(
                "SELECT table_name, column_name FROM information_schema.columns"
            ).fetchall()
        }
    finally:
        connection.close()

    for roof in ROOFS.values():
        for table, column in roof.columns.items():
            if table == "radiation":
                for suffix in RADIATION_COLUMN_SUFFIXES:
                    assert (table, radiation_column(roof, suffix)) in actual
            else:
                assert (table, column) in actual


# --- Plausibility bounds ------------------------------------------------------


@pytest.mark.parametrize("roof_name", sorted(ROOFS))
@pytest.mark.parametrize("table", ["swc", "tsoil"])
def test_the_bounds_admit_the_healthy_record(roof_name: str, table: str) -> None:
    """No false positives: every healthy site-day mean sits inside its column's bounds.

    A bound that rejected real readings would delete good cases from the catalog
    silently, which is worse than one that misses a bad one — the frozenness test
    exists for what bounds cannot see (``findings.md``
    § Validity-predicate specificity).
    """
    roof = ROOFS[roof_name]
    bounds = roof.bounds[table]
    # The wetland's soil-moisture sensor is dead from 2026-03-12; that stretch is
    # the next test's subject, not this one's.
    where = (
        f"timestamp < TIMESTAMP '{QWETLAND_DEAD_FROM} 00:00:00'"
        if table == "swc" and roof_name == "wetland"
        else "TRUE"
    )

    outside = [value for value in _daily_means(table, roof.columns[table], where) if not bounds.contains(value)]

    assert outside == []


def test_the_bounds_reject_the_dead_wetland_record() -> None:
    """The episode the plausibility predicate exists for.

    ``findings.md`` measures the choice as insensitive: any floor from 0.5 to
    3.0 %θ flags the same 43 days. This asserts the floor in the table is one of
    them, on the days it should flag and no others.
    """
    bounds = ROOFS["wetland"].bounds["swc"]
    dead = _daily_means("swc", "QWetland", f"timestamp >= TIMESTAMP '{QWETLAND_DEAD_FROM} 00:00:00'")

    flagged = [value for value in dead if not bounds.contains(value)]

    assert len(flagged) == 43
    assert max(flagged) < bounds.low


def test_the_gravel_roof_has_no_soil_moisture_floor() -> None:
    """A roof with no substrate reads ~0 %θ honestly (`findings.md`).

    Its band median is 0.06 %θ, so any floor borrowed from a planted roof would
    reject the whole column.
    """
    assert ROOFS["gravel"].bounds["swc"].low == 0.0


def test_column_bounds_resolves_every_table_the_same_way() -> None:
    """One entry point, so no caller has to know radiation's are per instrument."""
    assert column_bounds("swc", "QWetland") is ROOFS["wetland"].bounds["swc"]
    assert column_bounds("outflow", "Kies_Efflux") is ROOFS["gravel"].bounds["outflow"]
    assert column_bounds("radiation", "ED1_SWdown") is RADIATION_BOUNDS["SWdown"]
    # Not roof columns: the station's, and the two small test lysimeters that are
    # not roof segments (`findings.md` § Not every roof is instrumented).
    assert column_bounds("wetter", "Rain") is None
    assert column_bounds("outflow", "Zeitlysi_Efflux_x") is None
    assert column_bounds("radiation", "XX_SWdown") is None


# --- Aliases ------------------------------------------------------------------


def test_no_alias_is_claimed_by_two_roofs() -> None:
    """An alias map is only a map if the arrows are unambiguous."""
    claimed: dict[str, str] = {}
    for roof in ROOFS.values():
        for alias in roof.aliases:
            assert alias not in claimed, f"{alias!r}: {claimed.get(alias)} and {roof.name}"
            claimed[alias] = roof.name


def test_each_roof_answers_to_its_own_canonical_name() -> None:
    assert all(roof.name in roof.aliases for roof in ROOFS.values())
    assert all(alias == alias.strip().lower() for roof in ROOFS.values() for alias in roof.aliases)


def test_the_non_modellable_names_are_all_in_the_table() -> None:
    """The scope check's key list and the alias set must not drift apart.

    ``NON_MODELLABLE_ROOFS`` is keyed by the same vocabulary, and a name the
    table knows but that table does not would answer a question the tool means to
    decline.
    """
    for name in NON_MODELLABLE_ROOFS:
        roof = resolve_roof(name)
        assert roof is not None, name
        assert roof.name in {"gravel", "wetland"}


def test_resolving_normalizes_the_way_the_tool_entry_does() -> None:
    """`normalize_roof_type` is the tool's fold; this lookup applies the same one."""
    assert resolve_roof(normalize_roof_type("  Semi_Intensive ")) is ROOFS["semi_intensive"]
    assert resolve_roof("  Kiesdach ") is ROOFS["gravel"]
    assert resolve_roof("QEx1") is ROOFS["irrigated_extensive"]
    assert resolve_roof("the roof on the left") is None
