/**
 * Server-side auth-cookie helpers (D2, NFR1). The backend JWT lives in an
 * httpOnly, `SameSite=Lax`, `Secure`-in-prod cookie; only route handlers read
 * or write it. The browser never sees the token.
 */
import { cookies } from "next/headers";
import { AUTH_COOKIE } from "@/lib/config";

/** Read the backend JWT from the request cookie, or `undefined` if signed out. */
export async function getAuthToken(): Promise<string | undefined> {
  const store = await cookies();
  return store.get(AUTH_COOKIE)?.value;
}

/**
 * Cookie attributes for the auth token. Pass the token's `expires_at` so the
 * cookie lifetime mirrors the JWT's (D2); omit it for deletion.
 */
export function authCookieOptions(expires?: Date) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    ...(expires ? { expires } : {}),
  };
}
