"""Every number the irrigation rule turns on, in one versioned module.

The calculator, the oracles and the reference cards read their constants from
here (``agent_architecture.md`` §1 principle 4). A threshold that also appears in
a card's prose, in an oracle's arithmetic and in a docstring is three numbers
that agree today; this module is the one that is *the* number, with
``values_for(card_id)`` (T068) rendering it into the cards rather than a writer
transcribing it.

**Authored in the site's units, converted once.** The site states its thresholds
in %θ — that is what the SMT100 sensors read and what the ops manual says — and
the balance runs in millimetres. Every conversion in this repository goes through
:func:`~.tools.swc.theta_pct_to_mm` against the roof's own substrate height from
:mod:`.tools.roofs`, and it happens here, once, on the way out of this module.
Nothing downstream converts anything: a store in %VWC with millimetres added to
it is precisely the deployed controller's bug (``irrigation_tool.md`` § Units),
and the way to not reproduce it is to have one place where units change.

**Three provenances, and they are labelled.** Most of these values are the
deployed controller's, carried verbatim — including the flat 22 %θ capacity that
GR2L measures differently per roof, because this tool exists to reproduce the
site's decisions rather than to improve them
(``decisions.md`` § No fitted correction between the instrument and the oracle).
Two have **no deployed source at all** and are authored eval policy, marked
``EVAL POLICY`` where they are defined: the heatwave duration rule and the
retention target. One is **owed by the site** and stands empty rather than
guessed: the per-roof dose in millimetres. Confusing the three is how a testbed
convention ends up quoted back to a site as its own policy.

Pure: no I/O, no clock, no settings, no ADK. An oracle imports the same constant
the tool applies.
"""

import dataclasses

from water_assistant_agent.assistant.tools.roofs import ROOFS
from water_assistant_agent.assistant.tools.swc import theta_pct_to_mm

VERSION = "1.0"
"""Pinned as ``rules_constants_version`` (``agent_architecture.md`` §5).

Every irrigation answer in a measured run is a function of these numbers, and no
test would notice one of them moving between two runs. Bump on any value change.
"""


# --- The ladder's thresholds --------------------------------------------------

HEAT_THRESHOLD_C = 24.0
"""Below this daily maximum the rule stops at ``no_heat_no_stress`` (ladder row 2).

The deployed controller's, and the only temperature constant it carries — the
heatwave *duration* below is not its and is marked as such.
"""

OUTFLOW_EPSILON_MM = 0.01
"""Modelled outflow above this counts as "the roof will refill" (ladder row 4).

A floating-point guard rather than a physical quantity: the balance produces
exact zeros on dry days and dust otherwise, and ``> 0`` on a float sum would read
a rounding remainder as a refill.
"""


# --- Horizons, authored in hours ----------------------------------------------

DECISION_HORIZON_HOURS = 48
"""How far ahead soil moisture and heat are read (ladder rows 1-3)."""

REFILL_HORIZON_HOURS = 168
"""How far ahead an expected refill suppresses irrigation (ladder row 4)."""


