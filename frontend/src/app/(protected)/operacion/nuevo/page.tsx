import type { Metadata } from "next";

import { Guard } from "@/features/operacion/Guard";
import { WalkInForm } from "@/features/operacion/WalkInForm";

export const metadata: Metadata = {
  title: "Ingreso sin turno | carwash.app",
};

export default function WalkInPage() {
  return (
    <Guard permission="JOBS_OPERAR" title="el ingreso sin turno">
      <section className="space-y-4">
        <h1 className="text-2xl font-semibold">Ingreso sin turno</h1>
        <WalkInForm />
      </section>
    </Guard>
  );
}
