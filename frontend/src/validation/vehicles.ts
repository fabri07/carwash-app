import { z } from "zod";

import type { BodyTypeCode } from "@/types/api";

/**
 * Schemas del catálogo de vehículos (PR 4.4). Límites = los de `domain/vehicle_catalog.py` del
 * backend (marca 40, modelo 60, color 40). El backend además exige al menos una letra o número
 * (el nombre tiene que poder buscarse).
 */

/** Orden y etiquetas de las carrocerías: el mismo orden que el enum del backend. */
export const BODY_TYPES: { value: BodyTypeCode; label: string; hint: string }[] = [
  { value: "SEDAN", label: "Sedán", hint: "Cronos, Corolla, Vento" },
  { value: "HATCHBACK", label: "Hatchback", hint: "Gol, Onix, 208" },
  { value: "RURAL", label: "Rural", hint: "Familiar o monovolumen: Suran, Spin" },
  { value: "SUV", label: "SUV", hint: "EcoSport, Tracker, Compass" },
  { value: "PICKUP", label: "Pickup", hint: "Hilux, Ranger, Amarok" },
  { value: "UTILITARIO", label: "Utilitario", hint: "Kangoo, Partner, Fiorino" },
  { value: "FURGON", label: "Furgón", hint: "Sprinter, Master, Ducato" },
  { value: "MOTO", label: "Moto", hint: "Wave, YBR, Blitz" },
  { value: "OTRO", label: "Otro", hint: "Lo que no entra en las demás" },
];

const BODY_TYPE_VALUES = BODY_TYPES.map((b) => b.value) as [BodyTypeCode, ...BodyTypeCode[]];

export function bodyTypeLabel(value: BodyTypeCode): string {
  return BODY_TYPES.find((b) => b.value === value)?.label ?? value;
}

const LETTER_OR_DIGIT = /[a-z0-9]/i;

function name(max: number) {
  return z
    .string()
    .trim()
    .min(1, "Poné un nombre")
    .max(max, `Máximo ${max} caracteres`)
    .refine(
      (v) => LETTER_OR_DIGIT.test(v.normalize("NFKD")),
      "Tiene que tener al menos una letra o un número",
    );
}

export const makeSchema = z.object({ name: name(40) });
export type MakeInput = z.infer<typeof makeSchema>;

export const colorSchema = z.object({ name: name(40) });
export type ColorInput = z.infer<typeof colorSchema>;

export const modelSchema = z.object({
  name: name(60),
  body_type: z.enum(BODY_TYPE_VALUES, { errorMap: () => ({ message: "Elegí la carrocería" }) }),
});
export type ModelInput = z.infer<typeof modelSchema>;

/** Una fila por carrocería; `""` = no sugiere tamaño. */
export const bodyTypeSizesSchema = z.object({
  sizes: z.record(z.enum(BODY_TYPE_VALUES), z.string()),
});
export type BodyTypeSizesInput = z.infer<typeof bodyTypeSizesSchema>;
