import { z } from "zod";

/**
 * Un schema por formulario (ADR-0010 §3). El tipo del formulario sale de
 * `z.infer`: el schema es la única fuente, no hay interfaz paralela.
 */
export const loginSchema = z.object({
  // D4-3: el dueño entra con su email, el empleado con su usuario. Un solo
  // campo, sin validar formato de email (un usuario no lo tiene). Límite = el
  // de `LoginRequest.identifier`.
  identifier: z
    .string()
    .trim()
    .min(1, "Ingresá tu email o usuario")
    .max(254, "Máximo 254 caracteres"),
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

/**
 * Límite superior de una clave nueva: bcrypt ignora lo que pasa de 72 bytes y
 * el backend lo rechaza (`NewPassword`). Se mide en bytes UTF-8, no en
 * caracteres: "ñ" ocupa dos.
 */
export const PASSWORD_MAX_BYTES = 72;

/** Largo en bytes UTF-8, sin depender de `TextEncoder` (no está en todos los entornos). */
export function utf8Length(value: string): number {
  let bytes = 0;
  for (const ch of value) {
    const cp = ch.codePointAt(0)!;
    bytes += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
  }
  return bytes;
}

export const newPassword = z
  .string()
  .min(8, "Mínimo 8 caracteres")
  .refine((v) => utf8Length(v) <= PASSWORD_MAX_BYTES, {
    message: "Es demasiado larga (máximo 72 bytes)",
  });

export const changePasswordSchema = z
  .object({
    current_password: z.string().min(1, "Ingresá tu contraseña actual"),
    new_password: newPassword,
    confirm_password: z.string().min(1, "Repetí la contraseña nueva"),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    path: ["confirm_password"],
    message: "Las contraseñas no coinciden",
  })
  .refine((v) => v.new_password !== v.current_password, {
    path: ["new_password"],
    message: "La nueva tiene que ser distinta de la actual",
  });

export type ChangePasswordInput = z.infer<typeof changePasswordSchema>;
