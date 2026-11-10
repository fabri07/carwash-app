import type { Metadata } from "next";

import { OnboardingWizard } from "@/features/onboarding/OnboardingWizard";

export const metadata: Metadata = {
  title: "Configurá tu negocio | carwash.app",
};

export default function OnboardingPage() {
  return <OnboardingWizard />;
}
