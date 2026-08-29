"""The answer contract: the instruction that asks for it, and the parser that reads it.

The contract is **evaluation-only** (``agent_architecture.md`` §2). Production
answers in prose and may ask a clarifying question; an evaluation rollout answers
with one JSON object carrying a two-valued ``status`` and a scalar-or-null
``answer``, because a metric cannot score prose and the optimizer cannot be given
signal on a turn type no case exercises (``decisions.md`` § The answer contract).

Two things live here and they are two halves of one mechanism:

* :data:`EVALUATION_ROOT_INSTRUCTION` — the handwritten candidate instruction. It
  carries the contract, and it carries the **explicit no-clarification clause**:
  a question back would score as a wrong answer rather than as the correct
  behaviour it is in production, so the candidate is told there is nobody to ask.
  :data:`~water_assistant_agent.assistant.agents.root_agent.agent.ROOT_INSTRUCTION`
  is untouched by this module and keeps both the prose and the clarification.
* :func:`parse_contract` — the reader. It returns ``None`` for a final message
  that parses to **neither** status, and that outcome is a ``parse_failure``
  diagnostic rather than a wrong answer: a candidate degrading the output format
  has to stay distinguishable from one degrading its reasoning (§7).

**Authored, not derived.** The evaluation instruction is not the production one
with a section swapped: it is the search's starting point and is byte-stable
after the freeze (T107), so an edit to production's prose must not move it.

**Its routing half is deliberately weak, and its contract half is not.** The
instruction used to carry production's routing rules, its tool-result taxonomy
and its caveat list, because the tools are the same; the headroom experiment of
registration 3 cut all three back to a bare inventory of tool names, on the
hypothesis that a seed this detailed leaves an optimizer nothing to find. What
it did **not** touch is everything from ``### How you answer`` down to the
``explanation`` bullet: the contract block and the no-clarification clause are
what :func:`parse_contract` reads and what makes an abstention scorable, so
they are measurement apparatus rather than the thing under test, and they are
byte-identical to the text every earlier run used.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

CONTRACT_STATUSES: tuple[str, ...] = ("answered", "not_available")
"""The contract's two status values (``agent_architecture.md`` §2).

Two-valued on purpose, and **not** the tool-level outcome enum of §3: a tool's
``not_available`` is an upstream cause that can make the agent's abstention
correct, not the thing being measured. A tool ``error`` has no representation
here at all.
"""

CONTRACT_UNITS: tuple[str | None, ...] = (
    "L",
    "mm",
    "°C",
    "pp",
    "%θ",
    "%",
    "count",
    None,
)
"""The unit vocabulary the contract may name (``agent_architecture.md`` §2, §7).

Stated here for the instruction to quote and for a scorer to normalize against;
:func:`parse_contract` does **not** enforce it. A unit outside the vocabulary is
a wrong answer for the answer metric to catch, not a malformed contract — the
only thing that makes a message unparseable is failing to state a status.
"""


EVALUATION_ROOT_INSTRUCTION = """You are a water-management research assistant for a green-roof test site in Leipzig (51.353484 N, 12.432152 E, 142 m a.s.l.). The site records outflow, radiation, soil moisture, soil temperature and weather across five roof segments on one building.

### How you answer

**Answer the question you were asked, and never ask one back.** There is nobody to answer it: this conversation has exactly one user turn and your reply ends it. Where a request admits more than one reading, take the most plausible one and say which you took in `explanation`.

Your final message is **one JSON object and nothing else** — no prose before or after it, no code fence:

{"status": "answered" | "not_available",
 "answer": <bool | number | "YYYY-MM-DD" | null>,
 "unit": "<L | mm | °C | pp | %θ | % | count | null>",
 "explanation": "<how you got there, and every caveat a tool reported>"}

* **`status`** is `"answered"` when you can state the answer, and `"not_available"` when what was asked for does not exist at this site — a roof the water balance cannot model, a quantity no instrument records, a day beyond the forecast horizon. A tool that *failed* is not a `"not_available"`.
* **`answer`** carries the value alone, with no words around it: `true` or `false` for a yes/no question, a bare number for a quantity, `"YYYY-MM-DD"` for a date. It is `null` whenever `status` is `"not_available"`, and also `null` when what was asked for is a chart — there the chart is the deliverable and `explanation` says what it shows.
* **`unit`** names the unit `answer` is in, out of the list above, and is `null` for a boolean, a date or a null answer. Report the unit the tool gave you: never convert litres to millimetres or `%` to `%θ` yourself.
* **`explanation`** is free text. Put the reasoning and every caveat there.

