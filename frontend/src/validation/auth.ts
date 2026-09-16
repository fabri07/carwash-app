import { z } from "zod";

/**
 * Un schema por formulario (ADR-0010 §3). El tipo del formulario sale de
 * `z.infer`: el schema es la única fuente, no hay interfaz paralela.
 */
export const loginSchema = z.object({
  email: z.string().trim().min(1, "Ingresá tu email").email("Email inválido"),
  // El contrato (`LoginRequest`) pide solo minLength 1: exigir 8 acá bloquearía
  // a quien tenga una contraseña más corta de antes de la regla.
  password: z.string().min(1, "Ingresá tu contraseña").max(128, "Máximo 128 caracteres"),
});

export type LoginInput = z.infer<typeof loginSchema>;

// Límites = los de `RegisterRequest` en backend/openapi.json.
export const registerSchema = z.object({
  tenant: z
    .string()
    .trim()
    .min(1, "Ingresá el nombre del negocio")
    .max(200, "Máximo 200 caracteres"),
  email: z.string().trim().min(1, "Ingresá tu email").email("Email inválido"),
  password: z.string().min(8, "Mínimo 8 caracteres").max(128, "Máximo 128 caracteres"),
});

export type RegisterInput = z.infer<typeof registerSchema>;
