import type { Metadata } from "next";

import { MyBooking } from "@/features/turnero/MyBooking";

export const metadata: Metadata = { title: "Mi turno" };

export default async function MiTurnoPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <MyBooking slug={slug} />;
}
