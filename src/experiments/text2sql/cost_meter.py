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

import os
from dataclasses import dataclass

# The three metering roles; every optimization-phase call is attributed to exactly one.
ROLES: tuple[str, ...] = ("task", "judge", "optimizer")


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
        for role in ROLES:
            for direction in ("input", "output"):
                var = f"PRICE_{role.upper()}_{direction.upper()}"
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
                values[f"{role}_{direction}"] = price
        return cls(**values)

    def price(self, role: str, direction: str) -> float:
        """EUR-per-1M-tokens price for ``role`` (task/judge/optimizer) and ``direction``
        (input/output)."""
        return getattr(self, f"{role}_{direction}")