### The tools

You have six of them: text_to_sql_agent, predict_green_roof_water_balance_tool, get_weather_forecast_tool, calc_irrigation, lookup_reference and plot_timeseries. Each one's own declaration says what it does and what arguments it takes.
"""  # noqa: E501 - prompt text, wrapped as the model reads it


@dataclass(frozen=True)
class AnswerContract:
    """One parsed final message.

    ``status`` is always one of :data:`CONTRACT_STATUSES` — a message that stated
    neither never becomes an :class:`AnswerContract` at all. The other three
    fields are carried **as the model wrote them**: normalization is the answer
    scorer's (§7), and a harness that coerced here would hide the difference
    between a candidate that answered in the wrong unit and one that answered
    wrongly.
    """

    status: str
    answer: bool | int | float | str | None
    unit: str | None
    explanation: str


def parse_contract(text: str | None) -> AnswerContract | None:
    """Read *text* as an answer contract, or return ``None``.

    ``None`` is the ``parse_failure`` outcome of §7 and is **not** a wrong
    answer: the two are reported apart so an optimizer degrading the output
    format stays distinguishable from one degrading its reasoning.

    The one thing that decides it is the **status**. A message that carries a
    JSON object with ``status`` set to ``"answered"`` or ``"not_available"``
    parses; anything else — prose, a clarifying question, a
    ``needs_clarification`` status the contract does not have, an empty final
    message — does not. Fields other than ``status`` are read permissively: an
    answer of the wrong type or a unit outside the vocabulary is something the
    answer metric must see and score, not something to reject here.

    The object does not have to be alone in the message even though the
    instruction says it should be. A fenced block or an object embedded in prose
    is read, and where a message carries several, the **last** one wins: a model
    that restates its answer has stated the later one.
    """
    if not text:
        return None
    for candidate in _candidate_objects(text):
        status = candidate.get("status")
        if isinstance(status, str) and status in CONTRACT_STATUSES:
            unit = candidate.get("unit")
            explanation = candidate.get("explanation")
            return AnswerContract(
                status=status,
                answer=candidate.get("answer"),
                unit=unit if isinstance(unit, str) else None,
                explanation=explanation if isinstance(explanation, str) else "",
            )
    return None


def _candidate_objects(text: str) -> Iterator[dict[str, Any]]:
    """Yield every JSON object *text* could be carrying, best candidate first.

    The instructed shape comes first — the whole message, and nothing else —
    then fenced blocks, then objects embedded in prose. Within each of the last
    two the scan runs backwards, so the message's final restatement is the one
    that is read.
    """
    whole = _as_object(text.strip())
    if whole is not None:
        yield whole
    for block in reversed(_fenced_blocks(text)):
        obj = _as_object(block.strip())
        if obj is not None:
            yield obj
    for span in reversed(_braced_spans(text)):
        obj = _as_object(span)
        if obj is not None:
            yield obj


def _as_object(fragment: str) -> dict[str, Any] | None:
    """*fragment* as a JSON object, or ``None`` if it is not one."""
    if not fragment.startswith("{"):
        return None
    try:
        value = json.loads(fragment)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _fenced_blocks(text: str) -> list[str]:
    """The bodies of every ``` fenced block in *text*, in order.

    Split rather than matched with a regex so an unterminated fence — a
    truncated final message — contributes its body instead of nothing.
    """
    parts = text.split("```")
    blocks: list[str] = []
    for body in parts[1::2]:
        # ```json / ```JSON / ``` — drop an info string, keep the body.
        first, newline, rest = body.partition("\n")
        blocks.append(rest if newline and not first.strip().startswith("{") else body)
    return blocks


def _braced_spans(text: str) -> list[str]:
    """Every balanced ``{...}`` span in *text*, outermost only, in order.

    Brace counting is string-aware: a ``}`` inside a JSON string literal — an
    explanation quoting the contract, say — must not close the object.
    """
    spans: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start : index + 1])
    return spans
