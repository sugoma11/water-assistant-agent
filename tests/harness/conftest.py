"""A scripted model, so a rollout can be run without a provider.

``run_case`` is the one path the search and the measurement run share, and what
these tests exercise is that path itself — the context it builds, the tools it
binds, the event log it reads back. The model is the only thing faked, because
it is the only thing that cannot be pinned: everything below it (the as-of
views, the card store, the response cache) is already deterministic.

The script is a list of turns. Each is either a tool call to issue or the final
text to answer with, and :class:`ScriptedLlm` plays them in order — so a test
states the trajectory it wants and the rollout runs it for real, tools and all.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


@dataclass(frozen=True)
class Call:
    """A turn that issues one tool call."""

    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class Say:
    """A turn that answers with text and ends the run."""

    text: str


class ScriptedLlm(BaseLlm):
    """Plays a fixed list of turns, recording the requests it was given."""

    model: str = "scripted"
    script: Sequence[Call | Say] = ()
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
        if isinstance(turn, Say):
            part = types.Part(text=turn.text)
        else:
            part = types.Part(
                function_call=types.FunctionCall(name=turn.name, args=dict(turn.args))
            )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def scripted(*turns: Call | Say) -> ScriptedLlm:
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
