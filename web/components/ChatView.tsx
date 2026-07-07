"use client";

/**
 * The chat pane for a single conversation. It is mounted *inside* a
 * `<CopilotKit>` provider keyed by the conversation id (see `ChatApp`), so a
 * conversation switch remounts it cleanly against the new AG-UI `threadId`
 * (D3, FR11). Answers stream in through the CopilotKit runtime route unbuffered
 * (NFR2).
 *
 * History restore (T024, FR9, US6): R1 asked whether CopilotKit's pinned client
 * auto-replays the backend's empty-run `MessagesSnapshotEvent` on a thread
 * switch. Rather than depend on that, we take the plan's fully project-owned
 * fallback: on mount fetch the AG-UI-shaped history from
 * `GET /conversations/{id}/messages` and hand it straight to the agent via
 * `setMessages`. The internal chat hook forwards plain AG-UI message objects
 * (not GQL `Message` instances) directly to `agent.setMessages`, so no
 * conversion is needed and the same events that stream live also restore here.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  OnStopGeneration,
  useCopilotAction,
  useCopilotChatInternal,
} from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { UnauthorizedError, appendPartial, fetchHistory } from "@/lib/api";
import { TextToSqlResult } from "@/components/TextToSqlResult";

type ChatViewProps = {
  conversationId: string;
  onClearError?: () => void;
};

export function ChatView({ conversationId, onClearError }: ChatViewProps) {
  const { setMessages } = useCopilotChatInternal();
  const restored = useRef(false);
  const [restoring, setRestoring] = useState(true);

  // Render the text-to-SQL tool call inside the assistant turn (T026, FR15,
  // C9, SC8). `available: "disabled"` keeps it render-only — the frontend never
  // offers this backend tool to the model; it only paints its result, both live
  // and when replayed from restored history.
  useCopilotAction({
    name: "text_to_sql_agent",
    available: "disabled",
    render: ({ status, result }) => (
      <TextToSqlResult status={status} result={result} />
    ),
  });

  useEffect(() => {
    let cancelled = false;
    // Restore once per mount; the provider remounts (keyed) per conversation.
    if (restored.current) {
      return;
    }
    restored.current = true;
    fetchHistory(conversationId)
      .then((messages) => {
        if (cancelled) return;
        if (messages.length > 0) {
          // Plain AG-UI objects → forwarded straight to agent.setMessages.
          setMessages(messages as never);
        }
      })
      .catch((err) => {
        if (!(err instanceof UnauthorizedError)) {
          console.error("Failed to restore conversation history", err);
        }
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, setMessages]);

  // Stop control + partial durability (T025, C7, FR10, R2). CopilotChat's stop
  // button calls this before aborting the run; the streamed partial stays in the
  // UI (the agent keeps its messages), and we persist the received assistant
  // text via POST /partial so it also survives a reload — the aborted ADK turn
  // may never have been committed on its own.
  const handleStop = useCallback<OnStopGeneration>(
    ({ messages }) => {
      for (let i = messages.length - 1; i >= 0; i--) {
        const message = messages[i] as { role?: string; content?: unknown };
        if (message.role !== "assistant") {
          continue;
        }
        const text =
          typeof message.content === "string" ? message.content.trim() : "";
        if (text) {
          void appendPartial(conversationId, text).catch((err) => {
            if (!(err instanceof UnauthorizedError)) {
              console.error("Failed to persist the stopped answer", err);
            }
          });
        }
        break;
      }
    },
    [conversationId],
  );

  return (
    <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
      {restoring ? (
        <div style={restoringBadgeStyle} aria-hidden>
          Restoring…
        </div>
      ) : null}
      <CopilotChat
        className="wa-chat"
        onStopGeneration={handleStop}
        onSubmitMessage={() => onClearError?.()}
        labels={{
          title: "Water Assistant",
          initial: "Ask about green-roof sensor data or the water warehouse.",
        }}
      />
    </div>
  );
}

const restoringBadgeStyle: React.CSSProperties = {
  position: "absolute",
  top: 8,
  right: 12,
  zIndex: 1,
  fontSize: "0.72rem",
  color: "var(--wa-muted)",
  background: "var(--wa-panel)",
  border: "1px solid var(--wa-border)",
  borderRadius: 999,
  padding: "0.15rem 0.6rem",
};
