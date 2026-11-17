import type { Metadata } from "next";

import { InspectionPageClient } from "@/features/operacion/InspectionForm";

export const metadata: Metadata = {
  title: "Recepción | carwash.app",
};

export default async function InspectionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <InspectionPageClient id={id} />;
}
