/**
 * Shared auth guard for the Next.js route handlers (NFR1, R-003).
 *
 * Both the REST proxy and the CopilotKit runtime route need the same thing: read
 * the httpOnly cookie's JWT, and if it is absent answer with one identical 401
 * telling the client to go to `/login`. Centralising it here keeps that 401
 * contract in one place so the two handlers cannot drift.
 */
import { NextResponse } from "next/server";
import { getAuthToken } from "@/lib/auth";

/** The uniform 401 response used whenever the caller has no valid session. */
export function unauthorized(): NextResponse {
  return NextResponse.json(
    { error: "Not authenticated", redirect: "/login" },
    { status: 401 },
  );
}

/**
 * Return the backend JWT, or the {@link unauthorized} 401 response when the
 * caller is signed out. Consumers do `if (auth instanceof NextResponse) return
 * auth;` and otherwise use `auth` as the bearer token.
 */
export async function requireToken(): Promise<string | NextResponse> {
  const token = await getAuthToken();
  return token ?? unauthorized();
}
