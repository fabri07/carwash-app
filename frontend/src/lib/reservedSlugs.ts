/**
 * Slugs que ningún negocio puede usar (FASE-5-CONTRATO §3.8): chocarían con una
 * ruta del sistema. Espejo de `RESERVED_SLUGS` de `backend/app/domain/slug.py`:
 * un test del backend compara las dos listas, y uno del front verifica que cada
 * carpeta de primer nivel de `src/app/(grupo)/` y de `public/` esté acá.
 */
export const RESERVED_SLUGS: readonly string[] = [
  "admin",
  "api",
  "app",
  "auth",
  "cambiar-clave",
  "catalogo",
  "configuracion",
  "dashboard",
  "debug",
  "health",
  "icons",
  "login",
  "logout",
  "onboarding",
  "ready",
  "register",
  "registro",
  "reservas",
  "static",
  "turnos",
  "v1",
  "www",
];

export function isReservedSlug(slug: string): boolean {
  return RESERVED_SLUGS.includes(slug.trim().toLowerCase());
}
