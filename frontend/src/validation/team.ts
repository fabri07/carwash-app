import { z } from "zod";

import { ALL_PERMISSIONS } from "@/lib/permissions";
import { newPassword } from "@/validation/auth";
import type { PermissionCode } from "@/types/api";

/**
 * Schemas del equipo (PR 4.1). Límites = los de `schemas/team.py` del backend;
 * el servidor vuelve a validar todo.
 */

/** `^[a-z0-9._-]{3,40}$` (CHECK de `users.username`). El backend pasa a minúsculas. */
export const USERNAME_PATTERN = /^[a-z0-9._-]{3,40}$/;

const username = z
  .string()
  .trim()
  .toLowerCase()
  .min(1, "Elegí un usuario")
  .regex(
    USERNAME_PATTERN,
    "De 3 a 40 caracteres: letras sin tilde, números, punto, guion o guion bajo",
  );

/** Forma mínima de un email; la validación fina la hace el backend (`EmailStr`). */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Email opcional: vacío es "sin email". */
const optionalEmail = z
  .string()
  .trim()
  .max(254, "Máximo 254 caracteres")
  .refine((v) => v === "" || EMAIL_SHAPE.test(v), "Email inválido");

const profileId = z.string().min(1, "Falta elegir el perfil");

export const staffCreateSchema = z.object({
  username,
  password: newPassword,
  permission_profile_id: profileId,
  email: optionalEmail,
});
export type StaffCreateInput = z.infer<typeof staffCreateSchema>;

export const staffEditSchema = z.object({
  username,
  permission_profile_id: profileId,
  email: optionalEmail,
});
export type StaffEditInput = z.infer<typeof staffEditSchema>;

export const resetPasswordSchema = z
  .object({
    password: newPassword,
    confirm_password: z.string().min(1, "Repetí la contraseña"),
  })
  .refine((v) => v.password === v.confirm_password, {
    path: ["confirm_password"],
    message: "Las contraseñas no coinciden",
  });
export type ResetPasswordInput = z.infer<typeof resetPasswordSchema>;

export const profileSchema = z.object({
  name: z.string().trim().min(1, "Poné un nombre").max(40, "Máximo 40 caracteres"),
  permissions: z.array(z.enum(ALL_PERMISSIONS as [PermissionCode, ...PermissionCode[]])),
});
export type ProfileInput = z.infer<typeof profileSchema>;
