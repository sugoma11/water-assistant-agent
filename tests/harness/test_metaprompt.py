"""The two metaprompts, and the three ways one of them could be wrong (T133).

The templates are prose and prose is not testable, so what is checked here is
everything *around* the prose — the parts that decide whether the proposer ever
reads it and whether what it reads is true:

* **Dispatch.** GEPA looks each component up in the dict by the key MLflow's
  candidate uses, and a key that matches nothing does not fail — it logs a
  warning and falls back to the default template, which is the framing this
  module exists to replace. So the keys are asserted to be the seven registered
  prompt names.
* **Rendering.** ``<curr_param>`` and ``<side_info>`` are the only two
  substitutions, and a template missing one renders a proposal prompt with the
  current text or the evidence simply absent. Checked through GEPA's own
  renderer rather than by looking for the placeholders, so what is asserted is
  the prompt the reflection model would receive.
* **Names.** The whole point of the tool template is that the callable is
  ``calc_irrigation`` and not ``agent_tool_calc_irrigation``, so every tool's
  template is held to naming the real one — and to naming the registered one
  only where it disowns it.
"""

from __future__ import annotations

import pytest

from harness.candidates import CANDIDATE_COMPONENTS, PROMPT_NAMES, ROOT_INSTRUCTION
from harness.metaprompt import (
    ROOT_TEMPLATE,
    TOOL_TEMPLATE,
    MetapromptError,
    reflection_prompt_templates,
    templates_digest,
    tool_template,
)
from water_assistant_agent.assistant.toolset import TOOL_NAMES

# ── Dispatch ─────────────────────────────────────────────────────────────────


def test_every_component_has_a_template_under_its_registered_name() -> None:
    """The key is what GEPA dispatches on, and a miss is silent.

    ``propose_new_texts`` looks up ``self.reflection_prompt_template.get(name)``
    where *name* is the candidate's key — MLflow's ``target_prompts``, keyed by
    prompt name — and a ``None`` result logs one line and uses the default
    template. A component missing here would therefore be optimized under the
    framing T133 exists to replace, and nothing but a log would say so.
    """
    templates = reflection_prompt_templates()

    assert set(templates) == {
        PROMPT_NAMES[component] for component in CANDIDATE_COMPONENTS
    }
    assert len(templates) == 7


def test_the_root_gets_the_root_template_and_each_tool_gets_its_own() -> None:
    """Six of the seven are tool descriptions; exactly one is not."""
    templates = reflection_prompt_templates()

    assert templates[PROMPT_NAMES[ROOT_INSTRUCTION]] == ROOT_TEMPLATE
    for tool in TOOL_NAMES:
        assert templates[PROMPT_NAMES[tool]] == tool_template(tool)
    assert len({templates[PROMPT_NAMES[tool]] for tool in TOOL_NAMES}) == len(TOOL_NAMES)


def test_the_root_template_is_not_a_tool_template() -> None:
    """It says instruction where the others say declaration, which is the whole point."""
    assert "system instruction" in ROOT_TEMPLATE
    assert "function declaration" not in ROOT_TEMPLATE
    assert "agent_tool_" not in ROOT_TEMPLATE


# ── Rendering ────────────────────────────────────────────────────────────────


def test_every_template_renders_the_current_text_and_the_evidence() -> None:
    """Through GEPA's own renderer, so what is asserted is the prompt that is sent.

    Both halves matter and they fail differently: a template without
    ``<curr_param>`` asks for a rewrite of a text it does not show, and one
    without ``<side_info>`` asks for it with no feedback at all. Neither raises
    at rendering time — the renderer simply substitutes nothing.
    """
    from gepa.strategies.instruction_proposal import InstructionProposalSignature

    row = {
        "component_name": "agent_tool_calc_irrigation",
        "score": 0.5,
        "rationales": {"trajectory": "called the wrong tool"},
        "index": 0,
    }

    for component, template in reflection_prompt_templates().items():
        prompt = InstructionProposalSignature.prompt_renderer(
            {
                "current_instruction_doc": f"THE CURRENT TEXT OF {component}",
                "dataset_with_feedback": [row],
                "prompt_template": template,
            }
        )

        assert f"THE CURRENT TEXT OF {component}" in prompt
        assert "called the wrong tool" in prompt
        assert "<curr_param>" not in prompt
        assert "<side_info>" not in prompt


def test_a_template_gepa_would_refuse_is_refused_here_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GEPA runs this check inside the proposer — an hour and a full evaluation in."""
    import harness.metaprompt as metaprompt

    monkeypatch.setattr(metaprompt, "ROOT_TEMPLATE", "rewrite <curr_param> please")

    with pytest.raises(MetapromptError, match="side_info"):
        reflection_prompt_templates()


# ── Names ────────────────────────────────────────────────────────────────────


def test_each_tool_template_names_the_callable_and_disowns_the_registered_name() -> None:
    """The prefix leak, told to the proposer rather than renamed away.

    ``component_name`` reaches the model as ``agent_tool_…`` and cannot simply be
    renamed — the pin fixes the seven names and the prefix keeps the shared
    registry's text-to-SQL prompts apart from these. So the template names it and
    says what it is, and the sentence that does so is the only place it appears.
    """
    for tool in TOOL_NAMES:
        template = tool_template(tool)
        registered = PROMPT_NAMES[tool]

        assert f"The callable's name is `{tool}`" in template
        assert f"component `{registered}`, which is the name this text is" in template
        assert "is not a callable" in template
        # Named only where it is disowned: twice in that one paragraph, never as
        # something to call.
        assert template.count(registered) == 2


def test_the_tool_template_counts_the_tools_rather_than_asserting_six() -> None:
    """A seventh tool must not leave the metaprompt claiming there are six."""
    assert "{tools}" not in tool_template(TOOL_NAMES[0])
    assert "six tools" in tool_template(TOOL_NAMES[0])
    assert "five other tool descriptions" in tool_template(TOOL_NAMES[0])
    assert len(TOOL_NAMES) == 6


def test_a_template_cannot_be_asked_for_by_a_name_that_is_not_a_tool() -> None:
    """Including the registered name, which is the mistake this module is about."""
    with pytest.raises(ValueError, match="Not a tool"):
        tool_template("agent_tool_calc_irrigation")
    with pytest.raises(ValueError, match="Not a tool"):
        tool_template(ROOT_INSTRUCTION)


# ── The digest a run registers ───────────────────────────────────────────────


def test_the_digest_covers_the_keys_as_well_as_the_texts() -> None:
    """A template dispatched onto another component is a different search.

    The dict is too large to register or to log, so the digest is what travels
    with a run and what a fresh registration records (T134). It has to move when
    anything about the dispatch moves, not only when a word changes.
    """
    templates = reflection_prompt_templates()

    assert templates_digest(templates) == templates_digest()
    assert len(templates_digest(templates)) == 64

    swapped = dict(templates)
    root, first_tool = PROMPT_NAMES[ROOT_INSTRUCTION], PROMPT_NAMES[TOOL_NAMES[0]]
    swapped[root], swapped[first_tool] = swapped[first_tool], swapped[root]
    assert templates_digest(swapped) != templates_digest(templates)

    edited = dict(templates)
    edited[root] = ROOT_TEMPLATE + " "
    assert templates_digest(edited) != templates_digest(templates)


def test_the_unfilled_tool_template_is_never_what_is_sent() -> None:
    """Its three slots are names; an unformatted copy would ship `{callable}`."""
    assert "{callable}" in TOOL_TEMPLATE
    assert all(
        "{callable}" not in template
        for template in reflection_prompt_templates().values()
    )
