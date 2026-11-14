import type { Metadata } from "next";

import { TurneroShell } from "@/features/turnero/TurneroShell";

/**
 * Turnero público (FASE-5-CONTRATO §4.2, PR 5.2): `/<slug>` y `/<slug>/mi-turno`, sin sesión.
 * No está en `PROTECTED_PREFIXES` del middleware: lo abre cualquiera desde el celular. Un slug
 * reservado nunca llega acá: esas carpetas son rutas propias (`lib/reservedSlugs.ts`).
 */
export const metadata: Metadata = {
  title: "Reservá tu turno",
  description: "Turnos online del lavadero.",
};

export default async function TurneroLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <TurneroShell slug={slug}>{children}</TurneroShell>;
}
