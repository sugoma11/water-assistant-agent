"use client";

/**
 * Typed client for the backend REST routers, reached through the same-origin
 * `/api/backend/*` proxy (the browser never holds the JWT — NFR1). Every call
 * that comes back 401 means the cookie is gone or the user was revoked (EC1,
 * EC5); we send the visitor to `/login` and surface a sentinel so callers stop.
 */

export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  last_activity_at: string;
};

/** AG-UI-shaped message as returned by `GET /conversations/{id}/messages`. */
export type AguiMessage = Record<string, unknown> & { id: string; role: string };

const BACKEND = "/api/backend";

/** Thrown when the backend answered 401; the caller should stop and let the redirect happen. */
export class UnauthorizedError extends Error {
  constructor() {
    super("Unauthorized");
    this.name = "UnauthorizedError";
  }
}

function toLogin(): void {
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`${BACKEND}${path}`, init);
  if (res.status === 401) {
    toLogin();
    throw new UnauthorizedError();
  }
  return res;
}

async function json<T>(res: Response, context: string): Promise<T> {
  if (!res.ok) {
    throw new Error(`${context} failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export async function listConversations(): Promise<Conversation[]> {
  return json(await request("/conversations"), "Loading conversations");
}

export async function createConversation(
  firstMessage?: string,
): Promise<Conversation> {
  const res = await request("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(firstMessage ? { first_message: firstMessage } : {}),
  });
  return json(res, "Creating a conversation");
}

export async function renameConversation(
  id: string,
  title: string,
): Promise<Conversation> {
  const res = await request(`/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return json(res, "Renaming the conversation");
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await request(`/conversations/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Deleting the conversation failed (${res.status})`);
  }
}

export async function fetchHistory(id: string): Promise<AguiMessage[]> {
  const res = await request(`/conversations/${id}/messages`);
  const body = await json<{ messages: AguiMessage[] }>(res, "Loading history");
  return body.messages ?? [];
}

export async function appendPartial(id: string, content: string): Promise<void> {
  const res = await request(`/conversations/${id}/partial`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    throw new Error(`Persisting the partial answer failed (${res.status})`);
  }
}
