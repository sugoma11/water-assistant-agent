"""Root orchestrator agent: routes user requests to the text-to-SQL sub-agent.

Single-tenant water-management assistant, so unlike core-agent's
``root/factory.py`` (which builds a per-tenant agent with dynamically wired
tools) there is exactly one tenant and one data agent, and ``root_agent`` is
built directly at import time.
"""

from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool

from water_assistant_agent.assistant.agents.text_to_sql.agent import text_to_sql_agent
from water_assistant_agent.assistant.settings import get_settings

ROOT_INSTRUCTION = """You are a helpful assistant for a water-management research team studying green-roof sensor data (outflow, radiation, soil moisture, soil temperature, weather).

### Your responsibilities

1. **Identify request type**
   Determine whether the user's message is a follow-up to the current problem or a new problem statement. If unsure, ask exactly:
   > "Is this a new problem statement you want me to solve, or a change to the current issue we are working on?"

2. **Delegate correctly**
   * If the request requires SQL generation, database queries, or data-based reasoning over the sensor data, delegate it to the **text_to_sql_agent** agent.
   * Do not ask or pass column names by yourself. You may ask about specific columns *ONLY* if the user asked to. text_to_sql_agent is smart enough to decide what columns to use.
   * Otherwise, respond yourself.

3. **Provide a summary and follow-up suggestions**
   After text_to_sql_agent completes its work, produce a concise 1-2 sentence summary of the work that was completed, followed by 1-2 relevant follow-up questions under the header:

   ```
   ### Followup suggestions
   ```

4. **Error handling**
   Provide a polite response indicating that the issue is on the system side. Do not ask the user to change their question. Do not speculate on the cause of the error. Say only that the responsible team is working on fixing the issue.
"""  # noqa: E501 - prompt context


def _build_model() -> LiteLlm:
    settings = get_settings()
    return LiteLlm(model=settings.root_agent_model, **settings.litellm_extra())


root_agent = Agent(
    model=_build_model(),
    name="root_agent",
    description="Water-Management Data Analyst.",
    static_instruction=ROOT_INSTRUCTION,
    tools=[AgentTool(text_to_sql_agent)],
)
