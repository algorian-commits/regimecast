"use client";

// The bell in the nav (M12): fired alerts from /me/inbox, checked every 30 s while logged in.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { markInboxRead, readInbox, type Inbox } from "@/lib/account";
import { coin } from "@/lib/format";
import { clock } from "@/lib/replay";
import { useLoad } from "@/lib/use-load";

const KIND_LABEL: Record<string, string> = {
  price_above: "price",
  price_below: "price",
  stress_elevated: "stress",
  stress_high: "stress",
  regime_stressed: "regime",
  position_risk: "your position",
  whale_watch: "whale watch",
};

export function InboxBell() {
  const [box, reload] = useLoad(() => readInbox(), "inbox", 30_000);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const data: Inbox | null = box.state === "ok" ? box.data : null;
  const unread = data?.unread ?? 0;
  return (
    <div className="relative" ref={ref}>
      <button
        className="relative rounded-md px-2.5 py-2 text-muted hover:text-text"
        aria-label={unread ? `Alerts: ${unread} unread` : "Alerts"}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute right-0.5 top-0.5 min-w-4 rounded-full bg-bad px-1 text-center text-[10px] font-bold leading-4 text-white">{unread > 99 ? "99+" : unread}</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-[min(92vw,24rem)] rounded-xl border border-border bg-surface shadow-lg" role="dialog" aria-label="Alert inbox">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-sm font-semibold">Alerts</span>
            {unread > 0 && (
              <button className="text-xs text-accent hover:underline" onClick={async () => (await markInboxRead(), reload())}>
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {box.state === "loading" && <p className="p-3 text-sm text-muted">Loading…</p>}
            {box.state !== "ok" && box.state !== "loading" && <p className="p-3 text-sm text-muted">The inbox is not available right now.</p>}
            {data && data.items.length === 0 && (
              <p className="p-3 text-sm text-muted">
                Nothing yet. Set alerts on your{" "}
                <Link href="/account" className="text-accent hover:underline" onClick={() => setOpen(false)}>
                  account page
                </Link>
                ; they are checked once a minute.
              </p>
            )}
            {data && data.items.length > 0 && (
              <ul className="divide-y divide-border">
                {data.items.map((it) => (
                  <li key={it.id} className={`px-3 py-2.5 text-sm ${it.read ? "" : "bg-accent/5"}`}>
                    <div className="flex items-center gap-2">
                      {!it.read && <span className="h-2 w-2 shrink-0 rounded-full bg-accent" aria-label="unread" />}
                      <span className="font-medium">{it.title}</span>
                    </div>
                    <p className="mt-0.5 text-muted">{it.message}</p>
                    <div className="mt-1 text-xs text-muted">
                      {coin(it.symbol)} · {KIND_LABEL[it.kind] ?? it.kind} · {clock(it.fired_at)}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
