"""Every roof segment's identity, in one table.

Five roof segments sit on one building, and each system that touches them spells
them differently: ``QEx1`` in ``swc``, ``Extensiv1_Efflux`` in ``outflow``,
``ED1`` on the radiation mast, ``EGR1`` in the site's ops manual, "das
bewässerte Extensivdach" in a researcher's question. Every one of those
spellings used to live wherever it was first needed — a column map in
:mod:`swc`, a preset table in :mod:`gr2l_client`, an alias list in
``NON_MODELLABLE_ROOFS``, prose in half a dozen docstrings — so a roof was
described in as many places as it was used and nowhere in full.

This module is the one place (``agent_architecture.md`` §1 principle 4). Each
roof carries its canonical name, its DE/EN labels, the site's own id, substrate
height, lysimeter area, per-column plausibility bounds, the names it goes by, its
GR2L preset where it has one, and **its column in each of the five tables**.
:data:`~..tools.swc.ROOF_SWC_COLUMNS` and
:data:`~..tools.gr2l_client.ROOF_PRESETS` are projections of this table, not
tables of their own.

Two facts shape the structure rather than sitting in it as data:

**Absence is structural, not a null.** The semi-intensive roof has no lysimeter
and no radiation mast (``findings.md`` § Not every roof is instrumented), so its
entry has no ``outflow`` and no ``radiation`` key at all — ``swc`` and ``tsoil``
carry five roofs where those two carry four. A caller asking for a column it
does not have gets a ``KeyError``, not a ``None`` to be checked for and
forgotten. This is also why the catalog's sampling pools are read off the table
(:func:`roofs_with_column`) instead of being hand-listed: the pool *is* the set
of roofs instrumented in the table a template reads.

**Every lysimeter collects 1 m², so litres are numerically millimetres**
(``findings.md`` § The lysimeter collection area). :data:`LYSIMETER_AREA_M2` is
carried once, as the reason no area factor appears anywhere else in this
repository — an outflow column in litres is already a depth in millimetres, and
multiplying by an area would be the bug this constant exists to prevent.

``wetter`` is the station, one set of columns shared by every roof, so no roof
carries a column in it. Its absence from :attr:`RoofSegment.columns` is not an
instrumentation gap.

No imports beyond the standard library: this is layer 3's identity table, and a
tool wrapper, an oracle and the pin check must all be able to read it without
dragging in a client or a settings load.
"""

import dataclasses

VERSION = "1.0"
"""Pinned as ``roofs_version`` (``agent_architecture.md`` §5).

The values below are identities and physical properties, so a result measured
against one version of this table is not comparable with one measured against
another. Bump it whenever a value moves — not when prose around it changes.
"""

LYSIMETER_AREA_M2 = 1.0
"""The collection area of every lysimeter on this roof, in m².

One number for all four instrumented segments, and the whole reason it is worth
naming: at 1 m², a litre of outflow *is* a millimetre of depth, so no conversion
from litres to millimetres exists in this repository and none should be added
(``findings.md`` § The lysimeter collection area).
"""

