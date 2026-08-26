"""The metaprompt: telling the proposer which kind of component it is holding (T133).

**GEPA's default template calls every component "instructions for an
assistant".** Two slots and one framing — *"I provided an assistant with the
following instructions… Your task is to write a new instruction for the
assistant"* (``gepa/strategies/instruction_proposal.py``) — and six of this
repo's seven optimizable components are **tool descriptions**. So the proposer is
told that a function declaration is a system prompt, and the P8c search did what
it was told: it rewrote the GR2L docstring into a Role section with a routing
table, restated the root instruction's answer contract inside it, and named a
callable no toolset resolves (``findings.md``).

**The parameter for saying otherwise is supported and the path to it is proven.**
``gepa.optimize`` takes ``reflection_prompt_template`` as a dict of *component
name → template*, MLflow's merge literal does not carry the key so ``gepa_kwargs``
passes it through, and GEPA's ``propose_new_texts`` guard passes because
``MlflowGEPAAdapter`` does not define one. A search wired with two sentinel
templates put the root template on the root instruction's proposal and the tool
template on every tool proposal (``findings.md``).

**So there are two templates and the dispatch key is the registered name.** The
candidate GEPA holds is keyed by prompt name — ``agent_root_instruction`` and the
six ``agent_tool_*`` — so that is what :func:`reflection_prompt_templates` keys
on, and it is also the string the proposer *reads*: ``component_name`` reaches it
as the registered name, and the ``agent_tool_`` prefix exists because the registry
is shared with the text-to-SQL experiments (T120). It cannot be renamed away
without moving the pinned candidate surface, so the tool template names it and
says what it is: the registry's name for this text, and not a callable. The real
callable comes from :data:`~water_assistant_agent.assistant.toolset.TOOL_NAMES`,
which is the same source T136's sentence inside each tool text reads — the
template and the text it is editing say the same thing about that text.

**Both templates keep ``<curr_param>`` and ``<side_info>``**, which are the only
two substitutions GEPA makes and which ``validate_prompt_template`` enforces;
:func:`reflection_prompt_templates` runs that check against the *installed* gepa
rather than trusting the strings below, for the same reason
:func:`~harness.optimize.preflight` re-checks the aggregation.

**They are search hyperparameters.** They change what every proposal is asked
for, so a search before and one after are not comparable, and the fresh
registration the rerun needs (T134) records them the way it records the budget:
:func:`templates_digest` is the value to register, and a search reports its own
digest beside it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from harness.candidates import PROMPT_NAMES, ROOT_INSTRUCTION
from water_assistant_agent.assistant.toolset import TOOL_NAMES


class MetapromptError(ValueError):
    """A template GEPA would refuse, caught before a search spends anything.

    ``<curr_param>`` and ``<side_info>`` are the only substitutions the renderer
    makes, and a template missing one renders a prompt with the current text or
    the evidence simply absent. GEPA validates for exactly this, once per dict
    entry — inside the proposer, which is after the seed evaluation has been paid
    for.
    """


ROOT_TEMPLATE = """\
The text below is the system instruction of an assistant that answers questions
about a green-roof water-management site. It is the whole of that instruction:
the task, the contract its final answer has to satisfy, and whatever it says
about which tool to reach for when.

```
<curr_param>
```

The assistant does its work by calling tools. Each tool is declared separately —
its name, its parameter schema and its description — and each of those
descriptions is a text of its own, revised on its own and not shown to you here.
Nothing written below adds a tool, removes one or changes what one does: an
instruction describing a tool the assistant does not have leaves it with an
instruction it cannot follow.

The following are examples of task inputs given to the assistant, the
assistant's response to each of them, and feedback on how that response could
have been better:

```
<side_info>
```

Your task is to write a new system instruction for the assistant.

Read the inputs carefully and identify the input format and the task they
describe. Read every response and its feedback, and carry into the instruction
the domain facts, the answer-format rules and the strategies the feedback shows
the assistant needs — none of it will be in front of it next time.

