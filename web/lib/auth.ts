/**
 * Server-side auth-cookie helpers (D2, NFR1). The backend JWT lives in an
 * httpOnly, `SameSite=Lax`, `Secure`-in-prod cookie; only route handlers read
 * or write it. The browser never sees the token.
 */
import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { AUTH_COOKIE } from "@/lib/config";

/** Read the backend JWT from the request cookie, or `undefined` if signed out. */
export async function getAuthToken(): Promise<string | undefined> {
  const store = await cookies();
  return store.get(AUTH_COOKIE)?.value;
}

/**
 * Whether the request reached us over HTTPS. Keyed off the effective request
 * protocol — `x-forwarded-proto` when a TLS-terminating proxy fronts us (ngrok
 * sets `https`), else the request URL's own scheme — rather than `NODE_ENV`.
 * This is what decides the cookie's `Secure` flag: forcing `Secure` in
 * production regardless of scheme drops the cookie over plain-HTTP prod
 * (http://localhost:3000), which silently breaks login.
 */
export function isSecureRequest(req: NextRequest): boolean {
  const forwarded = req.headers.get("x-forwarded-proto");
  if (forwarded) {
    // May be a comma-separated proxy chain; the first hop is the client-facing one.
    return forwarded.split(",")[0].trim() === "https";
  }
  return req.nextUrl.protocol === "https:";
}

/**
 * Cookie attributes for the auth token. `secure` mirrors the request scheme
 * (see {@link isSecureRequest}) so the cookie sticks over HTTP and stays
 * `Secure` behind TLS (D2, NFR1). Pass the token's `expires_at` so the cookie
 * lifetime mirrors the JWT's; omit it for deletion.
 */
export function authCookieOptions(secure: boolean, expires?: Date) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure,
    path: "/",
    ...(expires ? { expires } : {}),
  };
}
