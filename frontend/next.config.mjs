/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // A static export (out/): Cloudflare Pages serves it, and the same build is
  // what a TWA or Capacitor app wraps (docs/plan.md, Deploy strategy). The
  // standing rule that follows: no route handlers, no SSR, no middleware —
  // `next build` fails on any of them with this set, which is the guard.
  output: 'export',
  // The frontend never talks to the backend or Neo4j directly — only the
  // gateway, whose URL is the single public endpoint it knows about.
  env: {
    NEXT_PUBLIC_GATEWAY_URL: process.env.NEXT_PUBLIC_GATEWAY_URL ?? 'http://localhost:3001',
  },
};

export default nextConfig;
