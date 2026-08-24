"""The three committed case files, and the emitter that wrote them (T114).

Two halves. The first tests the **emitter** — key order, sort order, byte
stability, and the one constraint a schema cannot express — on cases built in
memory, so a failure names the emitter. The second tests the **committed files**
as artifacts: they validate, they carry the ledger, and they load through
MLflow's own conversion as ``train_data``. That half would catch a file edited by
hand, which is the thing ``eval/schema/README.md`` says must never happen.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from mlflow.genai.evaluation.utils import _convert_eval_set_to_df
from mlflow.genai.optimize.util import validate_train_data

from eval.generation import emit
from eval.generation.instantiate import case_envelope, gold_conflicts, validate
from eval.generation.splits import SPLITS, day_pools, overlaps, stripe_report
from eval.generation.templates import TEMPLATES, as_of_at, band_days
from eval.oracles.pins import stamp
from water_assistant_agent.assistant.toolset import LOOKUP_TOOL, TEXT_TO_SQL_TOOL

COMMITTED = {split: emit.load(split) for split in SPLITS}
"""The three files as committed, read once. Everything below reads these."""

ALL_CASES = [case for cases in COMMITTED.values() for case in cases]


def _envelope(case_id: str = "T01-0001", **overrides: Any) -> dict[str, Any]:
    """One valid case, for the emitter tests that need a case rather than a suite."""
    case = case_envelope(
        TEMPLATES["T01"],
        case_id=case_id,
        as_of=as_of_at(band_days()[-1]),
        params={"roof": "wetland", "month": "2026-02"},
        materialized={
            "status": "answered",
            "answer": 39.4,
            "unit": "L",
            "pins": stamp(),
        },
    )
    for half, values in overrides.items():
        case[half] = {**case[half], **values}
    return case


# --- The emitter ------------------------------------------------------------------


def test_the_emitted_text_is_the_committed_shape():
    """Pretty-printed array, two-space indent, trailing newline (`schema/README.md`).

    An indented array diffs per field where JSONL diffs per line, which is the
    whole reason the format is not JSONL.
    """
    text = emit.serialize([_envelope()])
    assert text.startswith("[\n  {\n")
    assert text.endswith("}\n]\n")
    assert '\n      "case_id": "T01-0001"' in text
    assert json.loads(text) == [emit.ordered(_envelope())]


def test_keys_are_written_in_a_fixed_order_and_not_in_insertion_order():
    """The order a key was inserted in is a property of a code path, not of a case.

    `tolerance` is the one that proves it: the envelope appends it only where the
    answer is numeric, so insertion order puts it last on some cases and nowhere
    on others. In the committed order it keeps its place among the others.
    """
    numeric = emit.ordered(_envelope())
    assert list(numeric["inputs"]) == list(emit.INPUT_KEYS)
    assert list(numeric["expectations"]) == list(emit.EXPECTATION_KEYS)

    # An abstention, built the way the generator builds one: a null answer takes
    # no tolerance, because the schema admits none where nothing is compared.
    abstention = emit.ordered(
        case_envelope(
            TEMPLATES["T17a"],
            case_id="T17a-0001",
            as_of=as_of_at(band_days()[-1]),
            params={},
            materialized={
                "status": "not_available",
                "answer": None,
                "unit": None,
                "pins": stamp(),
            },
        )
    )
    assert "tolerance" in numeric["expectations"]
    assert list(abstention["expectations"]) == [
        key for key in emit.EXPECTATION_KEYS if key != "tolerance"
    ]
    # Same relative order with the optional key removed, so its absence moves one
    # line rather than every line after it.
    assert [k for k in numeric["expectations"] if k != "tolerance"] == list(
        abstention["expectations"]
    )


def test_an_unknown_key_is_refused_rather_than_written_in_dict_order():
    """A key the emitter does not know would be written unordered, or dropped silently.

    Both are worse than a message: the first breaks byte stability, the second
    loses a field the schema may since have required.
    """
    with pytest.raises(ValueError, match="unordered"):
        emit.ordered(_envelope(inputs={"style": "de_knapp"}))


def test_cases_are_sorted_by_case_id():
    """So a template's instances sit together and a re-draw moves one region.

    Sorted by the emitter rather than by the draw order, which follows the split
    loop and the rejection sampler.
    """
    text = emit.serialize([_envelope("T09-0003"), _envelope("T01-0002"), _envelope("T01-0001")])
    assert [case["inputs"]["case_id"] for case in json.loads(text)] == [
        "T01-0001",
        "T01-0002",
        "T09-0003",
    ]


def test_serializing_the_same_cases_twice_produces_the_same_bytes():
    """The emitter's half of byte stability, isolated from the draw's.

    A re-generation must produce the same file or the diff says nothing about the
    suite; the seed covers the draw, and this covers everything downstream of it.
    """
    cases = [_envelope("T01-0002"), _envelope("T01-0001")]
    assert emit.serialize(cases) == emit.serialize(cases)
    assert emit.serialize(cases) == emit.serialize(list(reversed(cases)))


def test_german_text_is_written_as_itself_and_not_escaped():
    """`ensure_ascii=False`, so a reviewer reads the question the agent is shown.

    `\\u00e4` is the same string and an unreadable diff, on half the suite.
    """
    case = _envelope()
    case["inputs"]["question"] = "Wie viel ist vom bewässerten Extensivdach abgeflossen?"
    text = emit.serialize([case])
    assert "bewässerten" in text
    assert "\\u00e4" not in text


# --- The constraint no schema can express -------------------------------------------


def test_a_gold_tool_may_not_also_be_a_must_not():
    """`eval/schema/README.md`'s fourth validator, which compares two sibling arrays.

    A schema validates one document and cannot say that one array's members are
    absent from another's, so this is the generator's. It is worth having because
    the failure is silent in both directions: the trajectory metric requires the
    call and the route metric penalizes it, so every candidate loses a point it
    cannot win back on a case that looks well-formed.
    """
    conflicted = _envelope(
        expectations={
            "expected_tool_calls": [{"name": TEXT_TO_SQL_TOOL}],
            "must_not_tools": [TEXT_TO_SQL_TOOL, LOOKUP_TOOL],
        }
    )
    assert gold_conflicts(conflicted) == (TEXT_TO_SQL_TOOL,)
    # And the schema itself is content with it, which is why the check exists.
    assert validate(conflicted) == ()
    assert any("gold call and a must-not" in problem for problem in emit.check([conflicted]))
    with pytest.raises(ValueError, match="gold call and a must-not"):
        emit.write([conflicted], Path("/dev/null"))


def test_no_committed_case_conflicts_its_own_gold_set():
    """The same rule over the suite as committed, which is where it has to hold."""
    for case in ALL_CASES:
        assert gold_conflicts(case) == (), case["inputs"]["case_id"]


def test_a_duplicate_case_id_is_refused():
    """Two splits draw the same template, so the instance numbers must not collide."""
    problems = emit.check([_envelope("T01-0001"), _envelope("T01-0001")])
    assert any("duplicate case_id" in problem for problem in problems)


def test_an_invalid_case_is_never_written():
    """The schema is checked at the point the case is built *and* before it is written.

    Twice on purpose: the generator's check names the draw that produced it, and
    this one is what a caller assembling cases from anywhere else runs into.
    """
    broken = _envelope(expectations={"answer": "not-a-date"})
    assert validate(broken)
    with pytest.raises(ValueError, match="invalid case"):
        emit.write([broken], Path("/dev/null"))


# --- The committed files ------------------------------------------------------------


def test_every_committed_case_satisfies_the_schema():
    """The files are the artifact; a case that does not validate is unscoreable."""
    for split, cases in COMMITTED.items():
        for case in cases:
            assert validate(case) == (), f"{split}: {case['inputs']['case_id']}"


def test_the_committed_files_are_in_the_emitted_form():
    """Sorted, ordered, indented — and therefore not hand-edited.

    Re-serializing what is committed has to reproduce the committed bytes, which
    it cannot if a field was reordered, a case inserted out of order, or the
    indentation touched.
    """
    for split, filename in emit.SPLIT_FILES.items():
        path = emit.CASES_DIR / filename
        assert path.read_text(encoding="utf-8") == emit.serialize(COMMITTED[split])


def test_the_committed_suite_carries_the_ledger_it_can():
    """§1.7's 100 / 125, and a test_unseen short by a stated amount.

    Train and test_seen are exactly `templates × m`. The holdout is 51 of 56: five
    instances of T26's two cross-roof variants answer the winning roof's canonical
    name, which `case.schema.json`'s `answer` does not admit, and both surfaces are
    frozen as of T107. Stated here rather than rounded away — the number is what
    the suite has, and `findings.md` carries why.
    """
    assert len(COMMITTED["train"]) == 100
    assert len(COMMITTED["test_seen"]) == 125
    assert len(COMMITTED["test_unseen"]) == 51
    assert len(ALL_CASES) == 276


def test_every_committed_case_id_is_unique_across_the_whole_suite():
    """Not only within a split: a case id names a case, and the three files are one suite."""
    ids = [case["inputs"]["case_id"] for case in ALL_CASES]
    assert len(ids) == len(set(ids))


def test_the_committed_splits_share_no_sampled_parameter():
    """T113's exit criterion, asserted against the files a reviewer can open.

    The strongest form of the check: it needs neither the sampler nor a
    generation run, only `inputs.params`, which §6.1 carries for exactly this.
    """
    assert overlaps(COMMITTED["train"], COMMITTED["test_seen"]) == {}


def test_the_committed_as_of_is_striped_and_not_cut():
    """The other exit criterion, likewise from the files alone.

    Longest consecutive run 1 in every split — a contiguous cut cannot produce
    that — and every case's `as_of` inside its own split's stripe.
    """
    report = stripe_report(COMMITTED)
    pools = day_pools()
    for split in SPLITS:
        assert report[split]["longest_consecutive_run"] == 1, split
        assert report[split]["months"] >= 10, split
        for case in COMMITTED[split]:
            day = datetime.fromisoformat(case["inputs"]["as_of"]).date()
            assert day in set(pools[split]), f"{split}: {case['inputs']['case_id']}"


def test_language_is_balanced_in_the_committed_files():
    """The stratum as emitted: 50/50 train, 63/62 test_seen, and both in the holdout."""
    counts = {
        split: {
            language: sum(1 for case in cases if case["inputs"]["language"] == language)
            for language in ("en", "de")
        }
        for split, cases in COMMITTED.items()
    }
    assert counts["train"] == {"en": 50, "de": 50}
    assert counts["test_seen"] == {"en": 63, "de": 62}
    assert counts["test_unseen"]["en"] and counts["test_unseen"]["de"]
    assert abs(counts["test_unseen"]["en"] - counts["test_unseen"]["de"]) <= 2


def test_every_committed_case_carries_the_database_it_was_answered_against():
    """A case whose pins have moved is not comparable to one whose have not (§6.1)."""
    for case in ALL_CASES:
        assert case["expectations"]["pins"]["duckdb_sha256"]


def test_no_committed_question_is_empty_or_carries_an_unsubstituted_placeholder():
    """A `{roof}` that reached the file is a question the agent cannot answer."""
    for case in ALL_CASES:
        question = case["inputs"]["question"]
        assert question.strip()
        assert "{" not in question and "}" not in question, case["inputs"]["case_id"]


# --- Loadable as MLflow train_data ---------------------------------------------------


@pytest.mark.parametrize("split", list(SPLITS))
def test_a_committed_split_loads_as_mlflow_train_data(split: str):
    """`optimize_prompts` takes a list of dicts and requires a non-empty `inputs`.

    Run through MLflow's own `_convert_eval_set_to_df` and `validate_train_data`
    rather than a paraphrase of them, so the claim is about the installed version
    (`findings.md` § Optimizer internals) and not about our reading of it. The
    search and the measurement run pass `load(split)` straight in.
    """
    cases = emit.load(split)
    frame = _convert_eval_set_to_df(cases)
    assert sorted(frame.columns) == ["expectations", "inputs"]
    validate_train_data(frame, None, None)
    assert emit.validate_as_train_data(cases) == ()


def test_a_record_with_no_inputs_is_what_mlflow_would_reject():
    """Not vacuous: the check above passes because the cases are right, not by luck."""
    assert emit.validate_as_train_data([{"inputs": {}, "expectations": {"status": "answered"}}])
    assert emit.validate_as_train_data([{"inputs": {"question": "x"}}])
