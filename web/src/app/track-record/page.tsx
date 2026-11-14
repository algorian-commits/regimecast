import type { Metadata } from "next";
import { TrackRecord } from "@/components/track-record";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Track Record" };

export default function TrackRecordPage() {
  return (
    <>
      <PageTitle title="Track Record">
        What RegimeCast forecast, written down live before the outcome was known, and how each forecast turned out. Only live results are here.
      </PageTitle>
      <TrackRecord />
    </>
  );
}
