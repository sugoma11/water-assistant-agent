/**
 * Authenticated proxy to the FastAPI REST routers (auth/me, conversations, …).
 *
 * The browser never talks to the backend directly (NFR1): it calls this
 * same-origin catch-all, which copies the httpOnly cookie's JWT into an
 * `Authorization: Bearer` header and forwards the request. The backend response
 * body is streamed straight through, unbuffered (NFR2). A 401 from any backend
 * call (expired/invalid token, or a deleted user — EC1, EC5) clears the cookie
 * and tells the client to go to `/login`, without touching conversation state.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, BACKEND_URL } from "@/lib/config";
import { requireToken, unauthorized } from "@/lib/route-auth";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const token = await requireToken();
  if (token instanceof NextResponse) {
    return token;
  }

  const target = `${BACKEND_URL}/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  headers.set("authorization", `Bearer ${token}`);
  for (const name of ["content-type", "accept", "x-request-id"]) {
    const value = req.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const body = hasBody ? await req.arrayBuffer() : undefined;

  let res: Response;
  try {
    res = await fetch(target, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });
  } catch {
    // EC2: backend unreachable — surface a clear error; conversation state is
    // untouched and the client can retry.
    return NextResponse.json({ error: "Backend unreachable" }, { status: 502 });
  }

  if (res.status === 401) {
    const store = await cookies();
    store.delete(AUTH_COOKIE);
    return unauthorized();
  }

  // Stream the backend response through unbuffered (NFR2). Drop hop-by-hop
  // encoding/length headers so Next re-frames the streamed body correctly.
  const responseHeaders = new Headers(res.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  responseHeaders.delete("transfer-encoding");

  return new NextResponse(res.body, {
    status: res.status,
    headers: responseHeaders,
  });
}

export async function GET(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}

export async function POST(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}

export async function PATCH(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}

export async function DELETE(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}
