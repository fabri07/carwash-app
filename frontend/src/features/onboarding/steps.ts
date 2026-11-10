import type {
  OnboardingMissingItem,
  OnboardingRequirementCode,
  OnboardingStepCode,
} from "@/types/api";

/**
 * Pasos del wizard de alta, en el orden de FASE-4-CONTRATO §6. El orden y los códigos son los del
 * backend (`domain/onboarding.py`); acá solo se agregan los textos.
 */
export interface WizardStep {
  id: OnboardingStepCode;
  label: string;
  title: string;
  description: string;
  /** Sin requisito para terminar: se puede saltar. */
  optional: boolean;
}

export const WIZARD_STEPS: WizardStep[] = [
  {
    id: "BUSINESS",
    label: "Negocio",
    title: "Tu negocio",
    description:
      "Cómo te encuentran tus clientes. Todo es opcional y lo podés cambiar después en Configuración.",
    optional: true,
  },
  {
    id: "BRANDING",
    label: "Personalización",
    title: "Personalización",
    description:
      "Colores, tipografía y logo. El logo es opcional: si ahora no se puede subir, seguí sin él.",
    optional: true,
  },
  {
    id: "SIZES",
    label: "Tamaños",
    title: "Tamaños y carrocerías",
    description:
      "Los tamaños de vehículo con los que cobrás distinto (por ejemplo Auto, SUV, Pickup). Abajo elegí qué tamaño se sugiere para cada carrocería.",
    optional: false,
  },
  {
    id: "SERVICES",
    label: "Servicios",
    title: "Servicios",
    description:
      "Elegí servicios de la plantilla de lavadero o de detailing y cargá su precio para cada tamaño. También podés crear los tuyos.",
    optional: false,
  },
  {
    id: "SCHEDULE",
    label: "Puestos y horarios",
    title: "Puestos y horarios",
    description: "Cuántos autos atendés a la vez (puestos) y en qué horarios abrís.",
    optional: false,
  },
  {
    id: "PAYMENTS",
    label: "Medios de pago",
    title: "Medios de pago",
    description: "Cómo te pagan tus clientes: efectivo, transferencia, Mercado Pago…",
    optional: false,
  },
  {
    id: "DEPOSIT",
    label: "Seña",
    title: "Seña y reservas",
    description:
      "Si pedís seña para reservar y cuánto. Si no la activás, el turno se confirma sin pago previo.",
    optional: true,
  },
  {
    id: "STAFF",
    label: "Empleados",
    title: "Empleados",
    description:
      "Opcional: creá las cuentas de quienes trabajan con vos. Lo podés hacer después desde Equipo.",
    optional: true,
  },
];

export function stepIndex(id: OnboardingStepCode): number {
  const i = WIZARD_STEPS.findIndex((s) => s.id === id);
  return i < 0 ? 0 : i;
}

const REQUIREMENT_MESSAGES: Record<OnboardingRequirementCode, string> = {
  VEHICLE_SIZE: "Cargá al menos un tamaño de vehículo.",
  SERVICE: "Agregá al menos un servicio.",
  SERVICE_PRICES: "Hay servicios sin precio para algún tamaño: cargalo con «Precios» en la lista.",
  RESOURCE: "Creá al menos un puesto.",
  BUSINESS_HOURS: "Cargá al menos una franja horaria.",
  PAYMENT_METHOD: "Cargá al menos un medio de pago para cobrar.",
};

/** Texto de un faltante, con el paso donde se resuelve. */
export function missingMessage(item: OnboardingMissingItem): string {
  const step = WIZARD_STEPS[stepIndex(item.step)]!;
  const base = REQUIREMENT_MESSAGES[item.requirement];
  const count = item.service_ids?.length ?? 0;
  const detail =
    item.requirement === "SERVICE_PRICES" && count > 1
      ? ` (${count} servicios)`
      : item.requirement === "SERVICE_PRICES" && count === 1
        ? " (1 servicio)"
        : "";
  return `${step.label}: ${base}${detail}`;
}
