"""Materialized ground truth, computed through the code the tools run.

One oracle per template family entry, registered by template id. An oracle takes
a case's ``inputs`` and the rollout context the answer is computed through, and
returns the four ``expectations`` keys it owns plus the pins the answer was
stamped against (:mod:`eval.oracles.base`).

**Where the set stands.** T103 wrote the first three — T01, T07 and T09 — and
T107 added the coverage draws: T06 (a stated constant) and the two abstentions,
T17a and T18a, whose oracles assert an outcome because there is no number to
compute. T110 adds the measured families in one packet — A, B, C and H — and the
model families D, E, F, G and I in the next. The registry is what T111's
generator looks a template up in, so a template with no oracle fails loudly at
generation rather than emitting a case with no answer.

**T24a is deliberately absent, and its absence is not an omission.** A plot's
deliverable is the spec, the answer is null and the answer metric skips, so there
is nothing for an oracle to materialize; what scores it is ``argument_checks``
over the call's own arguments. A registry entry would have to invent an answer to
have something to return.
"""

from eval.oracles.base import Oracle, OracleAnswer, OracleInputError
from eval.oracles.irrigation import t07_needs_irrigation_now
from eval.oracles.model_chain import t09_falls_below_threshold
from eval.oracles.reference import (
    t06_stated_constant,
    t17a_absent_constant,
    t17b_scope_near_miss,
)
from eval.oracles.sql import (
    t01_total_outflow,
    t02_hot_day_count,
    t03_irrigation_swc_gap,
    t04_outflow_occurred,
    t05_peak_outflow_day,
    t15a_past_rain,
)
from eval.oracles.weather import t18a_unservable_window

ORACLES: dict[str, Oracle] = {
    "T01": t01_total_outflow,
    "T02": t02_hot_day_count,
    "T03": t03_irrigation_swc_gap,
    "T04": t04_outflow_occurred,
    "T05": t05_peak_outflow_day,
    "T06": t06_stated_constant,
    "T07": t07_needs_irrigation_now,
    "T09": t09_falls_below_threshold,
    "T15a": t15a_past_rain,
    "T17a": t17a_absent_constant,
    "T17b": t17b_scope_near_miss,
    "T18a": t18a_unservable_window,
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
    "t02_hot_day_count",
    "t03_irrigation_swc_gap",
    "t04_outflow_occurred",
    "t05_peak_outflow_day",
    "t06_stated_constant",
    "t07_needs_irrigation_now",
    "t09_falls_below_threshold",
    "t15a_past_rain",
    "t17a_absent_constant",
    "t17b_scope_near_miss",
    "t18a_unservable_window",
]
