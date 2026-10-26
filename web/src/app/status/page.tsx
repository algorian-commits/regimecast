import type { Metadata } from "next";
import { LiveStatus } from "@/components/live-status";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Live Status" };

export default function StatusPage() {
  return (
    <>
      <PageTitle title="Live Status">Is everything running? The API that answers this app, and the recorder that collects live market data around the clock.</PageTitle>
      <LiveStatus />
    </>
  );
}
