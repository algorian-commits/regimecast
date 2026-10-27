"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { authenticate, checkAuthForm } from "@/lib/account";
import { safeNext } from "@/lib/dashboard";
import { Button, Card, ErrorBox, NotReady } from "./ui";

const input = "w-full rounded-lg border border-border bg-bg px-3 py-2.5 text-base outline-none focus:border-accent";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<{ kind: "error" | "not_ready"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const errs = checkAuthForm(email, password);
    setErrors(errs);
    setProblem(null);
    if (Object.keys(errs).length) return;
    setBusy(true);
    const r = await authenticate(mode, email, password);
    setBusy(false);
    if (r.state === "ok") router.push(next);
    else if (r.state === "not_ready") setProblem({ kind: "not_ready", text: r.detail });
    else if (r.state === "error") setProblem({ kind: "error", text: r.message });
  }

  const signup = mode === "signup";
  return (
    <Card className="mx-auto max-w-md">
      <form onSubmit={submit} className="space-y-4" noValidate>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">E-mail</span>
          <input className={input} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!errors.email} />
          {errors.email && <span className="mt-1 block text-xs text-bad">{errors.email}</span>}
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium">Password</span>
          <input
            className={input}
            type="password"
            autoComplete={signup ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={!!errors.password}
          />
          {errors.password ? (
            <span className="mt-1 block text-xs text-bad">{errors.password}</span>
          ) : (
            signup && <span className="mt-1 block text-xs text-muted">At least 8 characters. Stored only as a one-way hash.</span>
          )}
        </label>
        {problem?.kind === "error" && <ErrorBox message={problem.text} />}
        {problem?.kind === "not_ready" && <NotReady title="Accounts are not available right now">{problem.text}</NotReady>}
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Please wait…" : signup ? "Create account" : "Log in"}
        </Button>
        <p className="text-center text-sm text-muted">
          {signup ? "Already have an account? " : "New here? "}
          <Link href={`${signup ? "/login" : "/signup"}${next !== "/account" ? `?next=${encodeURIComponent(next)}` : ""}`} className="font-medium text-accent hover:underline">
            {signup ? "Log in" : "Create an account"}
          </Link>
        </p>
        {signup && <p className="text-center text-xs text-muted">Paper trading only: no real money, no exchange keys.</p>}
      </form>
    </Card>
  );
}
