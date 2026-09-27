import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to the FastAPI backend so
// the session cookie is first-party and API keys never reach the client.
const backend = process.env.BACKEND_URL ?? "http://localhost:8020";

const nextConfig: NextConfig = {
  output: "standalone",
  // Compression would buffer Server-Sent Events from the proxied API.
  compress: false,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
