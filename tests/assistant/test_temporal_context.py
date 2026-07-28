"""Current-date injection tests: the root agent must not guess "today".

Covers the rendered block itself and the fact that ADK actually delivers it on
every invocation (rather than freezing it into ``static_instruction`` at import
time, which would go stale after the first midnight).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.flows.llm_flows import instructions
from google.adk.models.llm_request import LlmRequest
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from water_assistant_agent.assistant.agents.root_agent.agent import root_agent
from water_assistant_agent.assistant.prompts.temporal import current_datetime_block
from water_assistant_agent.assistant.tools.site import SITE_TIMEZONE, site_now


def test_site_now_is_site_local_and_aware() -> None:
    now = site_now()
    assert now.tzinfo is not None
    expected = datetime.now(ZoneInfo(SITE_TIMEZONE))
    # Same wall-clock day at the site, whatever the host clock is set to.
    assert now.date() == expected.date()


def test_block_states_todays_date() -> None:
    block = current_datetime_block()
    assert site_now().strftime("%Y-%m-%d") in block
    assert SITE_TIMEZONE in block


@pytest.mark.asyncio
async def test_root_agent_request_carries_the_date() -> None:
    """The block reaches the model, and ROOT_INSTRUCTION stays the system prompt."""
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test", user_id="u")
    context = InvocationContext(
        session_service=session_service,
        invocation_id="i1",
        agent=root_agent,
        session=session,
        user_content=types.Content(
            role="user", parts=[types.Part(text="runoff over the last two weeks?")]
        ),
    )
    request = LlmRequest()
    async for _ in instructions.request_processor.run_async(context, request):
        pass

    today = site_now().strftime("%Y-%m-%d")
    rendered = " ".join(
        part.text or ""
        for content in request.contents
        for part in (content.parts or [])
    )
    assert today in rendered
    # The static block is set, so ADK routes the dynamic date to contents; the
    # system instruction must still hold the (byte-stable, cacheable) prompt.
    system_instruction = str(request.config.system_instruction)
    assert (
        "You are a helpful assistant for a water-management research team"
        in system_instruction
    )
    assert today not in system_instruction
