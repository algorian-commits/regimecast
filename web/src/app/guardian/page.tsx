import type { Metadata } from "next";
import { Guardian } from "@/components/guardian";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Guardian" };

export default function GuardianPage() {
  return (
    <>
      <PageTitle title="Position Guardian">
        RegimeCast watches your open leveraged positions every minute and tells you, here and on Telegram, when the risk changes, with a safer
        version of the position in the same message. It only reads: it can never trade or move money.
      </PageTitle>
      <Guardian />
    </>
  );
}