def horizon_rows(horizon_hours: int, *, step_hours: float) -> int:
    """How many rows *horizon_hours* spans at a series' *step_hours*.

    The horizons are authored in hours because the site runs an hourly ICON
    forcing and this system runs daily rows, and one implementation has to serve
    both (``irrigation_tool.md`` § Horizons and step): 48 h is 48 hourly rows and
    2 daily ones. Which *offset* each horizon is sliced from — soil moisture and
    heat include the seed step, outflow excludes it — is the rule's, not this
    function's.

    Raises ``ValueError`` on a non-positive step, or on a step so coarse that the
    horizon spans no rows at all; both would silently produce an empty window and
    a decision made on nothing.
    """
    if step_hours <= 0:
        raise ValueError(f"step_hours must be positive, got {step_hours}.")
    rows = int(horizon_hours // step_hours)
    if rows < 1:
        raise ValueError(
            f"A {horizon_hours} h horizon spans no rows at a {step_hours} h step."
        )
    return rows


# --- Per-roof thresholds ------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RoofRules:
    """One roof's trigger levels, authored in %θ and read in millimetres.

    The four levels are the deployed controller's for that roof. ``capacity_pct``
    is a flat 22.0 on all three substrate roofs, which is *not* what GR2L
    measures (22.9 / 32.6 / 30.4 %θ) — the difference is the deployed
    controller's, carried deliberately, and for the semi-intensive roof it is
    12.6 mm of storage the two models disagree about
    (``irrigation_tool.md`` § This is not GR2L).

    The millimetre properties are the only conversion: they read the roof's
    substrate height from :mod:`.tools.roofs`, so a roof's depth is stated in one
    place and its thresholds in another, and neither repeats the other.
    """

    roof: str
    wilting_pct: float
    """Ladder row 1: at or below this, irrigate whatever else is true."""

    dry_pct: float
    """Ladder row 3: above this, the roof is not dry enough to act on."""

    capacity_pct: float
    """Field capacity — the overflow level *and* the stress coefficient's upper
    end. The extraction carries these as two constants in two units
    (``SWC_CAPACITY = 22.0``, ``THETA_FIELD_CAPACITY = 0.22``); in millimetres
    they collapse into one, which is the conversion *removing* a constant."""

    residual_pct: float
    """The stress coefficient's lower end: ``ks = clip((S − S_r)/(S_fc − S_r), 0, 1)``."""

    valve_minutes: int
    """How long the deployed controller opens this roof's valve.

    Actuation is out of scope here (``irrigation_tool.md``), so this is carried
    for disclosure — it is what the site currently does when the answer is yes,
    and the only dose figure that exists today.
    """

    dose_mm: float | None = None
    """The dose as a depth: the roof's 90th-percentile historical ET.

    **Owed by the site** (``irrigation_tool.md`` § Open questions), and empty
    rather than guessed. It is policy, not a computation — the tool states it,
    never derives it (``agent_architecture.md`` §3.5) — so a plausible number
    invented here would be indistinguishable from the site's own once it is
    quoted back in an answer. Until it arrives, :attr:`valve_minutes` is what a
    disclosure can honestly state.
    """

    @property
    def substrate_height_cm(self) -> float:
        """The roof's depth, from the one table that carries it."""
        height = ROOFS[self.roof].substrate_height_cm
        if height is None:  # pragma: no cover - no rules exist for a roof without one
            raise ValueError(f"The {self.roof} roof has no substrate height.")
        return height

    @property
    def wilting_mm(self) -> float:
        return theta_pct_to_mm(self.wilting_pct, self.substrate_height_cm)

    @property
    def dry_mm(self) -> float:
        return theta_pct_to_mm(self.dry_pct, self.substrate_height_cm)

    @property
    def capacity_mm(self) -> float:
        return theta_pct_to_mm(self.capacity_pct, self.substrate_height_cm)

    @property
    def residual_mm(self) -> float:
        return theta_pct_to_mm(self.residual_pct, self.substrate_height_cm)


RESIDUAL_PCT = 2.5
"""The stress coefficient's lower end, the same %θ on every substrate roof.

One number, three roofs, three millimetre values — 1.75 mm at 7 cm and 3.75 mm
at 15 cm — which is what "authored in the site's units" buys.
"""

ROOF_RULES: dict[str, RoofRules] = {
    "irrigated_extensive": RoofRules(
        roof="irrigated_extensive",
        wilting_pct=5.0,
        dry_pct=10.0,
        capacity_pct=22.0,
        residual_pct=RESIDUAL_PCT,
        valve_minutes=30,
    ),
    "non_irrigated_extensive": RoofRules(
        roof="non_irrigated_extensive",
        wilting_pct=4.0,
        dry_pct=10.0,
        capacity_pct=22.0,
        residual_pct=RESIDUAL_PCT,
        valve_minutes=30,
    ),
    "semi_intensive": RoofRules(
        roof="semi_intensive",
        wilting_pct=10.0,
        dry_pct=16.0,
        capacity_pct=22.0,
        residual_pct=RESIDUAL_PCT,
        valve_minutes=31,
    ),
}
"""The three substrate roofs the rule applies to.

The gravel roof and the wetland are absent, and that absence is the same scope
statement ``NON_MODELLABLE_ROOFS`` makes: the rule reads a soil store in %θ
against a wilting point, which neither a bare drainage layer nor a ponded fleece
mat has. The deployed controller decides the wetland from a lysimeter level in
kg; that branch is out of scope here (``irrigation_tool.md`` § The rule).
"""


def rules_for(roof_type: str) -> RoofRules:
    """The trigger levels for *roof_type*.

    Raises ``ValueError`` naming the three roofs that have them — the caller that
    reaches this with a gravel roof has skipped a scope check, and the message
    should say so rather than return a plausible default.
    """
    rules = ROOF_RULES.get(roof_type)
    if rules is None:
        valid = ", ".join(sorted(ROOF_RULES))
        raise ValueError(
            f"No irrigation rules for roof_type {roof_type!r}. The rule applies to: {valid}."
        )
    return rules


# --- EVAL POLICY: no deployed source ------------------------------------------
#
# The two constants below are this thesis's, not the site's. They exist because a
# catalog template needs a definition to be scored against and the deployed
# system has none to lend, so they are authored here, in the same module as the
# site's own values but marked apart from them. Anything rendered from them into
# a card says so; an answer that cited either as "the manual's" would be
# reporting the testbed's convention as the site's policy.

HEATWAVE_MIN_CONSECUTIVE_DAYS = 3
"""**EVAL POLICY.** How many days at or above :data:`HEAT_THRESHOLD_C` make a heatwave.

T08 counts heatwave days and T20 asks whether a forecast qualifies; both need a
duration rule, and the deployed controller carries only the threshold
(``questions.md`` § T08). Three days at 24 °C is the definition that makes the
question answerable over the pinned record: it marks 53 days across six months
with per-month counts of 1 / 4 / 12 / 15 / 16 / 5, where four days would empty
two of those months and a 30 °C threshold would leave the whole record with one
non-empty month (``findings.md`` § Data record). Chosen against what the record
can support, then held fixed — not tuned per split.
"""

RETENTION_TARGET = 0.50
"""**EVAL POLICY.** The retention an event is judged against — 50 % of rainfall.

T12 asks whether a roof's retention over a rain event beat "the manual's target",
and no such target is deployed (``questions.md`` § T12). Fifty per cent is the
figure a green-roof ops manual conventionally states, and the pinned record makes
it workable rather than degenerate: over the 11 qualifying events' 41 (event,
roof) pairs it splits 25 above / 16 below, with 8 events carrying both classes
across their roofs — the headroom T12's balance rule needs
(``specs/agent_architecture/t12_rain_events.md``). Retention is
``(rain − outflow) / rain`` in millimetres, with **no area factor**: every
lysimeter collects 1 m², so a litre of outflow is already a millimetre
(:data:`~.tools.roofs.LYSIMETER_AREA_M2`).
"""
