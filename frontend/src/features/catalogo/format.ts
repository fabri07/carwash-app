import { formatDuration } from "@/lib/time";
import { formatARS } from "@/lib/money";
import { formatPercent } from "@/lib/percent";
import type { PricingModeCode, Service, ServiceCategory } from "@/types/api";

export const MODE_LABELS: Record<PricingModeCode, string> = {
  PRECIO_FIJO: "Precio fijo",
  A_COTIZAR: "A cotizar",
};

export const MODE_OPTIONS = [
  { value: "PRECIO_FIJO", label: "Precio fijo" },
  { value: "A_COTIZAR", label: "A cotizar (el precio es de referencia)" },
];

function range(values: number[], render: (n: number) => string): string {
  const min = Math.min(...values);
  const max = Math.max(...values);
  return min === max ? render(min) : `${render(min)} – ${render(max)}`;
}

/**
 * Precios resumidos: fijo = un precio o el rango entre tamaños; a cotizar = «Desde $ X» con el
 * más bajo, que es el precio base de referencia (D4-6).
 */
export function priceSummary(service: Pick<Service, "pricing_mode" | "prices">): string {
  if (service.prices.length === 0) return "Sin precios";
  const cents = service.prices.map((p) => p.price_cents);
  if (service.pricing_mode === "A_COTIZAR") return `Desde ${formatARS(Math.min(...cents))}`;
  return range(cents, formatARS);
}

export function durationSummary(service: Pick<Service, "prices">): string {
  if (service.prices.length === 0) return "—";
  return range(
    service.prices.map((p) => p.duration_min),
    formatDuration,
  );
}

/** Motivo de un ítem salteado al aplicar la plantilla (`TemplateSkippedResponse.reason`). */
export function skipReasonLabel(reason: string): string {
  if (reason === "GLOSSARY_CODE") return "ya tenías este servicio";
  if (reason === "NAME") return "ya tenías un servicio propio con ese nombre";
  return reason;
}

/** Opciones del desplegable de categoría; `""` = sin categoría. */
export const categoryOptions = (categories: ServiceCategory[]) => [
  { value: "", label: "Sin categoría" },
  ...categories.map((c) => ({ value: c.id, label: c.name })),
];

/** Seña de un servicio en la tabla: su % propio o «General» (usa la del negocio, D4-17). */
export function depositLabel(service: Pick<Service, "deposit_bps">): string {
  return service.deposit_bps == null ? "General" : formatPercent(service.deposit_bps);
}
