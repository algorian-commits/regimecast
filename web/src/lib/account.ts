"use client";

// Login state shared by the whole site (nav, dashboard, account page) and the account calls.
// The session itself is an httpOnly cookie that scripts cannot read; we only ask /api/me who we are.
import { useSyncExternalStore } from "react";
import { api, load, type Load, type Schemas, type Symbol } from "./api";

export type Me = Schemas["MeOut"];
export type Profile = Schemas["ProfileOut"];
export type SavedCheck = Schemas["CheckOut"];
export type Alert = Schemas["AlertOut"];

export type Session =
  | { state: "loading" }
  | { state: "out" }
  | { state: "in"; me: Me }
  | { state: "unavailable"; detail: string }; // no database on this server, or it is down

/** Turn the /me answer into a session state: 401 means logged out, not an error. */
export function toSession(status: number, body: unknown): Session {
  if (status === 200) return { state: "in", me: body as Me };
  if (status === 401) return { state: "out" };
  const d = (body ?? {}) as { detail?: unknown };
  return { state: "unavailable", detail: typeof d.detail === "string" ? d.detail : "Accounts are not available right now." };
}

let session: Session = { state: "loading" };
const listeners = new Set<() => void>();
let started = false;

function setSession(s: Session) {
  session = s;
  listeners.forEach((l) => l());
}

export async function refreshSession() {
  try {
    const { data, error, response } = await api.GET("/me");
    setSession(toSession(response.status, data ?? error));
  } catch {
    setSession({ state: "unavailable", detail: "Could not reach the server." });
  }
}

function subscribe(l: () => void) {
  listeners.add(l);
  if (!started) {
    started = true;
    refreshSession();
  }
  return () => listeners.delete(l);
}

const SERVER: Session = { state: "loading" };
export function useSession() {
  return useSyncExternalStore(subscribe, () => session, () => SERVER);
}

// ------------------------------------------------------------------ auth
export const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;

export function checkAuthForm(email: string, password: string): Record<string, string> {
  const e: Record<string, string> = {};
  if (!EMAIL_RE.test(email.trim())) e.email = "Enter a valid e-mail address.";
  if (password.length < 8) e.password = "Use at least 8 characters.";
  else if (password.length > 128) e.password = "Use at most 128 characters.";
  return e;
}

export async function authenticate(mode: "login" | "signup", email: string, password: string): Promise<Load<Me>> {
  const body = { email: email.trim(), password };
  const r = await load(() => (mode === "login" ? api.POST("/auth/login", { body }) : api.POST("/auth/signup", { body })));
  if (r.state === "ok") setSession({ state: "in", me: r.data });
  return r;
}

export async function logout() {
  await load(() => api.POST("/auth/logout"));
  setSession({ state: "out" });
}

// ------------------------------------------------------------------ profile and settings
export type ProfileForm = { balance: string; riskPct: string };

/** Paper balance (USDT) and risk limit (% chance of liquidation) from the form, or field errors. */
export function parseProfileForm(f: ProfileForm): { ok: true; body: { paper_balance: number; risk_limit: number } } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const balance = Number(f.balance.replace(/[,\s$]/g, ""));
  const risk = Number(f.riskPct.replace(/[%\s]/g, ""));
  if (!Number.isFinite(balance) || balance <= 0) errors.balance = "Enter a balance above 0.";
  else if (balance > 1e9) errors.balance = "Keep it under 1 billion USDT.";
  if (!Number.isFinite(risk) || risk < 0.05 || risk > 50) errors.riskPct = "Use a limit between 0.05% and 50%.";
  if (Object.keys(errors).length) return { ok: false, errors };
  return { ok: true, body: { paper_balance: balance, risk_limit: Math.round(risk * 100) / 10000 } };
}

export async function updateProfile(body: Schemas["ProfileIn"]): Promise<Load<Profile>> {
  const r = await load(() => api.PATCH("/me/profile", { body }));
  if (r.state === "ok" && session.state === "in") setSession({ state: "in", me: { ...session.me, profile: r.data } });
  return r;
}

/**
 * What the Risk Check should start from for a logged-in user (Phase 7 wires this into its form):
 * the risk limit sets the safe leverage and the largest position; the paper balance is the
 * cross-margin wallet; the margin mode is the user's default.
 */
export function riskDefaults(p: Profile) {
  return { risk_limit: p.risk_limit, wallet_balance: p.paper_balance, margin_mode: p.default_margin_mode };
}

/** Coins in the user's order: favourites first, then the rest. */
export function orderCoins(all: Symbol[], favourites: string[] | undefined): Symbol[] {
  const fav = (favourites ?? []).filter((s): s is Symbol => (all as string[]).includes(s));
  return [...fav, ...all.filter((s) => !fav.includes(s))];
}

// ------------------------------------------------------------------ saved checks and alerts
export const saveCheck = (body: Schemas["CheckIn"]) => load(() => api.POST("/me/checks", { body }));
export const listChecks = (query: { symbol?: Symbol; since?: string; until?: string; limit?: number; offset?: number } = {}) =>
  load(() => api.GET("/me/checks", { params: { query } }));
export const deleteCheck = (id: number) => load(() => api.DELETE("/me/checks/{check_id}", { params: { path: { check_id: id } } }));
export const listAlerts = () => load(() => api.GET("/me/alerts"));
export const createAlert = (body: Schemas["AlertIn"]) => load(() => api.POST("/me/alerts", { body }));
export const deleteAlert = (id: number) => load(() => api.DELETE("/me/alerts/{alert_id}", { params: { path: { alert_id: id } } }));

// ------------------------------------------------------------------ alert inbox (Phase 11)
export type Inbox = Schemas["InboxOut"];
export const readInbox = () => load(() => api.GET("/me/inbox", { params: { query: { limit: 30 } } }));
export const markInboxRead = (ids?: number[]) => load(() => api.POST("/me/inbox/read", { body: { ids: ids ?? null } }));
