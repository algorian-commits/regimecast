import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Small self-contained server for Docker / Railway (node .next/standalone/server.js)
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
