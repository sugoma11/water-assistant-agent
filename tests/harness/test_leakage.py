"""A candidate must not carry a gold answer, and the check must stay quiet otherwise.

The failure being guarded is T139's: the search wrote ``the irrigation threshold
for the extensive roofs is 10.0 %θ`` into the root instruction, which is the gold
answer of every T06 instance in train *and* test_seen. §7's train → test_seen gap
is stated to catch memorized constants and cannot catch that one — the splits
share their templates, so the memorized value is correct on test_seen and scores
as a gain.

Two properties carry the check and both are tested: it fires on an answer the
candidate added, and it stays silent on one the seed already contained. The
second is what makes it usable as a gate rather than as a warning nobody reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.leakage import (
    AnswerLeakError,
    enforce,
    find_leaks,
    gold_literals,
    report,
)


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    """Two templates: one answering a constant, one answering a boolean."""
    (tmp_path / "train.json").write_text(
        json.dumps(
            [
                {
                    "inputs": {"case_id": "T06-1", "template_id": "T06"},
                    "expectations": {"answer": 10.0, "unit": "%θ"},
                },
                {
                    "inputs": {"case_id": "T09-1", "template_id": "T09"},
                    "expectations": {"answer": True, "unit": None},
                },
                {
                    "inputs": {"case_id": "T21-1", "template_id": "T21"},
                    "expectations": {"answer": "2025-08-14", "unit": None},
                },
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_a_constant_the_candidate_added_is_a_leak(cases_dir: Path) -> None:
    """T139's actual failure, reduced to its shape."""
    leaks = find_leaks(
        {"root_instruction": "The irrigation threshold is 10.0 %θ."},
        {"root_instruction": "Answer the question you were asked."},
        splits=["train"],
        cases_dir=cases_dir,
    )

    assert len(leaks) == 1
    assert leaks[0].literal == "10.0"
    assert leaks[0].templates == ("T06",)
    assert "irrigation threshold" in leaks[0].context


def test_a_constant_the_seed_already_carried_is_not_a_leak(cases_dir: Path) -> None:
    """The whole reason the check is quiet enough to gate a run on.

    The seed names constants legitimately, and a check that flagged those would
    fire on every run and be turned off within a week. A literal counts only
    where the optimizer is what put it there.
    """
    text = "The irrigation threshold is 10.0 %θ."

    assert not find_leaks(
        {"root_instruction": text}, {"root_instruction": text},
        splits=["train"], cases_dir=cases_dir,
    )


def test_booleans_and_prose_numbers_are_not_literals(cases_dir: Path) -> None:
    """``true`` is ordinary English and ``1`` is an ordinary count.

    A check that accused a candidate of leaking T09's boolean would fire on any
    prose containing the word, which is every candidate ever proposed.
    """
    literals = gold_literals(["train"], cases_dir)

    assert "10.0" in literals
    assert "2025-08-14" in literals
    assert not {"True", "true", "1"} & set(literals)


@pytest.mark.parametrize("longer", ["A reading of 110.05.", "Version 3.10.0 shipped."])
def test_a_literal_inside_a_longer_number_does_not_match(
    cases_dir: Path, longer: str
) -> None:
    """``10.0`` is not in ``110.05``, and a ``\\b`` boundary would say it was."""
    assert not find_leaks(
        {"root_instruction": longer},
        {"root_instruction": ""},
        splits=["train"],
        cases_dir=cases_dir,
    )


def test_a_literal_ending_a_sentence_still_matches(cases_dir: Path) -> None:
    """The full stop is punctuation, not a decimal point.

    The regression this pins: a lookahead forbidding any following ``.`` misses
    ``the threshold is 10.0.``, which is exactly how a candidate writes the leak.
    """
    leaks = find_leaks(
        {"root_instruction": "The threshold is 10.0."},
        {"root_instruction": ""},
        splits=["train"],
        cases_dir=cases_dir,
    )

    assert [leak.literal for leak in leaks] == ["10.0"]


def test_a_date_answer_leaks_like_a_number(cases_dir: Path) -> None:
    leaks = find_leaks(
        {"root_instruction": "The driest day was 2025-08-14."},
        {"root_instruction": ""},
        splits=["train"],
        cases_dir=cases_dir,
    )

    assert [leak.literal for leak in leaks] == ["2025-08-14"]


def test_the_measurement_refuses_and_an_exploratory_run_is_labelled(
    cases_dir: Path,
) -> None:
    """Refused for a measurement, reported for a run that declares itself.

    The same asymmetry the pre-registration already runs on: a leaked candidate
    is still a search result worth recording and is never a test number.
    """
    candidate = {"root_instruction": "The threshold is 10.0 %θ."}
    seed = {"root_instruction": ""}

    with pytest.raises(AnswerLeakError, match="10.0"):
        enforce(candidate, seed, splits=["train"], cases_dir=cases_dir)

    leaks = enforce(
        candidate, seed, exploratory=True, splits=["train"], cases_dir=cases_dir
    )
    assert len(leaks) == 1


def test_a_clean_candidate_reports_as_clean(cases_dir: Path) -> None:
    assert "no gold answer" in report([])
