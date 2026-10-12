import type { Metadata } from "next";

import { OperationPageClient } from "@/features/configuracion/OperationPageClient";

export const metadata: Metadata = {
  title: "Operación | carwash.app",
};

export default function OperationPage() {
  return <OperationPageClient />;
}
