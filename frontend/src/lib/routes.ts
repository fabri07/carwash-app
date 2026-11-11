/**
 * Rutas que más de un módulo necesita conocer. Viven acá y no en `lib/api.ts`
 * para que las páginas no importen el cliente HTTP solo por una constante.
 */

/** Pantalla del cambio de clave obligatorio (FASE-4-CONTRATO §2.3). */
export const CHANGE_PASSWORD_PATH = "/cambiar-clave";

/** Adónde se va después de entrar si no hay `?next=`. */
export const HOME_PATH = "/dashboard";

/** Wizard de alta (FASE-4-CONTRATO §6): adonde va el dueño hasta terminarlo. */
export const ONBOARDING_PATH = "/onboarding";
