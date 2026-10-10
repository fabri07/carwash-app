"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { listProfiles, listStaff, type PageParams } from "@/services/team.service";

/**
 * Queries del equipo. Las claves cuelgan de `["team", …]` para poder
 * invalidar todo el equipo de una vez tras una alta, edición o baja.
 */
export const teamKeys = {
  all: ["team"] as const,
  staff: ["team", "staff"] as const,
  staffPage: (p: PageParams) => ["team", "staff", p] as const,
  profiles: ["team", "profiles"] as const,
  profilesPage: (p: PageParams) => ["team", "profiles", p] as const,
};

/** Página de empleados. `keepPreviousData`: al pasar de página la tabla no parpadea vacía. */
export function useStaffPage(params: PageParams) {
  return useQuery({
    queryKey: teamKeys.staffPage(params),
    queryFn: () => listStaff(params),
    placeholderData: keepPreviousData,
  });
}

export function useProfilesPage(params: PageParams) {
  return useQuery({
    queryKey: teamKeys.profilesPage(params),
    queryFn: () => listProfiles(params),
    placeholderData: keepPreviousData,
  });
}

/**
 * Tope de la paginación del backend (`MAX_LIMIT`). Los perfiles de un negocio
 * son un puñado (vienen 3 de fábrica); para el selector del alta y para
 * mostrar el nombre del perfil en la tabla de empleados alcanza una página.
 */
export const PROFILE_OPTIONS_LIMIT = 200;

/** Todos los perfiles (hasta el tope), para selectores y para nombrar el perfil de un empleado. */
export function useProfileOptions() {
  return useQuery({
    queryKey: teamKeys.profilesPage({ limit: PROFILE_OPTIONS_LIMIT, offset: 0 }),
    queryFn: () => listProfiles({ limit: PROFILE_OPTIONS_LIMIT, offset: 0 }),
  });
}