RADIATION_COLUMN_SUFFIXES = ("SWdown", "SWup", "LWdown", "LWup", "TSFC", "TSFCkorr")
"""The six columns each radiation mast carries, appended to the roof's mast prefix.

``radiation`` is the one table where a roof's entry is a *prefix* (``ED1``)
rather than a column: the mast reports six quantities, and ``ED1_SWdown`` is one
of them. :func:`radiation_column` joins the two halves so no call site builds
the name with an f-string of its own.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class Bounds:
    """Inclusive physical bounds one column's readings must fall inside.

    The plausibility half of the catalog's validity predicates
    (``questions.md`` §1.6): a reading outside these is the sensor failing, not
    the roof doing something interesting. Bounds are **per column, never
    global** — the gravel roof has no substrate and legitimately sits at a band
    median of 0.06 %θ, which any floor derived from a planted roof would reject
    (``findings.md`` § Validity-predicate specificity).

    They apply to *the value a case actually reads*: a daily mean for an
    aggregate, a half-hourly sample for a point query. That is the level
    ``findings.md``'s threshold sweeps were run at, and the level at which the
    dead ``QWetland`` record separates cleanly from the healthy one.
    """

    low: float
    high: float

    def contains(self, value: float) -> bool:
        """True when *value* is inside the bounds, endpoints included."""
        return self.low <= value <= self.high


@dataclasses.dataclass(frozen=True, slots=True)
class Gr2lPreset:
    """The GR2L parameters that are physical properties of one installed roof.

    Only the four that vary per roof live here. ``Sret``/``Sretmax``/``theta_02``
    are 0 and ``kg`` is 1 for every segment — none has a retention layer — so
    they are carried once in :data:`_NO_RETENTION_LAYER` rather than repeated
    four times. ``SH`` is the roof's own :attr:`RoofSegment.substrate_height_cm`.

    **The literal types here are load-bearing.** ``Ssubmax = 90`` is an ``int``
    and ``16.0`` is a ``float`` because ``gr2l_roof_presets_sha256`` hashes the
    canonical JSON of :data:`~..tools.gr2l_client.ROOF_PRESETS`, where ``90``
    and ``90.0`` are different documents. The annotations say ``float`` in the
    numeric-tower sense; the values are written exactly as the pin was captured,
    and a test recomputes the hash so a well-meaning ``90 → 90.0`` fails loudly
    instead of silently ending a comparison.
    """

    ssubmin: float
    ssubmax: float
    albedo: float
    open_water: bool


_NO_RETENTION_LAYER: dict[str, float | bool] = {
    "Sret": 0,
    "Sretmax": 0,
    "theta_02": 0,
    "kg": 1,
}
"""The GR2L retention-layer parameters, identical on all four modelled roofs.

