export default {
  // Lets a verification build (NEXT_DIST_DIR=.next-build next build) run without clobbering
  // the .next folder a running `next dev` is using.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  async rewrites() {
    // where the /api proxy points (read at build time); default: the local API on :8000
    const target = (process.env.API_PROXY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};
