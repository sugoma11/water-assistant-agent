"""In-process money meter for prompt-optimization runs (cost-budget stopping).

One :class:`CostMeter` owns all pricing and spend state for a single optimization run.
It attaches to the three call-path families where they already converge (D1): the
project litellm path (via :mod:`harness`), project-owned OpenAI clients (TextGrad, via
:mod:`prompt_skill`) and the two library-internal paths (GEPA reflection via a litellm
success callback; SkillOpt via its native ``TokenTracker`` deltas). Metering is gated by
:meth:`CostMeter.active` so evaluation paths outside optimization are byte-identical to
today (FR12, NFR4).

Prices come from six env vars ``PRICE_{TASK,JUDGE,OPTIMIZER}_{INPUT,OUTPUT}`` in EUR per
million tokens (D4); the budget is EUR. Roles are ``task``, ``judge`` and ``optimizer``.
"""

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The three metering roles; every optimization-phase call is attributed to exactly one.
ROLES: tuple[str, ...] = ("task", "judge", "optimizer")

# Unmetered-call failure threshold (OQ3, EC2): a run whose unmetered calls exceed
# ``max(UNMETERED_MIN, UNMETERED_FRACTION * metered_calls)`` has spend too unreliable to
# trust, so ``check_unmetered`` raises rather than reporting a misleading cost. Constant
# here, revisitable without spec impact.
UNMETERED_MIN = 5
UNMETERED_FRACTION = 0.05


# ---------------------------------------------------------------------------
# Process-global meter state: master gate + exclusion bucket (D1/D3).
# Deliberately NOT contextvars (revised 2026-07-03): ``mlflow.genai.evaluate`` runs
# every real dataset row on ``MlflowGenAIEvalPredict_N`` worker threads that do not
# inherit the caller's context — thread identity, not concurrency, breaks contextvar
# inheritance — so a contextvar gate/exclusion set around ``evaluate()`` would miss
# every ``eval_fn``-flowing call (TextGrad's gate + bracketing passes, GEPA's
# candidate evals, i.e. most of GEPA's spend). Module-level globals survive any
# thread; they are safe because exactly one meter is active per process and the
# excluded bracketing passes are sequential (nothing else is in flight while one
# runs). Role attribution is construction-bound at the call-site builders (the
# ``cost_meter_role`` completion kwarg) for the same reason.
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_active: Optional["CostMeter"] = None
_excluded: bool = False


def active_meter() -> Optional["CostMeter"]:
    """The meter currently gating metering, or ``None`` outside :meth:`CostMeter.active`
    (standalone eval, test-before/after phases) — the litellm seam no-ops then."""
    with _state_lock:
        return _active


def _is_excluded() -> bool:
    """Whether an :meth:`CostMeter.excluded` bracketing pass is in flight."""
    with _state_lock:
        return _excluded


class BudgetExhaustedStop(Exception):
    """Raised at a SkillOpt rollout checkpoint to abort ``trainer.train()`` when the
    budget is exhausted; :meth:`SkillOptPromptOptimizer.optimize` catches it and takes
    the budget-stop read-back path (D2)."""


class MeterIntegrityError(RuntimeError):
    """Raised by :meth:`CostMeter.check_unmetered` when unmetered calls are frequent
    enough to make the recorded spend meaningless (EC2, OQ3)."""


# ---------------------------------------------------------------------------
# Price configuration (T001)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PriceConfig:
    """Per-role, per-direction prices in EUR per 1M tokens (D4)."""

    task_input: float
    task_output: float
    judge_input: float
    judge_output: float
    optimizer_input: float
    optimizer_output: float

    @classmethod
    def from_env(cls) -> "PriceConfig":
        """Read the six ``PRICE_{TASK,JUDGE,OPTIMIZER}_{INPUT,OUTPUT}`` env vars,
        refusing absent, non-numeric or negative values with a message naming the
        offending env var (EC5, SC6, US5)."""
        values: dict[str, float] = {}
        for role_name in ROLES:
            for direction in ("input", "output"):
                var = f"PRICE_{role_name.upper()}_{direction.upper()}"
                raw = os.environ.get(var)
                if raw is None:
                    raise ValueError(
                        f"Price env var {var} is unset; set it (EUR per 1M tokens) "
                        "before optimizing (see .example.env)."
                    )
                try:
                    price = float(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"Price env var {var}={raw!r} is not a number; set it to a "
                        "non-negative EUR-per-1M-tokens value."
                    ) from exc
                if price < 0:
                    raise ValueError(
                        f"Price env var {var}={raw!r} is negative; set it to a "
                        "non-negative EUR-per-1M-tokens value."
                    )
                values[f"{role_name}_{direction}"] = price
        return cls(**values)

    def price(self, role: str, direction: str) -> float:
        """EUR-per-1M-tokens price for ``role`` (task/judge/optimizer) and ``direction``
        (input/output)."""
        return getattr(self, f"{role}_{direction}")

    def as_log_params(self) -> dict[str, float]:
        """The six prices as identically named, lowercased MLflow params
        (``price_task_input`` ...) so every technique logs them the same way (FR3,
        FR10)."""
        return {
            f"price_{role_name}_{direction}": self.price(role_name, direction)
            for role_name in ROLES
            for direction in ("input", "output")
        }


