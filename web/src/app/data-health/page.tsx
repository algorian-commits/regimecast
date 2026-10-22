import type { Metadata } from "next";
import { DataHealth } from "@/components/data-health";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Data Health" };

export default function DataHealthPage() {
  return (
    <>
      <PageTitle title="Data Health">
        Every model learns from Binance history since 2020. This is the validator&apos;s report: what we have, what was missing or duplicated, and
        how each known problem is handled (module M1).
      </PageTitle>
      <DataHealth />
    </>
  );
}
