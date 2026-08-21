"""What an oracle returns, and the contract every oracle keeps.

An oracle materializes **one case's ground truth** by running the same code the
tool under test runs (``agent_architecture.md`` §1 principle 1: oracles import
layer 3 directly). It answers the question and stamps the surface it answered
against; everything else in ``expectations`` — the tolerance, the gold
trajectory, the must-not set, the cards, the argument checks — is a per-template
constant copied in at generation (§6.1), and no oracle invents one.

**The point of importing the tool's own functions** is that oracle and tool
cannot disagree about *how* a number is computed, only about the inputs they were
given. T07 reaches ``irrigation_decision`` and T09 reaches ``run_gr2l`` through
the same call the tool makes, so a change to either moves both at once. Where an
oracle re-derives a step instead — T01's SQL, which has no shared core because
the "tool" is a language model writing a query — the divergence is stated in that
oracle's own docstring.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from water_assistant_agent.assistant.context import ScenarioContext


@dataclasses.dataclass(frozen=True, slots=True)
class OracleAnswer:
    """One materialized answer, and what it was computed against.

    Projects onto the four ``expectations`` keys an oracle owns. ``detail`` is
    not one of them: it carries the intermediate quantities a reviewer needs to
    hand-check the answer — the seed, the ladder's rung, the modelled minimum —
    and it stays out of the emitted case because the case schema admits no key
    for it and a scorer must never read it.
    """

    answer: bool | float | str | None
    unit: str | None
    pins: dict[str, Any]
    status: Literal["answered", "not_available"] = "answered"
    detail: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def expectations(self) -> dict[str, Any]:
        """The four keys this oracle owns, ready to merge with the template's.

        Deliberately partial: the result is not a valid ``expectations`` object
        on its own, and the generator that completes it (T114) validates the
        merged whole against ``eval/schema/case.schema.json``.
        """
        return {
            "status": self.status,
            "answer": self.answer,
            "unit": self.unit,
            "pins": dict(self.pins),
        }


class Oracle(Protocol):
    """The one shape every oracle has.

    *inputs* is the case's own ``inputs`` object — ``as_of`` and ``params`` are
    the two it reads — and *ctx* is the rollout context the answer is computed
    through, so an oracle sees the same as-of database, the same weather client
    and the same response cache a rollout does. Nothing is passed that a rollout
    would not also have; that is what makes the answer reachable.
    """

    async def __call__(
        self, inputs: Mapping[str, Any], ctx: ScenarioContext
    ) -> OracleAnswer: ...


class OracleInputError(ValueError):
    """A case an oracle refuses to answer, as a testbed defect rather than a wrong answer.

    Raised — never returned as an answer — for the same reason
    ``harness/assertions.py`` raises: a number computed over a case that should
    not have been sampled is a number about nothing. The generator's filters
    (``questions.md`` §1.6) should make this unreachable, so reaching it means a
    filter is missing rather than that this case is hard.
    """


def required_params(
    inputs: Mapping[str, Any], *names: str
) -> tuple[Any, ...]:
    """The named params, raising :class:`OracleInputError` on any that is absent.

    A missing parameter is a template/oracle mismatch, and reading it as ``None``
    would push the failure into arithmetic several frames later.
    """
    params = inputs.get("params") or {}
    missing = [name for name in names if params.get(name) is None]
    if missing:
        raise OracleInputError(
            f"case {inputs.get('case_id', '?')} is missing the parameter"
            f"{'s' if len(missing) > 1 else ''} {', '.join(missing)}"
        )
    return tuple(params[name] for name in names)
