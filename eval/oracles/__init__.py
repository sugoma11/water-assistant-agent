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

**T24a is registered, and what it materializes is a status rather than a
number.** The earlier reading — that a plot needs no oracle, because its
deliverable is the spec and its answer is null — was right about the answer and
wrong about the case. It held while the template drew one shape. Across the three
variants the measured pair and the model overlay are ``answered`` while the
non-modellable overlay is ``not_available``, and §6.1 gives ``status`` no channel
but this one, so something has to decide which per instance. The answer stays
null on every variant and the answer metric still skips; what the oracle adds is
the abstention the third variant is scored on
(:func:`~eval.oracles.presentation.t24a_plot_request`).
"""

from eval.oracles.base import Oracle, OracleAnswer, OracleInputError
from eval.oracles.hybrid import (
    t08_heatwave_days,
    t12_retention_above_target,
    t20_forecast_heatwave,
    t25_tomorrow_warmer_than_yesterday,
)
from eval.oracles.irrigation import (
    t07_needs_irrigation_now,
    t11_needs_irrigation_tomorrow,
    t16a_manual_on_stated_values,
    t16b_calculator_on_stated_values,
)
from eval.oracles.model_chain import (
    t09_falls_below_threshold,
    t10_predicted_minimum,
    t19_model_deviation,
)
from eval.oracles.presentation import t24a_plot_request, t24b_extensive_gap
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
from eval.oracles.weather import (
    t13_rain_expected,
    t14_forecast_max_temperature,
    t15b_future_rain,
    t18a_unservable_window,
    t18b_missing_variable,
)

ORACLES: dict[str, Oracle] = {
    "T01": t01_total_outflow,
    "T02": t02_hot_day_count,
    "T03": t03_irrigation_swc_gap,
    "T04": t04_outflow_occurred,
    "T05": t05_peak_outflow_day,
    "T06": t06_stated_constant,
    "T07": t07_needs_irrigation_now,
    "T08": t08_heatwave_days,
    "T09": t09_falls_below_threshold,
    "T10": t10_predicted_minimum,
    "T11": t11_needs_irrigation_tomorrow,
    "T12": t12_retention_above_target,
    "T13": t13_rain_expected,
    "T14": t14_forecast_max_temperature,
    "T15a": t15a_past_rain,
    "T15b": t15b_future_rain,
    "T16a": t16a_manual_on_stated_values,
    "T16b": t16b_calculator_on_stated_values,
    "T17a": t17a_absent_constant,
    "T17b": t17b_scope_near_miss,
    "T18a": t18a_unservable_window,
    "T18b": t18b_missing_variable,
    "T19": t19_model_deviation,
    "T20": t20_forecast_heatwave,
    "T24a": t24a_plot_request,
    "T24b": t24b_extensive_gap,
    "T25": t25_tomorrow_warmer_than_yesterday,
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
    "t08_heatwave_days",
    "t09_falls_below_threshold",
    "t10_predicted_minimum",
    "t11_needs_irrigation_tomorrow",
    "t12_retention_above_target",
    "t13_rain_expected",
    "t14_forecast_max_temperature",
    "t15a_past_rain",
    "t15b_future_rain",
    "t16a_manual_on_stated_values",
    "t16b_calculator_on_stated_values",
    "t17a_absent_constant",
    "t17b_scope_near_miss",
    "t18a_unservable_window",
    "t18b_missing_variable",
    "t19_model_deviation",
    "t20_forecast_heatwave",
    "t24a_plot_request",
    "t24b_extensive_gap",
    "t25_tomorrow_warmer_than_yesterday",
]
