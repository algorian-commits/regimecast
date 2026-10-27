import type { Metadata } from "next";
import { Account } from "@/components/account";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Account" };

export default function AccountPage() {
  return (
    <>
      <PageTitle title="Your account">Paper balance, risk limit and settings. Your saved checks and alerts are private: only you can see them.</PageTitle>
      <Account />
    </>
  );
}
