/**
 * Shared contract for the model's "thinking" (reasoning) stream.
 *
 * ag-ui-adk 0.7 emits reasoning as a separate stream that CopilotKit's runtime
 * drops (it discards `role:"reasoning"` messages). The backend works around this
 * by remapping reasoning onto an ordinary assistant text message whose content
 * opens with {@link THINKING_MARKER} — see
 * `src/water_assistant_agent/assistant/routers/agent.py` (`THINKING_MARKER`,
 * `_reasoning_as_text`) and the history-restore twin in `conversations.py`.
 *
 * The marker is a Unicode private-use code point: it never appears in real model
 * output and renders as nothing if it ever leaks. `AssistantMessage.tsx` uses
 * these helpers to detect a thinking bubble and strip the marker before display.
 * Keep this value byte-for-byte in sync with the backend constant.
 */
export const THINKING_MARKER = "\ue000";

/** True when an assistant message's content is a thinking-derived bubble. */
export function isThinkingContent(content: unknown): content is string {
  return typeof content === "string" && content.startsWith(THINKING_MARKER);
}

/** Remove the leading thinking sentinel, returning the human-readable text. */
export function stripThinkingMarker(content: string): string {
  return content.startsWith(THINKING_MARKER)
    ? content.slice(THINKING_MARKER.length)
    : content;
}
