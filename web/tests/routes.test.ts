/**
 * @vitest-environment node
 *
 * Every page.tsx under web/app is classified, and the middleware and the session helper behave.
 * Adding a page that is in neither PUBLIC_ROUTES nor PROTECTED_ROUTES makes this file fail.
 */
import { readdirSync } from "node:fs";
import { join, sep } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  PROTECTED_ROUTES,
  PUBLIC_ROUTES,
  isPublicRoute,
  isStaticAsset,
  matchesRoute,
} from "@/lib/auth/routes";

const APP_DIR = join(__dirname, "..", "app");

/** app/(auth)/signin/page.tsx -> /signin ; app/jobs/[id]/page.tsx -> /jobs/[id] ; app/page.tsx -> / */
function pageRoutes(): string[] {
  const files = readdirSync(APP_DIR, { recursive: true }) as string[];
  return files
    .map((f) => f.split(sep).join("/"))
    .filter((f) => f === "page.tsx" || f.endsWith("/page.tsx"))
    .map((f) => {
      const parts = f.split("/").slice(0, -1).filter((p) => !/^\(.*\)$/.test(p)); // drop (route groups)
      return "/" + parts.join("/");
    });
}

/** `/jobs/[id]` is covered by the pattern `/jobs/**` (a dynamic segment is just another path part). */
function classify(route: string): "public" | "protected" | "both" | "none" {
  const concrete = route.replace(/\[[^\]]+\]/g, "x");
  const pub = PUBLIC_ROUTES.some((p) => matchesRoute(concrete, p));
  const prot = PROTECTED_ROUTES.some((p) => matchesRoute(concrete, p));
  return pub && prot ? "both" : pub ? "public" : prot ? "protected" : "none";
}

describe("route classification", () => {
  it("finds the app's pages", () => {
    const routes = pageRoutes();
    expect(routes.length).toBeGreaterThan(3);
    expect(routes).toContain("/signin");
    expect(routes).toContain("/");
  });

  it("every page is explicitly public or explicitly protected, never both and never neither", () => {
    const undecided = pageRoutes().filter((r) => classify(r) === "none");
    const both = pageRoutes().filter((r) => classify(r) === "both");
    expect(undecided, `Decide public or protected in web/lib/auth/routes.ts for: ${undecided.join(", ")}`).toEqual([]);
    expect(both, `In both lists: ${both.join(", ")}`).toEqual([]);
  });

  it("an unclassified page would be caught (the check really can fail)", () => {
    expect(classify("/definitely-not-a-page")).toBe("none");
  });

  it("the six signed-in pages are protected", () => {
    for (const route of ["/today", "/tracker", "/inbox", "/companies", "/sources", "/profile"]) {
      expect(classify(route)).toBe("protected");
      expect(isPublicRoute(route)).toBe(false);
    }
  });

  it("dynamic segments are matched by their pattern", () => {
    expect(isPublicRoute("/demo/jobs/123")).toBe(true); // /demo/** is public
    expect(isPublicRoute("/jobs/123")).toBe(false); // the real feed needs the session
    expect(isPublicRoute("/jobs")).toBe(false);
    expect(isPublicRoute("/tracker")).toBe(false);
  });

  it("a dot in a path does NOT make it public (only real static-file extensions do)", () => {
    expect(isStaticAsset("/favicon.ico")).toBe(true);
    expect(isStaticAsset("/_next/static/chunk.js")).toBe(true);
    expect(isStaticAsset("/api/export.csv")).toBe(false);
    expect(isPublicRoute("/api/export.csv")).toBe(false);
    expect(isPublicRoute("/tracker.json")).toBe(false);
  });
});

describe("middleware and requireSession", () => {
  const getSession = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    getSession.mockReset();
    vi.doMock("@/lib/auth/config", () => ({ auth: { api: { getSession } } }));
    vi.doMock("next/headers", () => ({ headers: async () => new Headers() }));
  });

  async function run(path: string) {
    const { middleware } = await import("@/middleware");
    const { NextRequest } = await import("next/server");
    return middleware(new NextRequest(new URL(path, "http://localhost:3000")));
  }

  it("redirects a signed-out visitor from a protected page to /signin with a callback", async () => {
    getSession.mockResolvedValue(null);
    const res = await run("/tracker");
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/signin");
    expect(loc.searchParams.get("callbackUrl")).toBe("/tracker");
  });

  it("lets a signed-in visitor through", async () => {
    getSession.mockResolvedValue({ user: { email: "me@example.com" } });
    const res = await run("/tracker");
    expect(res.headers.get("location")).toBeNull();
  });

  it("does not even look up a session for a public page", async () => {
    const res = await run("/signin");
    expect(res.headers.get("location")).toBeNull();
    expect(getSession).not.toHaveBeenCalled();
  });

  it("requireSession throws without a session and returns it with one", async () => {
    const { requireSession } = await import("@/lib/auth/session");
    getSession.mockResolvedValueOnce(null);
    await expect(requireSession()).rejects.toThrow("Not signed in");
    getSession.mockResolvedValueOnce({ user: { email: "me@example.com" } });
    await expect(requireSession()).resolves.toMatchObject({ user: { email: "me@example.com" } });
  });
});
