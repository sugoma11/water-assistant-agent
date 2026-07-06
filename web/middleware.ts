/**
 * Route guard (FR5, US7): a visitor without the auth cookie can reach only the
 * sign-in page. Every other page redirects to `/login`. API route handlers
 * (`/api/*`) enforce auth themselves against the backend and are excluded here,
 * so an expired token surfaces as a 401 the client can act on (EC1) rather than
 * a redirect served to a `fetch`.
 */
import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE } from "@/lib/config";

export function middleware(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE)?.value;
  if (token) {
    return NextResponse.next();
  }
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  return NextResponse.redirect(url);
}

export const config = {
  // Guard every page except /login, Next internals, and the API routes (which
  // self-guard). There is no signup route to exempt (FR3).
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|login).*)"],
};
