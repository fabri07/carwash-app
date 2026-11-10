"use client";

import { useQuery } from "@tanstack/react-query";

import { getOnboardingStatus } from "@/services/onboarding.service";

export const onboardingKeys = {
  status: ["onboarding", "status"] as const,
};

/**
 * Estado del alta. Se pide fresco al entrar y al avanzar (cada paso cambia algo en el servidor), así
 * que no se guarda en caché entre visitas.
 */
export function useOnboardingStatus(enabled = true) {
  return useQuery({
    queryKey: onboardingKeys.status,
    queryFn: getOnboardingStatus,
    enabled,
    staleTime: 0,
    gcTime: 0,
  });
}
