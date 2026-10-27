import type { Metadata } from "next";
import { PageTitle } from "@/components/ui";
import { WhaleWatch } from "@/components/whale-watch";

export const metadata: Metadata = { title: "Whale Watch" };

export default function WhalesPage() {
  return (
    <>
      <PageTitle title="Whale Watch">
        What the biggest accounts on Hyperliquid, an on-chain exchange where every position is public, are doing in BTC, ETH and SOL, and when
        a big bet lands just before the market gets stressed.
      </PageTitle>
      <WhaleWatch />
    </>
  );
}
