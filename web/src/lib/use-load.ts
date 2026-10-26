"use client";

import { useEffect, useRef, useState } from "react";
import type { Load } from "./api";

/**
 * Run `fetcher` on mount, whenever `key` changes, and every `refreshMs` if given.
 * Returns the current state (loading while a new key is fetched) and a reload function.
 */
export function useLoad<T>(fetcher: () => Promise<Load<T>>, key: string, refreshMs?: number) {
  const [result, setResult] = useState<{ key: string; load: Load<T> } | null>(null);
  const [round, setRound] = useState(0);
  const latest = useRef(fetcher);
  useEffect(() => {
    latest.current = fetcher;
  });
  useEffect(() => {
    let live = true;
    const tick = () => latest.current().then((s) => live && setResult({ key, load: s }));
    tick();
    const id = refreshMs ? setInterval(tick, refreshMs) : undefined;
    return () => {
      live = false;
      if (id) clearInterval(id);
    };
  }, [key, refreshMs, round]);
  const load: Load<T> = result && result.key === key ? result.load : { state: "loading" };
  return [load, () => setRound((n) => n + 1)] as const;
}
