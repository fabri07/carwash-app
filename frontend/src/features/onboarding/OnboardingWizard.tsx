"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Stepper } from "@/components/ui/stepper";
import { ServicesTab } from "@/features/catalogo/ServicesTab";
import { TemplatesTab } from "@/features/catalogo/TemplatesTab";
import { BrandingTab } from "@/features/configuracion/BrandingTab";
import {
  BusinessHoursTab,
  PaymentMethodsTab,
  ResourcesTab,
  VehicleSizesTab,
} from "@/features/configuracion/CatalogTabs";
import { BusinessTab, DepositTab } from "@/features/configuracion/SettingsTabs";
import { StaffTab } from "@/features/equipo/StaffTab";
import { onboardingKeys, useOnboardingStatus } from "@/features/onboarding/queries";
import { missingMessage, stepIndex, WIZARD_STEPS } from "@/features/onboarding/steps";
import { BodyTypeSizesForm } from "@/features/vehiculos/BodyTypeSizesForm";
import { useIsOwner } from "@/hooks/usePermission";
import { onboardingError, onboardingMissing } from "@/lib/errors";
import { HOME_PATH } from "@/lib/routes";
import { completeOnboarding } from "@/services/onboarding.service";
import { useAuthStore } from "@/stores/authStore";
import { toast } from "@/stores/toastStore";
import type { OnboardingMissingItem, OnboardingStepCode } from "@/types/api";

const NEXT_LABEL = "Guardar y seguir";

/**
 * Wizard de alta (FASE-4-CONTRATO §6, PR 4.6): los mismos formularios y listas de Configuración,
 * Catálogo y Equipo, uno por paso. Nada se guarda "al final": cada paso escribe en el servidor
 * (los formularios al tocar "Guardar y seguir", las listas en cada alta), así que cortar a mitad y
 * volver no pierde nada y repetir un paso no duplica (las plantillas son idempotentes).
 *
 * Al entrar se reanuda en el paso que dice `GET /onboarding/status`. Un paso con requisito no deja
 * avanzar hasta cumplirlo; los opcionales se pueden saltar. "Terminar" llama a
 * `POST /onboarding/complete`: si falta algo (409), muestra la lista y lleva al primer paso que
 * falta.
 */
export function OnboardingWizard() {
  const isOwner = useIsOwner();
  if (!isOwner) {
    return (
      <section aria-labelledby="onboarding-title" className="rounded-xl border bg-card">
        <h1 id="onboarding-title" className="sr-only">
          Alta del negocio
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="El alta la hace el dueño"
          description="Cuando el dueño termine de configurar el negocio vas a poder usar todas las pantallas."
          action={{ label: "Volver al inicio", href: HOME_PATH }}
        />
      </section>
    );
  }
  return <OwnerWizard />;
}

