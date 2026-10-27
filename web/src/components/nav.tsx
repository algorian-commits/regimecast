"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useSession } from "@/lib/account";
import { InboxBell } from "./inbox-bell";
import { ThemeToggle } from "./theme-toggle";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/risk-check", label: "Risk Check" },
  { href: "/agent", label: "Agent" },
  { href: "/replay", label: "Replay" },
  { href: "/whales", label: "Whale Watch" },
  { href: "/data-health", label: "Data Health" },
  { href: "/status", label: "Live Status" },
];

export function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const session = useSession();
  const link = (l: (typeof LINKS)[number]) => {
    const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
    return (
      <Link
        key={l.href}
        href={l.href}
        onClick={() => setOpen(false)}
        aria-current={active ? "page" : undefined}
        className={`whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium ${
          active ? "bg-surface-2 text-text" : "text-muted hover:text-text"
        }`}
      >
        {l.label}
      </Link>
    );
  };
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-2">
        <Link href="/" className="mr-4 flex items-center gap-2 font-semibold">
          <span className="inline-block h-6 w-6 rounded-md bg-accent" aria-hidden />
          RegimeCast
        </Link>
        <nav className="hidden flex-1 gap-1 lg:flex">{LINKS.map(link)}</nav>
        <div className="ml-auto flex items-center gap-1">
          {session.state === "in" && <InboxBell />}
          {session.state === "in" ? (
            <Link href="/account" aria-current={path.startsWith("/account") ? "page" : undefined} className="hidden rounded-md px-3 py-2 text-sm font-medium text-muted hover:text-text lg:inline-block" title={session.me.email}>
              Account
            </Link>
          ) : session.state === "out" ? (
            <Link href="/login" className="rounded-md px-3 py-2 text-sm font-medium text-accent hover:underline">
              Log in
            </Link>
          ) : null}
          <ThemeToggle />
          <button
            className="rounded-md px-3 py-2 text-sm lg:hidden"
            aria-label="Menu"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>
      {open && <nav className="flex flex-col gap-1 border-t border-border px-4 py-2 lg:hidden">{[...LINKS, ...(session.state === "in" ? [{ href: "/account", label: "Account" }] : [])].map(link)}</nav>}
    </header>
  );
}
