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
import { configKeys } from "@/features/configuracion/queries";
import { BusinessTab, DepositTab } from "@/features/configuracion/SettingsTabs";
import { StaffTab } from "@/features/equipo/StaffTab";
import { onboardingKeys, useOnboardingStatus } from "@/features/onboarding/queries";
import { missingMessage, stepIndex, stepLabel, WIZARD_STEPS } from "@/features/onboarding/steps";
import { BodyTypeSizesForm } from "@/features/vehiculos/BodyTypeSizesForm";
import { useIsOwner, usePermissionsLoaded } from "@/hooks/usePermission";
import { genericMessage, onboardingMissing } from "@/lib/errors";
import { HOME_PATH } from "@/lib/routes";
import { completeOnboarding } from "@/services/onboarding.service";
import { useAuthStore } from "@/stores/authStore";
import { toast } from "@/stores/toastStore";
import type { OnboardingMissingItem, OnboardingStepCode } from "@/types/api";

const NEXT_LABEL = "Guardar y seguir";

/** Raíces de caché que no son datos del negocio: su refetch no cambia el estado del alta. */
const NOT_BUSINESS_DATA = new Set<unknown>(["onboarding", "auth"]);

/**
 * Wizard de alta (FASE-4-CONTRATO §6, PR 4.6): los mismos formularios y listas de Configuración,
 * Catálogo y Equipo, uno por paso. Nada se guarda "al final": cada paso escribe en el servidor
 * (los formularios al tocar "Guardar y seguir", las listas en cada alta), así que cortar a mitad y
 * volver no pierde nada y repetir un paso no duplica (las plantillas son idempotentes).
 *
 * Al entrar se reanuda en el paso que dice `GET /onboarding/status`. Un paso obligatorio (lo dice el
 * servidor en `steps[].required`) no deja avanzar hasta cumplirlo; los opcionales se pueden saltar.
 * "Terminar" llama a `POST /onboarding/complete`: si falta algo (409), lleva al primer paso que
 * falta, muestra ahí lo suyo y ofrece ir a los otros.
 */
export function OnboardingWizard() {
  const isOwner = useIsOwner();
  // El usuario persistido puede no estar (localStorage vacío) o estar viejo: hasta que llega la
  // sesión del servidor no se sabe si es el dueño. Mismo dato en el servidor y en el primer render
  // del cliente (no se persiste), así que tampoco hay desajuste de hidratación.
  const loaded = usePermissionsLoaded();
  if (!loaded) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }
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

/**
 * El estado del alta cambia con lo que se guarda dentro de cada paso (un tamaño, el logo, la
 * paleta…). Las pestañas reusadas no avisan al wizard; en cambio todas escriben su resultado en la
 * caché de React Query. Ante cualquier dato del negocio que se actualiza, se relee el estado (con
 * una pausa corta para juntar varias actualizaciones).
 */
function useRefreshStatusOnBusinessData() {
  const queryClient = useQueryClient();
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" || event.action.type !== "success") return;
      if (NOT_BUSINESS_DATA.has(event.query.queryKey[0])) return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: onboardingKeys.status });
      }, 250);
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
  }, [queryClient]);
}

function OwnerWizard() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const setOnboardingCompleted = useAuthStore((s) => s.setOnboardingCompleted);
  const status = useOnboardingStatus();
  const [index, setIndex] = useState<number | null>(null);
  /** Faltantes a mostrar: los del paso actual en el paso; los demás, como enlaces a su paso. */
  const [problems, setProblems] = useState<OnboardingMissingItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useRefreshStatusOnBusinessData();

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
        {status.isError ? genericMessage(status.error) : "No se pudo cargar el alta."}
      </p>
    );
  }

  const step = WIZARD_STEPS[index]!;
  const isLast = index === WIZARD_STEPS.length - 1;
  const serverSteps = new Map(data.steps.map((s) => [s.step, s]));
  const required = serverSteps.get(step.id)?.required ?? false;
  const stepDone = serverSteps.get(step.id)?.done ?? false;
  const here = problems.filter((m) => m.step === step.id);
  const elsewhere = [...new Set(problems.filter((m) => m.step !== step.id).map((m) => m.step))];

  /** `keepProblems`: ir a otro paso desde la lista de faltantes del 409, sin perderla. */
  function goTo(i: number, keepProblems = false) {
    if (!keepProblems) setProblems([]);
    setError(null);
    setIndex(Math.max(0, Math.min(WIZARD_STEPS.length - 1, i)));
    if (typeof window !== "undefined") window.scrollTo?.({ top: 0 });
  }

  /** Avanza si el paso quedó hecho en el servidor; si no, dice qué le falta. */
  async function next(from: OnboardingStepCode = step.id) {
    setBusy(true);
    setError(null);
    try {
      const fresh = await status.refetch();
      if (fresh.isError || !fresh.data) {
        setError(genericMessage(fresh.error));
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
        setError(genericMessage(e));
      }
      setBusy(false);
      return;
    }
    setOnboardingCompleted(true);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] }),
      queryClient.invalidateQueries({ queryKey: onboardingKeys.status }),
      queryClient.invalidateQueries({ queryKey: configKeys.settings }),
    ]);
    toast.success("¡Listo! Tu negocio quedó configurado.");
    router.replace(HOME_PATH);
  }

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
      <Stepper
        steps={WIZARD_STEPS.map(({ id, label }) => ({
          id,
          label,
          done: serverSteps.get(id)?.done ?? false,
        }))}
        current={index}
      />

      <div className="space-y-4 rounded-xl border bg-card p-4 sm:p-6">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">
            {step.title}
            {!required && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">(opcional)</span>
            )}
          </h2>
          <p className="text-sm text-muted-foreground">{step.description}</p>
        </div>

        {(here.length > 0 || elsewhere.length > 0) && (
          <div
            role="alert"
            aria-label="Falta completar"
            className="space-y-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm"
          >
            {here.length > 0 && (
              <>
                <p className="font-medium text-destructive">Para seguir falta:</p>
                <ul className="list-disc pl-5">
                  {here.map((m) => (
                    <li key={m.requirement}>{missingMessage(m)}</li>
                  ))}
                </ul>
              </>
            )}
            {elsewhere.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span>También falta completar:</span>
                {elsewhere.map((id) => (
                  <Button
                    key={id}
                    variant="link"
                    className="h-auto p-0"
                    onClick={() => goTo(stepIndex(id), true)}
                  >
                    {stepLabel(id)}
                  </Button>
                ))}
              </div>
            )}
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
          {!required && !isLast && (
            <Button variant="ghost" onClick={() => goTo(index + 1)} disabled={busy}>
              {stepDone ? "Seguir sin cambios" : "Saltar este paso"}
            </Button>
          )}
          {required && !isLast && (
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
