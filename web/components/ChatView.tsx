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
import { useEffect, useRef, useState } from "react";
import { useCopilotChatInternal } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { UnauthorizedError, fetchHistory } from "@/lib/api";

type ChatViewProps = {
  conversationId: string;
};

export function ChatView({ conversationId }: ChatViewProps) {
  const { setMessages } = useCopilotChatInternal();
  const restored = useRef(false);
  const [restoring, setRestoring] = useState(true);

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

  return (
    <div style={{ height: "100%", minHeight: 0, position: "relative" }}>
      {restoring ? (
        <div style={restoringBadgeStyle} aria-hidden>
          Restoring…
        </div>
      ) : null}
      <CopilotChat
        className="wa-chat"
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
