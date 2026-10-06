import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    // Résumé PDFs are uploaded through a server action (limit checked again in the action: 4 MB; Vercel caps bodies at 4.5 MB).
    serverActions: { bodySizeLimit: "4.5mb" },
  },
};

export default nextConfig;