Not one of them is a roof property here — no segment on this building has a
retention layer, so the model's generic defaults are overridden the same way
every time (``gr2l_tool.md`` "Roof types and their parameters").
"""


@dataclasses.dataclass(frozen=True, slots=True)
class RoofSegment:
    """One roof segment: what it is called, what measures it, what it is made of."""

    name: str
    """The canonical name, and the key every table in this repository is keyed by."""

    label_en: str
    label_de: str
    """How the roof is named in each language — the display forms, not the aliases.

    German is not decoration: the site's own vocabulary is German, the catalog
    asks questions in both languages (``questions.md`` §1.6), and the semantic
    layer's alias map has to bridge them.
    """

    site_id: str | None
    """The site's own id, where one exists.

    ``EGR1``/``EGR2``/``IGR`` are the deployed irrigation controller's and the
    ops manual's names for the three substrate roofs (``irrigation_tool.md``
    § Units). The gravel roof and the wetland carry none: the controller does not
    address them.
    """

    substrate_height_cm: float | None
    """``SH`` — the depth of the store, in centimetres, or ``None`` where there is none.

    The scale factor in ``θ% ↔ mm`` (:func:`~..tools.swc.theta_pct_to_mm`) and
    GR2L's ``SH`` argument alike. The wetland's 1.7 cm is its fleece mat; the
    water ponded above it is ``Ssubmax``'s business, not this field's. The gravel
    roof's is ``None`` for the reason it has no preset either: it has no
    substrate layer, so a depth of 0 would be a number where there is no
    quantity.

    Written with the literal types the presets were pinned with — see
    :class:`Gr2lPreset`.
    """

    columns: dict[str, str]
    """Table name → this roof's column in it, **absent where uninstrumented**.

    ``radiation``'s value is the mast prefix; see
    :data:`RADIATION_COLUMN_SUFFIXES`.
    """

    bounds: dict[str, Bounds]
    """Table name → the plausibility bounds of this roof's column in it.

    Keyed exactly like :attr:`columns`, minus ``radiation``: a mast's bounds are
    set by the instrument rather than by the roof under it, so they live in
    :data:`RADIATION_BOUNDS` and are shared.
    """

    aliases: frozenset[str]
    """Every lowercase spelling that means this roof.

    Database columns (``qex1``), German names (``extensivdach1``), site ids
    (``egr1``), mast prefixes (``ed1``) and the canonical name itself. Matched
    against a :func:`~..tools.gr2l_client.normalize_roof_type`\\ d argument, so
    case and surrounding space are already gone.
    """

    gr2l: Gr2lPreset | None
    """The GR2L preset, or ``None`` for a roof the model has no store for.

    The gravel roof is the ``None``: no substrate means no substrate-water state,
    which is why it is absent from both :data:`~..tools.swc.ROOF_SWC_COLUMNS` and
    :data:`~..tools.gr2l_client.ROOF_PRESETS` — a scope fact, not an oversight.
    The wetland *has* a preset and is still declined at layer 1
    (``NON_MODELLABLE_ROOFS``); the two are separate questions.
    """

    @property
    def lysimeter_area_m2(self) -> float | None:
        """:data:`LYSIMETER_AREA_M2` where this roof has a lysimeter, else ``None``.

        Derived from the ``outflow`` column rather than stored: a roof has a
        collection area exactly when it has a lysimeter, and writing 1.0 five
        times would let the semi-intensive roof quietly acquire one.
        """
        return LYSIMETER_AREA_M2 if "outflow" in self.columns else None


# Bounds derived from the pinned record and stated as physical limits, not as
# the record's own extremes:
#
#   *Floor* — half the column's healthy minimum, which puts it in the gap
#   between a dead sensor and the driest real reading. `findings.md`
#   § Validity-predicate specificity measures how little that choice matters:
#   on `QWetland` every threshold from 0.5 to 3.0 %θ flags the same 43 dead days
#   with no false positives, so the floor is insensitive over a sixfold range.
#   `QGravel` gets no floor at all — a roof with no substrate reads ~0 %θ
#   honestly, and there is nothing to be below.
#
#   *Ceiling* — the store's physical saturation: ~60 %θ for a mineral green-roof
#   substrate, 100 %θ for the ponded fleece mat, 20 %θ for a gravel drainage
#   layer whose whole record tops out at 8.9 %θ.
#
# Soil temperature takes one envelope, −25 °C to 70 °C: the probes are the same
# instrument in the same climate, and the record spans −12.0 to 55.3 °C
# (a dark gravel roof in August is genuinely that hot). Outflow's floor is hard
# — a lysimeter cannot un-shed water — and its ceiling of 100 mm is four times
# the wettest day in the record (22.9 mm), so it catches a sign flip or a
# litres/millilitres slip without rejecting a storm.
_SWC_SUBSTRATE_CEILING = 60.0
_TSOIL_BOUNDS = Bounds(-25.0, 70.0)
_OUTFLOW_BOUNDS = Bounds(0.0, 100.0)

RADIATION_BOUNDS: dict[str, Bounds] = {
    # Shortwave dips a few W/m² negative at night on every mast (the record's
    # minimum is −8.2), so a floor at zero would reject healthy darkness.
    "SWdown": Bounds(-20.0, 1400.0),
    "SWup": Bounds(-20.0, 500.0),
    "LWdown": Bounds(100.0, 600.0),
    "LWup": Bounds(100.0, 800.0),
    # Surface temperature is reported in kelvin, which is exactly the sort of
    # unit a bound should be able to catch a conversion away from.
    "TSFC": Bounds(230.0, 350.0),
    "TSFCkorr": Bounds(230.0, 350.0),
}
"""Per-column bounds for the radiation masts, shared by all four of them.

