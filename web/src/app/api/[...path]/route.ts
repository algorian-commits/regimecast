// Forwards /api/* to the FastAPI backend, so the browser only ever talks to this site:
// no CORS, and the backend address is read at run time (API_URL), not baked into the build.
const API_URL = () => (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function forward(req: Request, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  const url = `${API_URL()}/${path.map(encodeURIComponent).join("/")}${new URL(req.url).search}`;
  const init: RequestInit = { method: req.method, headers: { "content-type": "application/json" }, cache: "no-store" };
  if (req.method === "POST") init.body = await req.text();
  try {
    const r = await fetch(url, { ...init, signal: AbortSignal.timeout(15000) });
    return new Response(r.body, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ status: "unreachable", detail: "The RegimeCast API did not answer." }, { status: 502 });
  }
}

export const GET = forward;
export const POST = forward;
