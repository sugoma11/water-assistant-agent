"""A scripted model and a throwaway prompt registry, so a rollout needs no service.

``run_case`` is the one path the search and the measurement run share, and what
these tests exercise is that path itself — the context it builds, the tools it
binds, the event log it reads back. The model is the only thing faked, because
it is the only thing that cannot be pinned: everything below it (the as-of
views, the card store, the response cache) is already deterministic.

The script is a list of turns. Each is either a tool call to issue or the final
text to answer with, and :class:`ScriptedLlm` plays them in order — so a test
states the trajectory it wants and the rollout runs it for real, tools and all.

The :func:`registry` fixture is the second half: candidate text reaches a
rollout only through a registry read (§6), so a test of that channel needs a
registry. It stands a real MLflow one up on a throwaway SQLite file rather than
faking ``load_prompt``, because what the tests are about is the read itself.

The third half is the **search stub** — :class:`Recorded`, :func:`entry_point`,
:func:`cases_dir`, :func:`scripted_rollouts`. It lives here rather than in
``test_optimize.py`` because two modules now drive a search for two different
reasons: T126 asks what the entry point was handed, and T127 asks what the run
ledger recorded while it ran. One stub, so the two cannot end up testing two
different searches.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow
import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from mlflow.prompt.registry_utils import PromptCache

from harness.ledger import EXPERIMENT


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[str]:
    """A throwaway MLflow prompt registry, restored on the way out.

    The registry URI is process-global and so is MLflow's prompt cache, which
    keys on name and version with no TTL by default. Two tests registering
    ``agent_root_instruction`` version 1 against two different SQLite files would
    otherwise see each other's text, so the cache is cleared around each one —
    for the same reason a search's patch is reverted in a ``finally``.

    A throwaway store has to be throwaway all the way down. The run ledger (T127)
    logs a table and the pin file as *artifacts*, and MLflow's default artifact
    root for a SQLite tracking uri is ``./mlruns`` in the working directory — so
    the testbed's experiment is created here with an artifact location inside
    ``tmp_path``, and a test run leaves nothing in the repository.

    **A test that stands up this registry passes its versions in** (T134). A
    ``run_search`` without ``versions=`` resolves ``eval/pins.json``'s
    ``candidate_prompt_versions``, which is a fact about the *repository's*
    registry — v6 and v7 once a search has minted candidates against it — and
    nothing here has ever registered that many. The pin is not wrong and neither
    is the fallback; they simply belong to different registries, and a test that
    registered its own seed should search at the versions it got back. That is
    also the more honest assertion: it says which text the rollout read.
    """
    previous_registry = mlflow.get_registry_uri()
    previous_tracking = mlflow.get_tracking_uri()
    uri = f"sqlite:///{tmp_path / 'registry.db'}"
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    PromptCache.get_instance().clear()
    mlflow.create_experiment(
        EXPERIMENT, artifact_location=str(tmp_path / "artifacts")
    )
    try:
        yield uri
    finally:
        PromptCache.get_instance().clear()
        mlflow.set_registry_uri(previous_registry)
        mlflow.set_tracking_uri(previous_tracking)


@dataclass(frozen=True)
class Call:
    """A turn that issues one tool call."""

    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class Say:
    """A turn that answers with text and ends the run."""

    text: str


@dataclass(frozen=True)
class Think:
    """A reasoning model's answering turn: a thought part, then the reply beside it.

    The shape ADK returns for a model that reasons — one ``Content`` whose first
    part carries ``thought=True`` and whose second is the reply
    (observed on ``deepseek-v4-flash-0731`` through OpenRouter, T134). Scripted
    rather than probed live, because what is under test is which parts the
    harness reads, not that a provider sets the flag.
    """

    thought: str
    text: str


class ScriptedLlm(BaseLlm):
    """Plays a fixed list of turns, recording the requests it was given."""

    model: str = "scripted"
    script: Sequence[Call | Say | Think] = ()
    requests: list[LlmRequest] = []
    turn: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if self.turn >= len(self.script):
            raise AssertionError(
                f"The script ran out after {len(self.script)} turns; the agent "
                "asked for another."
            )
        turn = self.script[self.turn]
        self.turn += 1
        if isinstance(turn, Think):
            parts = [
                types.Part(text=turn.thought, thought=True),
                types.Part(text=turn.text),
            ]
        elif isinstance(turn, Say):
            parts = [types.Part(text=turn.text)]
        else:
            parts = [
                types.Part(
                    function_call=types.FunctionCall(name=turn.name, args=dict(turn.args))
                )
            ]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


def scripted(*turns: Call | Say | Think) -> ScriptedLlm:
    """A fresh :class:`ScriptedLlm` over *turns* — never shared between rollouts."""
    return ScriptedLlm(script=list(turns), requests=[], turn=0)


def _parts(request: LlmRequest) -> list[types.Part]:
    return [part for content in request.contents for part in (content.parts or [])]


def text_seen(request: LlmRequest) -> str:
    """Every text part the model was given on this turn, joined.

    Read part by part rather than off ``str(request.contents)``: ``google.genai``
    truncates long strings in its own ``__repr__``, so a substring check against
    the repr silently fails on exactly the long payloads worth checking.
    """
    return "\n".join(part.text or "" for part in _parts(request) if part.text)


def results_seen(request: LlmRequest) -> list[Any]:
    """Every tool result the model was given on this turn, in order."""
    return [
        part.function_response.response
        for part in _parts(request)
        if part.function_response
    ]


class Recorded:
    """Stands in for ``optimize_prompts``, keeping its arguments and driving rollouts.

    The rollouts go through MLflow's own ``convert_predict_fn`` rather than
    straight into the callable, because that function is where the entry point
    decides the ``predict_fn``'s *shape*: it validates the signature against the
    record's ``inputs`` keys and then calls ``predict_fn(**request)``. A stub
    that called the callable with the mapping would exercise a contract
    ``optimize_prompts`` does not have — and did, until a smoke run failed on it.
    """

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> Any:
        from mlflow.genai.utils.trace_utils import convert_predict_fn

        self.kwargs = kwargs
        records = kwargs["train_data"]
        # `sample_input=None` skips the library's own probe rollout, which would
        # export traces into the throwaway sqlite store this suite points at. The
        # splat is what it returns either way, and the splat is the shape being
        # exercised; the signature half is checked in
        # `test_the_adapter_satisfies_mlflows_own_signature_check`.
        predict_fn = convert_predict_fn(
            predict_fn=kwargs["predict_fn"], sample_input=None
        )
        # Records come pre-filtered; running each one leaves the candidate
        # surface read, which is what the pass's exit assertion is watching.
        for record in records:
            predict_fn(record["inputs"])
        return SearchOutcome()


class SearchOutcome:
    """What ``optimize_prompts`` returns, reduced to what the harness reads off it."""

    initial_eval_score = 0.5
    final_eval_score = 0.75
    initial_eval_score_per_scorer: dict[str, float] = {}
    final_eval_score_per_scorer: dict[str, float] = {}
    optimized_prompts: list[Any] = []


@pytest.fixture
def entry_point(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """``optimize_prompts`` replaced by a recorder that still drives one rollout."""
    from harness import optimize as optimize_module

    recorder = Recorded()
    monkeypatch.setattr(optimize_module.mlflow.genai, "optimize_prompts", recorder)
    return recorder


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    """A three-record ``train.json`` of real committed cases.

    Real records, because the pre-filter replays each one against the committed
    cache and a fabricated case would only prove the filter runs. A small file,
    because every test here would otherwise replay the whole split for an answer
    ``test_train_data.py`` already gives once.
    """
    from harness.train_data import load_split

    directory = tmp_path / "cases"
    directory.mkdir()
    (directory / "train.json").write_text(
        json.dumps(load_split("train")[:3], indent=2), encoding="utf-8"
    )
    return directory


@pytest.fixture
def scripted_rollouts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """``run_case`` replaced by a fixed answer, so no model and no service is needed.

    The rollout itself is T100's and T121's, tested there against the real
    toolset. What these files are about is the arguments around it, so the
    cheapest honest stand-in is one that still goes through ``predict_fn`` — the
    candidate is read per record, which is what the read assertion is watching.
    """
    seen: list[dict[str, Any]] = []

    def run_case(inputs: Any, **kwargs: Any) -> Any:
        seen.append({"inputs": dict(inputs), **{k: v for k, v in kwargs.items()}})
        from harness.contract import parse_contract
        from harness.run_case import CaseResult

        text = '{"status": "answered", "answer": 1.0, "unit": "L", "explanation": "…"}'
        return CaseResult(
            case_id=str(inputs.get("case_id", "")),
            template_id=str(inputs.get("template_id", "")),
            contract=parse_contract(text),
            final_text=text,
            trajectory=(),
            harness_error=False,
            exclusions=(),
            diagnostics={},
        )

    monkeypatch.setattr("harness.predict.run_case", run_case)
    return seen
