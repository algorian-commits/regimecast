// Shared building blocks: cards, buttons, stats, badges and the loading / not-ready / error states.
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { Load } from "@/lib/api";

export function Card({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-border bg-surface p-4 sm:p-5 ${className}`}>
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold text-muted">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Button({ variant = "primary", className = "", ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  const look =
    variant === "primary"
      ? "bg-accent text-accent-fg hover:opacity-90 disabled:opacity-50"
      : "border border-border text-text hover:bg-surface-2 disabled:opacity-50";
  return <button className={`rounded-lg px-4 py-2.5 text-sm font-semibold transition ${look} ${className}`} {...p} />;
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "warn" | "bad" }) {
  const color = tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "";
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted">{label}</div>
      <div className={`break-words font-mono text-lg font-semibold sm:text-xl ${color}`}>{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Badge({ tone = "muted", children }: { tone?: "good" | "warn" | "bad" | "muted"; children: ReactNode }) {
  const c = {
    good: "bg-good/15 text-good",
    warn: "bg-warn/15 text-warn",
    bad: "bg-bad/15 text-bad",
    muted: "bg-surface-2 text-muted",
  }[tone];
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c}`}>{children}</span>;
}

export function Skeleton({ className = "h-6 w-full" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-surface-2 ${className}`} aria-hidden />;
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-5 ${i % 2 ? "w-2/3" : "w-full"}`} />
      ))}
    </div>
  );
}

/** Friendly placeholder for parts of RegimeCast that are not built or not deployed yet. */
export function NotReady({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface-2/50 p-4 text-sm">
      <div className="font-medium">{title}</div>
      {children && <div className="mt-1 text-muted">{children}</div>}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-bad/40 bg-bad/10 p-3 text-sm" role="alert">
      <span>{message}</span>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry} className="py-1.5">
          Try again
        </Button>
      )}
    </div>
  );
}

/** Render a Load<T>: skeleton while loading, placeholder when not ready, error with retry, else `children(data)`. */
export function LoadView<T>({
  load,
  notReady,
  onRetry,
  lines,
  children,
}: {
  load: Load<T>;
  notReady: (detail: string) => ReactNode;
  onRetry?: () => void;
  lines?: number;
  children: (data: T) => ReactNode;
}) {
  if (load.state === "loading") return <Loading lines={lines} />;
  if (load.state === "not_ready") return <>{notReady(load.detail)}</>;
  if (load.state === "error") return <ErrorBox message={load.message} onRetry={onRetry} />;
  return <>{children(load.data)}</>;
}

export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
      {children && <p className="mt-1 max-w-2xl text-sm text-muted">{children}</p>}
    </div>
  );
}
