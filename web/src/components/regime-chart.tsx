"use client";

// Hourly candlestick chart with the background coloured by market regime (calm / active / stressed).
// History comes from /candles (each candle carries the regime known at its close); the forming
// candle updates every second from the live WebSocket. Built on TradingView's lightweight-charts.
import {
  AreaSeries,
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { getJson, type Schemas, type Symbol } from "@/lib/api";
import { tint } from "@/lib/dashboard";
import { coin } from "@/lib/format";
import type { LiveCandle } from "@/lib/live";
import { useLoad } from "@/lib/use-load";
import { ErrorBox, Loading, NotReady } from "./ui";

type Candle = Schemas["Candle"];
type Regime = "calm" | "active" | "stressed";
const TONE: Record<Regime, string> = { calm: "--good", active: "--warn", stressed: "--bad" };

const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function themeOptions() {
  const text = cssVar("--muted");
  const grid = cssVar("--border");
  return {
    layout: { background: { color: "transparent" }, textColor: text, fontFamily: "var(--font-geist-sans)", attributionLogo: true },
    grid: { vertLines: { color: "transparent" }, horzLines: { color: grid } },
    rightPriceScale: { borderColor: grid },
    timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
    crosshair: { horzLine: { labelBackgroundColor: cssVar("--surface-2") }, vertLine: { labelBackgroundColor: cssVar("--surface-2") } },
  };
}

function band(t: string, r: Regime | null | undefined) {
  const c = r ? tint(cssVar(TONE[r]), 0.16) : "rgba(0,0,0,0)";
  return { time: sec(t), value: 1, topColor: c, bottomColor: c, lineColor: c };
}

export function RegimeChart({ symbol, live }: { symbol: Symbol; live?: LiveCandle }) {
  const [hist, reload] = useLoad(() => getJson<Schemas["CandlesOut"]>(`/api/candles?symbol=${symbol}&hours=720`), symbol, 10 * 60_000);
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<{ api: IChartApi; candles: ISeriesApi<"Candlestick">; bands: ISeriesApi<"Area"> } | null>(null);
  const data = useRef<Candle[]>([]);

  // create the chart once; follow the light/dark theme
  useEffect(() => {
    if (!box.current) return;
    const api = createChart(box.current, {
      autoSize: true,
      ...themeOptions(),
      // the page keeps the mouse wheel and vertical swipes; drag or pinch moves the chart
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    });
    // background first, so the candles draw on top: a flat area at 1 on its own 0..1 scale fills the
    // whole height, each hour in its regime's colour (an area has no gaps between bars, a histogram does)
    const bands = api.addSeries(AreaSeries, {
      priceScaleId: "regime",
      lastValueVisible: false,
      priceLineVisible: false,
      lineVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
    });
    api.priceScale("regime").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
    const candles = api.addSeries(CandlestickSeries, {
      upColor: cssVar("--good"),
      downColor: cssVar("--bad"),
      wickUpColor: cssVar("--good"),
      wickDownColor: cssVar("--bad"),
      borderVisible: false,
    });
    chart.current = { api, candles, bands };
    const recolour = () => {
      api.applyOptions(themeOptions());
      candles.applyOptions({ upColor: cssVar("--good"), downColor: cssVar("--bad"), wickUpColor: cssVar("--good"), wickDownColor: cssVar("--bad") });
      bands.setData(data.current.map((c) => band(c.t, c.regime)));
    };
    const obs = new MutationObserver(recolour);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => {
      obs.disconnect();
      api.remove();
      chart.current = null;
    };
  }, []);

  // history
  useEffect(() => {
    const c = chart.current;
    if (!c || hist.state !== "ok") return;
    data.current = hist.data.candles;
    c.candles.setData(hist.data.candles.map((k) => ({ time: sec(k.t), open: k.open, high: k.high, low: k.low, close: k.close })));
    c.bands.setData(hist.data.candles.map((k) => band(k.t, k.regime)));
    const n = hist.data.candles.length; // 30 days loaded, the last 7 shown: drag or pinch for more
    c.api.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 168), to: n + 2 });
  }, [hist]);

  // the forming candle, every second
  useEffect(() => {
    const c = chart.current;
    const last = data.current[data.current.length - 1];
    if (!c || !live || !last || sec(live.t) < sec(last.t)) return;
    c.candles.update({ time: sec(live.t), open: live.open, high: live.high, low: live.low, close: live.close });
    c.bands.update(band(live.t, last.regime)); // regime as of the last complete hour
  }, [live]);

  const lastRegime = hist.state === "ok" ? hist.data.candles.at(-1)?.regime : undefined;
  return (
    <div>
      <div className="relative h-72 sm:h-96" role="img" aria-label={`${coin(symbol)} hourly candles, last 30 days, background coloured by market regime`}>
        <div ref={box} className="absolute inset-0" />
        {hist.state !== "ok" && (
          <div className="absolute inset-0 flex items-center justify-center bg-surface p-4">
            {hist.state === "loading" ? (
              <div className="w-full">
                <Loading lines={6} />
              </div>
            ) : hist.state === "not_ready" ? (
              <NotReady title="Chart is getting ready">The API rebuilds the hourly candles and regimes from live Binance data every 10 minutes.</NotReady>
            ) : (
              <ErrorBox message={hist.message} onRetry={reload} />
            )}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {(["calm", "active", "stressed"] as Regime[]).map((r) => (
          <span key={r} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: `color-mix(in srgb, var(${TONE[r]}) 35%, transparent)` }} aria-hidden />
            {r}
            {r === lastRegime && " (now)"}
          </span>
        ))}
        <span>Background = regime known at each candle&apos;s close (no hindsight). Times in UTC.</span>
      </div>
    </div>
  );
}
