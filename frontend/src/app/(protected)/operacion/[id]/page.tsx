import type { Metadata } from "next";

import { JobPageClient } from "@/features/operacion/JobPageClient";

export const metadata: Metadata = {
  title: "Trabajo | carwash.app",
};

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <JobPageClient id={id} />;
}
