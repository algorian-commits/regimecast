import type { Metadata } from "next";
import { CrashReplay } from "@/components/replay";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Crash Replay" };

export default function ReplayPage() {
  return (
    <>
      <PageTitle title="Crash replay">
        Play a past crash minute by minute and watch what RegimeCast would have told you: the price, the market regime, the stress gauge and the
        chance that your position gets liquidated.
      </PageTitle>
      <CrashReplay />
    </>
  );
}
