"""Materialized ground truth, computed through the code the tools run.

One oracle per template family entry, registered by template id. An oracle takes
a case's ``inputs`` and the rollout context the answer is computed through, and
returns the four ``expectations`` keys it owns plus the pins the answer was
stamped against (:mod:`eval.oracles.base`).

**The set is complete: all 32 catalog entries have one.** T103 wrote the first
three — T01, T07 and T09 — T107 added the coverage draws (T06 and the two
abstentions T17a and T18a, whose oracles assert an outcome because there is no
number to compute), and T110 finished the rest in two packets: the measured
families A, B, C and H, then the model families D, E, F, G and I. The registry is
what T111's generator looks a template up in, so a template with no oracle fails
loudly at generation rather than emitting a case with no answer.

**Registered is not the same as materializable, and one entry is currently the
difference.** T22 raises rather than answering, because the GR2L build serving
this deployment accepts ``albedo`` and ignores it, so every answer would equal
the un-overridden prediction and a candidate that never passed the argument would
score full marks (:func:`~eval.oracles.counterfactual.t22_albedo_override`). The
refusal is computed per draw against the roof's own default, so the entry starts
producing cases unchanged the day the service wires the parameter up. T23 raises
on the same grounds for a *draw* rather than for the template — a window whose
store has saturated has forgotten the seed it was given.

**Four modules carry the model families, split by what they share rather than by
family letter.** ``model_chain`` holds the GR2L scope check and the run every
family D and G oracle goes through; ``counterfactual`` adds the three overrides;
``irrigation`` holds both of the calculator's entry points, so family E's T07 and
T11 sit beside family F's T16a and T16b; ``hybrid`` holds the card-grounded
chains. Family E is therefore in two files, which is the tool boundary rather
than an accident.

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

from eval.oracles.availability import t27_non_modellable_roof
from eval.oracles.base import Oracle, OracleAnswer, OracleInputError
from eval.oracles.counterfactual import (
    t21_forced_rain_minimum,
    t22_albedo_override,
    t23_state_override,
    t26_composed_override,
)
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
    "T21": t21_forced_rain_minimum,
    "T22": t22_albedo_override,
    "T23": t23_state_override,
    "T24a": t24a_plot_request,
    "T24b": t24b_extensive_gap,
    "T25": t25_tomorrow_warmer_than_yesterday,
    "T26": t26_composed_override,
    "T27": t27_non_modellable_roof,
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
    "t21_forced_rain_minimum",
    "t22_albedo_override",
    "t23_state_override",
    "t24a_plot_request",
    "t24b_extensive_gap",
    "t25_tomorrow_warmer_than_yesterday",
    "t26_composed_override",
    "t27_non_modellable_roof",
]
