import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
const roots = new Set([
  "tickets",
  "employees",
  "repositories",
  "health",
  "ready",
  "agent-runs",
  "policies",
  "demo",
  "session",
  "approvals",
  "evaluations",
]);
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (
    !roots.has(path[0]) ||
    path.some((part) => !/^[a-zA-Z0-9_-]+$/.test(part))
  ) {
    return Response.json(
      { error: { code: "not_found", message: "Endpoint does not exist." } },
      { status: 404 },
    );
  }
  // Only the configured backend origin can receive requests or bearer credentials.
  const backend = new URL(process.env.BACKEND_URL ?? "http://127.0.0.1:8000");
  const target = new URL(
    `/api/${path.join("/")}${request.nextUrl.search}`,
    backend,
  );
  const headers = new Headers({ Accept: "application/json" });
  for (const key of ["authorization", "content-type", "x-request-id"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      cache: "no-store",
      redirect: "manual",
      body: ["GET", "HEAD"].includes(request.method)
        ? undefined
        : await request.text(),
      signal: AbortSignal.timeout(20000),
    });
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    for (const key of ["content-type", "x-request-id", "www-authenticate"]) {
      const value = upstream.headers.get(key);
      if (value) responseHeaders.set(key, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "backend_unavailable",
          message:
            "ResolveAI's backend is unavailable. Check that the service is running and retry.",
        },
      },
      { status: 502 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
