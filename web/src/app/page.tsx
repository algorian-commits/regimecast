import Link from "next/link";
import { MarketCards } from "@/components/market-cards";
import { ModelStatus } from "@/components/model-status";
import { NewsMoodPanel } from "@/components/news-mood-panel";
import { PageTitle } from "@/components/ui";

export default function Overview() {
  return (
    <div className="space-y-6">
      <PageTitle title="Risk Radar">
        Live prices for BTC, ETH and SOL perpetual futures, the news mood, and the risk models as they come online.{" "}
        <Link href="/risk-check" className="font-medium text-accent hover:underline">
          Check a trade →
        </Link>
      </PageTitle>
      <MarketCards />
      <NewsMoodPanel />
      <ModelStatus />
    </div>
  );
}
