import type {
  OnboardingMissingItem,
  OnboardingRequirementCode,
  OnboardingStepCode,
} from "@/types/api";

/**
 * Textos de los pasos del wizard de alta, en el orden de FASE-4-CONTRATO §6. El orden y los códigos
 * son los del backend (`domain/onboarding.py`); si un paso es obligatorio lo dice el servidor
 * (`steps[].required` de `GET /onboarding/status`), no esta lista.
 */
export interface WizardStep {
  id: OnboardingStepCode;
  label: string;
  title: string;
  description: string;
}

export const WIZARD_STEPS: WizardStep[] = [
  {
    id: "BUSINESS",
    label: "Negocio",
    title: "Tu negocio",
    description:
      "Cómo te encuentran tus clientes. Todo es opcional y lo podés cambiar después en Configuración.",
  },
  {
    id: "BRANDING",
    label: "Personalización",
    title: "Personalización",
    description:
      "Colores, tipografía y logo. El logo es opcional: si ahora no se puede subir, seguí sin él.",
  },
  {
    id: "SIZES",
    label: "Tamaños",
    title: "Tamaños y carrocerías",
    description:
      "Los tamaños de vehículo con los que cobrás distinto (por ejemplo Auto, SUV, Pickup). Abajo elegí qué tamaño se sugiere para cada carrocería.",
  },
  {
    id: "SERVICES",
    label: "Servicios",
    title: "Servicios",
    description:
      "Elegí servicios de la plantilla de lavadero o de detailing y cargá su precio para cada tamaño. También podés crear los tuyos.",
  },
  {
    id: "SCHEDULE",
    label: "Puestos y horarios",
    title: "Puestos y horarios",
    description: "Cuántos autos atendés a la vez (puestos) y en qué horarios abrís.",
  },
  {
    id: "PAYMENTS",
    label: "Medios de pago",
    title: "Medios de pago",
    description: "Cómo te pagan tus clientes: efectivo, transferencia, Mercado Pago…",
  },
  {
    id: "DEPOSIT",
    label: "Seña",
    title: "Seña y reservas",
    description:
      "Si pedís seña para reservar y cuánto. Si no la activás, el turno se confirma sin pago previo.",
  },
  {
    id: "STAFF",
    label: "Empleados",
    title: "Empleados",
    description:
      "Creá las cuentas de quienes trabajan con vos. Lo podés hacer después desde Equipo.",
  },
];

export function stepIndex(id: OnboardingStepCode): number {
  const i = WIZARD_STEPS.findIndex((s) => s.id === id);
  return i < 0 ? 0 : i;
}

export function stepLabel(id: OnboardingStepCode): string {
  return WIZARD_STEPS[stepIndex(id)]!.label;
}

const REQUIREMENT_MESSAGES: Record<OnboardingRequirementCode, string> = {
  VEHICLE_SIZE: "Cargá al menos un tamaño de vehículo.",
  SERVICE: "Agregá al menos un servicio.",
  SERVICE_PRICES: "Hay servicios sin precio para algún tamaño: cargalo con «Precios» en la lista.",
  RESOURCE: "Creá al menos un puesto.",
  BUSINESS_HOURS: "Cargá al menos una franja horaria.",
  PAYMENT_METHOD: "Cargá al menos un medio de pago para cobrar.",
};

/** Texto de un faltante (se muestra dentro de su paso). */
export function missingMessage(item: OnboardingMissingItem): string {
  const base = REQUIREMENT_MESSAGES[item.requirement];
  if (item.requirement !== "SERVICE_PRICES") return base;
  const count = item.service_ids?.length ?? 0;
  if (count === 1) return `${base} (1 servicio)`;
  return count > 1 ? `${base} (${count} servicios)` : base;
}
