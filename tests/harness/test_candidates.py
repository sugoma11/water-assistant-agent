"""The candidate surface is seven prompts, and an unread one is caught (T120).

Four claims, and the last is the packet's exit criterion:

1. Every optimizable component of §2 has **its own** registered prompt — the root
   instruction, the five tool docstrings and the sub-agent's outward
   ``description`` — and no two share a name. One concatenated blob is what
   ``decisions.md`` § The optimizer entry point and the candidate surface
   rejects.
2. The seed text registered for a component is the text that component actually
   runs on, read off the built objects rather than transcribed.
3. Candidate text comes back through a **registry read**, which is the only
   channel it has (§6).
4. A component left unread makes the pass fail. Tested by leaving one unread, not
   by reading the assertion.

The registry is a real MLflow one on a throwaway SQLite file (the ``registry``
fixture), because the read under test is the registry's.
"""

from __future__ import annotations

import pytest

from harness.candidates import (
    CANDIDATE_COMPONENTS,
    PROMPT_NAMES,
    ROOT_INSTRUCTION,
    UnreadCandidateError,
    baseline_texts,
    candidate_prompts_pin,
    candidate_uri,
    candidate_uris,
    evaluation_pass,
    prompt_name,
    read_candidate,
    read_candidates,
    register_candidates,
    text_sha256,
)
from harness.contract import EVALUATION_ROOT_INSTRUCTION
from water_assistant_agent.assistant.agents.text_to_sql.agent import AGENT_DESCRIPTION
from water_assistant_agent.assistant.toolset import (
    LOOKUP_TOOL,
    TEXT_TO_SQL_TOOL,
    TOOL_NAMES,
)


def test_one_prompt_per_component_and_never_a_blob() -> None:
    """Seven components, seven distinct prompt names: the instruction plus six tools."""
    assert CANDIDATE_COMPONENTS == (ROOT_INSTRUCTION, *TOOL_NAMES)
    assert len(CANDIDATE_COMPONENTS) == 7
    assert set(PROMPT_NAMES) == set(CANDIDATE_COMPONENTS)
    assert len(set(PROMPT_NAMES.values())) == 7
    # The sub-agent's outward description is one of the seven, not part of the
    # root instruction's prompt: `AgentTool` presents it to the root model and
    # §2 lists it among the optimizable components.
    assert TEXT_TO_SQL_TOOL in PROMPT_NAMES


def test_the_seed_is_the_text_the_component_runs_on() -> None:
    """Read off the built objects, so the registered seed cannot drift from them."""
    texts = baseline_texts()

    assert set(texts) == set(CANDIDATE_COMPONENTS)
    # The *evaluation* instruction, which carries the answer contract — not
    # production's prose one (`harness/contract.py`).
    assert texts[ROOT_INSTRUCTION] == EVALUATION_ROOT_INSTRUCTION
    assert texts[TEXT_TO_SQL_TOOL] == AGENT_DESCRIPTION
    # The one exception of §2: the topic vocabulary lives in the signature, so
    # the registered docstring is guidance a candidate may reword and the enum
    # is not in it to be deleted.
    assert "topic" in texts[LOOKUP_TOOL]
    assert all(text.strip() for text in texts.values())


def test_registration_is_one_version_per_component(registry: str) -> None:
    """Each component gets its own version, and identical text is not re-registered."""
    texts = baseline_texts()
    versions = register_candidates(texts, commit_message="seed")

    assert set(versions) == set(CANDIDATE_COMPONENTS)
    assert all(version == 1 for version in versions.values())
    # Registration runs again whenever the seed is re-pinned; unchanged text must
    # not mint a version, or the pinned seed would move under the reference arm.
    assert register_candidates(texts) == versions


def test_a_partial_surface_is_refused() -> None:
    """All seven or none: one left out is frozen while still appearing optimizable."""
    texts = baseline_texts()
    del texts[LOOKUP_TOOL]

    with pytest.raises(ValueError, match=LOOKUP_TOOL):
        register_candidates(texts)

    with pytest.raises(ValueError, match="not_a_component"):
        register_candidates({**baseline_texts(), "not_a_component": "x"})


