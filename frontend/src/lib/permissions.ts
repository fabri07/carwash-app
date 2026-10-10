import type { PermissionCode, User } from "@/types/api";

/**
 * Permisos del contrato (FASE-4-CONTRATO §2.1) con su etiqueta en castellano.
 *
 * El mapa está tipado contra el enum generado del OpenAPI (ADR-0013): si el
 * backend agrega un permiso, `tsc` falla acá hasta que tenga etiqueta y área.
 * Ningún otro archivo escribe el texto de un permiso.
 */
export const PERMISSION_LABELS: Record<PermissionCode, string> = {
  AGENDA_VER: "Ver agenda",
  TURNOS_GESTIONAR: "Gestionar turnos",
  JOBS_OPERAR: "Operar trabajos",
  COBROS_REGISTRAR: "Registrar cobros",
  COBROS_ANULAR: "Anular cobros",
  CAJA_VER: "Ver caja",
  CATALOGO_EDITAR: "Editar catálogo",
  PRECIOS_EDITAR: "Editar precios",
  CLIENTES_VER: "Ver clientes",
  CLIENTES_EDITAR: "Editar clientes",
  GASTOS_REGISTRAR: "Registrar gastos",
  REPORTES_VER: "Ver reportes",
};

/** Orden canónico (el del contrato), para mostrar y mandar siempre igual. */
export const ALL_PERMISSIONS = Object.keys(PERMISSION_LABELS) as PermissionCode[];

/** Áreas para agrupar los checkboxes del perfil. Cada permiso está en exactamente una. */
export const PERMISSION_GROUPS: { area: string; permissions: PermissionCode[] }[] = [
  { area: "Agenda y turnos", permissions: ["AGENDA_VER", "TURNOS_GESTIONAR"] },
  { area: "Operación", permissions: ["JOBS_OPERAR"] },
  { area: "Cobros y caja", permissions: ["COBROS_REGISTRAR", "COBROS_ANULAR", "CAJA_VER"] },
  { area: "Catálogo y precios", permissions: ["CATALOGO_EDITAR", "PRECIOS_EDITAR"] },
  { area: "Clientes", permissions: ["CLIENTES_VER", "CLIENTES_EDITAR"] },
  { area: "Gastos y reportes", permissions: ["GASTOS_REGISTRAR", "REPORTES_VER"] },
];

/** Ordena según el contrato y descarta repetidos (el backend rechaza repetidos con 422). */
export function sortPermissions(permissions: readonly PermissionCode[]): PermissionCode[] {
  const set = new Set(permissions);
  return ALL_PERMISSIONS.filter((p) => set.has(p));
}

/**
 * ¿Puede? El `OWNER` puede todo (Y5), aunque la lista venga vacía.
 *
 * Esto solo decide qué se MUESTRA. La autorización real es del servidor en
 * cada request (§2.3): esconder un botón no protege nada.
 */
export function can(
  user: Pick<User, "role"> | null,
  permissions: readonly PermissionCode[],
  permission: PermissionCode,
): boolean {
  if (!user) return false;
  if (user.role === "OWNER") return true;
  return permissions.includes(permission);
}
