import type { Metadata } from "next";
import { AgentView } from "@/components/agent";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Agent" };

export default function AgentPage() {
  return (
    <>
      <PageTitle title="Agent">
        A paper-trading risk manager for someone who holds BTC, ETH and SOL. Every hour it decides how much of each to hold (0x to 1x, never short) from
        RegimeCast&apos;s volatility forecast, stress warning and regime, and it is judged against simply holding (module M10).
      </PageTitle>
      <AgentView />
    </>
  );
}
