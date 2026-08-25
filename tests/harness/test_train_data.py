"""The search path's two protections, and the divergences it only reports (T123).

`harness_error` excludes on the measurement path and cannot on the search path,
because ``optimize_prompts`` consumes one float per record and has no channel
for "this one does not count". §7's answer is structural, and these tests hold
the structure to it:

* the training records are **pre-filtered** to cases whose committed answer
  replays from ``eval/cache/`` with the network blocked, and a case that does not
  is dropped **by name**;
* what still fails is **declared and counted**, including the shape MLflow
  swallows — a ``predict_fn`` that raised, which reaches the scorers as a string
  and would otherwise be counted nowhere;
* diverging counts between arms are **named**: failures mean the run is
  repeated, records mean the arms explored different argument space and nothing
  is repaired.

The ledger test runs through MLflow's own ``_build_eval_fn`` and this repo's own
scorers, because the claim is that the two halves agree — the record the ledger
counted is the record the scorers scored 0.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mlflow.genai.optimize.optimize import _build_eval_fn
from mlflow.genai.optimize.util import create_metric_from_scorers

from harness.scorers import SCORERS
from harness.scoring import METRICS, aggregate_scores
from harness.train_data import (
    PREDICT_FN,
    CaptureAudit,
    ResidualLedger,
    ResidualReport,
    cache_entries,
    compare_arms,
    fully_captured,
    guarded,
    load_split,
    residual_report,
    training_records,
)
from water_assistant_agent.assistant.toolset import TEXT_TO_SQL_TOOL

ABSTENTION_TEMPLATES = ("T17a", "T18a")
"""The two train templates whose oracle *refuses* rather than computing a number."""


def train_case(template_id: str) -> dict[str, Any]:
    """The first committed train record of *template_id*."""
    for record in load_split("train"):
        if record["inputs"]["template_id"] == template_id:
            return record
    raise AssertionError(f"no {template_id} case in the train split")


def uncaptured(record: dict[str, Any]) -> dict[str, Any]:
    """*record* re-stated at a horizon far outside the captured neighbourhood.

    ``d`` is the day count the candidate passes through verbatim and the capture
    pass warms at ``d ± 1``; 30 is nowhere near it, so the window this resolves
    to was never recorded and the replay must miss.
    """
    moved = json.loads(json.dumps(record))
    moved["inputs"]["case_id"] = "T09-UNCAPTURED"
    moved["inputs"]["params"]["d"] = 30
    return moved


def outputs_with(exclusions: list[dict[str, str]]) -> dict[str, Any]:
    """A rollout's outputs that completed and reported *exclusions*."""
    return {
        "case_id": "T01-0001",
        "template_id": "T01",
        "status": "answered",
        "answer": 39.4,
        "unit": "L",
        "explanation": "…",
        "final_text": "{}",
        "trajectory": [],
        "harness_error": bool(exclusions),
        "exclusions": exclusions,
        "diagnostics": {},
    }


# ── The pre-filter ───────────────────────────────────────────────────────────


def test_the_committed_train_split_is_fully_captured() -> None:
    """The precondition the search rests on, asserted rather than inherited.

    T116 committed a cache every case of every split replays from; this is the
    same claim restated where the search reads it, so a capture gap opened later
    surfaces as a shrinking ``train_data`` rather than as residual failures
    nobody can attribute.
    """
    audit = fully_captured(load_split("train"))

    assert audit.dropped == {}
    assert len(audit.kept) == audit.cases == 100


def test_a_case_whose_window_was_never_captured_is_dropped_by_name() -> None:
    """A gap is removed from the search *and* named, because the two differ.

    Dropping silently would shrink the objective's denominator with nothing
    saying so; the reason is carried because a case that stops replaying is
    either a capture gap to fill or a pin that moved under the suite, and only
    the text tells them apart.
    """
    captured = train_case("T09")

    audit = fully_captured([captured, uncaptured(captured)])

    assert [record["inputs"]["case_id"] for record in audit.kept] == [
        captured["inputs"]["case_id"]
    ]
    assert set(audit.dropped) == {"T09-UNCAPTURED"}
    assert "CacheMissError" in audit.dropped["T09-UNCAPTURED"]


def test_an_oracle_that_refuses_counts_as_captured() -> None:
    """Refusal is a materialization outcome, not a capture gap.

    The abstention templates fetch nothing and their gold status *is* the
    refusal, so treating it as a failure would drop from the search exactly the
    cases that probe abstention — the metric §7 reports in two halves.
    """
    records = [train_case(template) for template in ABSTENTION_TEMPLATES]

    audit = fully_captured(records)

    assert audit.dropped == {}
    assert len(audit.kept) == len(ABSTENTION_TEMPLATES)


def test_the_prefilter_blocks_sockets_and_puts_them_back() -> None:
    """"Captured" is a fact about the committed cache, not about a lucky call.

    The replay is bound to a ``ReplayCache`` *and* runs with
    ``httpx.AsyncClient.send`` replaced, on the same reasoning
    ``capture_cache.py --verify`` uses. The restore is asserted because a pass
    that left the block installed would break every later test in the process.
    """
    original = httpx.AsyncClient.send

    fully_captured([train_case("T01")])

    assert httpx.AsyncClient.send is original


def test_the_search_reads_a_handful_when_it_is_asked_for_one() -> None:
    """``limit`` takes captured records, never the first *n* of the file.

    A short search is a short search over cases that replay; filtering after
    truncating would let one uncaptured case cost the run a slot it could have
    measured.
    """
    records = training_records("train", limit=3)

    assert len(records) == 3
    assert all("inputs" in record and "expectations" in record for record in records)


# ── The declared residuals ───────────────────────────────────────────────────


