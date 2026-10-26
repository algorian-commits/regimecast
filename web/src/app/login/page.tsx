import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthForm } from "@/components/auth-form";
import { Loading, PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Log in" };

export default function LoginPage() {
  return (
    <>
      <PageTitle title="Log in">Your paper balance, risk limit and saved checks.</PageTitle>
      <Suspense fallback={<Loading lines={4} />}>
        <AuthForm mode="login" />
      </Suspense>
    </>
  );
}