# ---------------------------------------------------------------------------
# The meter (T002)
# ---------------------------------------------------------------------------
class CostMeter:
    """Thread-safe accumulator of tokens and EUR cost per role, with a budget-based
    stop signal. Per-role token/cost counters cover every metered call (billable +
    excluded) so they reconcile with the post-hoc trace audit (SC5); ``billable_cost``
    is the budget-governing subtotal and ``excluded_cost`` the reserved-pass subtotal
    (D3, SC4)."""

    def __init__(self, budget: float, prices: PriceConfig) -> None:
        self._budget = float(budget)
        self._prices = prices
        self._lock = threading.Lock()
        self._tokens: dict[str, dict[str, int]] = {
            r: {"input": 0, "output": 0} for r in ROLES
        }
        self._cost: dict[str, float] = {r: 0.0 for r in ROLES}
        self._billable_cost = 0.0
        self._excluded_cost = 0.0
        self._metered_calls = 0
        self._unmetered_calls = 0
        self._stop_reason = "completed"

    # -- metering ---------------------------------------------------------
    def record(self, role: Optional[str], input_tokens: int, output_tokens: int) -> None:
        """Accumulate one call's tokens and cost (``tokens/1e6 * price``) into the
        billable or excluded bucket per the :meth:`excluded` context. A missing or
        unknown role is a misattribution we refuse to guess at (R6): it falls through
        to :meth:`record_unmetered`, counting toward the same threshold as
        missing-usage calls."""
        if role not in ROLES:
            logger.warning(
                "COST METER: call with role=%r is not one of %s; counting it as "
                "unmetered rather than misattributing its spend.",
                role,
                ROLES,
            )
            self.record_unmetered(role)
            return
        in_cost = input_tokens / 1e6 * self._prices.price(role, "input")
        out_cost = output_tokens / 1e6 * self._prices.price(role, "output")
        call_cost = in_cost + out_cost
        excluded = _is_excluded()
        with self._lock:
            self._tokens[role]["input"] += input_tokens
            self._tokens[role]["output"] += output_tokens
            self._cost[role] += call_cost
            if excluded:
                self._excluded_cost += call_cost
            else:
                self._billable_cost += call_cost
            self._metered_calls += 1

    def record_unmetered(self, role: Optional[str]) -> None:
        """Register a call whose spend could not be measured (missing usage data, EC2)
        or attributed (missing role, R6) and emit a prominent warning. Tracked so
        :meth:`check_unmetered` can fail a run whose spend is meaningless."""
        with self._lock:
            self._unmetered_calls += 1
            count = self._unmetered_calls
        logger.warning(
            "COST METER: UNMETERED call (role=%r) — usage/role missing, its spend is "
            "NOT counted against the budget (unmetered so far: %d).",
            role,
            count,
        )

    # -- budget/stop primitives ------------------------------------------
    def exhausted(self) -> bool:
        """``billable_cost >= budget``; latches ``stop_reason='budget_exhausted'`` the
        first time it fires (default ``'completed'``; the runner sets ``'failed'`` via
        :meth:`mark_failed` when the optimization phase raises)."""
        with self._lock:
            over = self._billable_cost >= self._budget
            if over and self._stop_reason == "completed":
                self._stop_reason = "budget_exhausted"
            return over

    def check_unmetered(self) -> None:
        """Raise :class:`MeterIntegrityError` when unmetered calls exceed
        ``max(UNMETERED_MIN, UNMETERED_FRACTION * metered_calls)`` (OQ3) — frequent
        enough that the recorded spend can no longer be trusted."""
        with self._lock:
            unmetered = self._unmetered_calls
            threshold = max(UNMETERED_MIN, UNMETERED_FRACTION * self._metered_calls)
        if unmetered > threshold:
            raise MeterIntegrityError(
                f"{unmetered} unmetered LLM calls exceed the tolerated "
                f"{threshold:.1f} (max({UNMETERED_MIN}, "
                f"{UNMETERED_FRACTION:.0%} of metered calls)); recorded spend is "
                "unreliable, failing the run instead of reporting a misleading cost."
            )

    def exclude_accumulated_billable(self) -> None:
        """Reclassify all billable spend so far as excluded (D3). Used by
        ``BudgetStopper`` on its first call to move GEPA's seed full-val pass — which
        runs inside the engine before any stopper fires — into the excluded bucket."""
        with self._lock:
            self._excluded_cost += self._billable_cost
            self._billable_cost = 0.0

    def mark_failed(self) -> None:
        """Record that the optimization phase raised (FR9, EC4/EC6): the run's true
        stop reason is ``'failed'`` even if a budget stop had already latched."""
        with self._lock:
            self._stop_reason = "failed"

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    @property
    def budget(self) -> float:
        """The EUR budget governing :meth:`exhausted`, logged as the ``budget`` param."""
        return self._budget

    @property
    def prices(self) -> PriceConfig:
        """The price config this meter charges against, logged via
        :meth:`PriceConfig.as_log_params`."""
        return self._prices

    # -- reporting --------------------------------------------------------
    def spend_summary(self) -> dict[str, float]:
        """The FR9 metric dict logged by ``_run_optimization``: per-role token counts
        and cost, billable ``cost_total``, ``cost_excluded`` (reserved passes, SC4) and
        ``unmetered_calls`` (EC2)."""
        with self._lock:
            summary: dict[str, float] = {}
            for r in ROLES:
                summary[f"tokens_{r}_input"] = self._tokens[r]["input"]
                summary[f"tokens_{r}_output"] = self._tokens[r]["output"]
                summary[f"cost_{r}"] = self._cost[r]
            summary["cost_total"] = self._billable_cost
            summary["cost_excluded"] = self._excluded_cost
            summary["unmetered_calls"] = self._unmetered_calls
            return summary

    # -- contexts (D1/D3) -------------------------------------------------
    @contextmanager
    def active(self) -> Iterator["CostMeter"]:
        """Master gate: metering seams record only while a meter is active. Everything
        outside — standalone eval, test-before/after phases — is unmetered by design
        (FR12, EC3, NFR4). Backed by process-global state, not a contextvar, so calls
        made on mlflow's eval worker threads still see the gate (see module comment);
        exactly one meter may be active per process."""
        global _active
        with _state_lock:
            if _active is not None:
                raise RuntimeError(
                    "A CostMeter is already active in this process; the global "
                    "metering gate supports exactly one active meter (one "
                    "optimization run) at a time."
                )
            _active = self
        try:
            yield self
        finally:
            with _state_lock:
                _active = None

    @contextmanager
    def excluded(self) -> Iterator[None]:
        """Calls made under this context are still counted (visible as
        ``cost_excluded``) but never charge the budget (FR5, SC4). Backed by
        process-global state so the flag reaches mlflow's eval worker threads (see
        module comment); not reentrant — safe because the reserved bracketing passes
        are strictly sequential, with nothing else in flight."""
        global _excluded
        with _state_lock:
            _excluded = True
        try:
            yield
        finally:
            with _state_lock:
                _excluded = False


