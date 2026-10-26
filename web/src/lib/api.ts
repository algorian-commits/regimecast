// Typed API client. Types come from src/api/schema.d.ts, generated from docs/openapi.json
// with `npm run gen:api`; CI fails if that file is out of date.
import createClient from "openapi-fetch";
import type { components, paths } from "@/api/schema";

export type Schemas = components["schemas"];
export type Symbol = "BTCUSDT" | "ETHUSDT" | "SOLUSDT";
export const SYMBOLS: Symbol[] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

// Same-origin: /api/* is forwarded to the backend by src/app/api/[...path]/route.ts
export const api = createClient<paths>({ baseUrl: "/api" });

/** Every screen is in exactly one of these states. */
export type Load<T> =
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "not_ready"; detail: string }
  | { state: "error"; message: string };

/**
 * Turn an HTTP result into a Load state. 503 {"status":"not_ready"} (model or data not built
 * yet) and 404 (endpoint not deployed yet) become "not_ready" so the page shows a placeholder.
 */
export function toLoad<T>(status: number, body: unknown): Load<T> {
  if (status >= 200 && status < 300) return { state: "ok", data: body as T };
  const b = (body ?? {}) as { status?: string; detail?: unknown };
  if ((status === 503 && b.status === "not_ready") || status === 404) {
    return { state: "not_ready", detail: typeof b.detail === "string" ? b.detail : "not available yet" };
  }
  if (status === 422 && Array.isArray(b.detail)) {
    const msgs = b.detail.map((d: { msg?: string; loc?: unknown[] }) => {
      const field = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
      return `${field ? field + ": " : ""}${(d.msg ?? "invalid").replace(/^Value error, /, "")}`;
    });
    return { state: "error", message: msgs.join("; ") };
  }
  if (typeof b.detail === "string") return { state: "error", message: b.detail };
  return { state: "error", message: `The server answered ${status}.` };
}

/** Plain GET for endpoints not in the generated schema yet (e.g. /news-mood from Phase 3). */
export async function getJson<T>(path: string): Promise<Load<T>> {
  try {
    const r = await fetch(path, { cache: "no-store" });
    const body = await r.json().catch(() => null);
    return toLoad<T>(r.status, body);
  } catch {
    return { state: "error", message: "Could not reach the server. Check your connection." };
  }
}

/** Wrap an openapi-fetch call so network failures also become a Load state. */
export async function load<T>(call: () => Promise<{ data?: T; error?: unknown; response: Response }>): Promise<Load<T>> {
  try {
    const { data, error, response } = await call();
    return toLoad<T>(response.status, data ?? error);
  } catch {
    return { state: "error", message: "Could not reach the server. Check your connection." };
  }
}
