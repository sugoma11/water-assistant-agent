"use client";

/**
 * Minimal single-conversation chat pane (T021 milestone, US3/SC4). On mount it
 * ensures the signed-in user has a conversation — reusing their most recent one
 * or creating a fresh row via the backend proxy — then mounts CopilotKit against
 * that conversation's id as the AG-UI `threadId` (D3, FR11). Answers stream in
 * through the CopilotKit runtime route unbuffered (NFR2). The full sidebar and
 * history restore arrive in Phase 5; this is the streaming happy path.
 */
import "@copilotkit/react-ui/styles.css";
import { CSSProperties, useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { AGENT_NAME, COPILOTKIT_RUNTIME_URL } from "@/lib/constants";

type Conversation = { id: string; title: string };

function toLogin(): void {
  window.location.href = "/login";
}

async function ensureConversation(): Promise<string | null> {
  const listRes = await fetch("/api/backend/conversations");
  if (listRes.status === 401) {
    toLogin();
    return null;
  }
  if (!listRes.ok) {
    throw new Error("Failed to load conversations");
  }
  const conversations: Conversation[] = await listRes.json();
  if (conversations[0]?.id) {
    return conversations[0].id;
  }

  const createRes = await fetch("/api/backend/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (createRes.status === 401) {
    toLogin();
    return null;
  }
  if (!createRes.ok) {
    throw new Error("Failed to create a conversation");
  }
  const created: Conversation = await createRes.json();
  return created.id;
}

async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
  toLogin();
}

export function ChatPane() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    ensureConversation()
      .then((id) => {
        if (cancelled || id === null) {
          return;
        }
        setThreadId(id);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) {
          setStatus("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (status === "error") {
    return (
      <Centered>Couldn&apos;t load your conversation. Refresh to retry.</Centered>
    );
  }
  if (status === "loading" || threadId === null) {
    return <Centered>Loading…</Centered>;
  }

  return (
    <CopilotKit
      runtimeUrl={COPILOTKIT_RUNTIME_URL}
      agent={AGENT_NAME}
      threadId={threadId}
    >
      <div style={shellStyle}>
        <header style={headerStyle}>
          <strong>Water Assistant</strong>
          <button type="button" onClick={() => void logout()} style={logoutStyle}>
            Sign out
          </button>
        </header>
        <div style={{ flex: 1, minHeight: 0 }}>
          <CopilotChat
            className="wa-chat"
            labels={{
              title: "Water Assistant",
              initial:
                "Ask about green-roof sensor data or the water warehouse.",
            }}
          />
        </div>
      </div>
    </CopilotKit>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        color: "var(--wa-muted)",
        padding: "1.5rem",
      }}
    >
      {children}
    </div>
  );
}

const shellStyle: CSSProperties = {
  height: "100vh",
  display: "flex",
  flexDirection: "column",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "0.75rem 1.25rem",
  borderBottom: "1px solid var(--wa-border)",
  background: "var(--wa-panel)",
};

const logoutStyle: CSSProperties = {
  padding: "0.4rem 0.8rem",
  borderRadius: 8,
  border: "1px solid var(--wa-border)",
  background: "transparent",
  color: "var(--wa-text)",
  cursor: "pointer",
  fontSize: "0.85rem",
};