def test_a_predict_fn_that_raised_is_counted_and_still_scored_zero(
    registry: str,
) -> None:
    """The shape MLflow swallows, counted here and scored there.

    ``_run_single`` catches the exception and hands the scorers a string
    (``findings.md``), so the failure never reaches the results as a failure.
    :func:`guarded` re-raises after declaring it, which is what makes the two
    agree: the ledger counts the record and the scorers score it 0 as a declared
    residual — the same record, not two accounts of it.

    The throwaway registry is what keeps this fast: ``_build_eval_fn`` asks the
    tracking store for the record's trace, and against an unreachable default it
    is minutes of retries rather than a test.
    """
    ledger = ResidualLedger()

    def exploding(inputs: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the model endpoint is unreachable")

    eval_fn = _build_eval_fn(
        guarded(exploding, ledger),
        create_metric_from_scorers(list(SCORERS), aggregate_scores),
    )
    (result,) = eval_fn({}, [train_case("T01")])

    assert ledger.cases == {"T01-0001"}
    assert ledger.by_source == {PREDICT_FN: 1}
    assert "unreachable" in ledger.declared[0][2]
    assert result.score == 0.0
    assert set(result.individual_scores) == set(METRICS)


def test_an_exclusion_a_completed_rollout_reported_is_declared_from_its_outputs() -> None:
    """The other shape: the rollout finished and said what went wrong.

    Read off the outputs rather than re-derived, so the ledger's sources are the
    taxonomy ``run_case`` already assigns and cannot drift into a second one.
    """
    ledger = ResidualLedger()
    exclusion = {
        "tool": TEXT_TO_SQL_TOOL,
        "source": "text_to_sql_agent",
        "details": "Binder Error: no such column",
    }

    produced = guarded(lambda inputs: outputs_with([exclusion]), ledger)(
        train_case("T01")["inputs"]
    )

    assert produced["harness_error"] is True
    assert ledger.by_source == {"text_to_sql_agent": 1}
    assert TEXT_TO_SQL_TOOL in ledger.declared[0][2]


def test_two_exclusions_on_one_case_are_one_residual_case() -> None:
    """The count §7 compares between arms is cases, not failures.

    A rollout can report two exclusions; a candidate that fumbled twice on one
    case has not compromised two of them, and comparing failure *events* between
    arms would make a noisier arm look like a compromised one.
    """
    ledger = ResidualLedger()
    exclusions = [
        {"tool": "get_weather_forecast_tool", "source": "upstream", "details": "miss"},
        {"tool": TEXT_TO_SQL_TOOL, "source": "text_to_sql_agent", "details": "error"},
    ]

    guarded(lambda inputs: outputs_with(exclusions), ledger)(
        train_case("T01")["inputs"]
    )

    assert len(ledger.cases) == 1
    assert ledger.by_source == {"text_to_sql_agent": 1, "upstream": 1}


def test_the_report_publishes_the_residuals_beside_the_entries_recorded(
    tmp_path: Any,
) -> None:
    """Two numbers, together, because they answer different questions (§7).

    Residuals are what the arm could not measure; recorded entries are what its
    candidate went looking for that the oracle never did. A search runs in record
    mode precisely so the second is a discovery rather than a failure.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    before = cache_entries(cache)
    (cache / "deadbeef.json").write_text("{}", encoding="utf-8")

    ledger = ResidualLedger()
    ledger.declare("T01-0001", "upstream", "cache miss")

    report = residual_report(
        "baseline", ledger=ledger, records=10, entries_before=before, cache_dir=cache
    )

    assert report == ResidualReport(
        arm="baseline",
        records=10,
        residual_cases=1,
        residuals_by_source={"upstream": 1},
        recorded_entries=1,
    )
    assert "1/10 residual case(s)" in report.summary()


# ── The two divergences, named and not resolved ──────────────────────────────


def arm(name: str, *, residuals: int = 0, recorded: int = 0) -> ResidualReport:
    return ResidualReport(
        arm=name,
        records=100,
        residual_cases=residuals,
        residuals_by_source={"upstream": residuals} if residuals else {},
        recorded_entries=recorded,
    )


def test_diverging_failure_counts_say_to_repeat_the_run() -> None:
    """An outage that caught one arm makes the comparison uninterpretable."""
    (verdict,) = compare_arms([arm("baseline", residuals=0), arm("optimized", residuals=4)])

    assert verdict.startswith("REPEAT THE RUN")
    assert "'optimized': 4" in verdict


def test_diverging_record_counts_are_reported_and_not_repaired() -> None:
    """Different candidates reach for different windows; that is not a fault."""
    (verdict,) = compare_arms([arm("baseline", recorded=0), arm("optimized", recorded=17)])

    assert verdict.startswith("REPORTED, NOT REPAIRED")
    assert "different argument space" in verdict


def test_arms_that_agree_on_both_counts_say_nothing() -> None:
    """Silence is the passing case, and one arm has nothing to diverge from."""
    assert compare_arms([arm("baseline", recorded=3), arm("optimized", recorded=3)]) == []
    assert compare_arms([arm("baseline", residuals=9, recorded=3)]) == []


def test_an_audit_reports_what_it_kept_and_what_it_dropped() -> None:
    """The summary is what a search prints before it starts, so it names both."""
    audit = CaptureAudit(kept=(), dropped={"T09-0001": "CacheMissError: …"})

    assert "0 of 1" in audit.summary()
    assert "T09-0001" in audit.summary()


def test_the_ledger_is_empty_until_something_fails() -> None:
    """A clean arm reports zero rather than nothing, which is the point of counting."""
    ledger = ResidualLedger()

    assert ledger.cases == frozenset()
    assert ledger.by_source == {}
    assert pytest.approx(0) == residual_report(
        "baseline", ledger=ledger, records=5, entries_before=cache_entries()
    ).residual_cases
