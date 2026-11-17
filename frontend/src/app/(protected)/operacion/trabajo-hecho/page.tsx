import type { Metadata } from "next";

import { Guard } from "@/features/operacion/Guard";
import { RetrospectiveForm } from "@/features/operacion/RetrospectiveForm";

export const metadata: Metadata = {
  title: "Cargar trabajo hecho | carwash.app",
};

export default function RetrospectivePage() {
  return (
    <Guard permission="JOBS_OPERAR" title="la carga de trabajos">
      <section className="space-y-4">
        <h1 className="text-2xl font-semibold">Cargar trabajo hecho</h1>
        <RetrospectiveForm />
      </section>
    </Guard>
  );
}
