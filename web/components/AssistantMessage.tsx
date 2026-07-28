"use client";

/**
 * Custom assistant-message renderer for `CopilotChat` (see `ChatView`).
 *
 * The model's thinking arrives as an assistant message tagged with
 * {@link THINKING_MARKER} (see `lib/thinking` for the full backend contract).
 * Those bubbles render as a de-emphasized, collapsed "Thinking" disclosure so the
 * reasoning is available without cluttering the answer. Every other message —
 * real answers, tool calls, the text-to-SQL result card — is handed to
 * CopilotKit's default `AssistantMessage` untouched, so none of its built-in
 * rendering (generative UI, copy/regenerate controls) is lost.
 */
import {
  AssistantMessage as DefaultAssistantMessage,
  Markdown,
  type AssistantMessageProps,
} from "@copilotkit/react-ui";
import { isThinkingContent, stripThinkingMarker } from "@/lib/thinking";

export function AssistantMessage(props: AssistantMessageProps) {
  const content = props.message?.content;
  if (isThinkingContent(content)) {
    const thinking = stripThinkingMarker(content).trim();
    // An empty body means the reasoning stream has only just opened; render
    // nothing until text arrives so we don't flash an empty disclosure.
    if (!thinking) {
      return null;
    }
    return (
      <details className="wa-thinking">
        <summary className="wa-thinking-summary">Thinking</summary>
        <div className="wa-thinking-body">
          <Markdown content={thinking} />
        </div>
      </details>
    );
  }
  return <DefaultAssistantMessage {...props} />;
}
