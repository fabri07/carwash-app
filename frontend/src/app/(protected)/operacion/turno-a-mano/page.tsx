import type { Metadata } from "next";

import { Guard } from "@/features/operacion/Guard";
import { ManualBookingForm } from "@/features/operacion/ManualBookingForm";

export const metadata: Metadata = {
  title: "Turno a mano | carwash.app",
};

export default function ManualBookingPage() {
  return (
    <Guard permission="TURNOS_GESTIONAR" title="la carga de turnos">
      <section className="space-y-4">
        <h1 className="text-2xl font-semibold">Turno a mano</h1>
        <ManualBookingForm />
      </section>
    </Guard>
  );
}
