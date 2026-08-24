"""Writing the three case files, byte-stably and through the schema (T114).

``eval/cases/{train,test_seen,test_unseen}.json`` are **pretty-printed JSON
arrays, generated and never hand-edited** (``eval/schema/README.md``): fixed key
order, cases sorted by ``case_id``, ``indent=2``, trailing newline. An indented
array diffs per field where JSONL diffs per line, and the three files are the
suite — a scorer opens one record and needs no second file.

**Byte stability is the property, and it needs three things at once.** The same
generation pass must produce the same bytes on two runs, or a re-generation
produces a diff that says nothing about the suite:

* **key order** is :data:`INPUT_KEYS` and :data:`EXPECTATION_KEYS`, written out
  rather than taken from the dict's insertion order. A dict preserves insertion
  order, but the order a key was inserted in is a property of a code path — the
  optional ``tolerance`` arrives last on a numeric answer and never on a null one
  — and that is not something a committed file should record.
* **case order** is by ``case_id``, so a template's instances sit together and a
  re-draw that moves one case moves one region of one file.
* **the draw itself** is seeded, which is :func:`~eval.generation.instantiate.generate`'s
  affair rather than this module's; what belongs here is that nothing downstream
  of the draw introduces an order of its own.

**Loadable directly as MLflow ``train_data``.** ``optimize_prompts`` accepts a
list of dicts and requires one column: a non-empty ``inputs``
(``mlflow/genai/optimize/util.py:validate_train_data``, ``findings.md``
§ Optimizer internals). ``expectations`` is the channel scorers read. So
:func:`load` returns exactly what that parameter takes, and
:func:`validate_as_train_data` asserts the requirement rather than assuming it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from eval.generation.instantiate import GenerationRun, gold_conflicts, validate

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = REPO_ROOT / "eval" / "cases"

SPLIT_FILES: Mapping[str, str] = {
    "train": "train.json",
    "test_seen": "test_seen.json",
    "test_unseen": "test_unseen.json",
}
"""One file per split, named for it. ``pilot.json`` sits beside them and is the
pilot's committed record (T107), not a split."""

INPUT_KEYS: tuple[str, ...] = (
    "case_id",
    "template_id",
    "language",
    "as_of",
    "question",
    "params",
)
"""``inputs`` key order: what the case *is*, then when, then what it asks.

The identifying fields lead because a reviewer scanning a diff reads them first,
and ``params`` trails because it is the only one of variable size.
"""

EXPECTATION_KEYS: tuple[str, ...] = (
    "status",
    "answer",
    "unit",
    "tolerance",
    "answer_metric",
    "expected_tool_calls",
    "must_not_tools",
    "gold_cards",
    "argument_checks",
    "pins",
)
"""``expectations`` key order: the answer and how it compares, then the route, then
the surface it was materialized against. ``tolerance`` is optional and keeps its
place in the order wherever it appears, so its presence changes one line rather
than the position of every line after it.
"""


def ordered(case: Mapping[str, Any]) -> dict[str, Any]:
    """*case* with its keys in the committed order, and no key invented or dropped.

    Unknown keys raise rather than being passed through or silently discarded: a
    key this does not know is either a schema change nobody reordered for, or a
    field that would be written in dict order and break byte stability.
    """
    for half, keys in (("inputs", INPUT_KEYS), ("expectations", EXPECTATION_KEYS)):
        unknown = set(case[half]) - set(keys)
        if unknown:
            raise ValueError(
                f"{case['inputs'].get('case_id', '?')} carries unordered "
                f"{half} key{'s' if len(unknown) > 1 else ''} "
                f"{', '.join(sorted(unknown))}; add them to emit.py's key order"
            )
    return {
        "inputs": {key: case["inputs"][key] for key in INPUT_KEYS if key in case["inputs"]},
        "expectations": {
            key: case["expectations"][key]
            for key in EXPECTATION_KEYS
            if key in case["expectations"]
        },
    }


def serialize(cases: Iterable[Mapping[str, Any]]) -> str:
    """One split's file, as the exact text it is written as.

    Separate from :func:`write` so a byte-stability check compares strings rather
    than reading files back, and so a caller can diff a proposed file against the
    committed one without writing anything.
    """
    ordered_cases = sorted(
        (ordered(case) for case in cases), key=lambda case: case["inputs"]["case_id"]
    )
    return json.dumps(ordered_cases, indent=2, ensure_ascii=False) + "\n"


def check(cases: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Everything wrong with a split before it is written — empty is emittable.

    Three checks, and the third is the one no schema can make. The schema
    validates a document; ``case_id`` uniqueness and the gold/must-not conflict
    are both statements about a *set* of documents or a *pair of sibling arrays*,
    which is why ``eval/schema/README.md`` assigns them here.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for case in cases:
        case_id = case["inputs"]["case_id"]
        if case_id in seen:
            problems.append(f"{case_id}: duplicate case_id")
        seen.add(case_id)
        for error in validate(case):
            problems.append(f"{case_id}: {error}")
        conflicts = gold_conflicts(case)
        if conflicts:
            problems.append(
                f"{case_id}: {', '.join(conflicts)} is both a gold call and a must-not"
            )
    return tuple(problems)


def write(
    cases: Sequence[Mapping[str, Any]], path: Path, *, verify: bool = True
) -> str:
    """Write one split's file and return the text written.

    *verify* off is for a caller that has already run :func:`check` over the whole
    suite; nothing in the repository does, and the default is what a hand-run
    invocation should get.
    """
    if verify:
        problems = check(cases)
        if problems:
            raise ValueError(
                f"{path.name} would carry {len(problems)} invalid case"
                f"{'s' if len(problems) > 1 else ''}:\n  " + "\n  ".join(problems)
            )
    text = serialize(cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def emit(run: GenerationRun, *, directory: Path = CASES_DIR) -> dict[str, Path]:
    """Write every split a *run* produced, and return the files written."""
    written: dict[str, Path] = {}
    for split, filename in SPLIT_FILES.items():
        cases = run.for_split(split)
        if not cases:
            continue
        path = directory / filename
        write(cases, path)
        written[split] = path
    return written


# --- Reading the committed files ---------------------------------------------------


def load(split: str, *, directory: Path = CASES_DIR) -> list[dict[str, Any]]:
    """One split's cases, in the shape ``optimize_prompts`` takes as ``train_data``.

    A list of dicts, each with ``inputs`` and ``expectations`` — MLflow's own
    accepted format, so the search and the measurement run pass this straight in
    with no adapter between the file and the optimizer.
    """
    path = directory / SPLIT_FILES[split]
    return json.loads(path.read_text(encoding="utf-8"))


def validate_as_train_data(cases: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """MLflow's own requirement on a training record, asserted rather than assumed.

    ``validate_train_data`` raises where a record has no ``inputs`` or an empty
    one; ``expectations`` is the only channel a scorer reads. Both are checked
    here so the failure is a message about a case rather than an exception from
    inside an optimizer several hundred rollouts in.
    """
    problems: list[str] = []
    for index, case in enumerate(cases):
        if not case.get("inputs"):
            problems.append(f"record {index} has no inputs")
        if not case.get("expectations"):
            problems.append(f"record {index} has no expectations")
    return tuple(problems)


__all__ = [
    "CASES_DIR",
    "EXPECTATION_KEYS",
    "INPUT_KEYS",
    "SPLIT_FILES",
    "check",
    "emit",
    "load",
    "ordered",
    "serialize",
    "validate_as_train_data",
    "write",
]