function OwnerWizard() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const setOnboardingCompleted = useAuthStore((s) => s.setOnboardingCompleted);
  const status = useOnboardingStatus();
  const [index, setIndex] = useState<number | null>(null);
  const [problems, setProblems] = useState<OnboardingMissingItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const data = status.data;
  // Se reanuda una sola vez, al llegar el primer estado: después manda la navegación del usuario.
  useEffect(() => {
    if (index === null && data) setIndex(stepIndex(data.resume_step));
  }, [data, index]);

  if (status.isLoading || (data && index === null)) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }
  if (status.isError || !data || index === null) {
    return (
      <p role="alert" className="text-sm font-medium text-destructive">
        {status.isError ? onboardingError(status.error) : "No se pudo cargar el alta."}
      </p>
    );
  }

  const step = WIZARD_STEPS[index]!;
  const isLast = index === WIZARD_STEPS.length - 1;

  function goTo(i: number) {
    setProblems([]);
    setError(null);
    setIndex(Math.max(0, Math.min(WIZARD_STEPS.length - 1, i)));
    if (typeof window !== "undefined") window.scrollTo?.({ top: 0 });
  }

  /** Avanza si el paso actual quedó hecho en el servidor; si no, dice qué falta. */
  async function next(from: OnboardingStepCode = step.id) {
    setBusy(true);
    setError(null);
    try {
      const fresh = await status.refetch();
      if (fresh.isError || !fresh.data) {
        setError(onboardingError(fresh.error));
        return;
      }
      const current = fresh.data.steps.find((s) => s.step === from);
      if (current && current.required && !current.done) {
        setProblems(fresh.data.missing.filter((m) => m.step === from));
        return;
      }
      goTo(stepIndex(from) + 1);
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    setBusy(true);
    setError(null);
    setProblems([]);
    try {
      await completeOnboarding();
    } catch (e) {
      const missing = onboardingMissing(e);
      if (missing && missing.length > 0) {
        setIndex(stepIndex(missing[0]!.step));
        setProblems(missing);
      } else {
        setError(onboardingError(e));
      }
      setBusy(false);
      return;
    }
    setOnboardingCompleted(true);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
      queryClient.invalidateQueries({ queryKey: onboardingKeys.status }),
      queryClient.invalidateQueries({ queryKey: ["config", "settings"] }),
    ]);
    toast.success("¡Listo! Tu negocio quedó configurado.");
    router.replace(HOME_PATH);
  }

  const done = new Map(data.steps.map((s) => [s.step, s.done]));

  return (
    <section aria-labelledby="onboarding-title" className="space-y-6">
      <div className="space-y-1">
        <h1 id="onboarding-title" className="text-2xl font-semibold">
          Configurá tu negocio
        </h1>
        <p className="text-sm text-muted-foreground">
          Unos pasos y empezás a usar la app. Lo que cargues queda guardado: si cortás, seguís desde
          donde quedaste.
        </p>
      </div>
      <Stepper steps={WIZARD_STEPS.map(({ id, label }) => ({ id, label }))} current={index} />

      <div className="space-y-4 rounded-xl border bg-card p-4 sm:p-6">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">
            {step.title}
            {step.optional && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">(opcional)</span>
            )}
          </h2>
          <p className="text-sm text-muted-foreground">{step.description}</p>
        </div>

        {problems.length > 0 && (
          <div
            role="alert"
            aria-label="Falta completar"
            className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm"
          >
            <p className="font-medium text-destructive">Para seguir falta:</p>
            <ul className="mt-1 list-disc pl-5">
              {problems.map((m) => (
                <li key={m.requirement}>{missingMessage(m)}</li>
              ))}
            </ul>
          </div>
        )}
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}

        <StepContent id={step.id} onSaved={() => next(step.id)} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button variant="outline" onClick={() => goTo(index - 1)} disabled={index === 0 || busy}>
          Atrás
        </Button>
        <div className="flex flex-wrap gap-2">
          {step.optional && !isLast && (
            <Button variant="ghost" onClick={() => goTo(index + 1)} disabled={busy}>
              {done.get(step.id) ? "Seguir sin cambios" : "Saltar este paso"}
            </Button>
          )}
          {!step.optional && (
            <Button onClick={() => next()} disabled={busy}>
              {busy ? "Revisando…" : "Siguiente"}
            </Button>
          )}
          {isLast && (
            <Button onClick={finish} disabled={busy}>
              {busy ? "Terminando…" : "Terminar"}
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

/** El contenido de cada paso: los mismos componentes de Configuración, Catálogo y Equipo. */
function StepContent({ id, onSaved }: { id: OnboardingStepCode; onSaved: () => void }) {
  switch (id) {
    case "BUSINESS":
      return <BusinessTab submitLabel={NEXT_LABEL} onSaved={onSaved} />;
    case "BRANDING":
      return <BrandingTab submitLabel={NEXT_LABEL} onSaved={onSaved} />;
    case "SIZES":
      return (
        <div className="space-y-6">
          <VehicleSizesTab />
          <div className="space-y-2">
            <h3 className="font-medium">Carrocería y tamaño</h3>
            <BodyTypeSizesForm />
          </div>
        </div>
      );
    case "SERVICES":
      return (
        <div className="space-y-6">
          <TemplatesTab />
          <div className="space-y-2">
            <h3 className="font-medium">Tus servicios</h3>
            <ServicesTab />
          </div>
        </div>
      );
    case "SCHEDULE":
      return (
        <div className="space-y-6">
          <ResourcesTab />
          <BusinessHoursTab />
        </div>
      );
    case "PAYMENTS":
      return <PaymentMethodsTab />;
    case "DEPOSIT":
      return <DepositTab submitLabel={NEXT_LABEL} onSaved={onSaved} />;
    case "STAFF":
      return <StaffTab />;
  }
}
