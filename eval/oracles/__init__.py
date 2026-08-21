"""Materialized ground truth, computed through the code the tools run.

One oracle per template family entry, registered by template id. An oracle takes
a case's ``inputs`` and the rollout context the answer is computed through, and
returns the four ``expectations`` keys it owns plus the pins the answer was
stamped against (:mod:`eval.oracles.base`).

**The pilot set only** (T103): T01, T07 and T09, the three templates the
freeze-gate run exercises. The rest arrive with T110, family by family, and the
registry is what T111's generator will look a template up in — so a template with
no oracle fails loudly at generation rather than emitting a case with no answer.
"""

from eval.oracles.base import Oracle, OracleAnswer, OracleInputError
from eval.oracles.irrigation import t07_needs_irrigation_now
from eval.oracles.model_chain import t09_falls_below_threshold
from eval.oracles.sql import t01_total_outflow

ORACLES: dict[str, Oracle] = {
    "T01": t01_total_outflow,
    "T07": t07_needs_irrigation_now,
    "T09": t09_falls_below_threshold,
}
"""Template id → the oracle that answers it.

Keyed by ``inputs.template_id``, which is the case's own field, so nothing has to
map a case to its family by parsing an id.
"""

__all__ = [
    "ORACLES",
    "Oracle",
    "OracleAnswer",
    "OracleInputError",
    "t01_total_outflow",
    "t07_needs_irrigation_now",
    "t09_falls_below_threshold",
]
