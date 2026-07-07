/**
 * Server-only configuration shared by the route handlers and the CopilotKit
 * runtime. The browser never reads these — every backend call is proxied
 * through Next.js route handlers on this same origin (no CORS, token in an
 * httpOnly cookie, NFR1).
 */

/** Base URL of the FastAPI backend, trailing slash stripped. */
export const BACKEND_URL: string = (
  process.env.BACKEND_URL ?? "http://localhost:8080"
).replace(/\/+$/, "");

/**
 * Name of the httpOnly cookie holding the backend JWT (D2). Route handlers are
 * the only readers/writers; `middleware.ts` only checks for its presence.
 */
export const AUTH_COOKIE = "wa_token";
