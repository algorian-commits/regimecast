import type { Metadata } from "next";
import { PageTitle } from "@/components/ui";
import { Traders } from "@/components/traders";

export const metadata: Metadata = { title: "Traders" };

export default function TradersPage() {
  return (
    <>
      <PageTitle title="Traders">
        Hyperliquid&apos;s biggest accounts, sorted by whether their record holds up once luck is accounted for: what the skilled ones hold now, and
        where tracked positions would be liquidated. Lists of top traders are mostly lucky survivors, so read the sample sizes.
      </PageTitle>
      <Traders />
    </>
  );
}
