import type { Metadata } from "next";
import { Suspense } from "react";
import { RiskCheck } from "@/components/risk-check";
import { Loading, PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Risk Check" };

export default function RiskCheckPage() {
  return (
    <>
      <PageTitle title="Risk Check">
        Type a trade and see where it gets liquidated, how likely that is, and how much leverage keeps the risk small. Uses Binance&apos;s
        liquidation rules on the live mark price.
      </PageTitle>
      <Suspense fallback={<Loading lines={8} />}>
        <RiskCheck />
      </Suspense>
    </>
  );
}
