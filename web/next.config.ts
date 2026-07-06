import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Route handlers stream SSE from the backend; nothing here should buffer them
  // (NFR2). Next's App Router route handlers stream by default.
  reactStrictMode: true,
};

export default nextConfig;
