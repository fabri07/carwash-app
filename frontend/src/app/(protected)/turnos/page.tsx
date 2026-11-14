import type { Metadata } from "next";

import { TurnosPageClient } from "@/features/turnos/TurnosPageClient";

export const metadata: Metadata = {
  title: "Turnos | carwash.app",
};

export default function TurnosPage() {
  return <TurnosPageClient />;
}