# ---------------------------------------------------------------------------
# Attachment primitives (T003)
# ---------------------------------------------------------------------------
class BudgetStopper:
    """GEPA ``StopperProtocol`` (``(gepa_state) -> bool``) that stops the run once the
    budget is exhausted. On its first invocation it reclassifies the spend accumulated
    so far — GEPA's seed full-val pass, which the engine runs before any stopper fires —
    into the excluded bucket (D3), so the seed pass never charges the budget."""

    def __init__(self, meter: CostMeter) -> None:
        self.meter = meter
        self._snapshotted = False

    def __call__(self, gepa_state: Any) -> bool:
        if not self._snapshotted:
            self.meter.exclude_accumulated_billable()
            self._snapshotted = True
        self.meter.check_unmetered()
        return self.meter.exhausted()


def litellm_reflection_callback(meter: CostMeter):
    """Build a litellm success callback that records GEPA's reflection calls to the
    ``optimizer`` role. Task/judge calls carry a ``cost_meter_role`` metadata tag (see
    ``harness.build_completion_kwargs``) and are already metered directly in
    ``completion_with_retry``, so this callback skips them and attributes only the
    untagged, library-internal reflection calls (D1-3).

    The callback records only while ``meter`` is the active meter, so it may be
    registered for a window wider than the optimization phase (``train_gepa`` brackets
    the whole ``_run_optimization``, whose test-before/after eval phases must stay
    unmetered — FR12/EC3). litellm fires it on a background logging executor and
    swallows exceptions, so it must never raise; a call completing right at the end of
    optimization may land after the spend summary is read — bounded to one in-flight
    call and harmless at iteration-boundary checkpoints (R1)."""

    def _callback(
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        if active_meter() is not meter:
            return  # outside this meter's optimization phase (FR12)
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        if "cost_meter_role" in metadata:
            return  # already recorded directly by the litellm seam
        usage = getattr(response_obj, "usage", None)
        if usage is None:
            meter.record_unmetered("optimizer")
            return
        meter.record("optimizer", usage.prompt_tokens, usage.completion_tokens)

    return _callback
