"""Gold answers must not appear in candidate text (T144).

**The failure this exists for, observed rather than imagined.** T143's registered
search wrote ``The irrigation threshold for the extensive roofs is 10.0 %θ`` into
the root instruction. ``10.0`` is the gold answer of *every* T06 instance in
train **and** in test_seen: the template asks for one documented constant, so its
answer does not vary across instances, only its phrasing does. The candidate
therefore carried the answer to a whole template, and the agent could report it
without reading the card — T06's trajectory difference was ``+0.000``, so the
tool call still happened and only the *answer* came from the prompt.

**Why the splits do not catch it.** ``agent_architecture.md`` §7 says
``train → test_seen`` catches memorized constants. It does not, and cannot, when
the constant is invariant across a template's instances: test_seen shares its
templates with train, so a memorized T06 answer is *correct* on test_seen and
scores as a gain. Only test_unseen sees through it, and test_unseen is seven
templates that §7 restricts to description. The blind spot is structural, which
is why this check is mechanical rather than a reviewer's job.

**A leak is what the candidate added, not what the prompt always said.** The
seed legitimately names constants — the answer contract's unit vocabulary, the
site's coordinates — and a check that flagged those would cry wolf on every run.
So a literal counts only when it appears in the candidate and **not** in the
component's seed text. That single filter is what makes the check quiet enough to
gate a run on.

**Reported for a search, refused for a measurement**, the asymmetry §6 already
runs on for the pre-registration: refusing inside ``optimize_prompts`` would
discard a search that has been paid for, while a leaked answer reaching the
measurement path becomes a number in the thesis. ``--exploratory`` is the
declared way past it, and a run that takes it says so in its own record.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "eval" / "cases"

SPLITS: tuple[str, ...] = ("train", "test_seen", "test_unseen")
"""Every committed split. A train answer that is also a test answer is the whole
hazard, so the check reads all three rather than the one being run."""


class AnswerLeakError(AssertionError):
    """A candidate carries a gold answer the seed did not contain.

    Raised by the measurement driver, never by a search: a leaked candidate is
    still a search result worth recording, and is not a test number worth
    reporting.
    """


@dataclass(frozen=True)
class Leak:
    """One gold answer found in one candidate component."""

    component: str
    literal: str
    templates: tuple[str, ...]
    context: str

    def __str__(self) -> str:
        where = ", ".join(self.templates)
        return f"{self.component}: {self.literal!r} (gold for {where}) — …{self.context}…"


def _literals(answer: Any) -> set[str]:
    """The written forms of *answer* worth searching for.

    Booleans and nulls are excluded outright: ``true`` and ``false`` are ordinary
    English and a check for them would fire on every candidate ever written. The
    same reasoning bounds the numbers — ``0`` and ``1`` are the counts and indices
    of ordinary prose, so a literal must be at least two characters and carry
    either a decimal point or two digits before it is distinctive enough to
    accuse a candidate over.
    """
    if answer is None or isinstance(answer, bool):
        return set()
    if isinstance(answer, str):
        # A date. Distinctive on its own, and a candidate has no honest reason
        # to name one that happens to be an answer.
        return {answer} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", answer) else set()
    if isinstance(answer, (int, float)):
        forms = {f"{answer}", f"{float(answer):g}"}
        if isinstance(answer, float) and answer.is_integer():
            forms.add(str(int(answer)))
        return {f for f in forms if len(f) >= 2 and ("." in f or len(f.lstrip("-")) >= 2)}
    return set()


def gold_literals(
    splits: Sequence[str] = SPLITS, cases_dir: Path = CASES_DIR
) -> dict[str, set[str]]:
    """Literal → the templates it is the gold answer of, over every committed split.

    Keyed by the literal rather than by the case, because the hazard is a *value*
    reaching the prompt and one value may be the answer of many cases. The
    template set is what makes a report readable: a literal answering one
    template is a memorized constant, and a literal answering six is probably a
    number that means something else.
    """
    found: dict[str, set[str]] = {}
    for split in splits:
        path = cases_dir / f"{split}.json"
        if not path.exists():
            continue
        for case in json.loads(path.read_text(encoding="utf-8")):
            template = case["inputs"]["template_id"]
            for literal in _literals(case["expectations"].get("answer")):
                found.setdefault(literal, set()).add(template)
    return found


def _appears(literal: str, text: str) -> re.Match[str] | None:
    """*literal* in *text* as a value, not as digits inside a longer number.

    Both boundaries are about *numeric continuation* rather than about words,
    because ``\\b`` counts ``.`` as a boundary and would let ``10.0`` match
    inside ``110.05``. Behind: no digit and no decimal point, so ``110.05`` and
    ``3.10.0`` are both refused. Ahead: no digit, and no decimal point that is
    itself followed by one — which blocks ``10.05`` while still matching a
    literal that ends a sentence, ``the threshold is 10.0.`` being exactly how a
    candidate would write the leak this module exists to catch.
    """
    return re.search(rf"(?<![\d.]){re.escape(literal)}(?!\.?\d)", text)


def find_leaks(
    candidate: Mapping[str, str],
    seed: Mapping[str, str],
    *,
    splits: Sequence[str] = SPLITS,
    cases_dir: Path = CASES_DIR,
) -> list[Leak]:
    """Gold answers *candidate* carries that the corresponding *seed* text did not.

    Args:
        candidate: Component key → the candidate's text.
        seed: Component key → the seed's text for that component. A component
            missing here is treated as having an empty seed, which reports more
            rather than less.
        splits: Which committed splits supply the gold answers.
        cases_dir: Where those splits live.

    Returns:
        One :class:`Leak` per (component, literal), ordered by component then
        literal so a report is stable across runs.
    """
    literals = gold_literals(splits, cases_dir)
    leaks: list[Leak] = []
    for component in sorted(candidate):
        text = candidate[component]
        before = seed.get(component, "")
        for literal in sorted(literals):
            if _appears(literal, before):
                # The seed said it too, so the optimizer did not put it there.
                continue
            match = _appears(literal, text)
            if match is None:
                continue
            lo = max(0, match.start() - 60)
            hi = min(len(text), match.end() + 60)
            leaks.append(
                Leak(
                    component=component,
                    literal=literal,
                    templates=tuple(sorted(literals[literal])),
                    context=" ".join(text[lo:hi].split()),
                )
            )
    return leaks


def report(leaks: Iterable[Leak]) -> str:
    """The lines a run logs beside its result, empty when nothing leaked."""
    lines = [str(leak) for leak in leaks]
    if not lines:
        return "no gold answer appears in any candidate component"
    return f"{len(lines)} gold answer(s) in candidate text:\n  " + "\n  ".join(lines)


def enforce(
    candidate: Mapping[str, str],
    seed: Mapping[str, str],
    *,
    exploratory: bool = False,
    splits: Sequence[str] = SPLITS,
    cases_dir: Path = CASES_DIR,
) -> list[Leak]:
    """Refuse a leaking candidate on the measurement path, or label it and go on.

    Args:
        candidate: The arm's text, per component.
        seed: The seed text to difference against.
        exploratory: ``True`` where the caller has *declared* the run
            exploratory, on :mod:`harness.preregistration`'s terms: the leaks are
            logged and returned rather than raised.

    Returns:
        The leaks, empty for a clean candidate.

    Raises:
        AnswerLeakError: the candidate carries a gold answer and the run did not
            declare itself exploratory.
    """
    leaks = find_leaks(candidate, seed, splits=splits, cases_dir=cases_dir)
    if not leaks:
        return leaks
    if not exploratory:
        raise AnswerLeakError(
            report(leaks)
            + "\n\nA candidate carrying a gold answer scores on knowing it rather "
            "than on finding it, and test_seen cannot detect that where the "
            "answer is invariant across a template's instances (T143/T06). "
            "Re-run the search, or declare the run exploratory — an exploratory "
            "run is reported as one and is never a test number."
        )
    logger.warning("Exploratory run: declared answer leakage", leaks=[str(x) for x in leaks])
    return leaks


__all__ = [
    "CASES_DIR",
    "SPLITS",
    "AnswerLeakError",
    "Leak",
    "enforce",
    "find_leaks",
    "gold_literals",
    "report",
]
