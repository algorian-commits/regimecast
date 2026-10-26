// The live recorder's /health, fetched server-side (it sends no CORS headers).
// Only the fields the Live Status page shows are passed on.
const RECORDER_URL = () => (process.env.RECORDER_URL ?? "https://recorder-production-796f.up.railway.app").replace(/\/$/, "");

export async function GET() {
  try {
    const r = await fetch(`${RECORDER_URL()}/health`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
    const d = await r.json();
    return Response.json({
      http_status: r.status,
      status: d.status,
      started: d.started,
      stale: d.stale ?? {},
      connected: d.connected ?? {},
      messages: d.messages ?? {},
      last_message: d.last_message ?? {},
      recent_error_count: Array.isArray(d.recent_errors) ? d.recent_errors.length : 0,
      checked_at: new Date().toISOString(),
    });
  } catch {
    return Response.json({ status: "unreachable", detail: "The recorder did not answer." }, { status: 502 });
  }
}
