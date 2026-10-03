/**
 * The one place that decides which pages need the signed-in, allow-listed session.
 *
 * Every page under `app/` must appear in exactly one of the two lists below; the test in
 * `tests/routes.test.ts` fails when a page is in neither, so adding a page forces a decision.
 * Anything not public is protected by the middleware, so forgetting is safe, but the test
 * makes the decision explicit.
 */

/** Reachable without signing in. */
export const PUBLIC_ROUTES = [
  "/signin",
  "/signup", // dead end: sign-up is disabled, kept only until the sign-in screen is rebuilt
  "/not-allowed",
  "/demo/**", // the public demo mode shows synthetic data only
  "/api/auth/**", // Better Auth's own endpoints
  // Legacy v1 read-only pages: public ONLY until task h2-73 removes them.
  "/",
  "/jobs",
  "/jobs/**",
  "/skills",
  "/trends",
  "/coverage",
] as const;

/** Pages that require the session. Add new pages here (or to PUBLIC_ROUTES) when you create them. */
export const PROTECTED_ROUTES = [
  // none yet: the v1 dashboard was removed in h2-02b; the tracker, inbox and profile arrive with the UI tasks
] as readonly string[];

export function matchesRoute(pathname: string, pattern: string): boolean {
  if (pattern.endsWith("/**")) {
    const prefix = pattern.slice(0, -3);
    return pathname === prefix || pathname.startsWith(prefix + "/");
  }
  return pathname === pattern;
}

/** Next.js internals and static files (an explicit extension list, NOT "anything with a dot"). */
const STATIC_EXT = /\.(?:ico|png|jpe?g|gif|svg|webp|avif|css|js|map|txt|xml|woff2?|ttf)$/i;

export function isStaticAsset(pathname: string): boolean {
  return pathname.startsWith("/_next/") || STATIC_EXT.test(pathname);
}

export function isPublicRoute(pathname: string): boolean {
  return isStaticAsset(pathname) || PUBLIC_ROUTES.some((p) => matchesRoute(pathname, p));
}
