"""The pre-registration, and the check that a run really honoured it (T129).

§7's last reporting rule is a protocol rather than a metric: **budget and
hyperparameters are pre-registered before any test run, all method debugging
happens on train, and every arm is reported on test — never "best of"**. It is
the rule that replaces the fourth split ``decisions.md`` § Splits, sizing and the
holdout rejected: the entry point exposes one dataset channel, so the outer role
a validation set would have played — choosing between arms, reflection models and
budgets — has to be carried by a commitment made in advance instead.

**A commitment nothing checks is a note.** So the registration is a committed
artifact, ``eval/preregistration.json``, and this module is what a run compares
itself against. Two ways of using the comparison, and they are deliberately
different:

* A **search or a measurement run reports its deviations** — the list is logged
  with the run, empty for the registered protocol and non-empty for anything
  else. That is what makes ``just search-smoke`` self-labelling rather than
  forbidden: a three-record run at a budget of 8 is a useful thing to do and a
  useless thing to report as the registered search, and the difference belongs in
  the run's own record.
* The **measurement driver refuses** to run deviating without being told to. A
  test number produced under an unregistered budget is not the number the
  protocol promised, and producing it by accident is exactly what the protocol
  exists to prevent.

**What the digest is for.** ``eval/preregistration.json`` is hashed and the hash
travels with every run, so a reader can tell which registration a number was
produced under — and an edit after the fact moves the hash rather than quietly
redefining what was promised. It is the same mechanism ``eval/pins.json`` uses
and for the same reason.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

PREREG_FILE = REPO_ROOT / "eval" / "preregistration.json"
"""The registration itself: committed before the search, hashed into every run."""

SEARCH = "search"
MEASUREMENT = "measurement"
ANALYSIS = "analysis"


class PreregistrationViolation(RuntimeError):
    """A run's parameters are not the registered ones and it was not told to deviate.

    Raised by the measurement driver, never by a search: a short search at a
    smoke budget is a legitimate thing to run, and a test number under an
    unregistered budget is not a legitimate thing to publish.
    """


def load(path: Path = PREREG_FILE) -> dict[str, Any]:
    """The registration, as committed.

    Raises:
        FileNotFoundError: there is none. A missing registration is not an empty
            one — it means the protocol §7 asks for was never written down, and
            defaulting to "anything goes" would be the failure wearing a success.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"No pre-registration at {path}. Budget and hyperparameters are "
            "registered before any test run (§7); a run that cannot find one is "
            "not a registered run."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path = PREREG_FILE) -> str:
    """sha256 of the registration's bytes — the value logged beside every run."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deviations(
    section: str, observed: Mapping[str, Any], path: Path = PREREG_FILE
) -> list[str]:
    """How *observed* differs from the registered *section*, one line per difference.

    Args:
        section: ``"search"``, ``"measurement"`` or ``"analysis"``.
        observed: The parameters the run actually used, keyed as the
            registration keys them.
        path: The registration to compare against.

    Returns:
        One line per differing key, empty when the run is the registered one. A
        key the registration does not carry is itself reported: a parameter
        nobody registered is a parameter nobody committed to.

    Only the keys *observed* names are compared. The registration carries prose
    alongside the numbers — what the arms are, what was debugged where — and
    demanding that a caller restate all of it would make the check a transcription
    exercise rather than a comparison.
    """
    registered = load(path).get(section)
    if not isinstance(registered, Mapping):
        return [f"{section}: the registration has no {section!r} section"]
    lines: list[str] = []
    for key, have in observed.items():
        if key not in registered:
            lines.append(f"{section}.{key}: not pre-registered, ran {have!r}")
        elif registered[key] != have:
            lines.append(
                f"{section}.{key}: registered {registered[key]!r}, ran {have!r}"
            )
    return lines


def enforce(
    section: str,
    observed: Mapping[str, Any],
    *,
    exploratory: bool = False,
    path: Path = PREREG_FILE,
) -> list[str]:
    """Refuse a deviating run, or label it and let it through when told to.

    Args:
        section: Which registered section the run belongs to.
        observed: The parameters it used.
        exploratory: ``True`` where the caller has *declared* the run
            exploratory. The deviations are then logged and returned rather than
            raised — the run is still recorded as not being the registered one,
            which is the whole point of the flag existing rather than being
            implied by silence.
        path: The registration to compare against.

    Returns:
        The deviation lines, empty for the registered protocol.

    Raises:
        PreregistrationViolation: the run deviates and did not say so.
    """
    lines = deviations(section, observed, path)
    if not lines:
        return lines
    if not exploratory:
        raise PreregistrationViolation(
            "This run's parameters are not the pre-registered ones:\n  "
            + "\n  ".join(lines)
            + "\n\nBudget and hyperparameters are registered before any test run "
            "(§7). Re-run at the registered values, or declare the run "
            "exploratory — an exploratory run is reported as one and is never a "
            "test number."
        )
    logger.warning("Exploratory run: declared deviations", deviations=lines)
    return lines


def run_params(section: str, observed: Mapping[str, Any], *, exploratory: bool = False) -> dict[str, str]:
    """The registration's own columns for an MLflow run.

    The digest so a reader can tell which registration a number was produced
    under, the deviation list so a run that was not the registered one says so in
    its own record, and the declared-exploratory flag so silence never has to be
    interpreted.
    """
    lines = deviations(section, observed)
    return {
        "prereg.sha256": digest(),
        "prereg.section": section,
        "prereg.exploratory": str(bool(exploratory)).lower(),
        "prereg.deviations": "; ".join(lines) if lines else "none",
    }


__all__ = [
    "ANALYSIS",
    "MEASUREMENT",
    "PREREG_FILE",
    "PreregistrationViolation",
    "SEARCH",
    "deviations",
    "digest",
    "enforce",
    "load",
    "run_params",
]
