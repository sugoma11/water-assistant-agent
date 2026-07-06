/**
 * Logout route handler: clears the auth cookie (FR4). JWTs stay stateless, so
 * sign-out is purely dropping the cookie; the token simply expires server-side.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { AUTH_COOKIE } from "@/lib/config";

export async function POST() {
  const store = await cookies();
  store.delete(AUTH_COOKIE);
  return NextResponse.json({ ok: true });
}
