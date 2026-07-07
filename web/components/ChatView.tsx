"use client";

/**
 * The chat pane for a single conversation. It is mounted *inside* a
 * `<CopilotKit>` provider keyed by the conversation id (see `ChatApp`), so a
 * conversation switch remounts it cleanly against the new AG-UI `threadId`
 * (D3, FR11). Answers stream in through the CopilotKit runtime route unbuffered
 * (NFR2). History restore, stop/partial durability, and the text-to-SQL render
 * are layered on in later Phase-5 tasks.
 */
import { CopilotChat } from "@copilotkit/react-ui";

export function ChatView() {
  return (
    <div style={{ height: "100%", minHeight: 0 }}>
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
