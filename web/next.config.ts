import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Route handlers stream SSE from the backend; nothing here should buffer them
  // (NFR2). Next's App Router route handlers stream by default.
  reactStrictMode: true,
  // Emit a self-contained server bundle (.next/standalone/server.js) so the
  // production Docker image (web/Dockerfile) ships only traced dependencies.
  output: "standalone",
};

export default nextConfig;
