"""What a ``provenance: rendered`` card's generated blocks must equal (T068).

``rules_constants.py`` and ``roofs.py`` are the single source of truth for every
number the irrigation rule turns on (``agent_architecture.md`` §1 principle 4).
A reference card restates some of those numbers to a reader, which makes it a
second copy — and a second copy that drifts is worse than no card, because it is
confidently wrong in the agent's own voice.

**This is a projection, not a writer.** :func:`values_for` returns what a card's
``values:`` / ``applies_to:`` / ``not_applicable:`` blocks *should* contain; a
test asserts the committed card equals it, and ``just cards-check`` prints the
correct block when it does not. The card file stays hand-edited, because prose
and numbers share it and a generator would own the whole file, evicting the
prose that is the half a reader actually reads.

**Two sources and no third.** That is the whole of what ``rendered`` guarantees
(``decisions.md`` § Retrieval). ``data_freshness``'s values are record dates, so
it is ``static`` and absent from here — rendering it would have given this module
a database source and put a read inside a card store specified as a pure
function of packaged files (T081).

**The site's own scope statements are derived, never re-listed.** Which roofs a
card applies to and which it excludes come from the tables: the rule's roofs are
the keys of ``ROOF_RULES``, retention's are the roofs with a lysimeter column. A
hand-written exclusion list is the third copy of a fact that already exists
twice, and the wetland's exclusion is load-bearing enough that T17b is a scored
template about reading it.

Pure: no I/O, no clock, no ADK.
"""

import dataclasses

import yaml

from water_assistant_agent.assistant.rules_constants import (
    DECISION_HORIZON_HOURS,
    HEAT_THRESHOLD_C,
    HEATWAVE_MIN_CONSECUTIVE_DAYS,
    OUTFLOW_EPSILON_MM,
    REFILL_HORIZON_HOURS,
    RETENTION_TARGET,
    ROOF_RULES,
)
from water_assistant_agent.assistant.tools.roofs import (
    LYSIMETER_AREA_M2,
    ROOFS,
    roofs_with_column,
)

MM_DECIMALS = 2
"""Millimetre values are rounded here, once, on the way into a card.

``theta_pct_to_mm`` is exact arithmetic on floats, so field capacity on a
seven-centimetre roof lands as ``15.400000000000001``. That belongs in a
calculation and not in a document a person reads. Rounding is safe here and
nowhere else for one reason: **nothing reads a number back out of a card.** A
card is a statement to a reader; the calculator, the oracles and the tool all
import the constant itself (``agent_architecture.md`` §1 principle 4). The drift
test compares a rounded projection against a rounded card, so the two agree
exactly and the rounding is not a place drift can hide.
"""

EVAL_POLICY_KEY = "eval_policy"
"""Names, inside a ``values:`` block, the values that are this thesis's rather than the site's.

``rules_constants.py`` requires it: the heatwave duration rule and the retention
target have **no deployed source at all**, and "anything rendered from them into
a card says so". Without the marker an answer could cite either as the site's own
policy, which is a testbed convention being quoted back to a site as its manual.
Per key rather than per card, because ``heatwave_definition`` mixes the two — the
temperature threshold is the deployed controller's and only the duration is ours.
Present only where it is non-empty.
"""