Set by the instrument rather than by the roof beneath it, so unlike the other
three tables these do not vary per segment.
"""


ROOFS: dict[str, RoofSegment] = {
    "gravel": RoofSegment(
        name="gravel",
        label_en="gravel roof",
        label_de="Kiesdach",
        site_id=None,
        substrate_height_cm=None,
        columns={
            "swc": "QGravel",
            "tsoil": "TGravel",
            "outflow": "Kies_Efflux",
            "radiation": "KD",
        },
        bounds={
            "swc": Bounds(0.0, 20.0),
            "tsoil": _TSOIL_BOUNDS,
            "outflow": _OUTFLOW_BOUNDS,
        },
        aliases=frozenset({"gravel", "gravel_roof", "kies", "kiesdach", "kd", "qgravel"}),
        gr2l=None,
    ),
    "irrigated_extensive": RoofSegment(
        name="irrigated_extensive",
        label_en="irrigated extensive green roof",
        label_de="bewässertes Extensivdach",
        site_id="EGR1",
        substrate_height_cm=7,
        columns={
            "swc": "QEx1",
            "tsoil": "TEx1",
            "outflow": "Extensiv1_Efflux",
            "radiation": "ED1",
        },
        bounds={
            "swc": Bounds(1.0, _SWC_SUBSTRATE_CEILING),
            "tsoil": _TSOIL_BOUNDS,
            "outflow": _OUTFLOW_BOUNDS,
        },
        aliases=frozenset(
            {
                "irrigated_extensive",
                "extensiv1",
                "extensivdach1",
                "egr1",
                "ed1",
                "qex1",
            }
        ),
        gr2l=Gr2lPreset(ssubmin=3.3, ssubmax=22.8, albedo=0.2, open_water=False),
    ),
    "non_irrigated_extensive": RoofSegment(
        name="non_irrigated_extensive",
        label_en="non-irrigated extensive green roof",
        label_de="unbewässertes Extensivdach",
        site_id="EGR2",
        substrate_height_cm=7,
        columns={
            "swc": "QEx2",
            "tsoil": "TEx2",
            "outflow": "Extensiv2_Efflux",
            "radiation": "ED2",
        },
        bounds={
            "swc": Bounds(0.5, _SWC_SUBSTRATE_CEILING),
            "tsoil": _TSOIL_BOUNDS,
            "outflow": _OUTFLOW_BOUNDS,
        },
        aliases=frozenset(
            {
                "non_irrigated_extensive",
                "extensiv2",
                "extensivdach2",
                "egr2",
                "ed2",
                "qex2",
            }
        ),
        gr2l=Gr2lPreset(ssubmin=0.9, ssubmax=16.0, albedo=0.2, open_water=False),
    ),
    # No lysimeter and no radiation mast: two of the five tables have no column
    # for this roof, and the absence is the point (`findings.md`).
    "semi_intensive": RoofSegment(
        name="semi_intensive",
        label_en="semi-intensive green roof",
        label_de="Intensivdach",
        site_id="IGR",
        substrate_height_cm=15,
        columns={"swc": "QIn", "tsoil": "TIn"},
        bounds={"swc": Bounds(1.0, _SWC_SUBSTRATE_CEILING), "tsoil": _TSOIL_BOUNDS},
        aliases=frozenset(
            {
                "semi_intensive",
                "semi_intensive_roof",
                "intensiv",
                "intensivdach",
                "igr",
                "qin",
            }
        ),
        gr2l=Gr2lPreset(ssubmin=6.3, ssubmax=45.6, albedo=0.2, open_water=False),
    ),
    "wetland": RoofSegment(
        name="wetland",
        label_en="wetland green roof",
        label_de="Sumpfdach",
        site_id=None,
        substrate_height_cm=1.7,
        columns={
            "swc": "QWetland",
            "tsoil": "TWetland",
            "outflow": "Sumpf2_Efflux",
            "radiation": "SD",
        },
        bounds={
            # The floor that separates the dead record from the healthy one: the
            # sensor reads a flat ~0 %θ from 2026-03-12, where the healthy record
            # runs 4–96 %θ around a ~86 %θ saturation plateau.
            "swc": Bounds(2.0, 100.0),
            "tsoil": _TSOIL_BOUNDS,
            "outflow": _OUTFLOW_BOUNDS,
        },
        aliases=frozenset(
            {"wetland", "wetland_roof", "sumpf", "sumpfdach", "sumpf2", "sd", "qwetland"}
        ),
        gr2l=Gr2lPreset(ssubmin=1.3, ssubmax=90, albedo=0.06, open_water=True),
    ),
}


_BY_ALIAS: dict[str, RoofSegment] = {
    alias: roof for roof in ROOFS.values() for alias in roof.aliases
}


def resolve_roof(name: str) -> RoofSegment | None:
    """The roof *name* refers to, or ``None`` when nothing here answers to it.

    Soft on purpose: the callers that need an error already raise their own,
    naming the vocabulary *they* accept — :mod:`swc` lists the roofs it has a
    column for, :mod:`gr2l_client` the ones it has a preset for, and those two
    sets differ. A shared lookup that raised would have to pick one of them.
    """
    return _BY_ALIAS.get(name.strip().lower())


def roofs_with_column(table: str) -> tuple[str, ...]:
    """Canonical names of the roofs instrumented in *table*, in table order.

    The catalog's sampling pools (``questions.md`` §1.6): P1 is
    ``roofs_with_column("swc")``, P1f is ``roofs_with_column("outflow")``. A
    template reading an outflow column cannot sample the semi-intensive roof,
    and that follows from the data rather than from a second hand-written list.
    """
    return tuple(name for name, roof in ROOFS.items() if table in roof.columns)


def radiation_column(roof: RoofSegment, suffix: str) -> str:
    """``ED1`` + ``SWdown`` → ``ED1_SWdown``, for a roof that has a mast.

    Raises ``KeyError`` when the roof has no radiation mast — the semi-intensive
    roof — and ``ValueError`` on a suffix no mast reports.
    """
    if suffix not in RADIATION_COLUMN_SUFFIXES:
        valid = ", ".join(RADIATION_COLUMN_SUFFIXES)
        raise ValueError(f"Unknown radiation column {suffix!r}. Valid: {valid}.")
    return f"{roof.columns['radiation']}_{suffix}"


def column_bounds(table: str, column: str) -> Bounds | None:
    """Plausibility bounds for *column* of *table*, or ``None`` if it has none.

    The one entry point for the plausibility predicate, so a caller never has to
    know that ``radiation``'s bounds are per instrument while the other three
    tables' are per roof. ``wetter`` and the two small test lysimeters in
    ``outflow`` are not roof columns and return ``None``.
    """
    if table == "radiation":
        prefix, _, suffix = column.partition("_")
        masts = {roof.columns["radiation"] for roof in ROOFS.values() if "radiation" in roof.columns}
        return RADIATION_BOUNDS.get(suffix) if prefix in masts else None
    for roof in ROOFS.values():
        if roof.columns.get(table) == column:
            return roof.bounds.get(table)
    return None


def gr2l_preset_values(roof: RoofSegment) -> dict[str, float | bool]:
    """This roof's GR2L preset as the endpoint's own argument names.

    The projection :data:`~..tools.gr2l_client.ROOF_PRESETS` is built from, kept
    here so the argument spelling (``SH``, ``Ssubmin``, …) lives beside the
    values it names. Raises ``ValueError`` for a roof GR2L has no store for.
    """
    if roof.gr2l is None:
        raise ValueError(f"The {roof.name} roof has no GR2L preset.")
    return {
        "SH": roof.substrate_height_cm,
        "Ssubmin": roof.gr2l.ssubmin,
        "Ssubmax": roof.gr2l.ssubmax,
        **_NO_RETENTION_LAYER,
        "albedo": roof.gr2l.albedo,
        "open_water": roof.gr2l.open_water,
    }


MODELLED_ROOFS: tuple[str, ...] = tuple(
    name for name, roof in ROOFS.items() if roof.gr2l is not None
)
"""The roofs GR2L has a substrate store for — everything but gravel.

Not the same set as the roofs the *agent-facing* tools accept: the wetland has a
preset and is still declined at layer 1 (``NON_MODELLABLE_ROOFS``).
"""
