/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The frontend never talks to the backend or Neo4j directly — only the
  // gateway, whose URL is the single public endpoint it knows about.
  env: {
    NEXT_PUBLIC_GATEWAY_URL: process.env.NEXT_PUBLIC_GATEWAY_URL ?? 'http://localhost:3001',
  },
};

export default nextConfig;