_NO_SOIL_STORE = {
    "gravel": (
        "a bare drainage layer with no substrate, so there is no soil-water store to read"
    ),
    "wetland": (
        "a ponded fleece mat rather than a substrate; the deployed controller decides it "
        "from a lysimeter mass, a branch this system does not carry"
    ),
}
"""Why the two non-modellable roofs sit outside every irrigation card.

The same scope statement ``ROOF_RULES`` makes by omission and
``NON_MODELLABLE_ROOFS`` makes by name, said once here so a card can state it to
a reader. Derived in shape — the *set* is ``ROOFS`` minus ``ROOF_RULES`` — and
authored only in wording, which is what a reader of an exclusion needs.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class RenderedBlocks:
    """The three generated blocks of one card, as the projection produces them."""

    values: dict[str, object] = dataclasses.field(default_factory=dict)
    applies_to: list[str] = dataclasses.field(default_factory=list)
    not_applicable: dict[str, str] = dataclasses.field(default_factory=dict)

    def as_yaml(self) -> str:
        """The blocks as they should appear in the card file.

        What ``just cards-check`` prints on a mismatch: the fix, ready to paste,
        rather than a diff the reader has to transcribe by hand.
        """
        blocks = {
            "values": self.values,
            "applies_to": self.applies_to,
            "not_applicable": self.not_applicable,
        }
        return yaml.safe_dump(
            {key: value for key, value in blocks.items() if value},
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )


def _mm(value: float) -> float:
    """A millimetre value as a card states it."""
    return round(value, MM_DECIMALS)


def _rule_roofs() -> list[str]:
    """The roofs the irrigation rule applies to, from the table that defines them."""
    return list(ROOF_RULES)


def _outside_the_rule() -> dict[str, str]:
    """The roof segments the rule does not cover, with the reason it does not.

    Derived as ``ROOFS`` minus ``ROOF_RULES`` so that a roof gaining or losing
    rules moves through every card at once. A ``KeyError`` here is the intended
    failure: a new non-modellable roof needs a stated reason, not a silent
    omission from an exclusion block.
    """
    return {name: _NO_SOIL_STORE[name] for name in ROOFS if name not in ROOF_RULES}


def _irrigation_rule() -> RenderedBlocks:
    """What the rule turns on: one temperature, two horizons, one refill guard.

    The four keys are also what makes T17a answerable. Asked for a wind-speed
    shutoff, the agent fetches this card and finds the rule's conditions listed
    in full with no wind among them — a grounded absence rather than an inference
    from the topic vocabulary (``decisions.md`` § Retrieval).
    """
    return RenderedBlocks(
        values={
            "heat_threshold_c": HEAT_THRESHOLD_C,
            "decision_horizon_hours": DECISION_HORIZON_HOURS,
            "refill_horizon_hours": REFILL_HORIZON_HOURS,
            "refill_outflow_epsilon_mm": OUTFLOW_EPSILON_MM,
        },
        applies_to=_rule_roofs(),
        not_applicable=_outside_the_rule(),
    )


def _irrigation_threshold() -> RenderedBlocks:
    """The two trigger levels per roof, in both units.

    Both are stated because the site reads %θ off its sensors and the balance
    runs in millimetres; a card carrying one unit invites the reader to convert,
    and an unaided conversion against the wrong substrate depth is the deployed
    controller's own bug (``irrigation_tool.md`` § Units).

    Field capacity is deliberately *not* here — it is the store's property, not a
    trigger, and it lives on ``substrate_hydraulics``. That split is also what
    keeps either card from being named after a single constant.
    """
    return RenderedBlocks(
        values={
            roof: {
                "wilting_pct": rules.wilting_pct,
                "wilting_mm": _mm(rules.wilting_mm),
                "dry_pct": rules.dry_pct,
                "dry_mm": _mm(rules.dry_mm),
            }
            for roof, rules in ROOF_RULES.items()
        },
        applies_to=_rule_roofs(),
        not_applicable=_outside_the_rule(),
    )


def _substrate_hydraulics() -> RenderedBlocks:
    """The store itself: how deep it is, and the two ends of its water content.

    **GR2L's per-roof storage is deliberately absent.** ``roofs.py`` carries it
    and this module could render it, but the flat field capacity here is the
    deployed controller's and is known to disagree with what GR2L measures — a
    disagreement carried on purpose (``decisions.md`` § No fitted correction
    between the instrument and the oracle). Two models' storage numbers in one
    ``values:`` block would let an answer quote one as the other's, which is the
    confusion ``irrigation_tool.md`` § This is not GR2L exists to prevent. The
    prose states that they differ; the block states one of them.
    """
    return RenderedBlocks(
        values={
            roof: {
                "substrate_height_cm": rules.substrate_height_cm,
                "capacity_pct": rules.capacity_pct,
                "capacity_mm": _mm(rules.capacity_mm),
                "residual_pct": rules.residual_pct,
                "residual_mm": _mm(rules.residual_mm),
            }
            for roof, rules in ROOF_RULES.items()
        },
        applies_to=_rule_roofs(),
        not_applicable=_outside_the_rule(),
    )


def _irrigation_dose() -> RenderedBlocks:
    """How much water the answer "yes" means — which today is a valve time, not a depth.

    **The depth is owed by the site and stands empty rather than guessed.**
    ``dose_mm`` is rendered only for a roof that has one, so today the key is
    absent from every roof rather than present as a null. That follows
    ``roofs.py``'s rule that absence is structural: a null invites a reader to
    treat it as zero, and a plausible invented depth would be indistinguishable
    from the site's own once an answer quoted it back.

    The absence is enforced rather than trusted — the drift test asserts equality
    both ways, so a card that authored a dose would fail, and the day the site
    supplies one the same test demands the card be updated.
    """
    return RenderedBlocks(
        values={
            roof: (
                {"valve_minutes": rules.valve_minutes}
                if rules.dose_mm is None
                else {"valve_minutes": rules.valve_minutes, "dose_mm": _mm(rules.dose_mm)}
            )
            for roof, rules in ROOF_RULES.items()
        },
        applies_to=_rule_roofs(),
        not_applicable=_outside_the_rule(),
    )


def _heatwave_definition() -> RenderedBlocks:
    """A temperature that is the site's and a duration that is ours, marked apart.

    Roof-independent: it is a property of the weather, so ``applies_to`` is empty
    rather than listing all five segments.
    """
    return RenderedBlocks(
        values={
            "heat_threshold_c": HEAT_THRESHOLD_C,
            "min_consecutive_days": HEATWAVE_MIN_CONSECUTIVE_DAYS,
            EVAL_POLICY_KEY: ["min_consecutive_days"],
        }
    )


def _retention_target() -> RenderedBlocks:
    """The target an event is judged against, and the roofs it can be judged on.

    ``applies_to`` is the roofs with a lysimeter, read off the table rather than
    listed: retention is measured outflow against measured rain, and the
    semi-intensive roof has neither a lysimeter nor a radiation mast
    (``findings.md`` § Not every roof is instrumented). So its exclusion here is
    an instrumentation fact, unrelated to the soil-store exclusions the
    irrigation cards carry — which is why this card derives its own.

    The collection area is carried because it is the reason retention has no area
    factor: at one square metre a litre of outflow is already a millimetre, and a
    card stating the target without it invites the reader to reintroduce the
    conversion this repository does not have.
    """
    measurable = roofs_with_column("outflow")
    return RenderedBlocks(
        values={
            "retention_fraction": RETENTION_TARGET,
            "lysimeter_area_m2": LYSIMETER_AREA_M2,
            EVAL_POLICY_KEY: ["retention_fraction"],
        },
        applies_to=list(measurable),
        not_applicable={
            name: "no lysimeter on this segment, so its outflow is not measured"
            for name in ROOFS
            if name not in measurable
        },
    )


def _roof_reference_ranges() -> RenderedBlocks:
    """What a soil-moisture reading means on a roof: three contiguous bands in %θ.

    **The outer edges come from the ``swc`` record, the interior cuts from the
    rule.** ``roofs.py`` carries each roof's ``swc`` plausibility bounds, derived
    from the column's healthy range over the pinned record
    (``findings.md`` § Per-column healthy ranges); ``rules_constants.py`` carries
    the dry threshold and field capacity. So the band edges are a floor and a
    ceiling the sensor is trusted between, cut twice by the levels the site
    actually acts on — nothing here is a new number.

    **The bands are the site's policy, not the record's distribution.** Field
    capacity is the deployed controller's flat value on all three roofs, which is
    not what GR2L measures per roof, and that disagreement is carried on purpose
    (``decisions.md`` § No fitted correction between the instrument and the
    oracle). On the semi-intensive roof it shows plainly: half the record's days
    sit above the ``high`` edge (``findings.md`` § Where the flat field capacity
    puts the semi-intensive roof). A card whose bands were fitted to the record
    would hide exactly that, and it is a disclosure rather than a defect.

    **A roof needs both sources to have a band**, so this intersects them rather
    than assuming the rule's roofs are instrumented: gravel and the wetland have
    an ``swc`` column and a reading a person can ask about, but no dry threshold
    and no field capacity to cut a band at, so they carry the exclusion instead.

    Bands share their endpoints, and deliberately: which side a reading exactly
    on a cut falls is the rule's comparison to make (``<=`` at the wilting point,
    ``>`` at the dry threshold), not a fact about the range it lies in.
    """
    banded = [roof for roof in roofs_with_column("swc") if roof in ROOF_RULES]
    return RenderedBlocks(
        values={
            roof: {
                "low_pct": {"from": ROOFS[roof].bounds["swc"].low, "to": rules.dry_pct},
                "normal_pct": {"from": rules.dry_pct, "to": rules.capacity_pct},
                "high_pct": {
                    "from": rules.capacity_pct,
                    "to": ROOFS[roof].bounds["swc"].high,
                },
            }
            for roof, rules in ((name, ROOF_RULES[name]) for name in banded)
        },
        applies_to=banded,
        not_applicable=_outside_the_rule(),
    )


_PROJECTIONS = {
    "irrigation_rule": _irrigation_rule,
    "irrigation_threshold": _irrigation_threshold,
    "substrate_hydraulics": _substrate_hydraulics,
    "irrigation_dose": _irrigation_dose,
    "heatwave_definition": _heatwave_definition,
    "retention_target": _retention_target,
    "roof_reference_ranges": _roof_reference_ranges,
}


def values_for(card_id: str) -> RenderedBlocks:
    """The generated blocks the ``rendered`` card *card_id* must carry.

    Raises ``KeyError`` naming the rendered cards for anything else — including a
    ``static`` card, which has no projection by definition and whose values are
    pinned elsewhere (``decisions.md`` § Retrieval).
    """
    projection = _PROJECTIONS.get(card_id)
    if projection is None:
        rendered = ", ".join(sorted(_PROJECTIONS))
        raise KeyError(
            f"No projection for card {card_id!r}. The rendered cards are: {rendered}."
        )
    return projection()
