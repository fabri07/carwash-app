import type { Metadata } from "next";

import { TeamPageClient } from "@/features/equipo/TeamPageClient";

export const metadata: Metadata = {
  title: "Equipo | carwash.app",
};

export default function TeamPage() {
  return <TeamPageClient />;
}
