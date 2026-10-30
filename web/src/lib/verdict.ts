/** Small helpers for the trade verdict (Phase 12). */

/** A and B read as good, C as a warning, D and F as bad. */
export function gradeTone(g: string): "good" | "warn" | "bad" {
  return g === "A" || g === "B" ? "good" : g === "C" ? "warn" : "bad";
}

/** Colour of a gain or loss: Tailwind's text-good / text-bad / text-muted. */
export function moneyTone(x: number): "good" | "bad" | "muted" {
  return x > 0 ? "good" : x < 0 ? "bad" : "muted";
}

/** 12.3 -> "+12.3%", -4 -> "−4.0%" (a real minus sign). */
export function signedPct(x: number, digits = 1): string {
  return `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(digits)}%`;
}

/** 1234.5 -> "+$1,234.50", -20 -> "−$20.00". */
export function signedUsd(x: number): string {
  const v = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${x > 0 ? "+" : x < 0 ? "−" : ""}$${v}`;
}
