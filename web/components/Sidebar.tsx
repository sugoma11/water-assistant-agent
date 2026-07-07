"use client";

/**
 * Conversation sidebar (T023, US4, FR7/FR8/C6): lists the signed-in user's
 * conversations most-recently-active first (the backend already orders them),
 * and offers new / switch / rename / delete. Each conversation's id is the
 * AG-UI `threadId` (D3, FR11); switching just changes which id the chat pane
 * mounts against. Titles are the server-derived auto-titles (C6); rename is
 * inline. All mutations go through the `/api/backend` proxy via `lib/api`.
 */
import { CSSProperties, useState } from "react";
import type { Conversation } from "@/lib/api";

type SidebarProps = {
  conversations: Conversation[];
  activeId: string | null;
  busy: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
};

export function Sidebar({
  conversations,
  activeId,
  busy,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: SidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function startRename(conv: Conversation) {
    setEditingId(conv.id);
    setDraft(conv.title);
  }

  function commitRename() {
    if (editingId) {
      const title = draft.trim();
      if (title) {
        onRename(editingId, title);
      }
    }
    setEditingId(null);
  }

  return (
    <aside style={sidebarStyle}>
      <div style={sidebarHeaderStyle}>
        <strong style={{ fontSize: "0.95rem" }}>Conversations</strong>
        <button
          type="button"
          onClick={onNew}
          disabled={busy}
          style={newButtonStyle}
          title="New conversation"
        >
          + New
        </button>
      </div>

      <nav style={listStyle}>
        {conversations.length === 0 ? (
          <p style={emptyStyle}>No conversations yet. Start a new one.</p>
        ) : (
          conversations.map((conv) => {
            const isActive = conv.id === activeId;
            const isEditing = conv.id === editingId;
            return (
              <div
                key={conv.id}
                style={{
                  ...rowStyle,
                  ...(isActive ? activeRowStyle : null),
                }}
              >
                {isEditing ? (
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename();
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    style={renameInputStyle}
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => onSelect(conv.id)}
                    style={titleButtonStyle}
                    title={conv.title}
                  >
                    {conv.title}
                  </button>
                )}

                {!isEditing ? (
                  <span style={actionsStyle}>
                    <button
                      type="button"
                      onClick={() => startRename(conv)}
                      style={iconButtonStyle}
                      title="Rename"
                      aria-label="Rename conversation"
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(conv.id)}
                      style={iconButtonStyle}
                      title="Delete"
                      aria-label="Delete conversation"
                    >
                      ✕
                    </button>
                  </span>
                ) : null}
              </div>
            );
          })
        )}
      </nav>
    </aside>
  );
}

const sidebarStyle: CSSProperties = {
  width: 260,
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  borderRight: "1px solid var(--wa-border)",
  background: "var(--wa-panel)",
  minHeight: 0,
};

const sidebarHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "0.75rem 1rem",
  borderBottom: "1px solid var(--wa-border)",
};

const newButtonStyle: CSSProperties = {
  padding: "0.35rem 0.7rem",
  borderRadius: 8,
  border: "1px solid var(--wa-border)",
  background: "var(--wa-accent)",
  color: "white",
  cursor: "pointer",
  fontSize: "0.8rem",
  fontWeight: 600,
};

const listStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: "0.5rem",
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const emptyStyle: CSSProperties = {
  color: "var(--wa-muted)",
  fontSize: "0.85rem",
  padding: "0.5rem",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  borderRadius: 8,
  padding: "0.15rem 0.25rem",
};

const activeRowStyle: CSSProperties = {
  background: "var(--wa-bg)",
};

const titleButtonStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  textAlign: "left",
  background: "transparent",
  border: "none",
  color: "var(--wa-text)",
  cursor: "pointer",
  fontSize: "0.88rem",
  padding: "0.4rem 0.5rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const renameInputStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  background: "var(--wa-bg)",
  border: "1px solid var(--wa-accent)",
  borderRadius: 6,
  color: "var(--wa-text)",
  fontSize: "0.88rem",
  padding: "0.35rem 0.5rem",
};

const actionsStyle: CSSProperties = {
  display: "flex",
  gap: 2,
};

const iconButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--wa-muted)",
  cursor: "pointer",
  fontSize: "0.8rem",
  padding: "0.25rem 0.35rem",
  borderRadius: 6,
};
