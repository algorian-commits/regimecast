import { LiveDashboard } from "@/components/live-dashboard";
import { PageTitle } from "@/components/ui";

export default function Overview() {
  return (
    <div>
      <PageTitle title="Risk Radar">
        Live BTC, ETH and SOL perpetual futures: prices every second, the market regime behind the chart, the order book&apos;s inside, and
        our volatility, stress and news-mood readings.
      </PageTitle>
      <LiveDashboard />
    </div>
  );
}
