import Link from "next/link";
import { PageTitle } from "@/components/ui";

export default function NotFound() {
  return (
    <PageTitle title="Page not found">
      That page does not exist.{" "}
      <Link href="/" className="font-medium text-accent hover:underline">
        Back to the Risk Radar
      </Link>
    </PageTitle>
  );
}
