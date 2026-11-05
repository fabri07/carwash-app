import type { Metadata } from "next";

import { ConfigPageClient } from "@/features/configuracion/ConfigPageClient";

export const metadata: Metadata = {
  title: "Configuración | carwash.app",
};

export default function ConfigPage() {
  return <ConfigPageClient />;
}
