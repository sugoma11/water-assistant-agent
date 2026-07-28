/**
 * Login route handler: exchanges email+password for the backend JWT and stores
 * it in the httpOnly auth cookie (D2, NFR1, FR4). Never returns the token to the
 * browser; a bad credential surfaces as a uniform 401 (no user-enumeration).
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, BACKEND_URL } from "@/lib/config";
import { authCookieOptions, isSecureRequest } from "@/lib/auth";

export async function POST(req: NextRequest) {
  let email: unknown;
  let password: unknown;
  try {
    ({ email, password } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  const res = await fetch(`${BACKEND_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    // Uniform failure; do not leak whether the email exists.
    return NextResponse.json(
      { error: "Invalid email or password" },
      { status: 401 },
    );
  }

  const data: { access_token: string; expires_at: string } = await res.json();
  const expiresAt = new Date(data.expires_at);
  const store = await cookies();
  store.set(
    AUTH_COOKIE,
    data.access_token,
    authCookieOptions(isSecureRequest(req), expiresAt),
  );

  return NextResponse.json({ ok: true, expires_at: data.expires_at });
}
