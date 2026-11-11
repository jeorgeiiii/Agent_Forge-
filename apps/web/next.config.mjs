export default {
  // Lets a verification build (NEXT_DIST_DIR=.next-build next build) run without clobbering
  // the .next folder a running `next dev` is using.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/:path*" }];
  },
};