def test_candidate_text_comes_back_through_the_registry(registry: str) -> None:
    """The registry read is the channel — what was registered is what is read."""
    versions = register_candidates(baseline_texts())
    # A second version of one component, so the read is shown to resolve a
    # version rather than "whatever is latest".
    edited = {**baseline_texts(), ROOT_INSTRUCTION: "ANSWER WITH THE CONTRACT."}
    edited_versions = register_candidates(edited)

    assert edited_versions[ROOT_INSTRUCTION] == 2
    assert read_candidate(ROOT_INSTRUCTION, 1) == EVALUATION_ROOT_INSTRUCTION
    assert read_candidate(ROOT_INSTRUCTION, 2) == "ANSWER WITH THE CONTRACT."
    assert read_candidates(versions) == baseline_texts()


def test_the_unread_prompt_assertion_fires(registry: str) -> None:
    """The exit criterion: leave one component unread and the pass fails.

    Six of the seven are read and ``lookup_reference`` is not, which is exactly
    what a ``predict_fn`` that forgot to thread one docstring through would do.
    MLflow's only signal for it is a log warning, so the pass asserts.
    """
    versions = register_candidates(baseline_texts())
    unread = LOOKUP_TOOL

    with pytest.raises(UnreadCandidateError) as caught:
        with evaluation_pass():
            for component in CANDIDATE_COMPONENTS:
                if component != unread:
                    read_candidate(component, versions[component])

    reported = str(caught.value)
    assert unread in reported
    # And only that one: the other six were read and are not named.
    assert not [
        component
        for component in CANDIDATE_COMPONENTS
        if component != unread and component in reported
    ]


def test_a_pass_that_reads_everything_is_clean(registry: str) -> None:
    """The other half of the claim — the assertion is not simply always raising."""
    versions = register_candidates(baseline_texts())

    with evaluation_pass() as read_log:
        texts = read_candidates(versions)

    assert texts == baseline_texts()
    assert read_log.seen == frozenset(CANDIDATE_COMPONENTS)


def test_a_failing_pass_is_not_re_reported_as_unread(registry: str) -> None:
    """A pass whose body raised has a better explanation than its missing reads."""
    register_candidates(baseline_texts())

    with pytest.raises(ZeroDivisionError):
        with evaluation_pass():
            _ = 1 / 0


def test_two_passes_cannot_overlap(registry: str) -> None:
    """The candidate patch is process-global: one candidate is evaluable at a time."""
    with evaluation_pass(expected=[ROOT_INSTRUCTION]) as outer:
        with pytest.raises(RuntimeError, match="already open"):
            with evaluation_pass():
                pass
        # The refusal did not close the pass it refused to nest inside.
        outer.record(ROOT_INSTRUCTION)


def test_the_uris_name_the_registered_prompts() -> None:
    """``prompt_uris`` is built from these, one URI per optimizable component (§6)."""
    versions = dict.fromkeys(CANDIDATE_COMPONENTS, 3)

    uris = candidate_uris(versions)

    assert uris[ROOT_INSTRUCTION] == "prompts:/agent_root_instruction/3"
    assert uris[LOOKUP_TOOL] == candidate_uri(LOOKUP_TOOL, 3)
    assert len(set(uris.values())) == 7
    with pytest.raises(ValueError, match="not_a_component"):
        prompt_name("not_a_component")


def test_the_pin_carries_every_component() -> None:
    """``candidate_prompts`` closes with a name and a seed hash per component."""
    pin = candidate_prompts_pin()
    texts = baseline_texts()

    assert set(pin) == set(CANDIDATE_COMPONENTS)
    for component, entry in pin.items():
        assert entry["prompt"] == PROMPT_NAMES[component]
        assert entry["sha256"] == text_sha256(texts[component])
