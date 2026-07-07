"use client";

/**
 * Top-level chat orchestrator (US4). Owns the conversation list and which
 * conversation is active, renders the sidebar + chat pane, and hosts the
 * `<CopilotKit>` provider. The provider is keyed by the active conversation id
 * so switching conversations remounts the chat cleanly against the new AG-UI
 * `threadId` (D3, FR11). Every list mutation goes through the `/api/backend`
 * proxy via `lib/api`; a 401 there redirects to `/login` (EC1/EC5).
 */
import { CSSProperties, useCallback, useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { Sidebar } from "@/components/Sidebar";
import { ChatView } from "@/components/ChatView";
import { AGENT_NAME, COPILOTKIT_RUNTIME_URL } from "@/lib/constants";
import {
  Conversation,
  UnauthorizedError,
  createConversation,
  deleteConversation,
  listConversations,
  renameConversation,
} from "@/lib/api";

type Status = "loading" | "ready" | "error";

/** Run an async list mutation, swallowing the redirect-on-401 sentinel. */
async function guarded(action: () => Promise<void>): Promise<void> {
  try {
    await action();
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      return; // the api layer already redirected to /login
    }
    throw err;
  }
}

export function ChatApp() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void guarded(async () => {
      const list = await listConversations();
      if (cancelled) return;
      setConversations(list);
      setActiveId(list[0]?.id ?? null);
      setStatus("ready");
    }).catch(() => {
      if (!cancelled) setStatus("error");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const onNew = useCallback(async () => {
    setBusy(true);
    await guarded(async () => {
      const conv = await createConversation();
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
    }).finally(() => setBusy(false));
  }, []);

  const onRename = useCallback(async (id: string, title: string) => {
    await guarded(async () => {
      const updated = await renameConversation(id, title);
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title: updated.title } : c)),
      );
    });
  }, []);

  const onDelete = useCallback(
    async (id: string) => {
      await guarded(async () => {
        await deleteConversation(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        setActiveId((current) => {
          if (current !== id) return current;
          const next = conversations.find((c) => c.id !== id);
          return next?.id ?? null;
        });
      });
    },
    [conversations],
  );

  if (status === "error") {
    return (
      <Centered>Couldn&apos;t load your conversations. Refresh to retry.</Centered>
    );
  }
  if (status === "loading") {
    return <Centered>Loading…</Centered>;
  }

  return (
    <div style={shellStyle}>
      <header style={headerStyle}>
        <strong>Water Assistant</strong>
        <button type="button" onClick={() => void logout()} style={logoutStyle}>
          Sign out
        </button>
      </header>
      <div style={bodyStyle}>
        <Sidebar
          conversations={conversations}
          activeId={activeId}
          busy={busy}
          onSelect={setActiveId}
          onNew={() => void onNew()}
          onRename={(id, title) => void onRename(id, title)}
          onDelete={(id) => void onDelete(id)}
        />
        <main style={mainStyle}>
          {activeId ? (
            <CopilotKit
              key={activeId}
              runtimeUrl={COPILOTKIT_RUNTIME_URL}
              agent={AGENT_NAME}
              threadId={activeId}
            >
              <ChatView conversationId={activeId} />
            </CopilotKit>
          ) : (
            <Centered>Select a conversation, or start a new one.</Centered>
          )}
        </main>
      </div>
    </div>
  );
}

async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        height: "100%",
        display: "grid",
        placeItems: "center",
        color: "var(--wa-muted)",
        padding: "1.5rem",
        textAlign: "center",
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
  flexShrink: 0,
};

const bodyStyle: CSSProperties = {
  flex: 1,
  display: "flex",
  minHeight: 0,
};

const mainStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  minHeight: 0,
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
