// Headers sent with every page. CSP stays limited to framing/objects/base: Next.js needs inline scripts,
// so a strict script-src would need nonces (a later step).
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'" },
  // tells browsers to always use HTTPS here (browsers ignore it on plain http, so local stays fine)
  ...(process.env.NODE_ENV === "production"
    ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]
    : []),
];

export default {
  // Lets a verification build (NEXT_DIST_DIR=.next-build next build) run without clobbering
  // the .next folder a running `next dev` is using.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  poweredByHeader: false, // don't advertise "X-Powered-By: Next.js"
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    // where the /api proxy points (read at build time); default: the local API on :8000
    const target = (process.env.API_PROXY_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
    return [{ source: "/api/:path*", destination: `${target}/:path*` }];
  },
};
