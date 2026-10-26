"use client";

import { api, load } from "@/lib/api";
import { useLoad } from "@/lib/use-load";
import { Card, LoadView, NotReady } from "./ui";

/** Forecast and regime for BTC. Both models come in later phases, so these show placeholders for now. */
export function ModelStatus() {
  const [fc, r1] = useLoad(() => load(() => api.GET("/forecast", { params: { query: { symbol: "BTCUSDT" } } })), "btc");
  const [rg, r2] = useLoad(() => load(() => api.GET("/regime", { params: { query: { symbol: "BTCUSDT" } } })), "btc");
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Card title="Market regime (BTC)">
        <LoadView load={rg} onRetry={r2} lines={2} notReady={() => <NotReady title="Regime refreshing">Calm, active or stressed: the API recomputes the regime from live Binance data every 10 minutes.</NotReady>}>
          {(d) => <pre className="overflow-x-auto text-xs">{JSON.stringify(d, null, 2)}</pre>}
        </LoadView>
      </Card>
      <Card title="Volatility forecast, next 24h (BTC)">
        <LoadView load={fc} onRetry={r1} lines={2} notReady={() => <NotReady title="Forecast refreshing">The API recomputes the HAR-S volatility forecast from live Binance data every 10 minutes. Until the first one is ready, the Risk Check uses a clearly labelled placeholder.</NotReady>}>
          {(d) => <pre className="overflow-x-auto text-xs">{JSON.stringify(d, null, 2)}</pre>}
        </LoadView>
      </Card>
    </div>
  );
}
