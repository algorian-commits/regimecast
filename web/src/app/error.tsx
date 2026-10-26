"use client";

import { ErrorBox, PageTitle } from "@/components/ui";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <>
      <PageTitle title="Something went wrong" />
      <ErrorBox message="This page hit an unexpected problem. Your data is safe; try again." onRetry={reset} />
    </>
  );
}
