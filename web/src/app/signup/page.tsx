import type { Metadata } from "next";
import { Suspense } from "react";
import { AuthForm } from "@/components/auth-form";
import { Loading, PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Sign up" };

export default function SignupPage() {
  return (
    <>
      <PageTitle title="Create an account">Keep a paper balance and risk limit, pick your favourite coins, and save your risk checks.</PageTitle>
      <Suspense fallback={<Loading lines={4} />}>
        <AuthForm mode="signup" />
      </Suspense>
    </>
  );
}