Provide the new instruction inside a single ``` block and nothing else.
"""
"""The template for ``agent_root_instruction`` — the one component that is not a tool."""

TOOL_TEMPLATE = """\
The text below is the description of a single tool, `{callable}` — one of the
{tools} tools an assistant may call while answering questions about a green-roof
water-management site. It is the `description` field of that tool's function
declaration: the assistant's model reads it beside the other {siblings} tool
descriptions and its own system instruction, and uses it to decide whether to
call `{callable}` and with which arguments. It is not the assistant's
instruction and it is not a system prompt.

```
<curr_param>
```

Three things follow from what this text is, and the last of them is a naming
trap.

- It is read next to {siblings} other tool descriptions, so what it has to
  establish is when `{callable}` is the right tool to call and when it is not.
- The tool's parameters are fixed by the function's signature and cannot be
  added, removed or renamed from here; the format of the assistant's final
  answer is fixed by its system instruction, and a copy of that format written
  here is a second copy, free to disagree with the first.
- The callable's name is `{callable}`. The examples below may label this
  component `{component}`, which is the name this text is registered under in a
  prompt registry and is not a callable — an assistant told to call
  `{component}` would be calling a tool that does not exist.

The following are examples of task inputs given to the assistant, the
assistant's response to each of them, and feedback on how that response could
have been better:

```
<side_info>
```

Your task is to write a new description for the tool `{callable}`.

Read the responses and the feedback and identify what the assistant got wrong
about *this* tool — when to reach for it, what to pass it, how to read what it
returns — and what its description has to say for the assistant to get that
right with none of the feedback in front of it.

Provide the new tool description inside a single ``` block and nothing else.
"""
"""The template for the six tool descriptions, before its three names are filled in.

``{callable}`` is the real callable and ``{component}`` the registered prompt
name the proposer is shown; the counts are read off
:data:`~water_assistant_agent.assistant.toolset.TOOL_NAMES` so that a seventh
tool does not leave the metaprompt claiming there are six.
"""

_NUMBER_WORDS: tuple[str, ...] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
)


def tool_template(tool: str) -> str:
    """:data:`TOOL_TEMPLATE` for one tool, naming its callable and its registered name.

    Args:
        tool: A name from
            :data:`~water_assistant_agent.assistant.toolset.TOOL_NAMES`. The
            *callable's* name — the registered ``agent_tool_`` name is what this
            template exists to disown.

    Raises:
        ValueError: *tool* is not a tool. A template naming a callable that does
            not exist is the failure T133 is about, pointing the other way.
    """
    if tool not in TOOL_NAMES:
        raise ValueError(
            f"Not a tool: {tool!r}. The metaprompt names the callable, so it is "
            f"read from TOOL_NAMES: {', '.join(TOOL_NAMES)}."
        )
    return TOOL_TEMPLATE.format(
        callable=tool,
        component=PROMPT_NAMES[tool],
        tools=_word(len(TOOL_NAMES)),
        siblings=_word(len(TOOL_NAMES) - 1),
    )


def reflection_prompt_templates() -> dict[str, str]:
    """Registered prompt name → the metaprompt that component's proposal is made with.

    The key is the registered name because that is what GEPA dispatches on: the
    candidate it holds is MLflow's ``target_prompts``, keyed by prompt name, and
    ``propose_new_texts`` looks each component up in this dict by that key. A key
    that matched nothing would not fail — GEPA logs a warning and falls back to
    the default template, which is the framing this whole module exists to
    replace — so every component is covered here and the coverage is asserted in
    the tests rather than left to the log.

    Raises:
        MetapromptError: a template is one the installed gepa would refuse.
    """
    templates = {
        PROMPT_NAMES[ROOT_INSTRUCTION]: ROOT_TEMPLATE,
        **{PROMPT_NAMES[tool]: tool_template(tool) for tool in TOOL_NAMES},
    }
    _validate(templates)
    return templates


def templates_digest(templates: Mapping[str, str] | None = None) -> str:
    """sha256 over the whole dict, canonically — the value a run registers and reports.

    The dict itself is far too large to log as a run parameter and unreadable as
    a registered value, so what travels with a run is this. It covers the keys as
    well as the texts: a template dispatched onto a different component is a
    different search, and a digest over the values alone would not say so.
    """
    payload = json.dumps(
        dict(templates if templates is not None else reflection_prompt_templates()),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate(templates: Mapping[str, str]) -> None:
    """Hold every template to GEPA's own placeholder check, before the search starts.

    GEPA runs this check itself, once, when the proposer is constructed — which
    is after MLflow has built the adapter and after the seed candidate's full
    evaluation has been paid for. Running it here costs nothing and fails at
    wiring time instead.
    """
    from gepa.strategies.instruction_proposal import InstructionProposalSignature

    for component, template in templates.items():
        try:
            InstructionProposalSignature.validate_prompt_template(template)
        except ValueError as error:
            raise MetapromptError(
                f"The metaprompt for {component!r} is one gepa would refuse: "
                f"{error}. <curr_param> and <side_info> are the only two "
                "substitutions the renderer makes, so a template missing one "
                "renders a proposal prompt with the current text or the evidence "
                "simply absent."
            ) from error


def _word(count: int) -> str:
    """*count* as a word, so the templates read as prose rather than as a table."""
    return _NUMBER_WORDS[count] if count < len(_NUMBER_WORDS) else str(count)


__all__ = [
    "ROOT_TEMPLATE",
    "TOOL_TEMPLATE",
    "MetapromptError",
    "reflection_prompt_templates",
    "templates_digest",
    "tool_template",
]
