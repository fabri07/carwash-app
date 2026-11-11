import { z } from "zod";

import { parsePercent } from "@/lib/percent";

/**
 * Un porcentaje escrito como texto (`"20"`, `"12,5"`), validado sin convertirlo: el formulario nunca
 * guarda un `NaN`. Lo comparten la seña del negocio, la seña propia de un servicio y la comisión de un
 * medio de pago, para que el formato y los mensajes no diverjan.
 *
 * - `min`: 0,01 para la seña (el backend exige 1..10000 bps); 0 para la comisión.
 * - `optional`: `""` es válido (la seña propia vacía usa la del negocio).
 */
export function percentText({
  min,
  example,
  optional = false,
}: {
  min: 0 | 0.01;
  example: string;
  optional?: boolean;
}) {
  const range = min === 0 ? "Entre 0 % y 100 %" : "Entre 0,01 % y 100 %";
  return z
    .string()
    .trim()
    .refine(
      (v) => (optional && v === "") || parsePercent(v) !== null,
      `Poné un porcentaje, por ejemplo ${example}`,
    )
    .refine((v) => {
      const p = optional && v === "" ? null : parsePercent(v);
      return p === null || (p >= min && p <= 100);
    }, range);
}
