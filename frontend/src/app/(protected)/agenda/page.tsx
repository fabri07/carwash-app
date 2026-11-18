import type { Metadata } from "next";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";

export const metadata: Metadata = {
  title: "Agenda | carwash.app",
};

export default async function AgendaPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  return <AgendaPageClient initialDate={date} />;
}
