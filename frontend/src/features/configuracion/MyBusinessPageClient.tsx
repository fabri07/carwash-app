"use client";

import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { BookingRulesForm } from "@/features/configuracion/BookingRulesForm";
import { BusinessForm } from "@/features/configuracion/BusinessForm";
import { DepositForm } from "@/features/configuracion/DepositForm";
import { configKeys, useSettings } from "@/features/configuracion/queries";
import { useIsOwner } from "@/hooks/usePermission";
import { configError, FormSubmitError } from "@/lib/errors";
import { updateSettings } from "@/services/configuracion.service";
import { toast } from "@/stores/toastStore";
import type { SettingsPatch } from "@/types/api";

function Section({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={id}
      className="space-y-4 rounded-xl border bg-card p-4 text-card-foreground shadow sm:p-6"
    >
      <div className="space-y-1">
        <h2 id={id} className="font-semibold">
          {title}
        </h2>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {children}
    </section>
  );
}

/**
 * `/configuracion/mi-negocio` (PR 4.2): datos del negocio, seña y reglas de la
 * agenda. Solo el dueño (Y5). Cada sección guarda por separado con
 * `PATCH /settings` y manda solo sus campos: guardar la seña no pisa un texto
 * que otro dejó a medio editar en otra sección.
 *
 * Armada en secciones a propósito: el PR 4.5 suma la de personalización
 * (logo, paleta y tipografía).
 */
export function MyBusinessPageClient() {
  const isOwner = useIsOwner();

  if (!isOwner) {
    return (
      <section aria-labelledby="business-title" className="rounded-xl border bg-card">
        <h1 id="business-title" className="sr-only">
          Mi negocio
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a esta sección"
          description="Los datos del negocio, la seña y las reglas de la agenda los configura el dueño. Si necesitás un cambio, pedíselo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  return <MyBusinessSections />;
}

function MyBusinessSections() {
  const queryClient = useQueryClient();
  const settingsQuery = useSettings();
  const settings = settingsQuery.data;

  async function save(payload: SettingsPatch) {
    try {
      const updated = await updateSettings(payload);
      queryClient.setQueryData(configKeys.settings, updated);
    } catch (e) {
      throw new FormSubmitError(configError(e));
    }
    toast.success("Cambios guardados.");
  }

  return (
    <section aria-labelledby="business-title" className="space-y-4">
      <div>
        <h1 id="business-title" className="text-2xl font-semibold">
          Mi negocio
        </h1>
        <p className="text-sm text-muted-foreground">
          Cómo te encuentran tus clientes y las reglas para reservar.
        </p>
      </div>

      {settingsQuery.isError ? (
        <p role="alert" className="rounded-xl border bg-card p-6 text-sm text-destructive">
          {configError(settingsQuery.error).message}
        </p>
      ) : !settings ? (
        <div role="status" aria-label="Cargando" className="flex justify-center p-10">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-primary" />
        </div>
      ) : (
        <>
          <Section
            id="seccion-datos"
            title="Datos del negocio"
            description="Lo que ven tus clientes cuando sacan turno."
          >
            <BusinessForm settings={settings} onSubmit={save} />
          </Section>
          <Section
            id="seccion-sena"
            title="Seña"
            description="Una sola seña para todos los servicios."
          >
            <DepositForm settings={settings} onSubmit={save} />
          </Section>
          <Section
            id="seccion-agenda"
            title="Reglas de agenda"
            description="Tolerancias y tiempos que la agenda aplica sola."
          >
            <BookingRulesForm settings={settings} onSubmit={save} />
          </Section>
        </>
      )}
    </section>
  );
}
