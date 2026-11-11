import { api } from "@/lib/api";
import type { OnboardingStatus } from "@/types/api";

/**
 * Wizard de alta (FASE-4-CONTRATO §6, PR 4.6). Solo el dueño.
 *
 * `complete` responde 409 `ONBOARDING_INCOMPLETE` con `detail.missing` si falta algún requisito
 * (lo lee `onboardingMissing` de `lib/errors.ts`); si el alta ya estaba terminada, 200 sin cambiar
 * la fecha.
 */
export async function getOnboardingStatus(): Promise<OnboardingStatus> {
  const res = await api.get<OnboardingStatus>("/onboarding/status");
  return res.data;
}

export async function completeOnboarding(): Promise<OnboardingStatus> {
  const res = await api.post<OnboardingStatus>("/onboarding/complete");
  return res.data;
}
