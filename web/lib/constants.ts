/**
 * Constants shared by server route handlers and client components. No secrets
 * and no env access, so this is safe to import from the browser bundle.
 */

/**
 * CopilotKit agent id. Registered in the runtime route's `agents` map and
 * selected by `<CopilotKit agent={AGENT_NAME}>`.
 */
export const AGENT_NAME = "water_assistant";

/** Same-origin URL of the CopilotKit runtime route handler. */
export const COPILOTKIT_RUNTIME_URL = "/api/copilotkit";
