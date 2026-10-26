import { connection } from "next/server";

// Where the browser opens the live price WebSocket. Route handlers cannot forward WebSockets,
// so the browser connects to the API directly (public prices only, no cookie involved).
// PUBLIC_API_URL is for setups where API_URL is an internal name (docker compose: http://api:8000).
export async function GET() {
  await connection(); // read the environment at request time, not at build time
  const base = (process.env.PUBLIC_API_URL ?? process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  return Response.json({ ws_url: base.replace(/^http/, "ws") + "/ws/live" }, { headers: { "cache-control": "no-store" } });
}
