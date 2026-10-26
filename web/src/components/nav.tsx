"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ThemeToggle } from "./theme-toggle";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/risk-check", label: "Risk Check" },
  { href: "/data-health", label: "Data Health" },
  { href: "/status", label: "Live Status" },
];

export function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const link = (l: (typeof LINKS)[number]) => {
    const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
    return (
      <Link
        key={l.href}
        href={l.href}
        onClick={() => setOpen(false)}
        aria-current={active ? "page" : undefined}
        className={`rounded-md px-3 py-2 text-sm font-medium ${
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
        <nav className="hidden flex-1 gap-1 sm:flex">{LINKS.map(link)}</nav>
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          <button
            className="rounded-md px-3 py-2 text-sm sm:hidden"
            aria-label="Menu"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>
      {open && <nav className="flex flex-col gap-1 border-t border-border px-4 py-2 sm:hidden">{LINKS.map(link)}</nav>}
    </header>
  );
}
