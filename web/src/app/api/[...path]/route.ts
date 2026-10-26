// Forwards /api/* to the FastAPI backend, so the browser only ever talks to this site:
// no CORS, and the backend address is read at run time (API_URL), not baked into the build.
// The login cookie travels both ways (Cookie in, Set-Cookie out), so it belongs to this site's domain.
const API_URL = () => (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function forward(req: Request, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  const url = `${API_URL()}/${path.map(encodeURIComponent).join("/")}${new URL(req.url).search}`;
  const headers: Record<string, string> = { "content-type": "application/json" };
  const cookie = req.headers.get("cookie");
  if (cookie) headers.cookie = cookie;
  const init: RequestInit = { method: req.method, headers, cache: "no-store", redirect: "manual" };
  if (req.method !== "GET" && req.method !== "HEAD") init.body = await req.text();
  try {
    const r = await fetch(url, { ...init, signal: AbortSignal.timeout(15000) });
    const out = new Headers({ "content-type": r.headers.get("content-type") ?? "application/json", "cache-control": "no-store" });
    for (const c of r.headers.getSetCookie()) out.append("set-cookie", c);
    return new Response(r.body, { status: r.status, headers: out });
  } catch {
    return Response.json({ status: "unreachable", detail: "The RegimeCast API did not answer." }, { status: 502 });
  }
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const DELETE = forward;
