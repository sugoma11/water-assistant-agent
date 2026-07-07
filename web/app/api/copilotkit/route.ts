/**
 * CopilotKit runtime route (NFR2, NFR5, R4).
 *
 * Builds the AG-UI `HttpAgent` per request so the current cookie's JWT rides
 * every run as `Authorization: Bearer` (the browser never holds the token), and
 * forwards/creates an `X-Request-ID` so backend logs correlate with the chat
 * request (NFR5 — the backend's `CorrelationIdMiddleware` reads this header).
 * The agent points at the FastAPI AG-UI endpoint mounted at `/`. The SSE body is
 * streamed straight back through the runtime, unbuffered (NFR2).
 */
import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { BACKEND_URL } from "@/lib/config";
import { AGENT_NAME, COPILOTKIT_RUNTIME_URL } from "@/lib/constants";
import { requireToken } from "@/lib/route-auth";

// No LLM adapter: the run is fully driven by the backend AG-UI agent.
const serviceAdapter = new ExperimentalEmptyAdapter();

export async function POST(req: NextRequest) {
  const token = await requireToken();
  if (token instanceof NextResponse) {
    return token;
  }

  const requestId = req.headers.get("x-request-id") ?? randomUUID();

  const runtime = new CopilotRuntime({
    agents: {
      [AGENT_NAME]: new HttpAgent({
        url: `${BACKEND_URL}/`,
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Request-ID": requestId,
        },
      }),
    },
  });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: COPILOTKIT_RUNTIME_URL,
  });

  return handleRequest(req);
}
