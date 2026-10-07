import { auth, isAllowedEmail } from "@/lib/auth/config";
import { isPublicRoute } from "@/lib/auth/routes";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Redirects signed-out visitors to /signin for every page that is not public.
 * The public/protected decision lives in `lib/auth/routes.ts`.
 */
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (isPublicRoute(pathname)) return NextResponse.next();

  const session = await auth.api.getSession({ headers: request.headers });
  if (!session) {
    const signinUrl = new URL("/signin", request.url);
    signinUrl.searchParams.set("callbackUrl", pathname + request.nextUrl.search);
    return NextResponse.redirect(signinUrl);
  }
  // A person taken off the invite list is locked out even with a live session.
  if (!isAllowedEmail(session.user.email)) return NextResponse.redirect(new URL("/not-allowed", request.url));
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
